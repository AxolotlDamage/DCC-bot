import random, re, json, os
from typing import Tuple, List, Iterable
from utils.dice import roll_dice

# Ability score rolling

def roll_ability() -> int:
    return sum(random.randint(1, 6) for _ in range(3))

def get_modifier(score: int) -> int:
    if score <= 3: return -3
    if score <= 5: return -2
    if score <= 8: return -1
    if score <= 12: return 0
    if score <= 15: return 1
    if score <= 17: return 2
    return 3

# ---- Spellcasting ability gates and adjustments ----

def wizard_spells_known_adjustment(int_score: int) -> int:
    """Return the Wizard Spells Known adjustment from the INT score table.
    Table (Minimum of 1 spell when applicable):
    3: No spellcasting (treat as 0 here; gating is handled by max level)
    4-5: -2 spells
    6-7: -1 spell
    8-13: No adjustment (0)
    14-16: +1 spell
    17-18: +2 spells
    """
    try:
        s = int(int_score)
    except Exception:
        s = 0
    if s <= 0:
        return 0
    if s == 3:
        return 0
    if 4 <= s <= 5:
        return -2
    if 6 <= s <= 7:
        return -1
    if 8 <= s <= 13:
        return 0
    if 14 <= s <= 16:
        return 1
    if 17 <= s:
        return 2
    return 0

def max_spell_level_from_score(score: int) -> int:
    """Return maximum spell level allowed based on ability score.
    Mapping based on provided table (INT for wizards, PER for clerics):
    3: 0 (No spellcasting possible)
    4-7: 1
    8-9: 2
    10-11: 3
    12-14: 4
    15-17: 5
    18+: 6
    """
    try:
        s = int(score)
    except Exception:
        s = 0
    if s <= 3:
        return 0
    if 4 <= s <= 7:
        return 1
    if 8 <= s <= 9:
        return 2
    if 10 <= s <= 11:
        return 3
    if 12 <= s <= 14:
        return 4
    if 15 <= s <= 17:
        return 5
    return 6

# Centralized ability labels/emojis to avoid drift
ABILITY_ORDER: List[str] = ["STR","AGI","STA","INT","PER","LCK"]
ABILITY_INFO = {
    "STR": {"name": "Strength", "emoji": "💪", "short": "str"},
    "AGI": {"name": "Agility", "emoji": "🤸", "short": "agi"},
    "STA": {"name": "Stamina", "emoji": "🛡️", "short": "sta"},
    "INT": {"name": "Intelligence", "emoji": "🧠", "short": "int"},
    "PER": {"name": "Personality", "emoji": "🗣️", "short": "per"},
    "LCK": {"name": "Luck", "emoji": "🍀", "short": "lck"},
}

def ability_name(code: str) -> str:
    try:
        return str(ABILITY_INFO.get(code, {}).get('name') or code.title())
    except Exception:
        return code.title()

def ability_emoji(code: str) -> str:
    try:
        return str(ABILITY_INFO.get(code, {}).get('emoji') or "")
    except Exception:
        return ""

## roll_dice moved to utils.dice; imported above

# Luck helpers (simplified extraction)

def _parse_luck_value(val):
    try:
        if val is None: return 0
        if isinstance(val, int): return val
        s = str(val).strip()
        if '/' in s:
            parts = s.split('/')
            return int(parts[0].strip())
        return int(s)
    except Exception:
        return 0

def get_luck_current(char: dict) -> int:
    luck = char.get('luck')
    explicit = 0
    if isinstance(luck, dict):
        explicit = _parse_luck_value(luck.get('current', luck.get('max', 0)))
    elif luck is not None:
        explicit = _parse_luck_value(luck)
    lck = char.get('abilities', {}).get('LCK', {})
    lck_val = 0
    if isinstance(lck, dict):
        for key in ('current','max','score'):
            if key in lck:
                v = _parse_luck_value(lck.get(key))
                if v:
                    lck_val = v; break
        if lck_val == 0:
            try: lck_val = int(lck.get('mod', 0))
            except Exception: lck_val = 0
    if explicit and int(explicit) > 0:
        return int(explicit)
    return int(lck_val)

