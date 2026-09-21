#!/usr/bin/env python3
"""iOS/macOS Parancsok (Shortcuts) fájl generálása a mosógép indításához.

A kész .shortcut fájlt a `shortcuts sign` írja alá, az `open` importálja a
Parancsok appba, onnan pedig iCloudon át átszinkronizál az iPhone-ra.

Az SSH-paraméterkulcsok a rendszerből származnak:
/System/Library/PrivateFrameworks/WorkflowKit.framework/.../Localizable.loctable
"""

import os
import plistlib
import subprocess
import sys
import uuid
from pathlib import Path

import konfig

konfig.betoltes()

# A projekt saját könyvtára — így bárhova áthelyezhető.
MAPPA = konfig.PROJEKT
HOST = konfig.kell("SSH_HOST", "a Mac címe, amire a telefon csatlakozik")
PORT = os.environ.get("SSH_PORT", "22")
USER = konfig.kell("SSH_USER", "a Mac felhasználóneve az SSH-hoz")

# U+FFFC — ide illeszti be a Parancsok az előző művelet kimenetét.
HELYORZO = "￼"

PROGRAMOK = [
    {"kulcs": "sotetek", "nev": "Darks", "szin": 4282601983, "glif": 59511,
     "mod": "", "megerosites": True},
    {"kulcs": "sanitizer", "nev": "Sanitizer", "szin": 4292093695, "glif": 59511,
     "mod": "", "megerosites": True},
    # Teszt-parancs: próbamenetben fut, ezért SOHA nem indít mosást. Megerősítést
    # sem kér, így fejetlenül (pl. `shortcuts run`) is futtatható. Arra való, hogy
    # az SSH-lánc — kulcs, hoszt, útvonal — ellenőrizhető legyen kockázat nélkül.
    {"kulcs": "sotetek", "nev": "próba", "szin": 4274264319, "glif": 59511,
     "mod": " proba", "megerosites": False, "cimke": "Mosás - Próba"},
]


def valtozo_hivatkozas(uuid_str: str, nev: str = "Shell Script Result") -> dict:
    """Az előző művelet kimenetére mutató 'magic variable'."""
    return {
        "Value": {
            "attachmentsByRange": {
                "{0, 1}": {
                    "Type": "ActionOutput",
                    "OutputUUID": uuid_str,
                    "OutputName": nev,
                }
            },
            "string": HELYORZO,
        },
        "WFSerializationType": "WFTextTokenString",
    }


def parancs(program: dict) -> dict:
    ssh_uuid = str(uuid.uuid4()).upper()
    szkript = f"{MAPPA}/mosas.sh {program['kulcs']}{program['mod']}"

    muveletek = []
    if program["megerosites"]:
        # A Mégse megszakítja a parancsot.
        muveletek.append({
            "WFWorkflowActionIdentifier": "is.workflow.actions.alert",
            "WFWorkflowActionParameters": {
                "WFAlertActionTitle": "Mosógép",
                "WFAlertActionMessage":
                    f"Elindítsam a(z) „{program['nev']}” mosást?\n\n"
                    "Ellenőrizd, hogy a tárcsa Remote állásban van "
                    "és az ajtó be van csukva.",
                "WFAlertActionCancelButtonShown": True,
            },
        })
    muveletek += [
        # 2. A burkoló futtatása a Mac minin SSH-n át.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.runsshscript",
            "WFWorkflowActionParameters": {
                "UUID": ssh_uuid,
                "WFSSHHost": program.get("host", HOST),
                "WFSSHPort": PORT,
                "WFSSHUser": USER,
                "WFSSHAuthenticationType": "SSH Key",
                "WFSSHScript": szkript,
            },
        },
        # 3. A burkoló egysoros válaszának megmutatása.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {
                "Text": valtozo_hivatkozas(ssh_uuid),
            },
        },
    ]

    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": program["szin"],
            "WFWorkflowIconGlyphNumber": program["glif"],
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem", "WFArticleContentItem",
            "WFContactContentItem", "WFDateContentItem",
            "WFEmailAddressContentItem", "WFGenericFileContentItem",
            "WFImageContentItem", "WFiTunesProductContentItem",
            "WFLocationContentItem", "WFDCMapsLinkContentItem",
            "WFAVAssetContentItem", "WFPDFContentItem",
            "WFPhoneNumberContentItem", "WFRichTextContentItem",
            "WFSafariWebPageContentItem", "WFStringContentItem",
            "WFURLContentItem",
        ],
        "WFWorkflowActions": muveletek,
    }


def main() -> int:
    kimenet = MAPPA / "parancsok"
    kimenet.mkdir(exist_ok=True)

    for program in PROGRAMOK:
        nyers = kimenet / f"Mosas-{program['kulcs']}-alairatlan.shortcut"
        cimke = program.get("cimke", f"Mosás {program['nev']}")
        kesz = kimenet / f"{cimke}.shortcut"

        nyers.write_bytes(plistlib.dumps(parancs(program), fmt=plistlib.FMT_BINARY))

        r = subprocess.run(
            ["shortcuts", "sign", "-m", "anyone", "-i", str(nyers), "-o", str(kesz)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"HIBA az aláírásnál ({program['nev']}): {r.stderr.strip()}",
                  file=sys.stderr)
            return 1
        nyers.unlink()
        print(f"  kész: {kesz.name}  ({kesz.stat().st_size} bájt)")

    print(f"\nA fájlok itt vannak: {kimenet}")
    print("Importálás: open '<fájl>'  — a Parancsok appban rá kell bökni a Hozzáadásra.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
