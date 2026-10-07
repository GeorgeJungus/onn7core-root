#!/system/bin/sh
# gps_injector.sh - start/stop the DEX-based GPS injector (speed + bearing support).
#
# Why this exists: `cmd location providers set-test-provider-location` accepts only
# --location/--accuracy/--time. It CANNOT carry speed or bearing, so speed widgets
# and map heading read zero. GpsInjector.dex builds full Location objects instead.
#
# Usage:  gps_injector.sh start | stop | status

SH=/data/local/tmp
DEX=$SH/gpsinject.dex
LOG=$SH/gpslog/injector.log
CTL=$SH/gps_injector.pid

start() {
  mkdir -p "$SH/gpslog"
  if pidof_running; then echo "injector already running (pid $(cat $CTL 2>/dev/null))"; return 0; fi
  cd "$SH" || exit 1
  CLASSPATH="$DEX" nohup app_process /system/bin GpsInjector >"$LOG" 2>&1 &
  echo $! > "$CTL"
  sleep 3
  echo "started pid=$(cat $CTL)"
  head -3 "$LOG" 2>/dev/null
}

pidof_running() {
  [ -f "$CTL" ] || return 1
  P=$(cat "$CTL" 2>/dev/null)
  case "$P" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$P" 2>/dev/null
}

stop() {
  if [ -f "$CTL" ]; then
    P=$(cat "$CTL" 2>/dev/null)
    case "$P" in ''|*[!0-9]*) ;; *) kill -9 "$P" 2>/dev/null ;; esac
    rm -f "$CTL"
  fi
  # sweep any strays by scanning cmdline
  for d in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
    case "$c" in *GpsInjector*) kill -9 "${d#/proc/}" 2>/dev/null ;; esac
  done
  echo "stopped"
}

status() {
  if pidof_running; then
    echo "injector: RUNNING pid=$(cat $CTL)"
  else
    echo "injector: stopped"
  fi
  tail -3 "$LOG" 2>/dev/null | sed 's/^/  /'
}

case "${1:-status}" in
  start) start ;;
  stop)  stop ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}"; exit 2 ;;
esac