def consume_luck_and_save(char: dict, pts: int, filename: str=None) -> int:
    try:
        cur = get_luck_current(char)
        use = min(cur, max(0, int(pts)))
        if use <= 0: return 0
        existing_luck = char.get('luck')
        prev_max = None
        if isinstance(existing_luck, dict):
            prev_max = _parse_luck_value(existing_luck.get('max', existing_luck.get('current', 0)))
        elif existing_luck is not None:
            prev_max = _parse_luck_value(existing_luck)
        if not isinstance(char.get('luck'), dict):
            inferred_max = prev_max if prev_max and int(prev_max) > 0 else _parse_luck_value(char.get('abilities', {}).get('LCK', {}).get('max', char.get('abilities', {}).get('LCK', {}).get('current', cur)))
            if not inferred_max or int(inferred_max) <= 0: inferred_max = int(cur)
            char['luck'] = {'current': cur, 'max': int(inferred_max)}
        try:
            if int(char['luck'].get('max', 0)) <= 0:
                fallback = prev_max or _parse_luck_value(char.get('abilities', {}).get('LCK', {}).get('max', char.get('abilities', {}).get('LCK', {}).get('current', cur)))
                char['luck']['max'] = int(fallback or cur)
        except Exception:
            char['luck']['max'] = int(cur)
        char['luck']['current'] = max(0, int(cur - use))
        try:
            char.setdefault('abilities', {})
            lck = char['abilities'].setdefault('LCK', {})
            lck['current'] = int(char['luck']['current'])
            lck['max'] = int(lck.get('max', char['luck'].get('max', lck.get('current', 0))))
            try:
                from .utils import get_modifier  # self import guard (will work after first load)
            except Exception:
                pass
            try:
                from math import floor
                lck['mod'] = int(get_modifier(int(lck.get('current', 0))))
            except Exception:
                lck['mod'] = int(lck.get('mod', 0))
        except Exception:
            pass
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(char, f, indent=2)
            except Exception:
                pass
        return use
    except Exception:
        return 0

__all__ = [
    'roll_ability','get_modifier','roll_dice','get_luck_current','consume_luck_and_save','get_max_luck_mod',
    'get_equipped_weapons','is_dual_wielding','has_two_handed_equipped','effective_initiative_die',
    'dcc_dice_chain_step','is_weapon_trained','character_trained_weapons',
    'ABILITY_INFO','ABILITY_ORDER','ability_name','ability_emoji',
    'load_crit_tables','lookup_crit_entry',
    'load_fumble_tables','lookup_fumble_entry','load_conditions','tags_to_conditions',
    'load_attack_modifiers','compute_attack_roll_adjustments',
    'double_damage_dice_expr','resolve_crit_damage_bonus','has_weapon_equipped','has_shield_equipped',
    'select_crit_table_for_character','roll_multiple_dice_expr','get_global_roll_penalty',
    'apply_targeted_effects_from_tags','apply_targeted_effects_from_entry',
    'get_hp_current','set_hp_current','has_helm_equipped'
]

# --- Weapon helpers (forward-compatible with dual-wield) ---

def _normalize_weapon_key(w) -> str:
    try:
        if isinstance(w, dict):
            w = w.get('name') or w.get('key') or ''
        return str(w).strip().lower()
    except Exception:
        return ''

def get_equipped_weapons(char: dict) -> List[str]:
    """Return a list of equipped weapon keys (lowercased).
    Supports current shape (single string in `weapon`) and future `weapons: [primary, offhand]`.
    """
    try:
        ws = char.get('weapons')
        if isinstance(ws, list) and ws:
            return [k for k in (_normalize_weapon_key(w) for w in ws) if k]
        w = char.get('weapon')
        key = _normalize_weapon_key(w)
        return [key] if key else []
    except Exception:
        return []

def is_dual_wielding(char: dict) -> bool:
    try:
        ws = char.get('weapons')
        if isinstance(ws, list) and len([w for w in ws if _normalize_weapon_key(w)]) >= 2:
            return True
        return bool(char.get('dual_wield'))
    except Exception:
        return False

