#!/system/bin/sh
# gps_feeder.sh - GPS injection daemon for the onn 7 Core head unit.
#
# Injects a position into ALL THREE location providers (gps, network, fused).
# That triple-push is REQUIRED: once the tablet has internet, GMS derives its
# own (wrong) network fix, and the framework's `fused` provider prefers that
# over a `gps`-only mock. Pushing all three keeps them in agreement.
#
# MULTI-PHONE ARBITRATION (why this is safe with two drivers):
#   Every push may carry &src=<name> (the phone's own name, e.g. "phone-a"
#   or "phone-b"). Rules:
#     - If no push for > 15s, ANY source may take over (previous driver left).
#     - If a push arrives from a DIFFERENT source while the current one is
#       still fresh (< 15s), it is REJECTED with 409. Two active phones can
#       therefore never fight over the mock position.
#     - The current holder keeps control until it goes silent.
#
# Inputs:
#   HTTP :8080  GET /cgi-bin/gps?loc=LAT,LON[&acc=N][&src=NAME]
#   TCP  :5555  raw "LAT,LON[,ACC[,SRC]]"

PORT_TCP="${1:-5555}"
PORT_HTTP="${2:-8080}"
WEBROOT=/data/local/tmp/geo
LOG=/data/local/tmp/gps_feeder.log
FIFO=/data/local/tmp/gps.fifo
STOP=/data/local/tmp/gps_feeder.stop
PIDF=/data/local/tmp/gps_feeder.pid
NCPIDF=/data/local/tmp/gps_feeder.ncp
HTTPPIDF=/data/local/tmp/gps_feeder.httppid
STATE=/data/local/tmp/gps_last_source      # "epoch|src|lat,lon"

STALE_SECS=15

log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }

prepare_providers() {
  cmd location set-location-enabled true 2>/dev/null
  appops set 2000 android:mock_location allow 2>/dev/null
  cmd location providers remove-test-provider gps     2>/dev/null
  cmd location providers add-test-provider gps --requiresSatellite \
      --supportsAltitude --supportsSpeed --supportsBearing --powerRequirement 1 2>/dev/null
  cmd location providers remove-test-provider network 2>/dev/null
  cmd location providers add-test-provider network --requiresNetwork \
      --powerRequirement 1 2>/dev/null
  cmd location providers remove-test-provider fused   2>/dev/null
  cmd location providers add-test-provider fused --powerRequirement 1 \
      --supportsAltitude --supportsSpeed --supportsBearing 2>/dev/null
  for p in gps network fused; do
    cmd location providers set-test-provider-enabled "$p" true 2>/dev/null
  done
}

# Inject via the DEX injector (supports speed+bearing; `cmd location` does NOT).
# Falls back to `cmd location` (position only) if the injector is not running.
FIXFILE=/data/local/tmp/gps_fix
push_fix() {   # $1=lat,lon  $2=acc  $3=speed  $4=bearing
  if [ -f "$FIXFILE" ] || [ -w /data/local/tmp ]; then
    printf '%s,%s,%s,%s\n' "$1" "${2:-5}" "${3:-0}" "${4:-0}" > "$FIXFILE" 2>/dev/null && return 0
  fi
  for p in gps network fused; do
    cmd location providers set-test-provider-location "$p" \
        --location "$1" --accuracy "${2:-5}" >/dev/null 2>&1
  done
}

# arbitrate: returns 0 if $1 (src) may push, 1 if rejected
arbitrate() {
  SRC="$1"
  NOW=$(date +%s)
  if [ -f "$STATE" ]; then
    OLD=$(cat "$STATE" 2>/dev/null)
    # NOTE: unescaped ${v%%|*} returns EMPTY in Android's /system/bin/sh - use cut.
    OLD_T=$(echo "$OLD" | cut -d'|' -f1)
    OLD_SRC=$(echo "$OLD" | cut -d'|' -f2)
    case "$OLD_T" in ''|*[!0-9]*) OLD_T=0 ;; esac
    AGE=$((NOW - OLD_T))
    if [ "$OLD_SRC" != "$SRC" ] && [ "$AGE" -lt "$STALE_SECS" ]; then
      log "REJECT src=$SRC held by=$OLD_SRC age=${AGE}s"
      return 1
    fi
    [ "$OLD_SRC" != "$SRC" ] && log "TAKEOVER $OLD_SRC -> $SRC (prev idle ${AGE}s)"
  else
    log "ACQUIRE src=$SRC"
  fi
  return 0
}

