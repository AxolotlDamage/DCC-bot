import random, re
from typing import List, Tuple, Optional

__all__ = ["roll_dice", "parse_dice_notation", "explode_dice"]

def parse_dice_notation(expr: str) -> Tuple[int, int]:
    expr = str(expr).strip()
    m = re.match(r"^(\d*)d(\d+)$", expr, re.I)
    if not m:
        raise ValueError(f"Unsupported dice expression: {expr}")
    n = int(m.group(1)) if m.group(1) else 1
    s = int(m.group(2))
    return n, s

def roll_dice(expr: str, force: Optional[int] = None) -> Tuple[int, List[int]]:
    expr = str(expr).strip()
    m = re.match(r"^(\d*)d(\d+)$", expr, re.I)
    rolls: List[int] = []
    total = 0
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        s = int(m.group(2))
        if force is not None:
            # Force per-die result (clamped to [1..s])
            r = max(1, min(int(force), s))
            rolls = [r for _ in range(n)]
            total = sum(rolls)
        else:
            for _ in range(n):
                r = random.randint(1, s)
                rolls.append(r)
                total += r
    else:
        if force is not None:
            total = int(force)
            rolls = [total]
        else:
            try:
                total = int(expr)
                rolls = [total]
            except Exception:
                total = random.randint(1, 20)
                rolls = [total]
    return total, rolls

def explode_dice(expr: str, explode_on: int, force: Optional[int] = None) -> Tuple[int, List[int]]:
    n, s = parse_dice_notation(expr)
    rolls: List[int] = []
    total = 0
    if force is not None:
        # Force per-die result (no explosions enacted under forced mode to avoid infinite loops)
        r = max(1, min(int(force), s))
        rolls = [r for _ in range(n)]
        total = sum(rolls)
    else:
        for _ in range(n):
            r = random.randint(1, s)
            rolls.append(r)
            total += r
            while r == explode_on:
                r = random.randint(1, s)
                rolls.append(r)
                total += r
    return total, rolls