def has_two_handed_equipped(char: dict) -> bool:
    """True if any equipped weapon is marked two_handed in WEAPON_TABLE."""
    try:
        keys = get_equipped_weapons(char)
        if not keys:
            return False
        # Local import to avoid import cycles at module load time
        from modules.data_constants import WEAPON_TABLE  # type: ignore
        for k in keys:
            wd = WEAPON_TABLE.get(k)
            if isinstance(wd, dict) and wd.get('two_handed'):
                return True
        # If not found in table, try to match equipped key against inventory custom item and check tags
        inv = (char or {}).get('inventory') or []
        if isinstance(inv, list):
            kl = [str(x).strip().lower() for x in keys]
            for it in inv:
                try:
                    if not isinstance(it, dict):
                        continue
                    nm = str(it.get('name') or it.get('item') or '').strip().lower()
                    if not nm or nm not in kl:
                        continue
                    tags = it.get('tags') or it.get('tag') or []
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(',')]
                    tl = [str(t).strip().lower() for t in (tags or [])]
                    # Common encodings for two-handed flags
                    if any(t in tl for t in ('two-handed','two_handed','twohanded','2h')):
                        return True
                except Exception:
                    continue
        return False
    except Exception:
        return False

def effective_initiative_die(char: dict) -> int:
    """Return 16 if a two-handed weapon is equipped, else 20.
    Future rules can refine this; current behavior preserves legacy logic.
    """
    try:
        return 16 if has_two_handed_equipped(char) else 20
    except Exception:
        return 20

# --- Weapon training & dice chain helpers ---

_DCC_CHAIN = [3,4,5,6,7,8,10,12,14,16,20,24,30]

def dcc_dice_chain_step(die_expr: str, steps: int = -1) -> str:
    """Shift a single-die expression like '1d20' up/down the DCC dice chain.
    Only handles a single NdX term; returns original if unparsable.
    Example: step -1 on 1d20 -> 1d16; step +1 on 1d16 -> 1d20.
    """
    try:
        s = str(die_expr or '').strip().lower()
        if 'd' not in s or '+' in s or ',' in s:
            return die_expr
        parts = s.split('d', 1)
        n = int(parts[0] or '1')
        sides = int(parts[1])
        if sides not in _DCC_CHAIN:
            return die_expr
        if steps == 0:
            return f"{n}d{sides}"
        idx = _DCC_CHAIN.index(sides)
        new_idx = max(0, min(len(_DCC_CHAIN)-1, idx + int(steps)))
        return f"{n}d{_DCC_CHAIN[new_idx]}"
    except Exception:
        return die_expr

def character_trained_weapons(char: dict) -> set:
    """Return the set of weapon keys this character is trained with.
    Unions multiple sources:
    - Class defaults from CLASS_WEAPON_TRAINING by char['class'] (lowercased)
    - Per-character field 'weapon_training' (preferred, list[str])
    - Legacy field 'weapon_proficiencies' (list[str])
    - Special case: Lv0 are trained with their starting weapon (char['weapon'])
    """
    trained: set = set()
    try:
        from modules.data_constants import CLASS_WEAPON_TRAINING
    except Exception:
        CLASS_WEAPON_TRAINING = {}

    # Class defaults
    try:
        cls = str(char.get('class', '') or '').strip().lower()
        trained |= set(CLASS_WEAPON_TRAINING.get(cls, set()))
    except Exception:
        pass

    # Per-character training (new field)
    try:
        for w in (char.get('weapon_training') or []):
            if isinstance(w, str) and w.strip():
                trained.add(w.strip().lower())
    except Exception:
        pass

    # Legacy field support
    try:
        for w in (char.get('weapon_proficiencies') or []):
            if isinstance(w, str) and w.strip():
                trained.add(w.strip().lower())
    except Exception:
        pass

    # Lv0 starting weapon is always trained
    try:
        if str(char.get('class', '') or '').strip().lower() in ('lv0', '0', 'level 0', 'level-0'):
            w0 = str(char.get('weapon') or '').strip().lower()
            if w0:
                trained.add(w0)
    except Exception:
        pass

    return trained

def is_weapon_trained(char: dict, weapon_key: str) -> bool:
    try:
        key = str(weapon_key or '').strip().lower()
        if not key:
            return True
        return key in character_trained_weapons(char)
    except Exception:
        return True

# --- Simple dice helpers ---

def _parse_ndx(expr: str):
    try:
        s = str(expr or '').strip().lower()
        if 'd' not in s or '+' in s or '-' in s:
            return None
        n_s, x_s = s.split('d', 1)
        n = int(n_s) if n_s else 1
        x = int(x_s)
        return (n, x)
    except Exception:
        return None

