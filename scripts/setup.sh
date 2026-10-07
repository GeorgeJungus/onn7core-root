#!/usr/bin/env bash
# setup.sh — build the exact toolchain this procedure was verified against.
# Idempotent: safe to re-run.
#
# Usage: ./scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MTK="$ROOT/mtkclient"
MTK_COMMIT="cd25cf9c1ff6d36e82697ac2c798e69e9cfb78c3"
MAGISK_URL="https://github.com/topjohnwu/Magisk/releases/download/v30.7/Magisk-v30.7.apk"
MAGISK_SHA="e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5"

echo "=== 1/6 system packages ==="
if command -v apt >/dev/null; then
  sudo apt update -qq
  sudo apt install -y android-tools-adb android-tools-fastboot python3 python3-venv \
                      python3-dev build-essential git libusb-1.0-0 7zip curl
else
  echo "non-apt system: install adb/fastboot, python3-venv, build-essential, libusb, 7z manually"
fi

echo "=== 2/6 mtkclient @ $MTK_COMMIT ==="
if [ ! -d "$MTK/.git" ]; then
  git clone https://github.com/bkerler/mtkclient "$MTK"
fi
git -C "$MTK" fetch --all --quiet || true
git -C "$MTK" checkout --quiet "$MTK_COMMIT"
echo "  pinned at: $(git -C "$MTK" log -1 --format='%h %s')"

echo "=== 3/6 python venv + pinned deps ==="
[ -d "$MTK/.venv" ] || python3 -m venv "$MTK/.venv"
"$MTK/.venv/bin/pip" install --quiet --upgrade pip wheel setuptools
"$MTK/.venv/bin/pip" install --quiet -r "$ROOT/requirements-lock.txt"
echo "  deps installed from requirements-lock.txt"

echo "=== 4/6 udev rules (MediaTek 0e8d — lets you run mtkclient WITHOUT sudo) ==="
sudo cp "$MTK"/Setup/Linux/*.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "  installed: $(ls "$MTK"/Setup/Linux/*.rules | xargs -n1 basename | tr '\n' ' ')"

echo "=== 5/6 Magisk v30.7 APK ==="
if [ ! -f "$ROOT/Magisk-v30.7.apk" ]; then
  curl -L --fail -o "$ROOT/Magisk-v30.7.apk" "$MAGISK_URL"
fi
GOT=$(sha256sum "$ROOT/Magisk-v30.7.apk" | cut -d' ' -f1)
if [ "$GOT" = "$MAGISK_SHA" ]; then
  echo "  sha256 OK"
else
  echo "  !! sha256 MISMATCH"; echo "     got  $GOT"; echo "     want $MAGISK_SHA"; exit 1
fi

echo "=== 6/6 verify shipped preloader ==="
PRE="$ROOT/preloader/preloader_mid7030_mx_64.bin"
if [ -f "$PRE" ]; then
  sha256sum "$PRE"
  echo "  expect 25468f7c6c7ca05d0dac430762e42ed9d4138e31c8a0236d560c42e48ea2e641"
else
  echo "  preloader missing — you will have to dump it yourself (README step 2)"
fi

cat <<EOF

=== ready ===
  mtkclient : $MTK  ($(git -C "$MTK" describe --tags 2>/dev/null || echo "$MTK_COMMIT"))
  python    : $MTK/.venv/bin/python
  magisk    : $ROOT/Magisk-v30.7.apk
  preloader : $PRE

Next:
  ./scripts/preflight.sh              # confirm the tablet matches
  open README.md                      # follow steps 1-7
EOF
