#!/usr/bin/env bash
# Clear a wedged BROM port. See ROOT-CAUSE-NOTES.md.
#
# Symptom this fixes: mtkclient aborts with
#   "Please disconnect, start mtkclient and reconnect."
# while `lsusb` shows a leftover 0e8d:0003 that never handshakes.
#
# Cause: a previous session (esp. `dumppreloader`, which DISABLES the BROM
# watchdog) left the port stale. mtkclient's connect() then skips the handshake
# and exits. Only a real re-enumeration or power-cycle clears it.
#
# Usage: ./scripts/clear_stale_port.sh
set -uo pipefail

echo "=== current MediaTek usb devices ==="
lsusb | grep -i 0e8d || echo "  none"

NODE=""
for d in /sys/bus/usb/devices/*/idVendor; do
  if [ "$(cat "$d" 2>/dev/null)" = "0e8d" ]; then
    NODE=$(basename "$(dirname "$d")")
  fi
done

if [ -z "$NODE" ]; then
  echo "no stale MediaTek node — nothing to do"
  exit 0
fi

echo
echo "=== unbinding $NODE ==="
echo -n "$NODE" | sudo tee /sys/bus/usb/drivers/usb/unbind >/dev/null
sleep 3
if lsusb | grep -qi 0e8d; then
  echo "  still present — unbind did not take (in-use?). Options below."
else
  echo "  cleared"
fi

cat <<'EOF'

If it is still there, either:
  * unplug the cable and replug it, or
  * power-cycle the tablet (hold Power ~12 s), which is the only reliable
    way to reset a BROM left with its watchdog disabled.

Also worth doing before a fresh attempt (frees the interface from the kernel):
  sudo modprobe -r cdc_acm

REMINDER: never call `dumppreloader` in the same session you intend to read
partitions from. Pass --preloader <file> and go straight to the DA path.
EOF