def double_damage_dice_expr(expr: str) -> str:
    """Double the number of dice in a single NdX expression (no modifiers).
    Examples: 1d12 -> 2d12; 2d6 -> 4d6. If unparsable or compound, return original.
    """
    try:
        px = _parse_ndx(expr)
        if not px:
            return expr
        n, x = px
        return f"{max(1, n*2)}d{x}"
    except Exception:
        return expr

# --- Crit tables loader ---

_CRIT_TABLES_CACHE = None
_FUMBLE_TABLES_CACHE = None
_FUMBLE_TABLES_MTIME = None
_CONDITIONS_CACHE = None
_ATTACK_MODIFIERS_CACHE = None

def load_crit_tables(path: str = None) -> dict:
    """Load crit tables JSON once and cache.
    Path defaults to data/crit_tables.json adjacent to repo root.
    """
    global _CRIT_TABLES_CACHE
    if _CRIT_TABLES_CACHE is not None:
        return _CRIT_TABLES_CACHE
    try:
        # Try repo-relative data path
        base = os.path.dirname(os.path.dirname(__file__))  # modules/ -> repo root
        p = path or os.path.join(base, 'data', 'crit_tables.json')
        with open(p, 'r', encoding='utf-8') as f:
            _CRIT_TABLES_CACHE = json.load(f)
    except Exception:
        _CRIT_TABLES_CACHE = {"version": 1, "tables": {}}
    return _CRIT_TABLES_CACHE

def load_fumble_tables(path: str = None) -> dict:
    """Load fumble tables JSON once and cache.
    Path defaults to data/fumble_tables.json adjacent to repo root.
    """
    global _FUMBLE_TABLES_CACHE, _FUMBLE_TABLES_MTIME
    try:
        base = os.path.dirname(os.path.dirname(__file__))
        p = path or os.path.join(base, 'data', 'fumble_tables.json')
        # If cached and file unchanged, return cache
        try:
            mtime = os.path.getmtime(p)
        except Exception:
            mtime = None
        if _FUMBLE_TABLES_CACHE is not None and _FUMBLE_TABLES_MTIME is not None and mtime is not None and mtime == _FUMBLE_TABLES_MTIME:
            return _FUMBLE_TABLES_CACHE
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Normalize alternate schema where tables are top-level keys (e.g., {"FUMBLES": {...}})
        if isinstance(data, dict) and 'tables' not in data:
            tables = {}
            for k, v in list(data.items()):
                if isinstance(v, dict) and ('entries' in v):
                    tables[k] = v
            if tables:
                data = {'version': int(data.get('version', 1)) if isinstance(data.get('version', 1), (int, float, str)) else 1,
                        'tables': tables}
        _FUMBLE_TABLES_CACHE = data
        _FUMBLE_TABLES_MTIME = mtime
    except Exception:
        _FUMBLE_TABLES_CACHE = {"version": 1, "tables": {}}
    return _FUMBLE_TABLES_CACHE

def load_conditions(path: str = None) -> dict:
    """Load conditions registry from data/conditions.json and cache."""
    global _CONDITIONS_CACHE
    if _CONDITIONS_CACHE is not None:
        return _CONDITIONS_CACHE
    try:
        base = os.path.dirname(os.path.dirname(__file__))
        p = path or os.path.join(base, 'data', 'conditions.json')
        with open(p, 'r', encoding='utf-8') as f:
            _CONDITIONS_CACHE = json.load(f)
    except Exception:
        _CONDITIONS_CACHE = {"version": 1, "conditions": {}}
    return _CONDITIONS_CACHE

def load_attack_modifiers(path: str = None) -> dict:
    """Load Table 4-1 attack modifiers from data/attack_modifiers.json."""
    global _ATTACK_MODIFIERS_CACHE
    if _ATTACK_MODIFIERS_CACHE is not None:
        return _ATTACK_MODIFIERS_CACHE
    try:
        base = os.path.dirname(os.path.dirname(__file__))
        p = path or os.path.join(base, 'data', 'attack_modifiers.json')
        with open(p, 'r', encoding='utf-8') as f:
            _ATTACK_MODIFIERS_CACHE = json.load(f)
    except Exception:
        _ATTACK_MODIFIERS_CACHE = {"version": 1, "modifiers": {}}
    return _ATTACK_MODIFIERS_CACHE

