#!/bin/zsh
# Burkoló az iOS Parancsok appnak — SSH-n hívva egyetlen olvasható sort ad vissza.
#
#   mosas.sh sotetek          # éles indítás
#   mosas.sh sanitizer        # éles indítás
#   mosas.sh sotetek proba    # próbamenet, nem indít
#
# A megerősítést a Parancsok-parancs kéri (Megerősítés kérése művelet), ezért
# itt a --igen kapcsoló megy. A gép állapotellenőrzéseit ez NEM kerüli ki.

set -u

# A script saját könyvtára, így a projekt bárhova áthelyezhető.
MAPPA="${0:A:h}"
[[ -f "$MAPPA/config.sh" ]] && source "$MAPPA/config.sh"

PYTHON="$MAPPA/.venv/bin/python"
EMAIL="${HON_EMAIL:-}"

if [[ -z "$EMAIL" ]]; then
  echo "Hiányzik a HON_EMAIL. Másold a config.example.sh-t config.sh néven:"
  echo "  cp $MAPPA/config.example.sh $MAPPA/config.sh"
  exit 66
fi

PROGRAM="${1:-}"
MOD="${2:-eles}"

case "$PROGRAM" in
  sotetek)   NEV="Darks" ;;
  sanitizer) NEV="Sanitizer" ;;
  *) echo "Ismeretlen program: '${PROGRAM}'. Használható: sotetek, sanitizer"; exit 64 ;;
esac

if [[ ! -x "$PYTHON" ]]; then
  echo "Hiba: nincs meg a Python környezet ($PYTHON)"; exit 65
fi

if [[ "$MOD" == "proba" ]]; then
  KAPCSOLOK=()
else
  KAPCSOLOK=(--eles --igen)
fi

KIMENET="$("$PYTHON" "$MAPPA/indito.py" --email "$EMAIL" "$PROGRAM" "${KAPCSOLOK[@]}" 2>&1)"
KOD=$?

# Napló: SSH-n hívva ez az egyetlen mód megnézni, mi történt.
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S')  program=$PROGRAM mod=$MOD kod=$KOD"
  echo "$KIMENET"
} >> "$MAPPA/mosas.log" 2>/dev/null

# A részletes indoklást a script írja ki; ebből emeljük ki a lényeget.
INDOK="$(echo "$KIMENET" | grep -E "NEM INDÍTOK|FIGYELEM|HIBA|elutasította|Nincs interaktív" | head -2 | tr '\n' ' ')"

# Az indito.py géppel olvasható sora a felhő válaszáról (FELHO_HIBA /
# FELHO_ELUTASITAS). print -r: a nyers JSON-ban lehet backslash, amit az echo
# értelmezne.
felho_sor() { print -r -- "$KIMENET" | grep -m1 "^$1: " | sed "s/^$1: //"; }

case $KOD in
  0) if [[ "$MOD" == "proba" ]]; then
       echo "Próbamenet rendben: $NEV — a gépre semmi nem ment el."
     else
       echo "Elindítva: $NEV"
     fi ;;
  1) echo "Nem indult — bejelentkezési hiba vagy nincs meg a készülék. $INDOK" ;;
  # A tárcsás tanács csak akkor helyes, ha a gép tényleg nincs Remote-ban;
  # futó, szüneteltetett vagy késleltetett ciklusnál félrevezetne. A machMode-ot
  # az indito.py „Állapot:” sora tartalmazza.
  2) case "$(print -r -- "$KIMENET" | grep -o -m1 'machMode=[0-9]')" in
       machMode=2) echo "Nem indult — a gép éppen mos." ;;
       machMode=3) echo "Nem indult — a gép szüneteltetve van, a gépen folytasd." ;;
       machMode=[45]) echo "Nem indult — már be van állítva egy késleltetett indítás, a gép visszaszámol." ;;
       *) echo "Nem indult — a gép nincs készenlétben. Tekerd a tárcsát Remote állásba, csukd be az ajtót. $INDOK" ;;
     esac ;;
  3) echo "Megszakítva, semmi nem indult el." ;;
  4) R="$(felho_sor FELHO_ELUTASITAS)"; R="${R% | }"
     if [[ -n "$R" ]]; then
       echo "Nem indult — a hOn felhő elutasította a parancsot ($R)."
     else
       echo "Nem indult — a hOn felhő nem fogadta el a parancsot. $INDOK"
     fi ;;
  5) echo "Nem indult — a kért és a tényleges beállítások eltérnek. $INDOK" ;;
  6) echo "Nem indult — nem a várt készülék válaszolt (MAC-eltérés)." ;;
  7) echo "BIZONYTALAN — hálózati hiba küldés közben. Lehet, hogy MÉGIS elindult, ellenőrizd a gépen, mielőtt újrapróbálnád!" ;;
  8) T="$(felho_sor FELHO_HIBA)"; T="${T%% | *}"
     echo "Nem indult — hiba van a Haier szerverén${T:+ ($T)}, nem nálad. Próbáld később." ;;
  *) echo "Ismeretlen hiba (kód $KOD). $INDOK" ;;
esac

exit $KOD
