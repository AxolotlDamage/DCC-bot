#!/usr/bin/env python3
# Validates language tables in modules.data_constants.py against a CSV table.
# If no CSV path is provided, uses the embedded CSV below (from user).

import csv
import io
import os
import re
import sys
from typing import Dict, List, Set

# Import in-repo constants
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from modules.data_constants import (
    HALFLING_LANGUAGE_TABLE,
    ELF_LANGUAGE_TABLE,
    DWARF_LANGUAGE_TABLE,
    LV0_LANGUAGE_TABLE,
    WIZARD_LANGUAGE_TABLE,
    WARRIOR_LANGUAGE_TABLE,
    CLERIC_LANGUAGE_TABLE,
    THIEF_LANGUAGE_TABLE,
)

EMBEDDED_CSV = """Language,0-Level Human,Warrior,Cleric,Thief,Wizard,Halfling,Elf,Dwarf
Alignment tongue,01-20,01-20,01-20,01-15,01-10,01-25,01-20,01-20
Chaos,–,–,–,16-20,11-13,–,21-25,–
Law,–,–,–,21-25,14-16,–,26-30,–
Neutrality,–,–,–,26-30,17-19,–,31-35,–
Dwarf,21-30,21-30,21-25,31-32,20-21,26-35,36-40,–
Elf,31-35,31-35,26-30,33-34,22-23,36-40,–,21-25
Halfling,36-40,36-38,31-35,35-44,24-25,–,41-45,26-35
Gnome,41-45,–,36-40,45-49,26-27,41-50,–,36-40
Bugbear,46-47,39-43,41-45,50-54,28-29,51-55,–,41-45
Goblin,48-57,44-53,46-55,55-64,30-35,56-70,46-48,46-55
Gnoll,58-60,54-58,56-60,65-69,36-39,–,49-50,56-60
Harpy,–,59-63,–,70-71,40-41,–,51-52,–
Hobgoblin,61-65,64-70,61-65,72-74,42-45,71-80,53-54,61-65
Kobold,66-75,71-78,66-75,75-78,46-49,81-90,55-57,66-75
Lizard man,76-80,79-81,76-78,79,50-53,–,58,–
Minotaur,81,82-83,–,–,54-55,–,59,76
Ogre,82-83,84-88,79-80,–,56-57,–,60,77-81
Orc,84-93,89-95,81-82,–,58-62,–,61-63,82-86
Serpent-man,–,96,83,80-81,63-65,–,64,–
Troglodyte,94-99,97-98,84-88,82-83,66-68,–,65,87-91
Angelic (Celestial),–,–,89-92,–,69-72,–,66-70,–
Centaur,–,–,93,–,73,–,71-75,–
Demonic (Infernal/Abyssal),–,–,94-97,84,74-79,–,76-80,–
Doppelganger,–,–,–,85,80,–,–,–
Dragon,–,–,98,86-87,81-84,–,81-85,92-93
Pixie,–,–,99,88-89,85-86,91-93,86-90,–
Giant,100,99-100,100,90-91,87-88,–,–,94-97
Griffon,–,–,–,–,89,–,–,–
Naga,–,–,–,–,90,–,91-92,–
Bear,–,–,–,–,91-92,–,–,98
Eagle,–,–,–,–,93-94,–,93-94,–
Ferret,–,–,–,–,–,94-98,–,–
Horse,–,–,–,–,95-96,–,95-96,–
Wolf,–,–,–,–,97-98,–,–,–
Spider,–,–,–,–,99,–,–,–
Undercommon,–,–,–,92-100,100,99-100,97-100,99-100
"""

CLASS_TO_CONST = {
    "0-Level Human": LV0_LANGUAGE_TABLE,
    "Wizard": WIZARD_LANGUAGE_TABLE,
    "Halfling": HALFLING_LANGUAGE_TABLE,
    "Elf": ELF_LANGUAGE_TABLE,
    "Dwarf": DWARF_LANGUAGE_TABLE,
    "Warrior": WARRIOR_LANGUAGE_TABLE,
    "Cleric": CLERIC_LANGUAGE_TABLE,
    "Thief": THIEF_LANGUAGE_TABLE,
}