def compute_attack_roll_adjustments(kind: str, factors: dict) -> dict:
    """Compute die chain steps and flat bonuses for an attack.
    kind: 'melee' or 'missile'
    factors: {
      'range': 'short'|'medium'|'long' (missile only),
      'attacker': {... booleans ...},
      'defender': {... booleans ...}
    }
    Returns: {'die_expr': '1d20' -> stepped, 'bonus': int, 'notes': [..]}
    """
    data = load_attack_modifiers().get('modifiers', {})
    die_expr = '1d20'
    steps = 0
    bonus = 0
    notes = []
    k = kind.strip().lower()
    # Range
    r = (factors or {}).get('range')
    if k == 'missile' and r:
        rng = data.get('range', {}).get(str(r).lower(), {})
        steps += int(rng.get('missile_die_steps', 0))
        bonus += int(rng.get('missile_bonus', 0))
        if rng:
            notes.append(f"range:{r}")
    # Attacker
    att = (factors or {}).get('attacker', {}) or {}
    for key, on in att.items():
        if not on:
            continue
        src = data.get('attacker', {}).get(key, {})
        if k == 'melee':
            steps += int(src.get('melee_die_steps', 0))
            bonus += int(src.get('melee_bonus', 0))
        else:
            steps += int(src.get('missile_die_steps', 0))
            bonus += int(src.get('missile_bonus', 0))
        if src:
            notes.append(f"attacker:{key}")
    # Defender
    dfn = (factors or {}).get('defender', {}) or {}
    for key, on in dfn.items():
        if not on:
            continue
        src = data.get('defender', {}).get(key, {})
        if k == 'melee':
            steps += int(src.get('melee_die_steps', 0))
            bonus += int(src.get('melee_bonus', 0))
        else:
            steps += int(src.get('missile_die_steps', 0))
            bonus += int(src.get('missile_bonus', 0))
        if src:
            notes.append(f"defender:{key}")
    die = dcc_dice_chain_step(die_expr, steps)
    return {"die_expr": die or die_expr, "bonus": int(bonus), "notes": notes}

# --- Equipment presence helpers for conditionals ---

def has_weapon_equipped(char: dict) -> bool:
    """Rough check if target currently has a weapon equipped/available.
    Prefers explicit char['weapon']; falls back to any inventory item that is a known weapon.
    """
    try:
        w = str((char or {}).get('weapon') or '').strip().lower()
        if w:
            return True
        inv = (char or {}).get('inventory') or []
        if not isinstance(inv, list):
            return False
        try:
            from modules.data_constants import WEAPON_TABLE  # type: ignore
        except Exception:
            WEAPON_TABLE = {}
        for it in inv:
            try:
                nm = str(it.get('name') or it.get('item') or '').strip().lower()
                if nm and nm in WEAPON_TABLE and int(it.get('qty', 1) or 1) > 0:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False

def get_max_luck_mod(char: dict) -> int:
    """Return the Luck modifier derived from maximum Luck (static augury basis).
    Prefers persisted char['max_luck_mod'] if present; otherwise computes from the
    character's maximum Luck score found in either char['luck']['max'] or abilities.LCK.
    """
    try:
        # Prefer explicit cached value if available
        try:
            mlm = int(char.get('max_luck_mod', 0) or 0)
        except Exception:
            mlm = 0
        if mlm:
            return int(mlm)
        # Fallback: compute from maximum Luck score across known fields
        max_luck_score = 0
        luck = char.get('luck') or {}
        if isinstance(luck, dict):
            try:
                max_luck_score = int(luck.get('max') or 0)
            except Exception:
                max_luck_score = 0
        if not max_luck_score:
            lck_blk = (char.get('abilities') or {}).get('LCK') or {}
            try:
                max_luck_score = int(lck_blk.get('max') or lck_blk.get('current') or lck_blk.get('score') or 0)
            except Exception:
                max_luck_score = 0
        return int(get_modifier(int(max_luck_score))) if max_luck_score else 0
    except Exception:
        return 0

def has_shield_equipped(char: dict) -> bool:
    """Best-effort check for shield presence. Assumes inventory item named 'shield'."""
    try:
        inv = (char or {}).get('inventory') or []
        if not isinstance(inv, list):
            return False
        for it in inv:
            try:
                nm = str(it.get('name') or it.get('item') or '').strip().lower()
                if nm == 'shield' and int(it.get('qty', 1) or 1) > 0:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False

# --- Conditional crit helpers ---

