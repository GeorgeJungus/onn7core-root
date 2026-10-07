#!/system/bin/sh
# Remove the invalid '1SPU' indicator slot, robustly.
#
# Prior attempt failed two ways:
#   1. Inline `sed s/...>1SPU.../` had the '>' eaten by the shell as a redirect.
#   2. Agama rewrites SETTINGS.xml from its in-memory model, so the edit must
#      happen while it is genuinely stopped AND be verified before restart.
#
# This script avoids '>' in patterns entirely (uses the element name only).
set -u

PKG=altergames.carlauncher
PREF=/data/data/$PKG/shared_prefs/SETTINGS.xml
BAK=/data/local/tmp/agama_slotfix.bak

echo "== stop =="
am force-stop $PKG
sleep 4

# verify death properly (grep on ps output, not a shell whose own cmdline matches)
alive() {
  for d in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
    case "$c" in *"$PKG"*) return 0 ;; esac
  done
  return 1
}
if alive; then echo "   still alive - forcing again"; am force-stop $PKG; sleep 4; fi
if alive; then echo "   STILL ALIVE - aborting"; exit 1; fi
echo "   confirmed stopped"

echo "== backup =="
cp -f "$PREF" "$BAK" && echo "   $BAK"

echo "== before =="
grep -o 'name="icoDATA-[0-9]*"' "$PREF" | tr '\n' ' '; echo

echo "== delete the icoDATA-2 element =="
# match on element name only -> no '>' needed in the pattern
sed -i '/icoDATA-2/d' "$PREF"

echo "== after =="
grep -o 'name="icoDATA-[0-9]*"' "$PREF" | tr '\n' ' '; echo

# verify while still stopped
if grep -q 'icoDATA-2' "$PREF"; then
  echo "   FAIL: icoDATA-2 still present"
  exit 1
fi
echo "   verified removed (app still stopped)"

echo "== restart =="
am start -n $PKG/.MainActivity >/dev/null 2>&1
sleep 8

echo "== post-restart check =="
if grep -q 'icoDATA-2' "$PREF"; then
  echo "   REVERTED by app -> app must have its own copy elsewhere"
else
  echo "   PERSISTED - slot gone"
fi
grep -o 'name="icoDATA-[0-9]*"' "$PREF" | tr '\n' ' '; echo