NAME_NORMALIZE = {
    "by_alignment": "Alignment tongue",
    # Parenthetical wording normalization
    "Angelic (a.k.a. Celestial)": "Angelic (Celestial)",
    "Demonic (a.k.a. Infernal/Abyssal)": "Demonic (Infernal/Abyssal)",
}

DASHES = {"-", "–", "—"}

roll_pat = re.compile(r"^(\d{1,3})(?:\s*-\s*(\d{1,3}))?$")


def parse_rolls(cell: str) -> Set[int]:
    s = (cell or "").strip()
    if not s or s in DASHES:
        return set()
    m = roll_pat.match(s)
    if not m:
        raise ValueError(f"Bad roll cell: {cell!r}")
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if not (1 <= a <= 100 and 1 <= b <= 100 and a <= b):
        raise ValueError(f"Roll out of range: {cell!r}")
    return set(range(a, b + 1))


def norm_name(name: str) -> str:
    name = name.strip()
    return NAME_NORMALIZE.get(name, name)


def invert_table(table: Dict[range, str]) -> Dict[str, Set[int]]:
    inv: Dict[str, Set[int]] = {}
    for rng, lang in table.items():
        lname = norm_name(lang)
        inv.setdefault(lname, set()).update(set(rng))
    return inv


def load_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_embedded() -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(EMBEDDED_CSV)))


def compare():
    rows = load_csv(sys.argv[1]) if len(sys.argv) > 1 else load_embedded()
    header = rows[0].keys()
    classes = [h for h in header if h != "Language"]

    # Build in-repo maps
    inv_maps = {cls: invert_table(CLASS_TO_CONST[cls]) for cls in CLASS_TO_CONST}

    overall_ok = True

    for cls in classes:
        if cls not in CLASS_TO_CONST:
            print(f"SKIP: No in-repo table for class '{cls}'")
            continue
        inv = inv_maps[cls]
        # Build expected from CSV
        exp: Dict[str, Set[int]] = {}
        for row in rows:
            lang = norm_name(row["Language"])  # normalize names
            rolls = parse_rolls(row.get(cls, "") or "")
            if not rolls:
                continue
            exp.setdefault(lang, set()).update(rolls)
        # Compare coverage per language
        all_langs = set(inv.keys()) | set(exp.keys())
        for lang in sorted(all_langs):
            have = inv.get(lang, set())
            want = exp.get(lang, set())
            if have != want:
                overall_ok = False
                only_have = sorted(have - want)
                only_want = sorted(want - have)
                if only_have or only_want:
                    print(f"MISMATCH [{cls}] {lang} -> in-repo:{summary(have)} vs CSV:{summary(want)}")
        # Also check that union covers 1..100 with no overlaps
        if not coverage_ok(inv):
            overall_ok = False
            print(f"WARNING: Coverage/overlap issue in {cls} in-repo map")

    if overall_ok:
        print("Language table validation: OK (in-repo matches CSV for supported classes)")
    else:
        print("Language table validation: FOUND mismatches above")


def summary(s: Set[int]) -> str:
    if not s:
        return "—"
    # compress into ranges
    out = []
    start = prev = None
    for n in sorted(s):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ",".join([f"{a:02d}-{b:02d}" if a != b else f"{a:02d}" for a, b in out])


def coverage_ok(inv: Dict[str, Set[int]]) -> bool:
    seen: Dict[int, str] = {}
    for lang, rolls in inv.items():
        for r in rolls:
            if r < 1 or r > 100:
                return False
            if r in seen:
                # overlapping assignment
                return False
            seen[r] = lang
    return set(seen.keys()) == set(range(1, 101))


if __name__ == "__main__":
    compare()