def get_global_roll_penalty(char: dict) -> tuple[int, list[str]]:
    """Return a sum of global roll penalties from timed conditions.
    Currently supports a single 'groggy' condition with payload {'expires': epoch_seconds}.
    If not expired, applies -4 to all d20-based rolls (attack/check/save/skills).
    Returns (penalty, notes_list) for display.
    """
    try:
        notes = (char or {}).get('notes') or {}
        conds = notes.get('conditions') or []
        pen = 0
        labels: list[str] = []
        if isinstance(conds, list):
            import time
            now = int(time.time())
            for c in conds:
                try:
                    key = str(c.get('key') or '').strip().lower()
                    if key != 'groggy':
                        continue
                    payload = c.get('payload') or {}
                    exp = None
                    if isinstance(payload, dict):
                        try:
                            exp = int(payload.get('expires')) if payload.get('expires') is not None else None
                        except Exception:
                            exp = None
                    if (exp is None) or (now <= int(exp)):
                        pen -= 4
                        labels.append('groggy -4')
                except Exception:
                    continue
        return (int(pen), labels)
    except Exception:
        return (0, [])

def resolve_crit_damage_bonus(entry: dict, attacker: dict | None = None, defender: dict | None = None, context: dict | None = None) -> dict:
    """Evaluate conditional damage/save fields on a crit entry.
    Returns: { 'dice': '+NdX'|None, 'save': {...}|None, 'notes': [..] }

    Recognized fields:
    - damage_bonus: '+NdX' or '+NdX_if_no_weapon'
    - damage_bonus_on_second_hit: '+NdX' (applies only if context.second_attack_hit)
    - save_if_no_shield: save dict (applies only if defender has no shield)
    """
    notes: list[str] = []
    dice: str | None = None
    save: dict | None = None
    ctx = context or {}
    # Direct damage bonus
    db = str(entry.get('damage_bonus') or '').strip()
    if db:
        if db.endswith('_if_no_weapon'):
            base = db.split('_if_no_weapon', 1)[0]
            if defender is not None and not has_weapon_equipped(defender):
                dice = base
                notes.append('no_weapon:bonus_applied')
            else:
                notes.append('no_weapon:condition_not_met')
        else:
            dice = db
    # Second-hit only bonus
    db2 = str(entry.get('damage_bonus_on_second_hit') or '').strip()
    if db2:
        if ctx.get('second_attack_hit'):
            # stack additional dice
            dice = f"{(dice + ',' if dice else '')}{db2}"
            notes.append('second_hit:bonus_applied')
        else:
            notes.append('second_hit:condition_not_met')
    # Save if no shield
    sifns = entry.get('save_if_no_shield')
    if isinstance(sifns, dict):
        if defender is not None and not has_shield_equipped(defender):
            save = sifns
            notes.append('no_shield:save_applies')
        else:
            notes.append('no_shield:condition_not_met')
    return {'dice': dice, 'save': save, 'notes': notes}

# --- Crit table selection & dice rolling helpers ---

def select_crit_table_for_character(char: dict) -> str:
    """Choose a CRIT table key based on class/level, using project descriptions.
    Mapping assumptions:
    - Level 0 or Wizard -> CRIT_I
    - Thief or Elf -> CRIT_II
    - Cleric, Halfling, Warrior L1-2, Dwarf L1-3 -> CRIT_III
    - Warrior L3-4, Dwarf L4-5 -> CRIT_IV
    - Warrior L5+, Dwarf L6+ -> CRIT_V
    """
    try:
        cls = str((char or {}).get('class') or (char or {}).get('char_class') or '').strip().lower()
        lvl = 0
        try:
            lvl = int((char or {}).get('level', 0) or 0)
        except Exception:
            lvl = 0
        if cls in ('lv0','0','level 0','level-0') or cls == 'wizard':
            return 'CRIT_I'
        if cls in ('thief','elf'):
            return 'CRIT_II'
        if cls == 'cleric' or cls == 'halfling':
            return 'CRIT_III'
        if cls == 'dwarf':
            if lvl >= 6:
                return 'CRIT_V'
            if lvl >= 4:
                return 'CRIT_IV'
            return 'CRIT_III'
        if cls == 'warrior':
            if lvl >= 5:
                return 'CRIT_V'
            if lvl >= 3:
                return 'CRIT_IV'
            return 'CRIT_III'
        return 'CRIT_I'
    except Exception:
        return 'CRIT_I'