# Derive speed (m/s) and bearing (deg) between successive fixes, so speed
# widgets and the map heading work even though senders only report position.
PREV_FILE=/data/local/tmp/gps_prev
LAST_T=0
LAST_LAT=""
LAST_LON=""

haversine() {  # $1=lat1 $2=lon1 $3=lat2 $4=lon2  -> metres
  awk -v la1="$1" -v lo1="$2" -v la2="$3" -v lo2="$4" 'BEGIN{
    pi=3.14159265358979; R=6371000
    p1=la1*pi/180; p2=la2*pi/180
    dp=(la2-la1)*pi/180; dl=(lo2-lo1)*pi/180
    a=sin(dp/2)^2 + cos(p1)*cos(p2)*sin(dl/2)^2
    print 2*R*atan2(sqrt(a), sqrt(1-a))
  }'
}
bearing_to() {  # $1=lat1 $2=lon1 $3=lat2 $4=lon2 -> degrees
  awk -v la1="$1" -v lo1="$2" -v la2="$3" -v lo2="$4" 'BEGIN{
    pi=3.14159265358979
    p1=la1*pi/180; p2=la2*pi/180; dl=(lo2-lo1)*pi/180
    y=sin(dl)*cos(p2); x=cos(p1)*sin(p2)-sin(p1)*cos(p2)*cos(dl)
    b=atan2(y,x)*180/pi; if(b<0) b+=360; printf "%.1f", b
  }'
}

derive_motion() {   # $1=lat $2=lon  -> echoes "speed bearing"
  NOW=$(date +%s)
  if [ -n "$LAST_LAT" ] && [ "$NOW" -gt "$LAST_T" ]; then
    D=$(haversine "$LAST_LAT" "$LAST_LON" "$1" "$2")
    DT=$((NOW - LAST_T))
    SP=$(awk -v d="$D" -v t="$DT" 'BEGIN{ printf "%.1f", d/t }')
    BR=$(bearing_to "$LAST_LAT" "$LAST_LON" "$1" "$2")
    # ignore GPS jitter: under ~0.5 m/s is standing still
    CHK=$(awk -v s="$SP" 'BEGIN{ print (s<0.5)?1:0 }')
    [ "$CHK" = "1" ] && SP=0.0
  else
    SP=0.0; BR=0.0
  fi
  LAST_T=$NOW; LAST_LAT="$1"; LAST_LON="$2"
  echo "$SP $BR"
}

setup_http() {
  mkdir -p "$WEBROOT/cgi-bin"
  cat > "$WEBROOT/cgi-bin/gps" <<'CGI'
#!/system/bin/sh
echo "Content-Type: text/plain"
echo ""
qval() { echo "$QUERY_STRING" | tr '&' '\n' | sed -n "s/^$1=//p"; }
LOC=$(qval loc); ACC=$(qval acc); SRC=$(qval src)
SP_IN=$(qval sp); BR_IN=$(qval br)     # sender-supplied (authoritative)
[ -z "$ACC" ] && ACC=5
[ -z "$SRC" ] && SRC=unknown
[ -z "$LOC" ] && { echo "ERR missing loc"; exit 0; }

STATE=/data/local/tmp/gps_last_source
NOW=$(date +%s)
if [ -f "$STATE" ]; then
  OLD=$(cat "$STATE" 2>/dev/null)
  OLD_T=$(echo "$OLD" | cut -d'|' -f1)
  OLD_SRC=$(echo "$OLD" | cut -d'|' -f2)
  case "$OLD_T" in ''|*[!0-9]*) OLD_T=0 ;; esac
  AGE=$((NOW - OLD_T))
  if [ "$OLD_SRC" != "$SRC" ] && [ "$AGE" -lt 15 ]; then
    echo "REJECT 409 held by $OLD_SRC (${AGE}s ago)"
    exit 0
  fi
