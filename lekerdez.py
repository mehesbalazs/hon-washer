#!/usr/bin/env python3
"""Candy/Hoover/Haier hOn mosógép LEKÉRDEZÉSE — kizárólag olvasás.

A script szándékosan képtelen parancsot küldeni a gépnek: induláskor letiltja
a pyhOn összes parancsküldő metódusát (lásd: parancskuldes_tiltasa).

Jelszókezelés: a script SOHA nem tárol jelszót. Sorrendben próbálja:
  1. HON_EMAIL / HON_PASSWORD környezeti változó
  2. macOS Kulcskarika (security find-generic-password -s hon)
  3. ~/.config/hon/jelszo fájl (600) — SSH-munkamenethez, ahol a Kulcskarika
     36-os kóddal elbukik, mert nincs, aki a hozzáférést megerősítse
  4. interaktív bekérés (getpass — a gépelés nem látszik, nem kerül naplóba)
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from getpass import getpass
from pathlib import Path

from pyhon import Hon
from pyhon.commands import HonCommand
from pyhon.connection.api import HonAPI

import konfig

konfig.betoltes()

# A saját gép MAC-címe a config.sh-ból — ezzel vetjük össze, amit a felhő mond.
# Üresen hagyva az ellenőrzés kimarad.
VART_MAC = os.environ.get("MOSOGEP_MAC", "").strip()

# A Haier-firmware állapotnevei (zárójelben) az Andre0512/hon integráció
# const.py-jából. Az 5-öst élesben igazoltuk: késleltetett indítás után a gép
# ezt jelenti, miközben a delayTime percenként fogy.
MACHMODE = {
    "0": "nincs állapot / kikapcsolva",                    # NO_STATE
    "1": "készenlét (Remote állás, indításra vár)",        # SELECTION_MODE
    "2": "MOSÁS FOLYAMATBAN",                              # EXECUTION_MODE
    "3": "szüneteltetve",                                  # PAUSE_MODE
    "4": "késleltetett indítás beállítás alatt",           # DELAY_START_SELECTION_MODE
    "5": "KÉSLELTETETT INDÍTÁS — visszaszámol",            # DELAY_START_EXECUTION_MODE
    "6": "hiba",                                           # ERROR_MODE
    "7": "ciklus befejezve",                               # END_MODE
    "8": "teszt üzemmód",                                  # TEST_MODE
    "9": "leállítás folyamatban",                          # STOP_MODE
}

# Ezeket a mezőket emeljük ki külön; a többi a --mind kapcsolóval jön.
KIEMELT = [
    ("machMode", "Gép állapota"),
    ("remoteCtrValid", "Távvezérlés engedélyezve"),
    ("remainingTimeMM", "Hátralévő idő (perc)"),
    ("delayTime", "Késleltetés (perc)"),
    ("prCode", "Program kódja"),
    ("prPhase", "Ciklus fázisa"),
    ("temp", "Hőmérséklet (°C)"),
    ("spinSpeed", "Centrifuga (ford/perc)"),
    ("doorLockStatus", "Ajtózár"),
    ("doorStatus", "Ajtó"),
    ("dryLevel", "Szárítási szint"),
    ("errors", "Hibakód"),
    ("totalWashCycle", "Összes mosási ciklus"),
]


def parancskuldes_tiltasa() -> None:
    """Fizikailag lehetetlenné teszi a parancsküldést ebben a folyamatban."""

    async def tiltva(*_args, **_kwargs):
        raise RuntimeError(
            "BIZTONSÁGI ZÁR: ez a script csak lekérdezni tud. Parancsküldés letiltva."
        )

    HonCommand.send = tiltva
    HonCommand.send_specific = tiltva
    HonCommand.send_parameters = tiltva
    # A tényleges szűk keresztmetszet: minden parancs ezen megy ki. Ha a
    # könyvtár valaha új utat nyit a HonCommand megkerülésével, ez is fogja.
    HonAPI.send_command = tiltva


def belepesi_adatok(email_arg: str | None) -> tuple[str, str]:
    email = email_arg or os.environ.get("HON_EMAIL", "")
    if not email:
        email = input("hOn fiók e-mail címe: ").strip()

    jelszo = os.environ.get("HON_PASSWORD", "")
    forras = "HON_PASSWORD környezeti változó"

    if not jelszo:
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", "hon", "-a", email, "-w"],
                capture_output=True, text=True, timeout=10,
            )
            # Csak a záró újsort vágjuk le: a jelszó elején/végén lehet szóköz.
            jelszo = r.stdout.rstrip("\n") if r.returncode == 0 else ""
            forras = "macOS Kulcskarika"
            if r.returncode != 0:
                print(f"  Kulcskarika nem elérhető (kód {r.returncode}): "
                      f"{r.stderr.strip()[:200]}", file=sys.stderr)
        except Exception as e:
            print(f"  Kulcskarika hiba: {type(e).__name__}: {e}", file=sys.stderr)
            jelszo = ""

    # Tartalék: jelszófájl. SSH-munkamenetben a Kulcskarika 36-os kóddal elbukik
    # ("interakció nem engedélyezett"), mert nincs, aki a hozzáférést megerősítse.
    if not jelszo:
        f = Path.home() / ".config" / "hon" / "jelszo"
        if f.is_file():
            mod = f.stat().st_mode & 0o777
            if mod & 0o077:
                print(f"  FIGYELEM: {f} jogosultsága {oct(mod)}, legyen 600 "
                      f"(chmod 600 {f})", file=sys.stderr)
            jelszo = f.read_text().rstrip("\n")
            forras = str(f)

    if not jelszo:
        jelszo = getpass(f"hOn jelszó ({email}): ")
        forras = "interaktív bekérés"

    print(f"  jelszó forrása: {forras}", file=sys.stderr)
    return email, jelszo


def ertek(parameter) -> str:
    return str(getattr(parameter, "value", parameter))


def keret(cim: str) -> None:
    print()
    print("=" * 66)
    print(f" {cim}")
    print("=" * 66)


def gep_kiirasa(gep, args) -> None:
    keret(f"{gep.brand} {gep.model_name}  —  „{gep.nick_name}”")

    def mac_norm(m: str) -> str:
        return "".join(c for c in m.upper() if c in "0123456789ABCDEF")

    mac = gep.mac_address.upper()
    print(f"  Típusjel        : {gep.appliance_type}  (modell-ID {gep.appliance_model_id})")
    print(f"  MAC-cím         : {mac}")
    if not VART_MAC:
        print("  MAC-ellenőrzés  : kihagyva (nincs MOSOGEP_MAC a config.sh-ban)")
    elif mac_norm(mac) == mac_norm(VART_MAC):
        print("  MAC-ellenőrzés  : EGYEZIK a beállított készülékkel")
    else:
        print(f"  MAC-ellenőrzés  : NEM egyezik — a várt cím {VART_MAC}")
    print(f"  Kapcsolat       : {'online' if gep.connection else 'OFFLINE'}")

    attrs = gep.attributes or {}
    params = attrs.get("parameters", {})

    keret("Állapot")
    for kulcs, cimke in KIEMELT:
        if kulcs not in params:
            continue
        v = ertek(params[kulcs])
        if kulcs == "machMode":
            v = f"{v}  →  {MACHMODE.get(v, 'ismeretlen kód')}"
        elif kulcs == "remoteCtrValid":
            v = f"{v}  →  {'IGEN, fogad távoli parancsot' if v == '1' else 'NEM — a tárcsa nincs Remote állásban'}"
        print(f"  {cimke:<28}: {v}")

    # A "pause" mezőt szándékosan nem írjuk ki: a könyvtár HonAttribute-ot
    # hasonlít sztringhez (appliances/wm.py), ezért mindig False. A szüneteltetést
    # a machMode=3 mutatja, azt fentebb értelmezzük.
    for extra in ("lastConnEvent", "active"):
        if extra in attrs:
            print(f"  {extra:<28}: {attrs[extra] if extra != 'lastConnEvent' else attrs[extra].get('category', '?')}")

    if args.mind:
        keret("Összes paraméter")
        for k in sorted(params):
            print(f"  {k:<32}: {ertek(params[k])}")

    if args.programok:
        keret("Elérhető programok (csak felsorolás, indítás nélkül)")
        parancs = gep.commands.get("startProgram")
        if parancs is None:
            print("  (ez a gép nem tesz közzé startProgram parancsot)")
        else:
            szuro = ("iot_recipe", "iot_guided")
            progok = {
                nev: cmd for nev, cmd in parancs.categories.items()
                if nev != "_" and all(f not in nev for f in szuro)
            }
            print(f"  {len(progok)} program:\n")
            print(f"    {'prCode':<8}  {'azonosító (ezt kell megadni)':<34}")
            print(f"    {'-'*8}  {'-'*34}")
            for nev, cmd in sorted(progok.items()):
                prcode = cmd.parameters.get("prCode")
                print(f"    {ertek(prcode) if prcode else '?':<8}  {nev:<34}")

            keret("Állítható paraméterek (az összes program uniója)")
            for nev, p in sorted(parancs.available_settings.items()):
                if hasattr(p, "min"):
                    print(f"    {nev:<24}: {p.min:g}–{p.max:g} (lépés {p.step:g})")
                elif getattr(p, "values", None):
                    print(f"    {nev:<24}: {', '.join(map(str, p.values))}")
                else:
                    print(f"    {nev:<24}: {ertek(p)} (fix)")

    if args.kedvencek:
        keret("Saját mentett programok (kedvencek) — teljes paraméterkészlet")
        parancs = gep.commands.get("startProgram")
        if parancs is None:
            print("  (nincs startProgram parancs)")
        else:
            kedvencek = {
                nev: cmd for nev, cmd in parancs.categories.items()
                if (f := cmd.parameters.get("favourite")) and ertek(f) == "1"
            }
            if not kedvencek:
                print("  (nincs kedvencnek jelölt program)")
            for nev, cmd in sorted(kedvencek.items()):
                pr = cmd.parameters.get("prCode")
                print(f"\n  ── „{nev}”  (prCode {ertek(pr) if pr else '?'}) ──")
                erdekes = ("temp", "spinSpeed", "dirtyLevel", "prewash", "hygiene",
                           "anticrease", "acquaplus", "extraRinse1", "extraRinse2",
                           "extraRinse3", "goodNight", "delayTime", "dryLevel",
                           "energyLabel", "suggestedLoadW", "programFamily")
                for k in erdekes:
                    if k in cmd.parameters:
                        print(f"      {k:<20}: {ertek(cmd.parameters[k])}")

    if args.statisztika and gep.statistics:
        keret("Statisztika")
        for k, v in sorted(gep.statistics.items()):
            print(f"  {k:<32}: {v}")


async def main() -> int:
    p = argparse.ArgumentParser(description="hOn mosógép lekérdezése (csak olvasás)")
    p.add_argument("--email", help="hOn fiók e-mail címe")
    p.add_argument("--mind", action="store_true", help="az összes állapotparaméter kiírása")
    p.add_argument("--programok", action="store_true", help="elérhető programok listázása")
    p.add_argument("--kedvencek", action="store_true", help="saját mentett programok paraméterei")
    p.add_argument("--statisztika", action="store_true", help="élettartam-statisztika")
    p.add_argument("--json", action="store_true", help="nyers JSON kimenet")
    args = p.parse_args()

    parancskuldes_tiltasa()
    print("Biztonsági zár aktív: parancsküldés letiltva, csak olvasás.", file=sys.stderr)

    email, jelszo = belepesi_adatok(args.email)
    print("Bejelentkezés a hOn felhőbe…", file=sys.stderr)

    hon = Hon(email, jelszo)
    # Egyszeri lekérdezés: kihagyjuk a tartós MQTT-feliratkozást.
    hon._mqtt_client = object()

    try:
        await hon.create()
    except Exception as e:
        print(f"\nHIBA a bejelentkezésnél vagy a lekérdezésnél: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        if not hon.appliances:
            print("\nA fiókhoz nem tartozik készülék.", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(
                [{"brand": g.brand, "model": g.model_name, "nick": g.nick_name,
                  "mac": g.mac_address, "type": g.appliance_type,
                  "connection": g.connection, "data": g.data} for g in hon.appliances],
                ensure_ascii=False, indent=2, default=str))
        else:
            print(f"\n{len(hon.appliances)} készülék a fiókban.")
            for gep in hon.appliances:
                gep_kiirasa(gep, args)
            print()
    finally:
        await hon.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
