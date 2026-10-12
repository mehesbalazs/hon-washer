# Mosógép távvezérlés (Candy/Hoover/Haier hOn)

Lekérdezés és távoli programindítás Candy/Hoover/Haier mosógéphez, a hOn
felhő API-n keresztül — terminálból és iPhone-ról egyaránt.

Ezeknek a gépeknek a Wi-Fi modulja (ESP32-for-Haier) **nem nyit helyi portot**,
kizárólag kimenő AWS IoT MQTT kapcsolatot tart. Helyi hálózati vezérlés ezért
nem lehetséges; a felhő az egyetlen út. (A régebbi Candy simply-Fi generáció
ettől eltér: az ad helyi JSON API-t a 80-as porton.)

> **Nem hivatalos projekt.** A használt `pyhon-revived` könyvtár visszafejtett
> API-ra épül, és a gyártó bármikor megváltoztathatja azt. Semmilyen kapcsolatban
> nem áll a Haier Europe-pal. Saját felelősségre használd.

## Felépítés

```
  iPhone (Parancsok app)
        │  SSH
        ▼
  mosas.sh  ─────────▶  mosas.log
        │
        ▼
  indito.py  ──importál──▶  lekerdez.py
        │
        ▼
  hOn felhő  ─────────▶  mosógép
```

| fájl | szerep | mikor fut |
|---|---|---|
| `lekerdez.py` | lekérdezés + közös alapfüggvények | kézzel, és minden induláskor (importként) |
| `indito.py` | indítás, hat ellenőrzéssel | kézzel vagy a burkolón át |
| `mosas.sh` | telefonos burkoló, naplózás | minden SSH-hívásnál |
| `parancs_keszito.py` | a Parancsok-fájlok generálása | csak beállításkor |
| `kulcs_hozzaadas.sh` | SSH-kulcs hozzáfűzése | csak új eszköznél |

Az utolsó kettő beállító eszköz: a mindennapi működés során nem fut.

## Beállítás

```
cp config.example.sh config.sh     # majd töltsd ki a saját adataiddal
uv venv --python 3.12
uv pip install -r requirements.txt
```

A `config.sh` verziókövetésen kívül van, és négy dolgot ad meg: a hOn fiók
e-mail címét, a mosógép MAC-címét (ezzel ellenőrzi az indító, hogy tényleg a
te géped válaszol-e), valamint az SSH-hoszt és -felhasználót a telefonos
parancsokhoz. Ugyanezt a fájlt olvassa a shell és a Python is.

A jelszó **nem** kerül a config.sh-ba — lásd a következő szakaszt.

## Biztonsági zár

A `lekerdez.py` induláskor letiltja a pyhOn **négy** parancsküldő útvonalát:
`HonCommand.send`, `.send_specific`, `.send_parameters` és — a tényleges szűk
keresztmetszetet — a `HonAPI.send_command` metódust. A script ebben a
folyamatban **képtelen** bármit elindítani vagy megállítani a gépen, akkor is,
ha valaki később hibásan módosítja.

Részletek: [ARCHITEKTURA.md](ARCHITEKTURA.md).

## Jelszó

A script nem tárol jelszót. Sorrendben keresi:

1. `HON_EMAIL` / `HON_PASSWORD` környezeti változó
2. macOS Kulcskarika — `security find-generic-password -s hon -a <email> -w`
3. interaktív bekérés (`getpass`, a gépelés nem látszik)

A Kulcskarikába így kerül be (a `-w` kapcsoló érték nélkül **bekéri** a jelszót,
így nem marad benne a shell előzményekben):

```
security add-generic-password -s hon -a "az@email.cimed" -w
```

**SSH-n át a Kulcskarika NEM elérhető** — `security` 36-os kóddal bukik
("interakció nem engedélyezett"), mert az SSH-munkamenet más biztonsági
kontextusban fut, és nincs, aki a hozzáférést megerősítse. A
`set-generic-password-partition-list` sem oldja meg. Ezért kell a jelszófájl:

```
mkdir -p ~/.config/hon && chmod 700 ~/.config/hon
security find-generic-password -s hon -a "$E" -w > ~/.config/hon/jelszo
chmod 600 ~/.config/hon/jelszo
```

Terminálból továbbra is a Kulcskarikából megy; a fájl csak az SSH-úthoz kell.

## Használat

Az e-mail címet a `config.sh`-ból veszi, külön megadni nem kell
(a `--email` kapcsolóval felülírható):

```
.venv/bin/python lekerdez.py                # állapot
.venv/bin/python lekerdez.py --kedvencek    # saját mentett programok
.venv/bin/python lekerdez.py --programok    # az összes program
.venv/bin/python lekerdez.py --mind         # minden állapotparaméter
.venv/bin/python lekerdez.py --statisztika  # élettartam-számlálók
.venv/bin/python lekerdez.py --json         # nyers JSON
```

