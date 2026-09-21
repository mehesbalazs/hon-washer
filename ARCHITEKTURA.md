# Hogyan működnek a fájlok

Ez a dokumentum azoknak szól, akik hónapok múlva térnek vissza a projekthez, és
nem emlékeznek rá, miért úgy van megírva valami, ahogy. A használathoz nem kell
elolvasni — arra a [README](README.md) való.

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

## `lekerdez.py` — a lekérdező és a közös alap

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

## `indito.py` — az indító

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

## `mosas.sh` — a telefonos burkoló

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

## `parancs_keszito.py` — a Parancsok-fájlok generátora

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

## `kulcs_hozzaadas.sh` — SSH-kulcs biztonságos hozzáfűzése

Vágólapról vagy fájlból fűz hozzá nyilvános kulcsot, de csak hat ellenőrzés
után: nem üres, egyetlen sor, kulcsnak néz ki, az `ssh-keygen` is elfogadja,
ujjlenyomat alapján még nincs benne, és **van záró újsor a meglévő fájl
végén**.

Az utolsó nem kozmetika. Ha az `authorized_keys` utolsó sora nem újsorral
végződik, a hozzáfűzött kulcs **ráfolyik**, a két kulcs egyetlen értelmetlen
sorrá olvad, és attól **mindkettő** érvénytelen lesz — némán, mindössze annyi
történik, hogy a bejelentkezés nem megy. Ez a hiba a projekt során egyszer
már elő is fordult.

## A támogató fájlok

**`parancsok/`** — a három aláírt `.shortcut` fájl. Az appba importált
példányok ettől függetlenül élnek; ezek a forrás, új gépre vagy
visszaállításhoz. A fájlnevek egyeznek az appban lévő nevekkel, hogy egy
újragenerálás ne hozzon létre duplikátumot.

**`mosas.log`** — a burkoló naplója. Nyugodtan kiüríthető
(`: > mosas.log`), a naplózás attól még működik.

**`.venv/`** — Python 3.12 + `pyhon-revived` 0.19.2. Azért kell külön
környezet, mert a rendszer Pythonja 3.9.6, a könyvtár viszont 3.10+-t követel.
