#!/usr/bin/env bash
# Preflight — verify this tablet matches the reference config before you touch anything.
# Read-only: makes no changes to the device.
#
# Usage: ./scripts/preflight.sh [serial]
set -uo pipefail

SERIAL="${1:-}"
ADB=(adb); [ -n "$SERIAL" ] && ADB=(adb -s "$SERIAL")

pass=0; fail=0; warn=0
ok(){   printf '  [ OK ] %-34s %s\n' "$1" "$2"; pass=$((pass+1)); }
bad(){  printf '  [FAIL] %-34s %s\n' "$1" "$2"; fail=$((fail+1)); }
warnf(){ printf '  [WARN] %-34s %s\n' "$1" "$2"; warn=$((warn+1)); }

command -v adb >/dev/null || { echo "adb not found"; exit 2; }
"${ADB[@]}" wait-for-device 2>/dev/null || true
if ! "${ADB[@]}" get-state >/dev/null 2>&1; then
  echo "No adb device. Connect the tablet and enable USB debugging."; exit 2
fi

echo "=== device ==="
"${ADB[@]}" shell getprop ro.product.model
echo
echo "=== critical identity (must match) ==="
chk(){ # prop expected
  local got; got=$("${ADB[@]}" shell getprop "$1" 2>/dev/null | tr -d '\r')
  if [ "$got" = "$2" ]; then ok "$1" "$got"; else bad "$1" "got '$got' want '$2'"; fi
}
chk ro.product.model               36020962
chk ro.product.device              onn7core
chk ro.product.board               mid7030_mx_64
chk ro.hardware                    mt8786
chk ro.board.platform              mt6768
chk ro.build.version.sdk           36
chk ro.product.cpu.abi             arm64-v8a

echo
echo "=== build (should match to use the same patched image) ==="
for p in ro.build.id ro.build.version.incremental ro.build.version.security_patch ro.build.fingerprint; do
  printf '  %-34s %s\n' "$p" "$("${ADB[@]}" shell getprop "$p" 2>/dev/null | tr -d '\r')"
done
BID=$("${ADB[@]}" shell getprop ro.build.id | tr -d '\r')
INC=$("${ADB[@]}" shell getprop ro.build.version.incremental | tr -d '\r')
if [ "$BID" = "BP2A.250605.031.A3" ] && [ "$INC" = "20260121" ]; then
  ok build "matches reference"
else
  warnf build "DIFFERENT from reference — do NOT reuse a patched image from another build"
fi

echo
echo "=== bootloader / unlock ==="
for p in ro.boot.slot_suffix ro.boot.verifiedbootstate ro.boot.flash.locked \
         ro.boot.vbmeta.device_state ro.boot.veritymode ro.crypto.state ro.secure ro.debuggable; do
  printf '  %-34s %s\n' "$p" "$("${ADB[@]}" shell getprop "$p" 2>/dev/null | tr -d '\r')"
done
LOCKED=$("${ADB[@]}" shell getprop ro.boot.flash.locked | tr -d '\r')
if [ "$LOCKED" = "0" ]; then ok unlock "bootloader unlocked"; else warnf unlock "locked — run 'fastboot flashing unlock' first (wipes data)"; fi
SLOT=$("${ADB[@]}" shell getprop ro.boot.slot_suffix | tr -d '\r')
if [ "$SLOT" = "_a" ]; then ok slot "active slot a"; else warnf slot "active is $SLOT — adapt partition names to _b"; fi

echo
echo "=== partitions present ==="
for p in init_boot_a boot_a vbmeta_a vbmeta_system_a vbmeta_vendor_a vendor_boot_a dtbo_a lk_a boot_para super; do
  if "${ADB[@]}" shell "test -e /dev/block/by-name/$p" 2>/dev/null; then ok "$p" present; else bad "$p" MISSING; fi
done

echo
echo "=== storage for the backup (need >= ~11 GB free) ==="
"${ADB[@]}" shell 'df -h /data 2>/dev/null | tail -1'

echo
echo "=== host ==="
printf '  %-34s %s\n' adb "$(adb version 2>/dev/null | head -1)"
printf '  %-34s %s\n' fastboot "$(fastboot --version 2>/dev/null | head -1)"
printf '  %-34s %s\n' python "$(python3 -V 2>&1)"
printf '  %-34s %s\n' mtkclient "$([ -d mtkclient/.git ] && git -C mtkclient rev-parse --short HEAD || echo 'not cloned')"
printf '  %-34s %s\n' "0e8d udev rule" "$(grep -lq 0e8d /etc/udev/rules.d/*.rules 2>/dev/null && echo present || echo MISSING)"

echo
echo "=== summary: $pass ok, $warn warn, $fail fail ==="
[ "$fail" -eq 0 ] && echo "PREFLIGHT PASSED — safe to proceed" || echo "PREFLIGHT FAILED — resolve [FAIL] items first"
exit $(( fail > 0 ? 1 : 0 ))