## Indítás — `indito.py`

Két rögzített program, a mentett kedvencekből kiolvasott paraméterekkel:

| kapcsoló | program | beállítás |
|---|---|---|
| `sanitizer` | `iot_wash_baby_sanitizer` | 60 °C, 1000 ford, hygiene, extra öblítés |
| `sotetek` | `iot_wash_dark` | 30 °C, 800 ford |

**Alapértelmezésben nem indít.** Próbamenetben fut: kiírja a pontos payloadot,
de elfogja a küldést, így semmi nem jut el a géphez.

```
.venv/bin/python indito.py sotetek           # próba
.venv/bin/python indito.py sotetek --eles    # tényleges indítás
```

Éles módban minden alábbi feltételnek teljesülnie kell, különben nem indul:

1. a gép online (`lastConnEvent != DISCONNECTED`)
2. `remoteCtrValid = 1` — a tárcsa **Remote** állásban (különben megmondja, mit tegyél)
3. `machMode = 1` — készenlét; ha épp mos, szünetel vagy késleltetett indítás vár rá, elutasítja
4. minden kért paraméter beállítható volt
5. **minden kért paraméter vissza is olvasható ugyanazzal az értékkel** — a gép
   `programRules` szabályai egy későbbi paraméter beállításakor visszamenőleg
   átírhatnak egy korábbit, kivétel nélkül (`pyhon/rules.py::_apply_fixed`).
   A beállításkor feljegyzett érték ezért nem bizonyíték.
6. a gép MAC-címe egyezik a `VART_MAC`-kel

Ezután begépelt `INDIT` megerősítést kér, majd **újra ellenőrzi az állapotot**,
mert a megerősítés alatt eltelt idő alatt kinyithatták az ajtót vagy lejárhatott
a Remote mód. A `--igen` csak a kérdést hagyja ki, az ellenőrzéseket nem.

### Kilépési kódok

| kód | jelentés |
|---|---|
| 0 | rendben (próbamenet vagy sikeres indítás) |
| 1 | bejelentkezési hiba, nincs vagy több mosógép |
| 2 | a gép nincs indításra kész állapotban |
| 3 | megszakítva (nem `INDIT` vagy nincs interaktív bevitel) |
| 4 | a felhő elutasította — a mosás **nem** indult el |
| 5 | paraméter nem állítható be, vagy visszaolvasva eltér |
| 6 | a MAC nem egyezik a várt készülékkel |
| 7 | hálózati hiba küldés közben — **nem tudni, elindult-e**, ellenőrizd |
| 8 | hiba **a gyártó szerverén** — a mosás **nem** indult el, nem nálad a gond; próbáld később |

## iPhone — Parancsok app

A `mosas.sh` egyetlen olvasható sort ad vissza, ezt mutatja a telefon.

```
mosas.sh sotetek          # éles indítás
mosas.sh sanitizer        # éles indítás
mosas.sh sotetek proba    # próbamenet, nem indít
```

Három parancs készül:

| parancs | mit csinál | megerősítést kér |
|---|---|---|
| **Mosás Darks** | `mosas.sh sotetek` — éles indítás | igen |
| **Mosás Sanitizer** | `mosas.sh sanitizer` — éles indítás | igen |
| **Mosás - Próba** | `mosas.sh sotetek proba` — **soha nem indít** | nem |

A próba-parancs az SSH-lánc tesztelésére való: kulcs, hoszt, útvonal, jogosultságok.
Kockázat nélkül futtatható bármikor, telefonról is.

A parancsfájlok legenerálása és aláírása:

```
/usr/bin/python3 parancs_keszito.py     # -> parancsok/*.shortcut
open "parancsok/Mosás Darks.shortcut" # importálás (Hozzáadás gomb)
```

Importálás után iCloudon át átszinkronizál az iPhone-ra. Egyetlen kézi lépés
marad: az SSH-kulcs kiválasztása a parancsban (lásd lentebb).

A parancs felépítése (amit a generátor előállít):

1. **Megerősítés kérése** — „Elindítsam a Sötétek mosást?"
2. **Szkript futtatása SSH-n át**
   - Gép: `<mac-ip>` (vagy `<gepnev>.local`), Port: `22`, Felhasználó: `<felhasznalo>`
   - Hitelesítés: **SSH-kulcs** → a Parancsok generál egyet → *Nyilvános kulcs megosztása*
     → a kulcsot fűzd hozzá a Mac `~/.ssh/authorized_keys` fájljához
   - Szkript: `<projekt-utvonal>/mosas.sh sotetek`
3. **Értesítés megjelenítése** — a szkript kimenete

Kitétel: kezdőképernyő-widget, Zárolási képernyő, Művelet gomb, vagy Siri
(a parancs neve lesz a kimondandó szöveg).

