# Mosógép lekérdező (Candy/Hoover/Haier hOn)

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
iOS Parancsok  ──SSH──▶  mosas.sh  ──▶  indito.py  ──importál──▶  lekerdez.py
                             │                                          │
                             └──────────▶ mosas.log                     └──▶ hOn felhő ──▶ mosógép
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

Részletek: [Hogyan működnek a fájlok](#hogyan-működnek-a-fájlok).

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
3. `machMode = 1` — készenlét; ha épp mos vagy szünetel, elutasítja
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

## Hogyan működnek a fájlok

Ez a rész azoknak szól, akik hónapok múlva térnek vissza a projekthez, és nem
emlékeznek rá, miért úgy van megírva valami, ahogy.

### `lekerdez.py` — a lekérdező és a közös alap

Két szerepe van: önállóan futtatva kiírja a gép állapotát, importálva pedig
olyan függvényeket ad az indítónak, amiket nem érdemes kétszer megírni.

**`parancskuldes_tiltasa()` — a biztonsági zár.** Definiál egy `tiltva()`
korutint, ami kivételt dob, majd ezzel **felülírja** a könyvtár négy
parancsküldő metódusát futás közben (ezt hívják monkeypatchnek). A lényeg,
hogy nem arról szól, hogy „nem hívjuk meg" — hanem arról, hogy a hívás
**fizikailag nem tud lefutni**, akkor sem, ha valaki később hibásan módosítja
a kódot. A negyedik, a `HonAPI.send_command` a legfontosabb: ez a valódi szűk
keresztmetszet, amin minden parancs kimegy. Ha a könyvtár egy frissítéssel új
utat nyitna a `HonCommand` hármas megkerülésével, ez akkor is fogná.

**`belepesi_adatok()` — a jelszó megszerzése.** Négy forrást próbál
sorrendben: környezeti változó → macOS Kulcskarika → `~/.config/hon/jelszo`
fájl → interaktív bekérés. Minden esetben kiírja, **honnan vette** a jelszót.
Ez nem kozmetika: pontosan ez a sor derítette ki, hogy SSH-munkamenetben a
Kulcskarika 36-os kóddal elbukik, és hogy a fájlos tartalék valóban beugrik.

**`ertek()` — típusegységesítés.** A pyhOn hol nyers értéket, hol
`HonAttribute`, hol `HonParameter` objektumot ad vissza. Ez a pár soros
segéd mindegyikből sztringet csinál, hogy az összehasonlítások ne dőljenek el
azon, épp melyik típus jött. Fontos részlet: a könyvtár `str_to_float`
függvénye előbb `int()`-tel próbálkozik, ezért `"1"` értékből `1` lesz, nem
`1.0` — emiatt működnek a `== "1"` alakú összehasonlítások.

**`gep_kiirasa()` — a megjelenítés.** A `MACHMODE` szótár a nyers kódokat
(`0`–`7`) magyar mondatra fordítja, így nem kell fejben tartani, hogy a `2`
azt jelenti, fut a mosás. A MAC-összehasonlítás normalizál: a felhő
kötőjellel (`10-B4-...`), a hálózat kettősponttal (`10:B4:...`) adja vissza
ugyanazt a címet — az első verzióm emiatt tévesen „nem egyezik"-et írt.

Kapcsolók: `--mind` (minden állapotparaméter), `--programok` (mind a 118),
`--kedvencek` (a saját mentett programok teljes paraméterkészlettel),
`--statisztika`, `--json`.

### `indito.py` — az indító

Ez az egyetlen fájl, ami parancsot küld a gépnek. Ezért itt van a legtöbb
védelem, és ezért érdemes minden sorát érteni.

**`PROGRAMOK` — a két program definíciója.** Nem kitalált értékek: a
`lekerdez.py --kedvencek` kimenetéből másoltuk ki őket, vagyis a gép saját
katalógusából. A `sanitizer` a `hygiene` és `extraRinse1` kapcsolókat is
bekapcsolja.

**`Proba` — a próbamenet.** Egy context manager (`with` blokkal használható
osztály), ami a küldés pillanatában lecseréli a `HonAPI.send_command`
metódust egy elfogóra. A `send()` így teljesen végigfut: összeállítja a
payloadot, alkalmazza a szabályokat — de az adat nem hagyja el a gépet.
Így pontosan látható, **mi menne el**, mielőtt bármi elindulna. Kilépéskor
visszaállítja az eredeti metódust, kivétel esetén is.

**`allapot_ellenorzes()` — négy feltétel.** Online-e a gép, engedélyezve
van-e a távvezérlés (`remoteCtrValid = 1`), készenlétben van-e
(`machMode = 1`), és nem fut vagy szünetel-e épp. Egy nem nyilvánvaló
döntés: az online állapotot a `lastConnEvent` mezőből olvassa, **nem** a
könyvtár `connection` tulajdonságából. Utóbbi csak csatlakozáskor
számolódik ki, és utána már csak MQTT-esemény frissítené — amit ez a script
szándékosan nem használ, mert egyszeri lekérdezést akarunk, nem tartós
kapcsolatot.

**`program_beallitasa()` — a program kiválasztása.** Két lépés, és a
sorrendjük számít. A program kiválasztása **lecseréli** az
`appliance.commands["startProgram"]` bejegyzést a programspecifikus
parancsra, ezért utána **újra ki kell olvasni** a parancsot — különben a
régin dolgoznánk tovább. Ezután jönnek a paraméterek, típus szerint
normalizálva: az enum típusúak sztringként tárolják az értéküket (`'1000'`),
a range típusúak számként. Ha ezt elrontjuk, a beállítás csendben elveszik,
és a gép az alapértelmezéssel indulna.

**`visszaolvasas()` — a legfontosabb védelem.** A gép `programRules`
szabályai egy **későbbi** paraméter beállításakor visszamenőleg átírhatnak
egy **korábbit**, és ezt semmilyen kivétel nem jelzi. Emiatt a beállítás
pillanatában feljegyzett érték nem bizonyíték semmire. Ez a függvény küldés
előtt visszaolvassa a tényleges értékeket, és eltérés esetén az indítás
megtagadja magát. A jelenlegi két programnak nincs ilyen szabálya — de egy
firmware- vagy programfrissítés bármikor behozhatja, és épp az élesítés a
legrosszabb pillanat kideríteni.

**A `main()` sorrendje**, mert ez adja a tényleges biztonságot:

1. bejelentkezés (hiba esetén lezárja a kapcsolatot, nem szivárog session)
2. mosógép kiválasztása — **több gépnél inkább nem indít**, hanem kilistázza őket
3. állapotellenőrzés
4. program és paraméterek beállítása, majd visszaolvasás
5. **mindig lefut a próbamenet**, és kiírja a payloadot
6. `--eles` nélkül itt megáll
7. hat kapu: állapot, hiányzó paraméter, visszaolvasási eltérés, MAC-egyezés
8. begépelt `INDIT` megerősítés (a `--igen` csak ezt hagyja ki, a kapukat nem)
9. **újabb állapotellenőrzés** — a megerősítés percekig tarthat, azalatt
   kinyithatták az ajtót vagy lejárhatott a Remote mód
10. küldés, külön kezelve a felhő elutasítását (`ApiError`) és a hálózati hibát

A 10. pont árnyalata: a könyvtár `send()` metódusa felhő-elutasításnál **nem**
`False`-t ad vissza, hanem kivételt dob. Az eredeti kódom emiatt tracebacket
adott volna az első elutasításnál.

### `mosas.sh` — a telefonos burkoló

Fordítóréteg a Parancsok app és a Python között. Három feladata van.

**Argumentumból beállítás.** A `sotetek` / `sanitizer` kulcsból nevet és
kapcsolókat képez. `proba` módban elhagyja az `--eles --igen` párost, így az
indítás lehetetlen — ez a teszt-parancs alapja.

**Naplózás.** A teljes kimenetet a `mosas.log`-ba fűzi, időbélyeggel és
kilépési kóddal. SSH-n át **ez az egyetlen ablak** arra, mi történt: a telefon
csak egy rövid mondatot lát, és ha valami elromlik, enélkül semmit nem lehetne
kideríteni. A projekt két legnehezebb hibáját is ez a napló fejtette meg.

**Kilépési kód → mondat.** A nyolc kódot emberi magyar mondatra fordítja. A
7-es a különleges eset: hálózati hiba küldés közben, amikor **nem tudni**,
elindult-e a mosás — ilyenkor kifejezetten figyelmeztet, hogy ellenőrizd,
mielőtt újrapróbálnád.

### `parancs_keszito.py` — a Parancsok-fájlok generátora

Beállító eszköz. Egy `.shortcut` fájl valójában aláírt plist, amiben a
műveletek egy tömbben ülnek. A generátor háromelemű tömböt épít:
megerősítő jelzés, SSH-szkript futtatása, majd az eredmény megjelenítése.

Az SSH-művelet paramétereinek neveit (`WFSSHHost`, `WFSSHUser`,
`WFSSHAuthenticationType`, `WFSSHScript`…) nem fejből írtuk: a rendszer
`WorkflowKit.framework` lokalizációs táblájából nyertük ki őket, hogy
karakterre egyezzenek.

A harmadik művelet egy „magic variable" hivatkozás — így kerül az SSH
kimenete az értesítésbe. Ennek formája egy `U+FFFC` helyőrző karakter a
szövegben, mellette pedig az SSH-művelet UUID-ja, ami megmondja, melyik
művelet kimenetét kell odailleszteni.

Végül a `shortcuts sign -m anyone` aláírja a fájlt, hogy bármelyik eszközön
importálható legyen. A `cimke` mező azért van külön a `nev`-től, hogy a
fájlnév eltérhessen a program nevétől (a próba-parancs neve `Mosás - Próba`).

### `kulcs_hozzaadas.sh` — SSH-kulcs biztonságos hozzáfűzése

Vágólapról vagy fájlból fűz hozzá nyilvános kulcsot, de csak hat ellenőrzés
után: nem üres, egyetlen sor, kulcsnak néz ki, az `ssh-keygen` is elfogadja,
ujjlenyomat alapján még nincs benne, és **van záró újsor a meglévő fájl
végén**.

Az utolsó nem kozmetika. Ha az `authorized_keys` utolsó sora nem újsorral
végződik, a hozzáfűzött kulcs **ráfolyik**, a két kulcs egyetlen értelmetlen
sorrá olvad, és attól **mindkettő** érvénytelen lesz — némán, mindössze annyi
történik, hogy a bejelentkezés nem megy. Ez a hiba a projekt során egyszer
már elő is fordult.

### A támogató fájlok

**`parancsok/`** — a három aláírt `.shortcut` fájl. Az appba importált
példányok ettől függetlenül élnek; ezek a forrás, új gépre vagy
visszaállításhoz. A fájlnevek egyeznek az appban lévő nevekkel, hogy egy
újragenerálás ne hozzon létre duplikátumot.

**`mosas.log`** — a burkoló naplója. Nyugodtan kiüríthető
(`: > mosas.log`), a naplózás attól még működik.

**`.venv/`** — Python 3.12 + `pyhon-revived` 0.19.2. Azért kell külön
környezet, mert a rendszer Pythonja 3.9.6, a könyvtár viszont 3.10+-t követel.

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
[Hogyan működnek a fájlok](#hogyan-működnek-a-fájlok) szakaszban.

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
