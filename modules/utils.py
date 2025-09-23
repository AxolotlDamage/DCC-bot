import random, re, json, os
from typing import Tuple, List

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

def roll_dice(expr: str) -> Tuple[int, List[int]]:
    expr = str(expr).strip()
    m = re.match(r"^(\d*)d(\d+)$", expr, re.I)
    rolls: List[int] = []
    total = 0
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        s = int(m.group(2))
        for _ in range(n):
            r = random.randint(1, s)
            rolls.append(r)
            total += r
    else:
        try:
            total = int(expr)
            rolls = [total]
        except Exception:
            total = random.randint(1, 20)
            rolls = [total]
    return total, rolls

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
    'roll_ability','get_modifier','roll_dice','get_luck_current','consume_luck_and_save'
]
