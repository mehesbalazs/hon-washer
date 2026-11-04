#!/usr/bin/env python3
"""Candy TCA286TM5-S — a két rögzített program indítása.

ALAPÉRTELMEZÉSBEN NEM INDÍT. Próbamenetben fut: kiírja a pontos payloadot,
amit elküldene, de nem küldi el. Éles indításhoz a --eles kapcsoló kell,
és a script előtte ellenőrzi a gép állapotát, majd megerősítést kér.

    ./indito.py --email <cím> sotetek              # próba, nem indít
    ./indito.py --email <cím> sotetek --eles       # tényleges indítás
    ./indito.py --email <cím> sotetek --vege 16:00 # késleltetve, 16:00-ig végez (próba)

A jelszót a lekerdez.py-val azonos módon szerzi (env / Kulcskarika / jelszófájl / bekérés).
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
from datetime import datetime, time as idopont, timedelta
from pathlib import Path
from pprint import pformat
from typing import NamedTuple

from pyhon import Hon
from pyhon.connection.api import HonAPI
from pyhon.exceptions import ApiError
from pyhon.parameter.enum import HonParameterEnum, clean_value

from lekerdez import belepesi_adatok, ertek, keret, MACHMODE, VART_MAC

# A --kedvencek lekérdezésből kiolvasott, ellenőrzött paraméterek. A
# becsles_perc csak addig kell a késleltetett indításhoz, amíg nincs mért
# futásidő (lásd futasido()); inkább hosszabb legyen: akkor korábban végez.
PROGRAMOK = {
    "sanitizer": {
        "azonosito": "iot_wash_baby_sanitizer",
        "nev": "Sanitizer",
        "parameterek": {"temp": 60, "spinSpeed": 1000, "hygiene": 1, "extraRinse1": 1},
        "becsles_perc": 150,
    },
    "sotetek": {
        "azonosito": "iot_wash_dark",
        "nev": "Sötétek",
        "parameterek": {"temp": 30, "spinSpeed": 800},
        "becsles_perc": 100,
    },
}

# A mért futásidők programonként. A gép a program hosszát előre nem árulja el
# (a katalógusban nincs ilyen mező), indításkor pedig csak becsül: a Darks-nál
# 77 percet mondott, és 95 alatt végzett. Ezért a tényleges futásokat mérjük.
FUTASIDO_FAJL = Path.home() / ".config" / "hon" / "futasidok.json"
MERESEK_SZAMA = 10
# Egy mérés akkor hihető, ha a gép indításkori becslésének a fele és a
# kétszerese közé esik. Az első két mérésnél az arány 0,88 és 1,23 volt.
HIHETO_ARANY = (0.5, 2.0)


class Proba:
    """Elfogja a küldést és kiírja, mi ment volna el. Semmit nem küld."""

    def __init__(self) -> None:
        self.elfogott = None
        self._eredeti = HonAPI.send_command

    def __enter__(self):
        proba = self

        async def elfog(_self, appliance, command, parameters, ancillary, program_name=""):
            proba.elfogott = {
                "macAddress": appliance.mac_address,
                "commandName": command,
                "programName": program_name.upper(),
                "applianceType": appliance.appliance_type,
                "parameters": parameters,
                "ancillaryParameters": ancillary,
            }
            return True

        HonAPI.send_command = elfog
        return self

    def __exit__(self, *_exc):
        HonAPI.send_command = self._eredeti
        return False


def allapot_ellenorzes(gep) -> tuple[bool, str]:
    params = (gep.attributes or {}).get("parameters", {})
    mach = ertek(params.get("machMode", "?"))
    remote = ertek(params.get("remoteCtrValid", "?"))

    # A gep.connection csak a csatlakozáskor számolódik ki, és utána már csak
    # MQTT-esemény frissíti — amit ez a script nem használ. A lastConnEvent
    # viszont a load_attributes() során frissül, ezért az újraellenőrzésnél
    # ez az egyetlen megbízható forrás.
    conn = (gep.attributes or {}).get("lastConnEvent", {}).get("category", "")
    if conn == "DISCONNECTED" or not gep.connection:
        return False, f"A gép OFFLINE (lastConnEvent={conn or '?'})."
    if remote != "1":
        return False, (
            "A távvezérlés NINCS engedélyezve (remoteCtrValid=%s).\n"
            "     Tekerd a programválasztó tárcsát 'Remote' állásba, "
            "zárd be az ajtót, és próbáld újra." % remote
        )
    if mach == "2":
        return False, "A gép ÉPPEN MOS (machMode=2). Nem küldök indítást rá."
    if mach == "3":
        return False, "A gép szüneteltetve van (machMode=3). Kézzel folytasd."
    if mach in ("4", "5"):
        return False, (
            "Már be van állítva egy késleltetett indítás (machMode=%s), a gép "
            "visszaszámol. Nem küldök rá újabbat." % mach)
    if mach != "1":
        return False, "A gép nem készenléti állapotban van: machMode=%s → %s" % (
            mach, MACHMODE.get(mach, "ismeretlen"))
    return True, "Készenlétben, távvezérlés engedélyezve."


def program_beallitasa(gep, valasztott: dict):
    """Kiválasztja a programot és ráállítja a rögzített paramétereket."""
    cmd = gep.commands.get("startProgram")
    if cmd is None:
        raise RuntimeError("A gép nem tesz közzé startProgram parancsot.")

    azonosito = valasztott["azonosito"]
    if azonosito not in cmd.categories:
        raise RuntimeError(
            "A(z) '%s' program nincs a gép katalógusában." % azonosito)

    # A program kiválasztása lecseréli az appliance.commands['startProgram']-ot
    # az adott programhoz tartozó parancsra, ezért utána újra ki kell olvasni.
    cmd.parameters["program"].value = azonosito
    cmd = gep.commands["startProgram"]

    beallitott, kihagyott = {}, {}
    for kulcs, ertek_ in valasztott["parameterek"].items():
        p = cmd.parameters.get(kulcs)
        if p is None:
            kihagyott[kulcs] = "nincs ilyen paraméter ennél a programnál"
            continue
        # Az enum-paraméterek sztringként tárolják az értékeiket ('1000'),
        # a range-ek számként. A könyvtár saját normalizálóját használjuk.
        ujertek = clean_value(ertek_) if isinstance(p, HonParameterEnum) else ertek_
        try:
            p.value = ujertek
            beallitott[kulcs] = ertek(p)
        except ValueError as e:
            kihagyott[kulcs] = str(e)
    return cmd, beallitott, kihagyott


def visszaolvasas(cmd, kert: dict) -> dict:
    """Visszaolvassa a ténylegesen beállt értékeket, és jelzi az eltéréseket.

    Miért kell: a gép programRules szabályai egy KÉSŐBBI paraméter beállításakor
    visszamenőleg átírhatnak egy KORÁBBAN beállítottat (pyhon/rules.py::_apply_fixed),
    és ez nem dob kivételt. A HonParameterFixed ráadásul semmilyen validációt nem
    végez. Ezért a beállítás pillanatában feljegyzett érték nem bizonyíték —
    közvetlenül küldés előtt vissza kell olvasni.
    """
    elteresek = {}
    for kulcs, kert_ertek in kert.items():
        p = cmd.parameters.get(kulcs)
        if p is None:
            continue  # ezt már a `kihagyott` jelezte
        if clean_value(p.value) != clean_value(kert_ertek):
            elteresek[kulcs] = (kert_ertek, p.value)
    return elteresek


class Terv(NamedTuple):
    """Késleltetett indítás: a delayTime (perc), az indulás, a várható vége és
    hogy az indulás „ma” vagy „holnap” van."""

    keses: int
    indulas: datetime
    vege: datetime
    nap: str


def idopont_arg(szoveg: str) -> idopont:
    try:
        return datetime.strptime(szoveg, "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError(f"ÓÓ:PP alakban kell, pl. 16:00 (nem: {szoveg!r})")


def futasidok() -> dict:
    try:
        return json.loads(FUTASIDO_FAJL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def program_azonosito(program_nev) -> str:
    """'PROGRAMS.WM_WD.IOT_WASH_DARK' és 'IOT_WASH_DARK' egyaránt: 'iot_wash_dark'."""
    return str(program_nev or "").rsplit(".", 1)[-1].lower()


def melyik_program(program_nev: str, parameterek: dict) -> str | None:
    """A PROGRAMOK kulcsa, ha a parancs pontosan az egyik rögzített program volt."""
    azonosito = program_azonosito(program_nev)
    for kulcs, prog in PROGRAMOK.items():
        if prog["azonosito"] == azonosito and all(
                clean_value(parameterek.get(k, "")) == clean_value(v)
                for k, v in prog["parameterek"].items()):
            return kulcs
    return None


async def gepi_becsles(hon, gep, parancs: dict) -> int | None:
    """A gép saját becslése (perc) a parancs indításakor, ha még megvan.

    A felhő a legutóbbi távoli indítást a gép akkori állapotával együtt őrzi
    (retrieve-last-activity), ebben a remainingTimeMM a gép becslése. A
    következő távoli indítás felülírja, ezért csak akkor fogadjuk el, ha a
    program, a késleltetés, a hőfok és a centrifuga is egyezik a mért
    parancséval.
    """
    try:
        aktivitas = await hon.api.load_last_activity(gep)
    except Exception:
        return None
    if not isinstance(aktivitas, dict):
        return None
    parameterek = parancs.get("parameters", {})
    if program_azonosito(aktivitas.get("programName")) != program_azonosito(
            parancs.get("programName")):
        return None
    if any(clean_value(aktivitas.get(k, "")) != clean_value(parameterek.get(k, ""))
           for k in ("delayTime", "temp", "spinSpeed")):
        return None
    try:
        perc = int(float(aktivitas.get("remainingTimeMM") or 0))
    except (TypeError, ValueError):
        return None
    return perc if perc > 0 else None


async def futasido_tanulas(hon, gep) -> None:
    """Megméri a legutóbbi, már befejeződött távoli mosás tényleges futásidejét.

    Kezdet: a legutóbbi startProgram parancs végrehajtásának ideje, plusz a
    késleltetése. Vég: amikor az adott program számlálója utoljára nőtt. A gép
    programonként számolja a befejezett ciklusokat (program<prPosition>: a
    Darks a program10, a Sanitizer a program14), és a felhő minden mezőhöz
    megőrzi a legutóbbi változás idejét. Ezért egy közben kézzel indított,
    másik programos mosás nem rontja el a mérést, egy megszakított mosás pedig
    nem számít, mert a számlálója nem nő.

    Csak a két rögzített program számít, ugyanazokkal a beállításokkal, és csak
    a 20–300 perc közötti eredmény. Ha a gép indításkori becslése még megvan,
    azt is eltesszük, és a mérésnek a HIHETO_ARANY szerinti sávba kell esnie:
    így az sem kerül be, ha közben kézzel ugyanezt a programot is lefuttattad
    (az órákkal később ér véget).
    """
    try:
        elozmeny = await hon.api.load_command_history(gep) or []
    except Exception as e:
        print(f"  (a parancs-előzmény nem olvasható: {type(e).__name__}: {e})", file=sys.stderr)
        return
    utolso = next((t for t in elozmeny
                   if t.get("command", {}).get("commandName") == "startProgram"), None)
    if utolso is None or not utolso.get("timestampExecuted"):
        return
    parancs = utolso["command"]
    parameterek = parancs.get("parameters", {})
    kulcs = melyik_program(parancs.get("programName", ""), parameterek)
    if kulcs is None:
        return

    vegrehajtva = datetime.fromisoformat(utolso["timestampExecuted"])
    keses = int(float(parameterek.get("delayTime") or 0))
    kezdet = vegrehajtva + timedelta(minutes=keses)
    adat = futasidok()
    bejegyzes = adat.get(kulcs, {})
    if bejegyzes.get("parameterek") != PROGRAMOK[kulcs]["parameterek"]:
        bejegyzes = {"parameterek": PROGRAMOK[kulcs]["parameterek"], "meresek": []}
    azon = kezdet.isoformat(timespec="seconds")
    if any(m.get("kezdet") == azon for m in bejegyzes["meresek"]):
        return  # ezt a mosást már megmértük

    szamlalo = (gep.attributes or {}).get("parameters", {}).get(
        f"program{parameterek.get('prPosition', '')}")
    vege = getattr(szamlalo, "last_update", None)
    if vege is None:
        return  # nincs programszámláló, így nem tudni biztosan, mikor ért véget
    # Futó vagy késleltetésre váró mosásnál a számláló még az előző befejezést
    # mutatja: a különbség negatív, és a mérés kiesik.
    perc = round((vege - kezdet).total_seconds() / 60)
    if not 20 <= perc <= 300:
        if keses and abs(perc) < 20:
            # Ha a delayTime a befejezésig számolna, a mosás épp a késleltetés
            # lejártakor érne véget — ezt érdemes látni a naplóban.
            print(f"  FIGYELEM: a késleltetett mosás a késleltetés lejártakor ért véget "
                  f"({perc:+} perc). Lehet, hogy a delayTime ezen a gépen a befejezésig számol.",
                  file=sys.stderr)
        return

    becsles = await gepi_becsles(hon, gep, parancs)
    if becsles and not HIHETO_ARANY[0] * becsles <= perc <= HIHETO_ARANY[1] * becsles:
        print(f"  (nem mérés: {perc} perc jött ki, a gép {becsles} percet becsült — "
              f"valószínűleg közben kézzel is lefutott ugyanez a program)", file=sys.stderr)
        return

    meres = {"kezdet": azon, "perc": perc}
    if becsles:
        meres["gep_becsles"] = becsles
    bejegyzes["meresek"] = (bejegyzes["meresek"] + [meres])[-MERESEK_SZAMA:]
    adat[kulcs] = bejegyzes
    FUTASIDO_FAJL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    FUTASIDO_FAJL.write_text(json.dumps(adat, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    gepe = f", a gép {becsles} percet becsült" if becsles else ""
    print(f"  futásidő megjegyezve: {PROGRAMOK[kulcs]['nev']} {perc} perc{gepe} "
          f"({len(bejegyzes['meresek'])} mérés)", file=sys.stderr)


def futasido(kulcs: str) -> tuple[int, int]:
    """A program várható hossza percben, és hány mérésből jön (0: becslés).

    Medián, nem számtani átlag: egy szokatlanul hosszú — például
    szüneteltetett — mosás mérése így nem húzza el a tervet.
    """
    prog = PROGRAMOK[kulcs]
    bejegyzes = futasidok().get(kulcs, {})
    meresek = []
    if bejegyzes.get("parameterek") == prog["parameterek"]:
        meresek = [m["perc"] for m in bejegyzes.get("meresek", []) if isinstance(m.get("perc"), int)]
    if meresek:
        return round(statistics.median(meresek)), len(meresek)
    return prog["becsles_perc"], 0


def kesleltetes(vege: idopont, perc: int, p, most: datetime | None = None) -> Terv | None:
    """Mennyi delayTime kell, hogy a program a legközelebbi `vege` időpontig végezzen.

    A delayTime az INDULÁSIG számol, nem a befejezésig: a hOn Home Assistant-
    integráció „Start Time”-nak hívja, a hOn appban is az indulást lehet
    megadni, és a két korábbi, appból beállított késleltetés is ezzel egyezik
    (mindkettő ~14:20-ra időzített indulást adott). Ezért indulás = vége −
    futásidő. A gép csak lépésközzel (30 perc) fogad késleltetést: lefelé
    kerekítünk, így legfeljebb egy lépéssel korábban végez, de nem később. Ha
    ma már nem fér bele, a holnapi időpontra számol. None, ha a gép felső
    korlátjába (1410 perc) így sem fér bele.
    """
    most = most or datetime.now().astimezone()
    # Az .astimezone() a naiv helyi időt az adott nap saját eltolásával látja
    # el, így egy közbeeső óraátállítás sem csúsztatja el a számítást.
    cel = datetime.combine(most.date(), vege).astimezone()
    if cel - timedelta(minutes=perc) < most:
        cel = datetime.combine(most.date() + timedelta(days=1), vege).astimezone()
    nyers = (cel - timedelta(minutes=perc) - most).total_seconds() / 60
    lepes = int(p.step)
    keses = max(int(p.min), int(nyers // lepes) * lepes)
    if keses > p.max:
        return None
    # Az újabb .astimezone() az időpontot a saját napja szerinti helyi időben
    # írja ki, akkor is, ha a késleltetés átnyúlik egy óraátállításon.
    indulas = (most + timedelta(minutes=keses)).astimezone()
    nap = "ma" if indulas.date() == most.date() else "holnap"
    return Terv(keses, indulas, (indulas + timedelta(minutes=perc)).astimezone(), nap)


class FelhoValasz(logging.Handler):
    """Elkapja a felhő válaszát, amit a pyhOn csak naplóz, de nem ad vissza.

    Elutasításkor a könyvtár a szerver válaszát a naplóba írja
    (pyhon/connection/api.py: `_LOGGER.error(await response.text())`), majd
    egy semmitmondó `ApiError("Can't send command")`-ot dob. Ez a kezelő a
    küldés idejére feliratkozik a naplóra, hogy a szerver saját hibaüzenete
    ne vesszen el.
    """

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.uzenetek: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.uzenetek.append(record.getMessage())


def felho_hiba(uzenetek: list[str]) -> dict | None:
    """Kikeresi a naplózott üzenetek közül a felhő JSON-válaszát.

    A válaszból két esetet lehet megkülönböztetni:
    - szerverhiba: a válaszban `errorType` van (pl. AWS Lambda
      `Runtime.ImportModuleError`) — a gyártó oldalán romlott el valami;
    - elutasítás: csak `resultCode` és esetleg üzenet — a felhő működik,
      de nem fogadta el a parancsot.
    """
    for uzenet in uzenetek:
        try:
            adat = json.loads(uzenet)
        except ValueError:
            continue
        payload = adat.get("payload") if isinstance(adat, dict) else None
        if not isinstance(payload, dict):
            continue
        return {
            "tipus": str(payload.get("errorType") or ""),
            "uzenet": str(payload.get("errorMessage") or payload.get("message") or ""),
            "kod": payload.get("resultCode"),
            "nyers": uzenet,
        }
    return None


async def kuldes(cmd, nev: str, terv: Terv | None = None) -> int:
    """Élesben elküldi a parancsot, és a kimenetet kilépési kódra fordítja.

    A felhő válaszát két géppel olvasható sorban írja ki (`FELHO_HIBA:` és
    `FELHO_ELUTASITAS:`), ezekből rakja össze a mosas.sh a telefonnak szóló
    mondatot. A jelölők szándékosan ASCII-k: SSH-munkamenetben a locale nem
    garantált.
    """
    naplo = logging.getLogger("pyhon")
    fogo = FelhoValasz()
    naplo.addHandler(fogo)
    try:
        # A send() felhő-elutasításnál NEM False-t ad vissza, hanem ApiError-t
        # dob (pyhon/commands.py::send_parameters). False csak hitelesítési
        # hibánál jön.
        siker = await cmd.send()
    except ApiError:
        hiba = felho_hiba(fogo.uzenetek)
        nyers = hiba["nyers"] if hiba else (fogo.uzenetek[0] if fogo.uzenetek else "")
        if nyers:
            print(f"  nyers felhőválasz: {nyers[:500]}", file=sys.stderr)

        if hiba and hiba["tipus"]:
            print(f"\nFELHO_HIBA: {hiba['tipus']} | {hiba['uzenet']}", file=sys.stderr)
            print("A gyártó szerverén hiba van, a mosás NEM indult el. Ez nem a "
                  "beállításod hibája — próbáld később.", file=sys.stderr)
            return 8

        if hiba:
            sor = f"resultCode={hiba['kod']} | {hiba['uzenet']}"
        else:
            # Nem JSON jött (pl. egy 502-es HTML-oldal): a nyers eleje többet mond,
            # mint egy üres resultCode.
            sor = f"nem értelmezhető válasz | {nyers[:80]}"
        print(f"\nFELHO_ELUTASITAS: {sor}", file=sys.stderr)
        print("A hOn felhő elutasította a parancsot, a mosás NEM indult el.",
              file=sys.stderr)
        return 4
    except Exception as e:
        # Hálózati hiba a küldés közben: nem tudjuk, a parancs kiment-e.
        print(f"\nHIBA küldés közben: {type(e).__name__}: {e}", file=sys.stderr)
        print("FIGYELEM: nem tudom, a parancs kiment-e. A mosás LEHET, hogy "
              "elindult — ellenőrizd a gépen vagy a lekerdez.py-val, "
              "MIELŐTT újra próbálnád.", file=sys.stderr)
        return 7
    finally:
        naplo.removeHandler(fogo)

    if siker:
        if terv:
            print(f"\nBEÁLLÍTVA: „{nev}” — {terv.nap} {terv.indulas:%H:%M}-kor indul, "
                  f"várható vége {terv.vege:%H:%M}")
        else:
            print(f"\nELINDÍTVA: „{nev}”")
        return 0
    print("\nA hitelesítés nem sikerült, a mosás nem indult el.", file=sys.stderr)
    return 4


async def main() -> int:
    p = argparse.ArgumentParser(description="A két rögzített mosóprogram indítása")
    p.add_argument("program", choices=sorted(PROGRAMOK), help="melyik programot")
    p.add_argument("--email", help="hOn fiók e-mail címe")
    p.add_argument("--eles", action="store_true",
                   help="TÉNYLEGESEN elindítja a gépet (enélkül csak próbamenet)")
    p.add_argument("--igen", action="store_true",
                   help="--eles mellett kihagyja a megerősítő kérdést")
    p.add_argument("--vege", type=idopont_arg, metavar="ÓÓ:PP",
                   help="késleltetett indítás: úgy indul, hogy erre az időre végezzen")
    args = p.parse_args()

    valasztott = PROGRAMOK[args.program]
    uzemmod = "ÉLES INDÍTÁS" if args.eles else "PRÓBAMENET (nem indít semmit)"
    print(f"Üzemmód: {uzemmod}", file=sys.stderr)

    email, jelszo = belepesi_adatok(args.email)
    hon = Hon(email, jelszo)
    hon._mqtt_client = object()

    try:
        await hon.create()
    except Exception as e:
        print(f"HIBA a bejelentkezésnél: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            await hon.close()
        except Exception:
            pass
        return 1

    try:
        gepek = [g for g in hon.appliances if g.appliance_type == "WM"]
        if not gepek:
            print("Nem találtam mosógépet a fiókban.", file=sys.stderr)
            return 1
        if len(gepek) > 1:
            # Több mosógépnél nem tippelünk, melyiket indítsuk.
            print("Több mosógép van a fiókban, nem tudom melyiket indítsam:",
                  file=sys.stderr)
            for g in gepek:
                print(f"    {g.model_name}  {g.mac_address}", file=sys.stderr)
            return 1
        gep = gepek[0]
        await futasido_tanulas(hon, gep)

        keret(f"{gep.brand} {gep.model_name}")
        rendben, uzenet = allapot_ellenorzes(gep)
        print(f"  Állapot: {uzenet}")

        cmd, beallitott, kihagyott = program_beallitasa(gep, valasztott)
        print(f"  Program: „{valasztott['nev']}”  ({valasztott['azonosito']})")

        # A késleltetés ugyanúgy kért paraméter, mint a többi: a visszaolvasás
        # és az eltérés-ellenőrzés rá is vonatkozik.
        kert = dict(valasztott["parameterek"])
        terv = None
        if args.vege:
            perc, meresek = futasido(args.program)
            p_kes = cmd.parameters.get("delayTime")
            if p_kes is None:
                print("\nNEM INDÍTOK: ez a program nem ismer késleltetett indítást.",
                      file=sys.stderr)
                return 5
            terv = kesleltetes(args.vege, perc, p_kes)
            if terv is None:
                print(f"\nNEM INDÍTOK: {args.vege:%H:%M}-ig nem tud végezni, ehhez "
                      f"{p_kes.max:g} percnél hosszabb késleltetés kellene.", file=sys.stderr)
                return 9
            kert["delayTime"] = terv.keses
            try:
                p_kes.value = terv.keses
            except ValueError as e:
                kihagyott["delayTime"] = str(e)
            forras = f"{meresek} mérés mediánja" if meresek else "BECSLÉS, még nincs mérés"
            print(f"  Késleltetés: {terv.keses} perc — {terv.nap} {terv.indulas:%H:%M}-kor indul, "
                  f"várható vége {terv.vege:%H:%M} (futásidő {perc} perc, {forras})")
            # Géppel olvasható sor a mosas.sh-nak: nap, indulás, vége, mérések száma.
            print(f"KESLELTETES: {terv.nap} {terv.indulas:%H:%M} {terv.vege:%H:%M} {meresek}")

        # A ténylegesen visszaolvasott értékeket írjuk ki, nem a beállításkor
        # feljegyzetteket — lásd visszaolvasas() docstringjét.
        tenylegesek = {k: ertek(cmd.parameters[k]) for k in kert if k in cmd.parameters}
        print(f"  Visszaolvasott paraméterek: {tenylegesek}")
        elteresek = visszaolvasas(cmd, kert)
        if kihagyott:
            print(f"  FIGYELEM, nem sikerült beállítani: {kihagyott}")
        if elteresek:
            print("  FIGYELEM, a gép szabályai felülírták a kért értéket:")
            for k, (kert_e, tenyl) in elteresek.items():
                print(f"      {k}: kért={kert_e}  ->  tényleges={tenyl}")

        with Proba() as proba:
            await cmd.send()
        keret("Ez a payload menne el")
        print(pformat(proba.elfogott, width=100))

        if not args.eles:
            print("\nPróbamenet volt — a gépre SEMMI nem ment el.")
            print("Éles indításhoz tedd hozzá: --eles")
            return 0

        if not rendben:
            print("\nNEM INDÍTOK: a gép nincs indításra kész állapotban.", file=sys.stderr)
            return 2

        if kihagyott:
            print("\nNEM INDÍTOK: nem sikerült minden paramétert beállítani, "
                  "és nem indítok ismeretlen beállításokkal mosást.", file=sys.stderr)
            return 5

        if elteresek:
            print("\nNEM INDÍTOK: a gép szabályai felülírtak legalább egy kért "
                  "paramétert, így nem azt a mosást indítanám, amit kértél.",
                  file=sys.stderr)
            return 5

        if not VART_MAC:
            # Beállított MAC nélkül nem tudjuk igazolni, hogy a jó géphez szólunk.
            print("\nNEM INDÍTOK: nincs beállítva a MOSOGEP_MAC a config.sh-ban, "
                  "így nem tudom ellenőrizni, hogy tényleg a te géped válaszol-e.",
                  file=sys.stderr)
            return 6

        mac_ok = "".join(c for c in gep.mac_address.upper() if c in "0123456789ABCDEF")
        mac_vart = "".join(c for c in VART_MAC.upper() if c in "0123456789ABCDEF")
        if mac_ok != mac_vart:
            print(f"\nNEM INDÍTOK: a gép MAC-címe ({gep.mac_address}) nem az ismert "
                  f"készüléké ({VART_MAC}). Ha cserélted a gépet, írd át a "
                  f"MOSOGEP_MAC értékét a config.sh-ban.", file=sys.stderr)
            return 6

        if not args.igen:
            if terv:
                print(f"\nBe fog állni a(z) „{valasztott['nev']}” program: {terv.nap} "
                      f"{terv.indulas:%H:%M}-kor indul, várható vége {terv.vege:%H:%M}.")
            else:
                print(f"\nEl fog indulni a(z) „{valasztott['nev']}” program a mosógépen.")
            try:
                valasz = input('Megerősítés — írd be, hogy INDIT: ').strip()
            except (EOFError, KeyboardInterrupt):
                print("\nNincs interaktív bevitel — megszakítva, semmi nem indult el.",
                      file=sys.stderr)
                print("Ha tudatosan, megerősítés nélkül indítanál: --igen", file=sys.stderr)
                return 3
            if valasz != "INDIT":
                print("Megszakítva, semmi nem indult el.")
                return 3

        # A megerősítés eltarthat percekig — addig a gép állapota változhat
        # (ajtó kinyitva, Remote mód lejárt). Küldés előtt újra ellenőrizzük.
        # A machMode és a remoteCtrValid NEM parancsparaméterek, ezért a küldés
        # előtti sync_command_to_params() nem zárolja őket: a load_attributes()
        # ezekre valódi, friss felhőértéket ad.
        await gep.load_attributes()
        rendben_meg, uzenet_meg = allapot_ellenorzes(gep)
        if not rendben_meg:
            print(f"\nNEM INDÍTOK: az állapot megváltozott. {uzenet_meg}", file=sys.stderr)
            return 2

        if terv:
            # Ugyanezért a késleltetést is a küldés pillanatára számoljuk újra,
            # különben a megerősítéssel töltött idővel később végezne.
            uj = kesleltetes(args.vege, perc, cmd.parameters["delayTime"])
            if uj is None:
                print(f"\nNEM INDÍTOK: {args.vege:%H:%M}-ig már nem tud végezni.", file=sys.stderr)
                return 9
            if uj.keses != terv.keses:
                cmd.parameters["delayTime"].value = uj.keses
                if visszaolvasas(cmd, {"delayTime": uj.keses}):
                    print("\nNEM INDÍTOK: az újraszámolt késleltetést a gép szabályai "
                          "felülírták.", file=sys.stderr)
                    return 5
                print(f"  A késleltetés közben {uj.keses} percre változott.")
            terv = uj
            print(f"KESLELTETES: {terv.nap} {terv.indulas:%H:%M} {terv.vege:%H:%M} {meresek}")

        return await kuldes(cmd, valasztott["nev"], terv)
    finally:
        await hon.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
