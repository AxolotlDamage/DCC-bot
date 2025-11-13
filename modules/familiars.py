import os, json, random
from typing import Dict, Any, Tuple

from utils.dice import roll_dice

FAMILIARS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'familiars.json')

def _load_config() -> Dict[str, Any]:
    try:
        with open(FAMILIARS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _hp_from_formula(expr: str) -> int:
    """Parse simple NdS+K style like '1d4+2'."""
    expr = str(expr or '').strip()
    if '+' in expr:
        dice_part, flat_part = expr.split('+', 1)
        total, _ = roll_dice(dice_part)
        try:
            flat = int(flat_part)
        except Exception:
            flat = 0
        return int(total) + int(flat)
    else:
        total, _ = roll_dice(expr)
        return int(total)

def resolve_familiar_type(cfg: Dict[str, Any], alignment: str, spell_check: int) -> str:
    alignment = (alignment or '').title()
    bands = []
    for entry in cfg.get('result_bands', []):
        if str(entry.get('alignment')).title() == alignment:
            bands = entry.get('bands', [])
            break
    for band in bands:
        try:
            lo = int(band.get('min'))
            hi = int(band.get('max'))
            if lo <= spell_check <= hi:
                return str(band.get('familiar_type'))
        except Exception:
            continue
    # fallback to first band or guardian
    return str(bands[0].get('familiar_type')) if bands else 'guardian'

def roll_physical_trait(cfg: Dict[str, Any], alignment: str) -> Tuple[str, str, str]:
    rows = cfg.get('physical_table', [])
    if not rows:
        return ('N/A', 'N/A', 'No physical table')
    r, _ = roll_dice('1d14')
    row = next((rw for rw in rows if int(rw.get('roll', -1)) == r), None)
    if not row:
        row = random.choice(rows)
    key = alignment.lower()
    side = row.get(key) or {}
    return (str(r), str(side.get('creature', 'Unknown')), str(side.get('benefit_raw', '')))

def roll_personality(cfg: Dict[str, Any]) -> Tuple[str, str]:
    rows = cfg.get('personalities', [])
    if not rows:
        return ('N/A', 'No personalities table')
    r, _ = roll_dice('1d20')
    row = next((rw for rw in rows if int(rw.get('roll', -1)) == r), None)
    if not row:
        row = random.choice(rows)
    return (str(r), str(row.get('personality', 'Unknown')))

def generate_familiar_record(wizard: Dict[str, Any], spell_check: int) -> Dict[str, Any]:
    cfg = _load_config()
    alignment = (wizard.get('alignment') or 'Neutral').title()
    fam_type = resolve_familiar_type(cfg, alignment, int(spell_check))
    base = cfg.get('base_traits', {})
    type_meta = (cfg.get('familiar_types', {}) or {}).get(fam_type, {})
    # Roll HP: base + extra if present
    hp_total = _hp_from_formula(base.get('hit_points_formula', '1d4'))
    extra_hp_formula = type_meta.get('extra_hp_formula')
    if extra_hp_formula:
        hp_total += _hp_from_formula(extra_hp_formula)
    ac_base = int(base.get('ac', 10) or 10)
    ac_bonus = int(type_meta.get('ac_bonus', 0) or 0)
    ac_total = ac_base + ac_bonus
    # Attack bonus
    atk_bonus = int(type_meta.get('attack_bonus', 0) or 0)
    if bool(type_meta.get('scales_with_caster_attack_bonus')):
        try:
            wiz_ab = int(str(wizard.get('attack_bonus', '0')).replace('+',''))
            atk_bonus += wiz_ab
        except Exception:
            pass
    # Physical + personality rolls
    phys_roll, phys_creature, phys_benefit = roll_physical_trait(cfg, alignment.lower())
    pers_roll, personality = roll_personality(cfg)
    # No ability scores included per user request; preserve only intelligence as metadata
    int_score = int(base.get('intelligence', 5) or 5)
    notes = {
        'familiar': {
            'type': fam_type,
            'alignment': alignment,
            'spell_check': spell_check,
            'creature_form': phys_creature,
            'creature_benefit': phys_benefit,
            'personality': personality,
            'personality_roll': pers_roll,
            'physical_roll': phys_roll,
            'base_traits_applied': True,
            'type_traits_applied': list(type_meta.keys()),
            'master': wizard.get('name'),
            'intelligence_score': int_score,
        }
    }
    # Include type metadata snapshot for reference
    notes['familiar']['type_meta'] = type_meta
    # Build record
    record = {
        'name': f"{wizard.get('name','Unknown')}'s Familiar",
        'owner': wizard.get('owner'),
        'level': 0,
        'class': 'familiar',
        'alignment': alignment,
        # 'abilities' intentionally omitted
        'luck': {'current': 0, 'max': 0},
        'hp': {'current': hp_total, 'max': hp_total},
        'ac': ac_total,
        'attack_bonus': f"+{atk_bonus}",
        'notes': notes,
        'schema_version': 1,
    }
    # Add attack damage if present
    dmg = type_meta.get('attack_damage')
    if dmg:
        record['attacks'] = [{'name': 'Natural', 'damage': str(dmg)}]
    return record

__all__ = [
    'generate_familiar_record',
    'resolve_familiar_type',
]