fi
echo "$NOW|$SRC|$LOC" > "$STATE"
# Inject via the DEX injector when available (carries speed+bearing).
# `cmd location` cannot carry speed/bearing, so it is only the fallback.
FIXFILE=/data/local/tmp/gps_fix
LAT=${LOC%%,*}; LON=${LOC#*,}
PREV=/data/local/tmp/gps_prev
SP=0.0; BR=0.0
# Prefer speed/bearing reported by the sender's own GNSS (authoritative).
# Deriving on-tablet is only a fallback; it is valid ONLY between two fixes
# from the SAME source, otherwise interleaved senders yield nonsense.
if [ -n "$SP_IN" ]; then
  SP=$SP_IN
  BR=${BR_IN:-0.0}
elif [ -f "$PREV" ] && [ "$(cut -d'|' -f4 "$PREV")" = "$SRC" ]; then
  PLAT=$(cut -d'|' -f1 "$PREV"); PLON=$(cut -d'|' -f2 "$PREV"); PT=$(cut -d'|' -f3 "$PREV")
  NOWT=$(date +%s); DT=$((NOWT - PT))
  [ "$DT" -lt 1 ] && DT=1
  D=$(awk -v la1="$PLAT" -v lo1="$PLON" -v la2="$LAT" -v lo2="$LON" 'BEGIN{
     pi=3.14159265358979; R=6371000; p1=la1*pi/180; p2=la2*pi/180
     dp=(la2-la1)*pi/180; dl=(lo2-lo1)*pi/180
     a=sin(dp/2)^2+cos(p1)*cos(p2)*sin(dl/2)^2
     print 2*R*atan2(sqrt(a),sqrt(1-a))}')
  # Jitter guard: a phone standing still still reports sub-metre drift, which
  # over a 1s interval looks like several m/s. Require a real displacement
  # (>=3 m) AND clamp to a plausible road speed (<=80 m/s ~ 288 km/h).
  SP=$(awk -v d="$D" -v t="$DT" 'BEGIN{
        s=d/t
        if (d<3.0 || s<1.0 || s>80.0) s=0
        printf "%.1f", s }')
  if [ "$(awk -v s="$SP" 'BEGIN{print (s>0)?1:0}')" = "1" ]; then
    BR=$(awk -v la1="$PLAT" -v lo1="$PLON" -v la2="$LAT" -v lo2="$LON" 'BEGIN{
       pi=3.14159265358979; p1=la1*pi/180; p2=la2*pi/180; dl=(lo2-lo1)*pi/180
       y=sin(dl)*cos(p2); x=cos(p1)*sin(p2)-sin(p1)*cos(p2)*cos(dl)
       b=atan2(y,x)*180/pi; if(b<0)b+=360; printf "%.1f", b}')
  else
    BR=0.0     # stationary: no meaningful heading
  fi
fi
echo "$LAT|$LON|$(date +%s)|$SRC" > "$PREV"
if [ -f "$FIXFILE" ]; then
  printf '%s,%s,%s,%s\n' "$LOC" "$ACC" "$SP" "$BR" > "$FIXFILE" 2>/dev/null
else
  for p in gps network fused; do
    cmd location providers set-test-provider-location "$p" --location "$LOC" --accuracy "$ACC" >/dev/null 2>&1
  done
fi
echo "OK $LOC $ACC src=$SRC"
echo "$(date '+%m-%d %H:%M:%S') http src=$SRC loc=$LOC sp=$SP br=$BR" >> /data/local/tmp/gps_feeder.log
CGI
  chmod 755 "$WEBROOT/cgi-bin/gps"; chmod 755 "$WEBROOT"

  # Publish the phone-side sender APK for deployment (survives reboot).
  # /data/local/tmp is cleared on boot, so the APK is stored under /data/adb
  # and re-copied into the webroot here on every start.
  APK_SRC=/data/adb/cargps/CarGpsSender.apk
  if [ -f "$APK_SRC" ]; then
    cp -f "$APK_SRC" "$WEBROOT/CarGpsSender.apk" 2>/dev/null
    chmod 644 "$WEBROOT/CarGpsSender.apk" 2>/dev/null
  fi

  # /cgi-bin/apk streams the APK with an Android MIME type. busybox httpd has
  # no .apk entry, so the plain file URL sends no Content-Type and Chrome saves
  # the download as .zip (an APK is a zip, so it sniffs it as one).
  CGI_SRC=/data/adb/cargps/apk-cgi
  if [ -f "$CGI_SRC" ]; then
    cp -f "$CGI_SRC" "$WEBROOT/cgi-bin/apk" 2>/dev/null
    chmod 755 "$WEBROOT/cgi-bin/apk" 2>/dev/null
  fi

  cat > "$WEBROOT/cgi-bin/status" <<'CGI'
#!/system/bin/sh
echo "Content-Type: text/plain"
echo ""
STATE=/data/local/tmp/gps_last_source
NOW=$(date +%s)
if [ ! -f "$STATE" ]; then echo "NONE|0||none"; exit 0; fi
OLD=$(cat "$STATE" 2>/dev/null)
T=$(echo "$OLD" | cut -d'|' -f1)
SRC=$(echo "$OLD" | cut -d'|' -f2)
LOC=$(echo "$OLD" | cut -d'|' -f3)
case "$T" in ''|*[!0-9]*) T=0 ;; esac
AGE=$((NOW - T))
if [ "$AGE" -lt 15 ]; then ST=active; else ST=idle; fi
echo "$SRC|$AGE|$LOC|$ST"
CGI
  chmod 755 "$WEBROOT/cgi-bin/status"
}

