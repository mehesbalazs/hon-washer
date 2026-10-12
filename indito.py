#!/usr/bin/env python3
"""Candy TCA286TM5-S — a két rögzített program indítása.

ALAPÉRTELMEZÉSBEN NEM INDÍT. Próbamenetben fut: kiírja a pontos payloadot,
amit elküldene, de nem küldi el. Éles indításhoz a --eles kapcsoló kell,
és a script előtte ellenőrzi a gép állapotát, majd megerősítést kér.

    ./indito.py --email <cím> sotetek              # próba, nem indít
    ./indito.py --email <cím> sotetek --eles       # tényleges indítás

A jelszót a lekerdez.py-val azonos módon szerzi (Kulcskarika / env / bekérés).
"""

import argparse
import asyncio
import json
import logging
import sys
from pprint import pformat

from pyhon import Hon
from pyhon.connection.api import HonAPI
from pyhon.exceptions import ApiError
from pyhon.parameter.enum import HonParameterEnum, clean_value

from lekerdez import belepesi_adatok, ertek, keret, MACHMODE, VART_MAC

# A --kedvencek lekérdezésből kiolvasott, ellenőrzött paraméterek.
PROGRAMOK = {
    "sanitizer": {
        "azonosito": "iot_wash_baby_sanitizer",
        "nev": "Sanitizer",
        "parameterek": {"temp": 60, "spinSpeed": 1000, "hygiene": 1, "extraRinse1": 1},
    },
    "sotetek": {
        "azonosito": "iot_wash_dark",
        "nev": "Sötétek",
        "parameterek": {"temp": 30, "spinSpeed": 800},
    },
}


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


async def kuldes(cmd, nev: str) -> int:
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

        keret(f"{gep.brand} {gep.model_name}")
        rendben, uzenet = allapot_ellenorzes(gep)
        print(f"  Állapot: {uzenet}")

        cmd, beallitott, kihagyott = program_beallitasa(gep, valasztott)
        print(f"  Program: „{valasztott['nev']}”  ({valasztott['azonosito']})")

        # A ténylegesen visszaolvasott értékeket írjuk ki, nem a beállításkor
        # feljegyzetteket — lásd visszaolvasas() docstringjét.
        tenylegesek = {k: ertek(cmd.parameters[k])
                       for k in valasztott["parameterek"] if k in cmd.parameters}
        print(f"  Visszaolvasott paraméterek: {tenylegesek}")
        elteresek = visszaolvasas(cmd, valasztott["parameterek"])
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

        return await kuldes(cmd, valasztott["nev"])
    finally:
        await hon.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
