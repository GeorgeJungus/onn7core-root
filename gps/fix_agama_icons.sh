#!/system/bin/sh
# Fix Agama button icons so each matches its bound action.
#
# Agama resolves a button icon as:  ico_k_<k<N>_ico>.png
# An invalid name silently renders no icon while the ACTION still fires —
# which is the "action works, icon doesn't" symptom.
#
# Valid names are the ico_k_*.png basenames inside the APK. Invalid values
# found here:  k4_ico='CAR'  -> no such asset (button action = Car Scanner).
#
# Agama caches SharedPreferences in memory and rewrites on exit, so stop first.
set -u

PKG=altergames.carlauncher
PREF=/data/data/$PKG/shared_prefs/SETTINGS.xml
BAK=/data/local/tmp/agama_SETTINGS.icons.bak

echo "== stopping Agama =="
am force-stop $PKG
sleep 2

echo "== backing up =="
cp -f "$PREF" "$BAK" && echo "   saved $BAK"

apply() {   # $1=key  $2=current  $3=wanted
  KEY="$1"; CUR="$2"; WANT="$3"
  if grep -q "name=\"$KEY\">$CUR<" "$PREF"; then
    sed -i "s|name=\"$KEY\">$CUR<|name=\"$KEY\">$WANT<|" "$PREF"
    echo "   $KEY: '$CUR' -> '$WANT'"
  else
    echo "   $KEY: '$CUR' not found (already '$(grep -o "name=\"$KEY\">[^<]*" "$PREF" | cut -d'>' -f2)')"
  fi
}

echo "== fixing icons =="
# k4 action = Car Scanner (app4.1). 'obd' is the matching bundled icon.
apply k4_ico CAR obd

echo "== resulting button icons =="
for i in 1 2 3 4 5 6; do
  N=$(grep -o "name=\"k${i}_name\">[^<]*" "$PREF" | cut -d'>' -f2)
  I=$(grep -o "name=\"k${i}_ico\">[^<]*" "$PREF" | cut -d'>' -f2)
  printf '   k%s  %-10s icon=%s\n' "$i" "$N" "$I"
done

echo "== starting Agama =="
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "   done"
