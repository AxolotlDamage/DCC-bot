import os, json
from core.config import SAVE_FOLDER  # type: ignore
from modules.utils import get_modifier  # type: ignore

"""
One-time migration helper:
- For characters with birth augur effect "Attack and damage rolls for 0-level starting weapon"
  created when Pack hunter was mistakenly applied as a global attack bonus at creation time,
  subtract max Luck mod from the stored 'attack' field if numeric.
- Skip records that use 'attack_bonus' (string like '+3' or int), as those are class-based.
- Idempotent: will not reduce below the ability+class baseline; only touches a plain numeric 'attack'.

Run:
  python scripts/fix_pack_hunter.py
"""


def _parse_int(val, default=0):
    try:
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip()
        return int(s)
    except Exception:
        return int(default)


def main():
    changed = 0
    for fn in os.listdir(SAVE_FOLDER):
        if not fn.lower().endswith('.json'):
            continue
        path = os.path.join(SAVE_FOLDER, fn)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        aug_eff = str((data.get('birth_augur') or {}).get('effect') or '').strip()
        if aug_eff != 'Attack and damage rolls for 0-level starting weapon':
            continue
        # If character uses attack_bonus (leveled class), don't touch it
        if 'attack_bonus' in data:
            continue
        atk = data.get('attack', None)
        if atk is None:
            continue
        try:
            max_luck_mod = int(data.get('max_luck_mod', 0) or 0)
        except Exception:
            max_luck_mod = 0
        if not max_luck_mod:
            # Fallback best-effort: compute from LCK max/current
            try:
                abil = (data.get('abilities') or {}).get('LCK') or {}
                lck_max = _parse_int(abil.get('max', abil.get('current', abil.get('score', 0))), 0)
                from modules.utils import get_modifier  # late import to avoid cycles
                max_luck_mod = int(get_modifier(lck_max))
            except Exception:
                max_luck_mod = 0
        if max_luck_mod == 0:
            continue
        try:
            atk_val = _parse_int(atk, None)
        except Exception:
            atk_val = None
        if atk_val is None:
            continue
        # Subtract once
        new_val = atk_val - max_luck_mod
        if new_val == atk_val:
            continue
        data['attack'] = int(new_val)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            changed += 1
        except Exception:
            continue
    print(f"Pack hunter migration complete. Updated {changed} character(s).")


if __name__ == '__main__':
    main()
