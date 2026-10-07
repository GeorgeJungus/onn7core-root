#!/system/bin/sh
# Remove the invalid speed indicator slot (icoDATA-2 = '1SPU').
#
# '1SPU' maps to string resource ico_spu, which does not exist in Agama's
# indicator set, so the tile renders as an empty '+' placeholder. There is no
# speedometer indicator in this version of Agama at all (ico_p_speed.png exists
# as an asset but is not wired to a pickable indicator).
#
# Agama caches SharedPreferences in memory and rewrites the file on exit, so the
# app MUST be stopped before editing or the change is discarded.
set -u

PKG=altergames.carlauncher
PREF=/data/data/$PKG/shared_prefs/SETTINGS.xml
BAK=/data/local/tmp/agama_before_slot_removal.xml

echo "== stopping Agama =="
am force-stop $PKG
sleep 3

echo "== backup =="
cp -f "$PREF" "$BAK" && echo "   saved $BAK"

echo "== before =="
grep -o 'name="icoDATA-[0-9]*">[^<]*' "$PREF" | tr '\n' ' '; echo

echo "== removing the icoDATA-2 / 1SPU entry =="
# drop the whole <string name="icoDATA-2">...</string> element
sed -i '/name="icoDATA-2">1SPU</d' "$PREF"

echo "== after =="
grep -o 'name="icoDATA-[0-9]*">[^<]*' "$PREF" | tr '\n' ' '; echo

echo "== xml still well-formed? =="
if [ "$(head -c 40 "$PREF" | grep -c '<?xml')" = "1" ]; then
  echo "   header ok"
fi

echo "== starting Agama =="
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "   done"