# ------------------------------- run -------------------------------------
rm -f "$STOP"
echo $$ > "$PIDF"
[ -p "$FIFO" ] || mkfifo "$FIFO"

# Single owner of provider registration.
#
# The DEX injector is authoritative when present: it can write speed+bearing
# (vel=/bear=), which `cmd location` cannot. If BOTH register, the two
# registrations come from different UIDs and evict each other, so the mock
# providers vanish. Therefore: injector present -> do not register here.
INJECTOR_PID=/data/local/tmp/gps_injector.pid
injector_up() {
  [ -f "$INJECTOR_PID" ] || return 1
  P=$(cat "$INJECTOR_PID" 2>/dev/null)
  case "$P" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$P" 2>/dev/null
}

if injector_up; then
  log "injector (pid $(cat $INJECTOR_PID)) owns providers - skipping local registration"
  cmd location set-location-enabled true 2>/dev/null
  appops set 2000 android:mock_location allow 2>/dev/null
else
  log "no injector - registering providers here (position only, no speed)"
  prepare_providers
fi
setup_http

/data/adb/magisk/busybox httpd -p "$PORT_HTTP" -h "$WEBROOT" 2>>"$LOG" &
echo $! > "$HTTPPIDF"

log "start tcp=$PORT_TCP http=$PORT_HTTP pid=$$ providers=gps,network,fused stale=${STALE_SECS}s"

while [ ! -f "$STOP" ]; do
  nc -l -p "$PORT_TCP" > "$FIFO" 2>/dev/null &
  echo $! > "$NCPIDF"
  while read -r line < "$FIFO"; do
    case "$line" in ""|\#*) continue ;; esac
    LAT=${line%%,*}; REST=${line#*,}; LON=${REST%%,*}
    R2=${REST#*,}; ACC=${R2%%,*}
    R3=${R2#*,}; SPD=${R3%%,*}
    R4=${R3#*,}; BEAR=${R4%%,*}
    SRC=${R4#*,}; [ "$SRC" = "$R4" ] && SRC=unknown
    [ "$ACC" = "$R2" ] && ACC=5
    [ "$SPD" = "$R3" ] && SPD=0
    [ "$BEAR" = "$R4" ] && BEAR=0
    if arbitrate "$SRC"; then
      push_fix "$LAT,$LON" "$ACC" "$SPD" "$BEAR"
      echo "$(date +%s)|$SRC|$LAT,$LON" > "$STATE"
      log "tcp src=$SRC $LAT,$LON acc=$ACC OK"
    else
      log "tcp src=$SRC $LAT,$LON REJECTED"
    fi
  done
  wait 2>/dev/null
  sleep 1
done

log "stop signal received"
kill "$(cat "$HTTPPIDF" 2>/dev/null)" 2>/dev/null
rm -f "$PIDF" "$NCPIDF" "$HTTPPIDF"
log "stopped"
