#!/system/bin/sh
# Magisk service.d boot hook - GPS injection stack.
# Installed to /data/adb/service.d/gps_feeder.sh (mode 755).
#
# Starts two processes, IN ORDER, and verifies each:
#   1. GpsInjector  - owns the mock providers; can write speed+bearing (vel=)
#                     which `cmd location` cannot. Must start FIRST.
#   2. gps_feeder   - HTTP :8080 + TCP :5555 ingest, arbitration, writes gps_fix
#
# Why ordering matters:
#   * Mock providers are tied to the registering UID/process. If the feeder
#     also registers them (different UID), the two registrations evict each
#     other and the providers vanish -> no GPS at all.
#   * If the feeder starts before the location service exists, provider
#     registration fails silently.
# Starting from service.d (init context) rather than a transient `su -c`
# shell is what makes the background processes actually survive.

SH=/data/local/tmp
LOG=$SH/gps_feeder.log
PIDF=$SH/gps_feeder.pid
IPID=$SH/gps_injector.pid
FIX=$SH/gps_fix

log() { echo "$(date '+%m-%d %H:%M:%S') service.d: $*" >> "$LOG"; }

alive() {  # $1 = pidfile
  [ -f "$1" ] || return 1
  P=$(cat "$1" 2>/dev/null)
  case "$P" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$P" 2>/dev/null
}

# ---- already up? ----
if alive "$PIDF" && alive "$IPID"; then
  log "both already running; nothing to do"
  exit 0
fi

# ---- wait for boot + location service ----
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 90 ]; do
  sleep 2; i=$((i+1))
done
log "boot_completed; waiting for location service"

loc_ready() { cmd location is-location-enabled >/dev/null 2>&1; }
i=0
while ! loc_ready && [ $i -lt 60 ]; do sleep 3; i=$((i+1)); done
if ! loc_ready; then
  log "location service never ready; aborting"
  exit 1
fi
cmd location set-location-enabled true >/dev/null 2>&1
appops set 2000 android:mock_location allow >/dev/null 2>&1
log "location service ready"

# ---- 1. injector (owns providers; carries speed) ----
if alive "$IPID"; then
  log "injector already running (pid $(cat $IPID))"
else
  mkdir -p "$SH/gpslog"
  cd "$SH" || exit 1
  CLASSPATH="$SH/gpsinject.dex" nohup app_process /system/bin GpsInjector \
      > "$SH/gpslog/injector.log" 2>&1 &
  echo $! > "$IPID"
  sleep 6
  if alive "$IPID"; then
    log "injector started (pid $(cat $IPID))"
  else
    log "WARN injector failed to stay up"
  fi
fi

# the CGI writes fixes here; it must exist for the injector path to be used
[ -f "$FIX" ] || : > "$FIX"
chmod 666 "$FIX" 2>/dev/null

# ---- 2. feeder (ingest + arbitration) ----
if alive "$PIDF"; then
  log "feeder already running (pid $(cat $PIDF))"
  exit 0
fi

try=1
while [ $try -le 3 ]; do
  nohup sh $SH/gps_feeder.sh 5555 8080 >/dev/null 2>&1 &
  echo $! > "$PIDF"
  sleep 14
  if netstat -ltn 2>/dev/null | grep -q ':8080'; then
    log "feeder started (pid $(cat $PIDF), attempt $try)"
    exit 0
  fi
  log "attempt $try: :8080 not listening, retrying"
  try=$((try+1))
  sleep 5
done

log "feeder FAILED after 3 attempts"
exit 1
