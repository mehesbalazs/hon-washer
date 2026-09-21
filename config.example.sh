# Saját beállítások — MINTA
#
# Másold `config.sh` néven, és töltsd ki a saját adataiddal:
#     cp config.example.sh config.sh
#
# A config.sh verziókövetésen kívül van (lásd .gitignore), a minta viszont
# a repóban marad. A formátum szándékosan egyszerű: a mosas.sh source-olja,
# a Python scriptek pedig kiolvassák belőle az export sorokat.

# A hOn fiók e-mail címe — ugyanaz, mint a mobilappban.
export HON_EMAIL="pelda@example.com"

# A mosógép MAC-címe. Az indító ezzel veti össze, hogy tényleg a te géped
# válaszol-e, mielőtt bármit elindítana. Üresen hagyva ez az ellenőrzés kimarad.
# Honnan tudod meg: hOn app -> Készülék -> Információ.
export MOSOGEP_MAC="00:11:22:33:44:55"

# A Mac SSH-elérése, amire a telefonos Parancsok-parancsok csatlakoznak.
# Csak akkor kell, ha a parancs_keszito.py-val generálsz parancsfájlokat.
export SSH_HOST="192.168.1.10"
export SSH_PORT="22"
export SSH_USER="felhasznalonev"