### iPhone-oldali akadályok (mind kötelező, sorrendben)

1. **iCloud-szinkron** a Parancsok appban a Macen (Beállítások), különben a
   parancsok meg sem jelennek a telefonon
2. **Szkriptek futtatásának engedélyezése**: iPhone → Beállítások →
   Alkalmazások → Parancsok → Speciális. Alapból KI van kapcsolva; enélkül
   *"a Parancsok biztonsági beállításai nem teszik lehetővé"* hibát ad
3. **Helyi hálózat** engedély az első futtatáskor (felugró kérdés)
4. **A telefon SAJÁT ed25519 kulcsa** — nem azonos a Macével, külön hozzá kell
   fűzni az `authorized_keys`-hez: `./kulcs_hozzaadas.sh` (vágólapról vagy fájlból)

**NFC matrica:** Parancsok → Automatizálás → NFC → a matrica beolvasása →
ez a parancs. Tedd a matricát a gép tetejére: bepakolás és tárcsatekerés után
hozzáérinted a telefont, és indul.

**Otthonról kívülről** a belső cím nem érhető el. Erre VPN a megoldás — például
a routeren beállított WireGuard. SSH-t ne nyiss ki az internet felé.

### Tűzfal — ez fogta meg a legtovább

A macOS alkalmazás-tűzfal blokkolhatja az `/usr/libexec/sshd-auth`-ot
(macOS 15-től az sshd három külön binárisra bomlik, és előfordul, hogy kettő
engedélyezett, a harmadik nem). Ugyanígy blokkol a „minden bejövő kapcsolat
blokkolása" beállítás. Tünet: a Parancsok azt mondja, *"nem tudott
csatlakozni az SSH-szerverhez"*, a `mosas.log`-ba pedig semmi nem kerül — mert
a szkript el sem indul. A loopback (`127.0.0.1`) ilyenkor is működik, mert az
nem megy át a tűzfalon; ez a különbség árulja el a hibát.

Ellenőrzés:

```
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
/usr/libexec/ApplicationFirewall/socketfilterfw --listapps | grep -A1 sshd-auth
```

## Hogyan működik

A belső működés — függvényenként, a tervezési döntések indoklásával — külön
dokumentumban: **[ARCHITEKTURA.md](ARCHITEKTURA.md)**.

## Kompatibilitás

Fejlesztve és tesztelve: **Candy TCA286TM5-S** (hOn-generációs modul). Elvben
minden hOn-kompatibilis Candy/Hoover/Haier mosógéppel működik, de a
programkatalógus és az elérhető paraméterek modellenként eltérnek — a saját
gépedét a `lekerdez.py --programok` és `--kedvencek` írja ki.

A régebbi, **simply-Fi** generációhoz ez a projekt nem való: azoknál van helyi
JSON API a 80-as porton, amit érdemesebb közvetlenül használni.

## Amit tudni érdemes

- A tárcsát ciklusonként fizikailag **Remote** állásba kell tekerni. Utána
  **24 órás** ablak nyílik, amin belül bárhonnan indítható a gép.
- A `remoteCtrValid` mező jelzi a készenlétet (`1` = fogad távoli parancsot).
  Ez NEM azonos a `remoteActionable` / `remoteVisible` mezőkkel — azok kimenő
  parancs-paraméterek, nem állapotjelzők.
- Ciklus végén a távvezérlés magától kikapcsol.

## Kulcs hozzáadása — `kulcs_hozzaadas.sh`

```
./kulcs_hozzaadas.sh                      # a vágólapról
./kulcs_hozzaadas.sh ~/Downloads/x.pub    # fájlból
```

Hat ellenőrzés után fűz hozzá; a miértje a
[ARCHITEKTURA.md](ARCHITEKTURA.md) szakaszban.

## Környezet

`uv` + Python 3.12 + [`pyhon-revived`](https://pypi.org/project/pyhon-revived/) 0.19.2
(az eredeti `pyhOn` 2024-08-14 óta nem frissül).

Újraépítés: `uv venv --python 3.12 && uv pip install pyhon-revived`

## Licenc

MIT — lásd a [LICENSE](LICENSE) fájlt.

A függőség, a [`pyhon-revived`](https://pypi.org/project/pyhon-revived/) szintén
MIT licencű (Copyright © 2023 Andre Basche). A projekt csak függ tőle, a kódját
nem tartalmazza.

## Felelősség

Ez a projekt egy mosógépet indít el távolról — vízzel és árammal dolgozó gépet.
A szerző nem vállal felelősséget semmilyen kárért. A gyártó biztonsági
megkötése (a programválasztó tárcsa fizikai „Remote" állásba fordítása minden
ciklus előtt) szándékos, és a projekt **nem kerüli meg** — a scriptek ellenőrzik
a gép állapotát, és nem indítanak, ha az nincs készenlétben.