def roll_multiple_dice_expr(dice_expr: str) -> tuple[int, list]:
    """Roll a comma-separated list of NdX expressions and sum.
    Returns (total, rolls) where rolls is a list of per-expression totals.
    """
    total = 0
    parts_rolls = []
    try:
        parts = [p.strip() for p in str(dice_expr or '').split(',') if p.strip()]
        for p in parts:
            q = p.lstrip('+').strip()
            t, rl = roll_dice(q)
            total += int(t)
            parts_rolls.append(int(t))
    except Exception:
        pass
    return total, parts_rolls

# --- HP and targeted effects helpers ---

def get_hp_current(char: dict) -> int:
    try:
        hp = char.get('hp')
        if isinstance(hp, dict):
            return int(hp.get('current', hp.get('max', 0)) or 0)
        if isinstance(hp, int):
            return hp
        return 0
    except Exception:
        return 0

def set_hp_current(char: dict, value: int) -> None:
    try:
        v = int(value)
    except Exception:
        v = 0
    hp = char.get('hp')
    if not isinstance(hp, dict):
        char['hp'] = {'current': v, 'max': max(v, get_hp_current(char))}
    else:
        hp['current'] = v

def has_helm_equipped(char: dict) -> bool:
    try:
        inv = (char or {}).get('inventory') or []
        if not isinstance(inv, list):
            return False
        for it in inv:
            try:
                nm = str(it.get('name') or it.get('item') or '').strip().lower()
                if nm in ('helm','helmet') and int(it.get('qty', 1) or 1) > 0:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False

def _adjust_ability(char: dict, code: str, delta: int) -> None:
    try:
        code = code.upper()
        abil = char.setdefault('abilities', {})
        blk = abil.setdefault(code, {})
        cur = 0
        mx = 0
        try:
            cur = int(blk.get('current', blk.get('score', 0)) or 0)
        except Exception:
            cur = 0
        try:
            mx = int(blk.get('max', cur) or cur)
        except Exception:
            mx = cur
        new_cur = max(0, cur + int(delta))
        new_max = max(0, mx + int(delta))
        blk['current'] = new_cur
        blk['max'] = max(new_max, new_cur)
        try:
            blk['mod'] = get_modifier(int(new_cur))
        except Exception:
            pass
    except Exception:
        pass

def apply_targeted_effects_from_tags(char: dict, tags: list) -> list[str]:
    """Apply concrete, safe effects to a character from known tags.
    Returns list of human-readable change summaries.
    """
    results: list[str] = []
    tl = [str(t).lower() for t in (tags or [])]
    # Instant death
    if 'instant_death' in tl:
        set_hp_current(char, 0)
        results.append('HP set to 0 (instant death)')
    # Permanent INT loss 1d12
    if 'perm_int_loss_1d12' in tl:
        dmg, _ = roll_dice('1d12')
        _adjust_ability(char, 'INT', -int(dmg))
        results.append(f'INT permanently reduced by {int(dmg)}')
    # Others are represented as conditions already (e.g., blinded_until_healed, deaf, etc.)
    return results

def apply_targeted_effects_from_entry(char: dict, entry: dict, context: dict | None = None) -> list[str]:
    """Apply effects detectable from entry content (tags and key phrases), e.g., 50% HP loss.
    Returns list of change summaries.
    """
    changes: list[str] = []
    try:
        # 50% current HP loss phrases
        eff = str(entry.get('effect') or '')
        if 'loses 50% of his remaining hit points' in eff.lower() or 'loses 50% of current hit points' in eff.lower():
            cur = get_hp_current(char)
            lose = cur // 2
            set_hp_current(char, max(0, cur - lose))
            changes.append(f'Lost 50% current HP ({lose})')
        # Permanent INT loss if no helm
        tags = [str(t).lower() for t in (entry.get('tags') or [])]
        if 'perm_int_loss_1d4_if_no_helm' in tags:
            if not has_helm_equipped(char):
                dmg, _ = roll_dice('1d4')
                _adjust_ability(char, 'INT', -int(dmg))
                changes.append(f'INT permanently reduced by {int(dmg)} (no helm)')
        # Merge other tag-driven targeted effects
        changes += apply_targeted_effects_from_tags(char, tags)
    except Exception:
        pass
    return changes

