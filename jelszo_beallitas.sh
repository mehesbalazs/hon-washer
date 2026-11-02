#!/bin/zsh
# Egyszeri beállítás, terminálból futtatva:
#   1. config.sh létrehozása a mintából, az e-mail címeddel (ha még nincs)
#   2. a hOn jelszó a ~/.config/hon/jelszo fájlba (600) — ebből olvas a
#      script SSH-n át, ahol a Kulcskarika nem érhető el
#   3. kérésre a macOS Kulcskarikába is (hon szolgáltatásnéven), visszaolvasva:
#      ha nem ugyanaz a jelszó került oda, mint a fájlba, a tételt törli
#
# A jelszó nem jelenik meg a képernyőn, nem kerül naplóba, és parancssori
# argumentumba sem (a printf beépített parancs, nem külön folyamat).
# Jelszócsere után is ezt kell újrafuttatni.

set -eu
MAPPA="${0:A:h}"
[[ -f "$MAPPA/config.sh" ]] && source "$MAPPA/config.sh"

EMAIL="${HON_EMAIL:-}"
if [[ -z "$EMAIL" || "$EMAIL" == "pelda@example.com" ]]; then
  read "EMAIL?hOn fiók e-mail címe: "
fi
# Az e-mail címben nincs szóköz: a véletlen szóközöket, tabokat mind levágjuk.
EMAIL="${EMAIL//[[:space:]]/}"
if [[ -z "$EMAIL" || "$EMAIL" != *@* ]]; then
  echo "Ez nem tűnik e-mail címnek: '$EMAIL'"; exit 1
fi
# A config.sh-t a shell source-olja: ezek a karakterek ott kódként futnának.
if [[ "$EMAIL" == *[\"\$\`\\]* ]]; then
  echo "Az e-mail címben nem lehet \", \$, \` vagy \\ karakter."; exit 1
fi

# A config.sh e-mail sorát akkor is javítani kell, ha a fájl már létezik:
# aki előbb `cp config.example.sh config.sh`-t csinált, annak a mintacím
# maradna benne, és minden belépés a pelda@example.com címmel bukna.
umask 077
FORRAS="$MAPPA/config.example.sh"
[[ -f "$MAPPA/config.sh" ]] && FORRAS="$MAPPA/config.sh"
UJ="$MAPPA/config.sh.uj"
while IFS= read -r sor; do
  if [[ "$sor" == "export HON_EMAIL="* ]]; then
    print -r -- "export HON_EMAIL=\"$EMAIL\""
  else
    print -r -- "$sor"
  fi
done < "$FORRAS" > "$UJ"
# A mintába később bekerült beállítások pótlása: enélkül a régóta meglévő
# config.sh-ból örökre hiányoznának az új kulcsok (a szkriptek meg hivatkoznak rájuk).
for kulcs in ${(f)"$(grep -o '^export [A-Z_]*' "$MAPPA/config.example.sh" | cut -d' ' -f2)"}; do
  grep -q "^export ${kulcs}=" "$UJ" && continue
  echo "  új beállítás a mintából: $kulcs"
  {
    echo
    # A kulcs elé tartozó megjegyzésblokk is átjön, hogy legyen mihez nyúlni.
    awk -v k="export ${kulcs}=" '
      /^#/ { blokk = blokk $0 "\n"; next }
      index($0, k) == 1 { printf "%s%s\n", blokk, $0; exit }
      { blokk = "" }
    ' "$MAPPA/config.example.sh"
  } >> "$UJ"
done

if [[ -f "$MAPPA/config.sh" ]] && diff -q "$UJ" "$MAPPA/config.sh" >/dev/null; then
  rm -f "$UJ"
else
  mv "$UJ" "$MAPPA/config.sh"
  chmod 600 "$MAPPA/config.sh"
  echo "config.sh frissítve ($EMAIL)."
fi

# -r nélkül a backslash eltűnne, IFS= nélkül a széli szóköz levágódna — a
# jelszó így némán megcsonkulna, és csak az SSH-s belépés bukna el tőle.
IFS= read -rs "JELSZO?hOn jelszó ($EMAIL): "; echo
IFS= read -rs "JELSZO2?Még egyszer: "; echo
if [[ -z "$JELSZO" ]]; then echo "Üres jelszó, nem mentettem."; exit 1; fi
if [[ "$JELSZO" != "$JELSZO2" ]]; then echo "A két jelszó nem egyezik, nem mentettem."; exit 1; fi

umask 077
mkdir -p ~/.config/hon
chmod 700 ~/.config/hon
printf '%s\n' "$JELSZO" > ~/.config/hon/jelszo
chmod 600 ~/.config/hon/jelszo
unset JELSZO JELSZO2
echo "Jelszó elmentve: ~/.config/hon/jelszo (600)."

IFS= read -r "VALASZ?A macOS Kulcskarikába is mentsem? (i/n) "
if [[ "$VALASZ" == [iIyY]* ]]; then
  # A -w érték nélkül maga kéri be a jelszót, így az sem kerül argumentumba.
  # A -U a meglévő tételt frissíti.
  echo "Most a security kéri be még egyszer UGYANAZT a hOn jelszót, nem a Mac"
  echo "jelszavát (a „password data for new item” és a „retype password” kérdésre):"
  security add-generic-password -U -s hon -a "$EMAIL" -w
  # A security csak azt nézi, hogy kétszer ugyanazt írtad-e be, azt nem, hogy a
  # fájlba mentett jelszót. Az eltérő tétel terminálból elsőbbséget kap, és ott
  # minden belépést elrontana (SSH-n meg a fájl menne) — ezért visszaolvassuk.
  if [[ "$(security find-generic-password -s hon -a "$EMAIL" -w 2>/dev/null)" == "$(<"$HOME/.config/hon/jelszo")" ]]; then
    echo "Kulcskarikába mentve és visszaolvasva (hon)."
  else
    security delete-generic-password -s hon -a "$EMAIL" >/dev/null 2>&1 || true
    echo
    echo "FIGYELEM: a Kulcskarikából visszaolvasott jelszó nem egyezik a fájlba"
    echo "mentettel, ezért töröltem a Kulcskarika-bejegyzést. A jelszófájl marad a"
    echo "forrás terminálból is. Ha mégis kell a Kulcskarika, futtasd újra a szkriptet."
  fi
elif security find-generic-password -s hon -a "$EMAIL" >/dev/null 2>&1; then
  # Terminálból a Kulcskarika az első forrás: egy ott ragadt régi jelszóval
  # minden helyi futás bukna, miközben SSH-n a friss fájl menne.
  echo
  echo "FIGYELEM: a Kulcskarikában már van 'hon' bejegyzés ehhez a fiókhoz,"
  echo "és az elsőbbséget élvez a most mentett fájllal szemben."
  IFS= read -r "TORLES?Töröljem a Kulcskarika-bejegyzést? (i/n) "
  if [[ "$TORLES" == [iIyY]* ]]; then
    security delete-generic-password -s hon -a "$EMAIL" >/dev/null
    echo "Kulcskarika-bejegyzés törölve."
  fi
fi

echo
echo "Kész. Próbáld ki (csak olvas):"
echo "  $MAPPA/.venv/bin/python $MAPPA/lekerdez.py"
