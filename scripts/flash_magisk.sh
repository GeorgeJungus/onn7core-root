#!/usr/bin/env bash
# Validate + flash a Magisk-patched init_boot image, then verify root.
# Serial is auto-detected (or pass as $2).
#
# Usage: ./scripts/flash_magisk.sh <magisk_patched-*.img> [serial]
set -euo pipefail

PATCHED="${1:?usage: flash_magisk.sh <magisk_patched.img> [serial]}"
SERIAL="${2:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB=(adb); [ -n "$SERIAL" ] && ADB=(adb -s "$SERIAL")

[ -f "$PATCHED" ] || { echo "not found: $PATCHED"; exit 1; }

echo "=== 1. validate the patched image ==="
ls -l "$PATCHED"; sha256sum "$PATCHED"
S=$(stat -c%s "$PATCHED")
[ "$S" = "8388608" ] && echo "  size OK (8 MiB)" || { echo "  !! unexpected size $S"; exit 1; }
MAGIC=$(xxd -l 8 -p "$PATCHED")
[ "$MAGIC" = "414e44524f494421" ] && echo "  magic OK (ANDROID!)" || { echo "  !! bad magic: $MAGIC"; exit 1; }

STOCK="$ROOT/backup/init_boot_a.img"
if [ -f "$STOCK" ]; then
  if cmp -s "$STOCK" "$PATCHED"; then
    echo "  !! IDENTICAL to stock — the patch did NOT apply. Aborting."; exit 1
  fi
  echo "  differs from stock: $(cmp -l "$STOCK" "$PATCHED" 2>/dev/null | wc -l) bytes (patch applied)"
fi

echo
echo "=== 2. device state ==="
"${ADB[@]}" devices
SLOT=$("${ADB[@]}" shell getprop ro.boot.slot_suffix | tr -d '\r')
LOCKED=$("${ADB[@]}" shell getprop ro.boot.flash.locked | tr -d '\r')
echo "  slot_suffix=$SLOT  flash.locked=$LOCKED"
[ "$LOCKED" = "0" ] || { echo "  !! bootloader is LOCKED — cannot flash"; exit 1; }
[ "$SLOT" = "_a" ] || echo "  NOTE: active slot is $SLOT — flashing init_boot$SLOT below"

echo
echo "=== 3. flash ==="
"${ADB[@]}" reboot bootloader
sleep 8
fastboot devices
fastboot flash "init_boot$SLOT" "$PATCHED"
fastboot reboot

echo
echo "=== 4. wait for boot ==="
"${ADB[@]}" wait-for-device
for i in $(seq 1 30); do
  [ "$("${ADB[@]}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
  sleep 5
done
sleep 20

echo
echo "=== 5. verify root ==="
"${ADB[@]}" shell 'su -c id' 2>&1 || true
"${ADB[@]}" shell 'ps -A | grep -c magiskd' 2>&1 | xargs -I{} echo "magiskd processes: {}"
"${ADB[@]}" shell 'su -c "magisk -v"' 2>&1 || true

cat <<'EOF'

Expected: uid=0(root) ... context=u:r:magisk:s0
If you see "su: request rejected (2000)" that is normal on first call —
authorize it in Magisk -> Superuser -> [SharedUID] Shell -> toggle on,
then re-run the check.
EOF
