#!/usr/bin/env bash
# One-shot BROM read: full boot-chain restore set + super.
#
# ONE BROM session = ONE handshake. Everything goes in a single `r` command.
# Order matters: critical boot chain first, super (9.66 GB) last.
#
# BEFORE RUNNING: the tablet must already be in BROM mode, with the preloader
# at preloader/preloader_mid7030_mx_64.bin. Start this command FIRST, then do
# the button dance — the BROM window is only ~2.7 s (see ROOT-CAUSE-NOTES.md).
#
# Usage: ./scripts/read_all.sh [--out DIR]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/backup"
[ "${1:-}" = "--out" ] && OUT="$2"
mkdir -p "$OUT"

PRE="$ROOT/preloader/preloader_mid7030_mx_64.bin"
MTK="$ROOT/mtkclient/mtk.py"
PY="$ROOT/mtkclient/.venv/bin/python"

[ -x "$PY" ] || { echo "venv missing — run ./scripts/setup.sh"; exit 1; }
[ -f "$PRE" ] || { echo "preloader missing at $PRE — dump it first (README step 2)"; exit 1; }

PARTS=preloader_a,preloader_b,boot_a,init_boot_a,vendor_boot_a,dtbo_a,boot_para,\
vbmeta_a,vbmeta_system_a,vbmeta_vendor_a,lk_a,md1img_a,tee_a,scp_a,sspm_a,spmfw_a,gz_a,super

FILES="$OUT/preloader_a.bin,$OUT/preloader_b.bin,$OUT/boot_a.img,$OUT/init_boot_a.img,$OUT/vendor_boot_a.img,$OUT/dtbo_a.img,$OUT/boot_para.img,$OUT/vbmeta_a.img,$OUT/vbmeta_system_a.img,$OUT/vbmeta_vendor_a.img,$OUT/lk_a.img,$OUT/md1img_a.img,$OUT/tee_a.img,$OUT/scp_a.img,$OUT/sspm_a.img,$OUT/spmfw_a.img,$OUT/gz_a.img,$OUT/super_stock.img"

echo "=== reading into $OUT ==="
echo "    (preloader_a/b will report 'Couldn't detect partition' — EXPECTED,"
echo "     they are raw eMMC boot regions, not GPT partitions)"
echo
cd "$ROOT/mtkclient"
exec "$PY" "$MTK" r "$PARTS" "$FILES" --preloader "$PRE"
