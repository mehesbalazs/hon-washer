"""Beállítások betöltése a config.sh fájlból.

Egyetlen konfigfájl szolgálja ki a shell scriptet és a Python scripteket is:
a `mosas.sh` source-olja, ezt a parser pedig kiolvassa belőle az `export`
sorokat. Nem futtat shellt — csak `KULCS="érték"` alakú sorokat fogad el,
így egy elgépelt konfigfájl nem tud kódot végrehajtani.

A környezeti változó mindig erősebb: ami már be van állítva, azt nem írja felül.
Így a `mosas.sh` által exportált értékek érvényben maradnak.
"""

# A parancs_keszito.py a rendszer Pythonjával fut (3.9), a többi a .venv-ből
# (3.12) — ez teszi a modern típusjelöléseket mindkettőn értelmezhetővé.
from __future__ import annotations

import os
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent


def betoltes(fajl: Path | None = None) -> Path | None:
    """Betölti a config.sh-t, ha létezik. Visszaadja a betöltött fájl útvonalát."""
    f = fajl or PROJEKT / "config.sh"
    if not f.is_file():
        return None

    for sor in f.read_text(encoding="utf-8").splitlines():
        sor = sor.strip()
        if not sor.startswith("export "):
            continue
        kulcs, jel, ertek = sor[len("export "):].partition("=")
        if not jel:
            continue
        kulcs = kulcs.strip()
        # A záró megjegyzést csak akkor vágjuk le, ha az érték nincs idézőjelben.
        ertek = ertek.strip()
        if ertek[:1] in ('"', "'") and ertek[:1] == ertek[-1:] and len(ertek) >= 2:
            ertek = ertek[1:-1]
        elif "#" in ertek:
            ertek = ertek.split("#", 1)[0].strip()
        if kulcs and kulcs not in os.environ:
            os.environ[kulcs] = ertek
    return f


def kell(kulcs: str, mire: str) -> str:
    """Kötelező beállítás kiolvasása, beszédes hibával, ha hiányzik."""
    ertek = os.environ.get(kulcs, "").strip()
    if not ertek:
        raise SystemExit(
            f"Hiányzik a(z) {kulcs} beállítás ({mire}).\n"
            f"Másold a config.example.sh-t config.sh néven, és töltsd ki:\n"
            f"  cp {PROJEKT}/config.example.sh {PROJEKT}/config.sh"
        )
    return ertek
