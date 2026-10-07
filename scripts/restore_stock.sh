#!/usr/bin/env bash
# Restore stock init_boot. Use if anything goes wrong after flashing.
# Tries fastboot, then BROM.
#
# Usage: ./scripts/restore_stock.sh [serial]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERIAL="${1:-}"
ADB=(adb); [ -n "$SERIAL" ] && ADB=(adb -s "$SERIAL")

STOCK="$ROOT/backup/init_boot_a.img"
EXPECT="689a504a41a0d89758aba5a727d9e6b9bf22ecbfe4e07bbd544ede3a73fa7463"

echo "=== stock image integrity ==="
[ -f "$STOCK" ] || { echo "missing $STOCK"; exit 1; }
sha256sum "$STOCK"
GOT=$(sha256sum "$STOCK" | cut -d' ' -f1)
if [ "$GOT" != "$EXPECT" ]; then
  echo "  NOTE: hash differs from the reference unit. That is fine IF this is your own dump."
  echo "        Reference: $EXPECT"
else
  echo "  matches reference unit dump"
fi

echo
echo "=== path A: fastboot ==="
if fastboot devices 2>/dev/null | grep -q .; then
  fastboot flash init_boot_a "$STOCK" && fastboot reboot && exit 0
fi

if "${ADB[@]}" get-state >/dev/null 2>&1; then
  echo "  adb present -> rebooting to bootloader"
  "${ADB[@]}" reboot bootloader; sleep 8
  if fastboot devices 2>/dev/null | grep -q .; then
    fastboot flash init_boot_a "$STOCK" && fastboot reboot && exit 0
  fi
fi

echo
echo "=== path B: BROM (no fastboot/adb) ==="
cat <<EOF
Put the tablet in BROM: power off, hold VolUp+VolDown, plug USB.

  cd "$ROOT/mtkclient"
  ./.venv/bin/python mtk.py w  init_boot_a "$STOCK" --preloader "$ROOT/preloader/preloader_mid7030_mx_64.bin"

Full partition restore (last resort):
  ./.venv/bin/python mtk.py wl "$ROOT/backup" --preloader "$ROOT/preloader/preloader_mid7030_mx_64.bin"

After a write, power-cycle the tablet (BROM leaves the port stale).
EOF