def _ensure_note_conditions(char: dict) -> list:
    notes = char.setdefault('notes', {}) if isinstance(char, dict) else {}
    conds = notes.setdefault('conditions', [])
    if not isinstance(conds, list):
        conds = []
        notes['conditions'] = conds
    return conds

def apply_condition(char: dict, key: str, payload: dict = None) -> None:
    """Attach a condition entry to a character's notes.conditions, de-duping by key.
    payload may include fields like duration, value, note.
    """
    try:
        key = str(key or '').strip()
        if not key:
            return
        conds = _ensure_note_conditions(char)
        # if already present, update payload
        for c in conds:
            if c.get('key') == key:
                if payload:
                    c.update({'payload': {**c.get('payload', {}), **payload}})
                return
        conds.append({'key': key, 'payload': payload or {}})
    except Exception:
        pass

def remove_condition(char: dict, key: str) -> None:
    try:
        conds = _ensure_note_conditions(char)
        notes = char.get('notes', {})
        notes['conditions'] = [c for c in conds if c.get('key') != key]
    except Exception:
        pass

def has_condition(char: dict, key: str) -> bool:
    try:
        for c in _ensure_note_conditions(char):
            if c.get('key') == key:
                return True
    except Exception:
        return False
    return False

def tags_to_conditions(tags: list) -> list:
    """Translate result tags to condition keys with optional payloads."""
    out = []
    tl = [str(t).lower() for t in (tags or [])]
    def add(k, **payload):
        out.append({'key': k, 'payload': {**payload} if payload else {}})
    if 'prone' in tl or 'prone_this_round' in tl or 'prone_next_round' in tl:
        add('prone')
    if any(t.startswith('blinded') for t in tl) or 'blind' in tl:
        add('blinded')
    if any(t.startswith('immobilized') for t in tl) or 'cannot_attack_1d3_rounds' in tl:
        add('immobilized')
    if 'disarmed' in tl:
        add('disarmed')
    if 'entangled' in tl:
        add('entangled')
    for t in tl:
        if t.startswith('attack_-'):
            try:
                val = int(t.split('attack_-',1)[1].split('_',1)[0])
                add('attack_penalty_next', value=val)
            except Exception:
                pass
        if t.startswith('ac_-'):
            try:
                val = int(t.split('ac_-',1)[1].split('_',1)[0])
                add('ac_penalty', value=val)
            except Exception:
                pass
    return out

def _match_roll_key(key: str, roll: int) -> bool:
    key = str(key).strip()
    if key.endswith('+'):
        try:
            val = int(key[:-1])
            return roll >= val
        except Exception:
            return False
    if '-' in key:
        lo, hi = key.split('-', 1)
        lo = lo.strip()
        hi = hi.strip()
        try:
            if lo == '0' and hi == '':
                return roll <= 0
            lo_v = int(lo) if lo else None
            hi_v = int(hi) if hi else None
            if lo_v is None and hi_v is not None:
                return roll <= hi_v
            if lo_v is not None and hi_v is None:
                return roll >= lo_v
            return lo_v <= roll <= hi_v
        except Exception:
            return False
    try:
        return int(key) == int(roll)
    except Exception:
        return False

def lookup_crit_entry(table: str, roll: int) -> dict:
    """Return the crit entry dict for a given table name and roll.
    Table is the key under data. Supports exact numbers, ranges like '2-3', and '20+'.
    Includes a special key '0-' to mean '0 or less'.
    """
    data = load_crit_tables()
    t = data.get('tables', {}).get(table)
    if not t:
        return {}
    entries = t.get('entries', {})
    # Prefer exact match first
    if str(roll) in entries:
        return entries.get(str(roll), {})
    # Then ranges/suffixes
    for k, v in entries.items():
        if k == '0-' and roll <= 0:
            return v
        if _match_roll_key(k, roll):
            return v
    return {}

def lookup_fumble_entry(table: str, roll: int) -> dict:
    """Return the fumble entry dict for a given table name and roll.
    Supports exact numbers, ranges like '2-3', and suffixes like '16+' and '0-'.
    """
    data = load_fumble_tables()
    t = data.get('tables', {}).get(table)
    if not t:
        return {}
    entries = t.get('entries', {})
    if str(roll) in entries:
        return entries.get(str(roll), {})
    for k, v in entries.items():
        if k == '0-' and roll <= 0:
            return v
        if _match_roll_key(k, roll):
            return v
    return {}
