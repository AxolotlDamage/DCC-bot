"""Lightweight health check for the DCC Bot.

Run: python scripts/health_check.py [--strict] [--json]

Checks performed:
1. Dependency presence (discord, dotenv, jsonschema (optional)).
2. Data files existence & JSON parse: Spells.json, data/wizard_patrons.json, data/familiars.json, occupations_full.json.
3. Patron schema validation (if jsonschema available).
4. Character file parse sanity (name, class, hp shape) in SAVE_FOLDER.
5. Familiar linkage integrity (wizard notes.familiar_name points to an existing familiar record and vice versa).

Outputs a summary; with --json emits machine-readable JSON.
Exit code: 0 unless --strict given and any failures occur.
"""
from __future__ import annotations
import os, json, sys, argparse, importlib, traceback
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
SAVE_FOLDER = os.getenv('SAVE_FOLDER') or str(Path(ROOT) / 'characters')

Result = Dict[str, Any]

def _check_import(module_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
        return True, 'ok'
    except Exception as e:
        return False, f'{e.__class__.__name__}: {e}'

def _load_json(path: Path) -> tuple[bool, Any, str]:
    if not path.exists():
        return False, None, 'missing'
    try:
        with path.open('r', encoding='utf-8') as f:
            return True, json.load(f), 'ok'
    except Exception as e:
        return False, None, f'parse error: {e}'

def check_dependencies() -> Result:
    deps = {}
    for mod in ['discord', 'dotenv', 'jsonschema']:
        ok, msg = _check_import(mod)
        deps[mod] = {'present': ok, 'detail': msg}
    return {'dependencies': deps}

def check_data_files() -> Result:
    targets = {
        'Spells.json': ROOT / 'Spells.json',
        'wizard_patrons.json': DATA / 'wizard_patrons.json',
        'wizard_patrons.schema.json': DATA / 'wizard_patrons.schema.json',
        'familiars.json': DATA / 'familiars.json',
        'occupations_full.json': ROOT / 'occupations_full.json',
    }
    out = {}
    for name, path in targets.items():
        ok, blob, detail = _load_json(path)
        out[name] = {'exists': path.exists(), 'ok': ok, 'detail': detail, 'size': path.stat().st_size if path.exists() else 0}
    return {'data_files': out}

def validate_patrons_schema(data_files: Result) -> Result:
    patrons = data_files['data_files'].get('wizard_patrons.json', {})
    schema = data_files['data_files'].get('wizard_patrons.schema.json', {})
    if not patrons.get('ok') or not schema.get('ok'):
        return {'patrons_schema': {'validated': False, 'detail': 'patrons or schema not OK'}}
    try:
        import jsonschema  # type: ignore
    except Exception:
        return {'patrons_schema': {'validated': False, 'detail': 'jsonschema not installed'}}
    # Reload full objects
    p_path = DATA / 'wizard_patrons.json'
    s_path = DATA / 'wizard_patrons.schema.json'
    _, patrons_obj, _ = _load_json(p_path)
    _, schema_obj, _ = _load_json(s_path)
    try:
        jsonschema.validate(patrons_obj, schema_obj)
        return {'patrons_schema': {'validated': True, 'detail': 'ok'}}
    except jsonschema.ValidationError as ve:
        return {'patrons_schema': {'validated': False, 'detail': f'validation error: {ve.message}'}}
    except Exception as e:
        return {'patrons_schema': {'validated': False, 'detail': f'error: {e}'}}

def scan_characters() -> Result:
    folder = Path(SAVE_FOLDER)
    if not folder.exists():
        return {'characters': {'count': 0, 'detail': 'folder missing'}}
    issues = []
    count = 0
    fams = {}
    wizards = {}
    for f in folder.glob('*.json'):
        ok, blob, detail = _load_json(f)
        if not ok:
            issues.append(f'{f.name}: {detail}')
            continue
        count += 1
        name = str(blob.get('name') or f.stem)
        cls = str(blob.get('class') or '')
        if cls.lower() == 'familiar':
            fams[name] = blob
        if cls.lower() in {'wizard','mage','elf'}:
            wizards[name] = blob
        hp = blob.get('hp')
        if isinstance(hp, dict):
            if 'current' not in hp or 'max' not in hp:
                issues.append(f'{name}: hp dict missing current/max')
        elif not isinstance(hp, (int, float)):
            issues.append(f'{name}: hp malformed')
    # Cross-check familiar linkage
    linkage_issues = []
    for wiz_name, wiz in wizards.items():
        fam_name = ((wiz.get('notes') or {}).get('familiar_name'))
        if fam_name:
            if fam_name not in fams:
                linkage_issues.append(f'{wiz_name}: notes.familiar_name="{fam_name}" not found among familiars')
    for fam_name, fam in fams.items():
        master = ((fam.get('notes') or {}).get('familiar') or {}).get('master')
        if master and master not in wizards:
            linkage_issues.append(f'{fam_name}: master="{master}" not found among wizard/mage/elf records')
    return {'characters': {
        'count': count,
        'issues': issues,
        'familiar_count': len(fams),
        'wizard_like_count': len(wizards),
        'linkage_issues': linkage_issues,
    }}

def summarize(results: Dict[str, Any]) -> str:
    lines = []
    deps = results.get('dependencies', {}).get('dependencies', {}) or results.get('dependencies', {})
    if deps:
        lines.append('Dependencies:')
        for k, v in deps.items():
            lines.append(f'  - {k}: {"OK" if v.get("present") else "MISSING"} ({v.get("detail")})')
    df = results.get('data_files', {}).get('data_files', {}) or results.get('data_files', {})
    if df:
        lines.append('Data Files:')
        for k, v in df.items():
            status = 'OK' if v.get('ok') else ('MISSING' if not v.get('exists') else 'ERROR')
            lines.append(f'  - {k}: {status} ({v.get("detail")})')
    ps = results.get('patrons_schema', {})
    if ps:
        lines.append(f"Patrons Schema: {'OK' if ps.get('validated') else 'FAIL'} ({ps.get('detail')})")
    chars = results.get('characters', {})
    if chars:
        lines.append(f"Characters: count={chars.get('count')} familiars={chars.get('familiar_count')} wizards={chars.get('wizard_like_count')}")
        if chars.get('issues'):
            lines.append('  Character Issues:')
            for i in chars.get('issues', [])[:50]:
                lines.append(f'    - {i}')
            extra = max(0, len(chars.get('issues', [])) - 50)
            if extra:
                lines.append(f'    … {extra} more')
        if chars.get('linkage_issues'):
            lines.append('  Familiar Linkage Issues:')
            for i in chars.get('linkage_issues', [])[:50]:
                lines.append(f'    - {i}')
            extra = max(0, len(chars.get('linkage_issues', [])) - 50)
            if extra:
                lines.append(f'    … {extra} more')
    return "\n".join(lines)

def main(argv=None):
    ap = argparse.ArgumentParser(description='DCC Bot health check')
    ap.add_argument('--strict', action='store_true', help='Exit non-zero on any failures')
    ap.add_argument('--json', action='store_true', help='Emit JSON instead of text summary')
    args = ap.parse_args(argv)
    results: Dict[str, Any] = {}
    try:
        results.update(check_dependencies())
        df = check_data_files()
        results.update(df)
        results.update(validate_patrons_schema(df))
        results.update(scan_characters())
    except Exception:
        results['fatal'] = traceback.format_exc()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(summarize(results))
        if 'fatal' in results:
            print('\nFatal error:\n' + results['fatal'])
    if args.strict:
        # Determine failures: missing dependency, data file error, schema fail, character issues
        failures = []
        for mod, v in results.get('dependencies', {}).items():
            if not v.get('present'): failures.append(f'dep:{mod}')
        for name, v in results.get('data_files', {}).items():
            if not v.get('ok'): failures.append(f'data:{name}')
        ps = results.get('patrons_schema', {})
        if not ps.get('validated'): failures.append('schema:patrons')
        chars = results.get('characters', {})
        if chars.get('issues') or chars.get('linkage_issues'): failures.append('characters')
        if failures or 'fatal' in results:
            sys.exit(1)
    sys.exit(0)

if __name__ == '__main__':
    main()
