import json
import os
import re
import sys
from pathlib import Path

try:
    from jsonschema import Draft7Validator
except Exception as e:
    print("jsonschema not installed: ", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PATRONS_FILE = DATA / "wizard_patrons.json"
SCHEMA_FILE = DATA / "wizard_patrons.schema.json"

with PATRONS_FILE.open("r", encoding="utf-8") as f:
    data = json.load(f)
    # Ignore top-level $schema property during validation
    if isinstance(data, dict) and "$schema" in data:
        data = {k: v for k, v in data.items() if k != "$schema"}

with SCHEMA_FILE.open("r", encoding="utf-8") as f:
    schema = json.load(f)

# Validate against schema
v = Draft7Validator(schema)
errors = sorted(v.iter_errors(data), key=lambda e: e.path)
if errors:
    print("Schema validation errors:")
    for err in errors:
        loc = "/".join([str(p) for p in err.path])
        print(f"- {loc}: {err.message}")
    sys.exit(1)

# Additional roll-key sanity checks for invoke, taint, spellburn
pat = re.compile(r"^(?:[0-9]+|[0-9]+-[0-9]+|[0-9]+\+)$")
issues = []
for patron in data.get("patrons", []):
    name = patron.get("name")
    for section in ("invoke_patron", "patron_taint", "spellburn"):
        sec = patron.get(section, {})
        if isinstance(sec, dict):
            for k in sec.keys():
                if not pat.match(k):
                    issues.append((name, section, k))

if issues:
    print("Roll key format issues:")
    for name, section, key in issues:
        print(f"- {name} -> {section}: '{key}'")
    sys.exit(3)

print("Validation OK. Patrons:", len(data.get("patrons", [])))
