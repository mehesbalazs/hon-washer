#!/bin/zsh
# Nyilvános SSH-kulcs biztonságos hozzáfűzése az authorized_keys-hez.
#
#   ./kulcs_hozzaadas.sh              # a vágólapról
#   ./kulcs_hozzaadas.sh ~/Downloads/valami.pub
#
# Miért nem elég egy sima >> : egy elrontott vagy záró újsor nélküli sor
# összefolyik a szomszédjával, és attól MINDKÉT kulcs érvénytelen lesz —
# némán, csak a bejelentkezés bukik. Ezért itt előbb ellenőrzünk.

set -u
F=~/.ssh/authorized_keys

if [[ $# -ge 1 ]]; then
  KULCS="$(cat "$1")"
  FORRAS="$1"
else
  KULCS="$(pbpaste)"
  FORRAS="vágólap"
fi

KULCS="$(printf '%s' "$KULCS" | tr -d '\r' | sed '/^[[:space:]]*$/d')"

if [[ -z "$KULCS" ]]; then
  echo "Üres a forrás ($FORRAS). Másold a vágólapra a nyilvános kulcsot, vagy add meg a fájlt."; exit 1
fi

if [[ $(printf '%s\n' "$KULCS" | wc -l) -ne 1 ]]; then
  echo "Több sort találtam — egyszerre egy kulcsot adj hozzá."; exit 1
fi

if [[ ! "$KULCS" =~ '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+) [A-Za-z0-9+/=]+' ]]; then
  echo "Ez nem néz ki nyilvános kulcsnak (forrás: $FORRAS):"
  echo "  ${KULCS:0:60}..."
  echo "Nyilvános kulcs kell, ami 'ssh-ed25519 AAAA…'-val kezdődik — NE a privát kulcsot."
  exit 1
fi

# Formai ellenőrzés a valódi ssh-keygen-nel, mielőtt hozzáérünk a fájlhoz.
TMP="$(mktemp)"; printf '%s\n' "$KULCS" > "$TMP"
if ! UJ="$(ssh-keygen -lf "$TMP" 2>&1)"; then
  echo "Az ssh-keygen nem fogadta el: $UJ"; rm -f "$TMP"; exit 1
fi
rm -f "$TMP"

UJ_FP="$(printf '%s' "$UJ" | awk '{print $2}')"
if ssh-keygen -lf "$F" 2>/dev/null | grep -q "$UJ_FP"; then
  echo "Ez a kulcs MÁR benne van:"; echo "  $UJ"; exit 0
fi

mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch "$F" && chmod 600 "$F"
# Záró újsor biztosítása, mielőtt hozzáfűzünk.
[[ -s "$F" && -n "$(tail -c1 "$F")" ]] && printf '\n' >> "$F"
printf '%s\n' "$KULCS" >> "$F"

echo "Hozzáadva ($FORRAS):"
echo "  $UJ"
echo
echo "Most $(grep -c . "$F") kulcs van engedélyezve:"
ssh-keygen -lf "$F" | sed 's/^/  /'
