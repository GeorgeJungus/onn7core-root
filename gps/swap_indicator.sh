#!/system/bin/sh
# Swap Agama's GPS indicator slot for the speed indicator.
#   icoDATA-2 = '1GPS'  ->  '1SPU'
#
# Agama holds SharedPreferences in memory and rewrites the file on exit, so the
# app MUST be stopped before the edit or the change is overwritten.
set -u

PREF=/data/data/altergames.carlauncher/shared_prefs/SETTINGS.xml
BAK=/data/local/tmp/agama_SETTINGS.xml.bak

echo "== stopping Agama =="
am force-stop altergames.carlauncher
sleep 2

echo "== backing up =="
cp -f "$PREF" "$BAK" && echo "   saved $BAK"

echo "== before =="
grep -o 'name="icoDATA-2">[^<]*' "$PREF"

echo "== editing =="
sed -i 's|name="icoDATA-2">1GPS<|name="icoDATA-2">1SPU<|' "$PREF"

echo "== after =="
grep -o 'name="icoDATA-2">[^<]*' "$PREF"

echo "== starting Agama =="
am start -n altergames.carlauncher/.MainActivity >/dev/null 2>&1
echo "   done"
