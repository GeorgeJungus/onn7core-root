#!/usr/bin/env bash
# Verify a BROM backup set: exact sizes, zero-byte files, partition magics,
# super size, then write SHA256SUMS.
#
# Usage: ./scripts/verify_backup.sh [backup-dir]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
B="${1:-$ROOT/backup}"
cd "$B" || { echo "no such dir: $B"; exit 1; }

echo "=== sizes ==="; ls -l
echo
echo "=== expected sizes (bytes, slot a) ==="
printf '%-24s %-12s %-12s %s\n' FILE EXPECTED ACTUAL STATUS

declare -A EXP=(
  # preloader lives in the raw eMMC boot region (mmcblk0boot0), NOT a GPT
  # partition -> captured as preloader.bin, dumped from RAM
  [preloader.bin]=330216
  [boot_a.img]=67108864
  [init_boot_a.img]=8388608
  [vendor_boot_a.img]=67108864
  [dtbo_a.img]=8388608
  [boot_para.img]=27262976
  [vbmeta_a.img]=8388608
  [vbmeta_system_a.img]=8388608
  [vbmeta_vendor_a.img]=8388608
  [lk_a.img]=2097152
  [md1img_a.img]=134217728
  [tee_a.img]=7340032
  [scp_a.img]=12582912
  [sspm_a.img]=2097152
  [spmfw_a.img]=1048576
  [gz_a.img]=33554432
)
FAIL=0
for p in $(printf '%s\n' "${!EXP[@]}" | sort); do
  if [ -f "$p" ]; then
    A=$(stat -c%s "$p")
    if [ "$A" = "${EXP[$p]}" ]; then S=OK; else S=MISMATCH; FAIL=$((FAIL+1)); fi
  else
    A="-"; S=MISSING; FAIL=$((FAIL+1))
  fi
  printf '%-24s %-12s %-12s %s\n' "$p" "${EXP[$p]}" "$A" "$S"
done

echo
echo "=== zero-byte check ==="
Z=$(find . -maxdepth 1 -size 0 -type f -print)
if [ -n "$Z" ]; then echo "$Z"; echo "!! ZERO-BYTE FILES PRESENT"; FAIL=$((FAIL+1)); else echo "none"; fi

echo
echo "=== super_stock.img ==="
if [ -f super_stock.img ]; then
  ls -l super_stock.img
  python3 - <<'PY'
import os
sz=os.path.getsize('super_stock.img')
print(f"bytes={sz}  GiB={sz/2**30:.2f}")
print("SIZE OK" if sz==9663676416 else "!! unexpected size (expected 9663676416)")
PY
else
  echo "!! super_stock.img MISSING (boot chain alone is still a valid partial restore)"; FAIL=$((FAIL+1))
fi

echo
echo "=== partition magics ==="
for f in boot_a.img init_boot_a.img; do
  [ -f "$f" ] && { printf '%-20s ' "$f"; xxd -l 8 "$f" | awk '{print $2, $3}'; }
done
[ -f vendor_boot_a.img ] && { printf '%-20s ' vendor_boot_a.img; xxd -l 8 vendor_boot_a.img | awk '{print $2, $3}'; }
[ -f vbmeta_a.img ] && { printf '%-20s ' vbmeta_a.img; xxd -l 8 vbmeta_a.img | awk '{print $2, $3}'; }

echo
echo "=== sha256 ==="
sha256sum *.bin *.img 2>/dev/null > SHA256SUMS
wc -l < SHA256SUMS | xargs echo "hashes written:"
cat SHA256SUMS

echo
if [ "$FAIL" -eq 0 ]; then echo "RESULT: ALL CHECKS PASSED"; else echo "RESULT: $FAIL PROBLEM(S)"; fi
exit $(( FAIL > 0 ? 1 : 0 ))
