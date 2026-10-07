#!/usr/bin/env bash
# gpsctl.sh - start / stop / status the on-device GPS injection feeder.
#
#   ./gpsctl.sh start  [serial]
#   ./gpsctl.sh stop   [serial]
#   ./gpsctl.sh status [serial]        <- shows the ACTIVE DRIVER
#   ./gpsctl.sh watch  [serial]        <- live driver/age, refreshes
set -uo pipefail

CMD="${1:-status}"
SER="${2:-$(adb devices | awk '/device$/{print $1; exit}')}"
TABLET="${TABLET:-TABLET_IP}"
[ -z "$SER" ] && { echo "no device"; exit 1; }
A=(adb -s "$SER")
SH="/data/local/tmp"

driver() {   # prints: SRC|AGE|LOC|STATE
  curl -s --max-time 8 "http://$TABLET:8080/cgi-bin/status" 2>/dev/null
}

case "$CMD" in
  start)
    echo "== stopping any existing feeder =="
    "${A[@]}" shell "su -c 'touch $SH/gps_feeder.stop'" 2>/dev/null
    sleep 4
    echo "== pushing + starting =="
    "${A[@]}" push "$(dirname "$0")/gps_feeder.sh" "$SH/gps_feeder.sh" >/dev/null
    "${A[@]}" shell "chmod 755 $SH/gps_feeder.sh"
    "${A[@]}" shell "su -c 'rm -f $SH/gps_feeder.stop'"
    "${A[@]}" shell "su -c 'nohup sh $SH/gps_feeder.sh 5555 8080 >/dev/null 2>&1 &'"
    sleep 8
    "${A[@]}" shell "su -c 'netstat -ltnp 2>/dev/null | grep -E \"5555|8080\"'"
    ;;
  stop)
    "${A[@]}" shell "su -c 'touch $SH/gps_feeder.stop'" 2>/dev/null
    sleep 4
    echo "stopped"
    ;;
  status)
    echo "== ports =="
    "${A[@]}" shell "su -c 'netstat -ltnp 2>/dev/null | grep -E \"5555|8080\"'" 2>/dev/null || echo "  not listening"
    echo "== ACTIVE DRIVER =="
    D=$(driver)
    if [ -z "$D" ]; then
      echo "  (status endpoint unreachable)"
    else
      SRC=$(echo "$D" | cut -d'|' -f1); AGE=$(echo "$D" | cut -d'|' -f2)
      LOC=$(echo "$D" | cut -d'|' -f3); ST=$(echo "$D" | cut -d'|' -f4)
      if [ "$SRC" = "NONE" ]; then
        echo "  no driver (nobody pushing)"
      else
        echo "  driver : $SRC"
        echo "  state  : $ST  (last push ${AGE}s ago)"
        echo "  pos    : $LOC"
      fi
    fi
    echo "== providers =="
    "${A[@]}" shell "su -c 'dumpsys location 2>/dev/null | grep -E \"^    [a-z]+ provider\"'" 2>/dev/null
    ;;
  watch)
    while true; do
      D=$(driver)
      SRC=$(echo "$D" | cut -d'|' -f1); AGE=$(echo "$D" | cut -d'|' -f2)
      LOC=$(echo "$D" | cut -d'|' -f3); ST=$(echo "$D" | cut -d'|' -f4)
      printf '%s  driver=%-12s %-6s age=%ss  %s\n' "$(date +%H:%M:%S)" "$SRC" "$ST" "$AGE" "$LOC"
      sleep 3
    done
    ;;
  *) echo "usage: $0 {start|stop|status|watch} [serial]"; exit 2 ;;
esac
