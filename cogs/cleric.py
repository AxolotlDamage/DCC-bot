import os
import json
from typing import Optional, Tuple, Any, List

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from utils.dice import roll_dice


class ClericCog(commands.Cog):
    """Cleric-specific actions (e.g., Turn Unholy)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- helpers ----
    def _root_dir(self) -> str:
        return os.path.dirname(os.path.dirname(__file__))

    async def _load_record(self, name: str) -> Optional[dict]:
        safe = name.strip().lower().replace(' ', '_')
        path = os.path.join(SAVE_FOLDER, f"{safe}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    async def _save_record(self, name: str, data: dict) -> bool:
        safe = (data.get('name') or name).strip().lower().replace(' ', '_')
        path = os.path.join(SAVE_FOLDER, f"{safe}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False

    def _ability_mod(self, data: dict, key: str) -> int:
        try:
            abl = data.get('abilities', {})
            v = abl.get(key)
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            # Fallback if numeric
            from modules.utils import get_modifier  # type: ignore
            return int(get_modifier(int(v)))
        except Exception:
            return 0

    def _load_spells_data(self) -> dict:
        try:
            path = os.path.join(self._root_dir(), 'Spells.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _parse_result_key(self, key: str) -> Tuple[Optional[int], Optional[int]]:
        s = str(key).strip()
        import re
        m = re.match(r"^(-?\d+)\s*or\s*lower$", s, re.I)
        if m:
            return None, int(m.group(1))
        m = re.match(r"^(-?\d+)\+$", s)
        if m:
            return int(m.group(1)), None
        m = re.match(r"^(-?\d+)\s*-\s*(-?\d+)$", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return (a, b) if a <= b else (b, a)
        try:
            n = int(s)
            return n, n
        except Exception:
            return None, None

    def _roll_disapproval(self, spells_data: dict) -> Tuple[int, str, str]:
        table_def = spells_data.get('Disapproval Table', {})
        tbl = {}
        if isinstance(table_def, dict):
            tbl = table_def.get('table', {}) or {}
        if not isinstance(tbl, dict) or not tbl:
            return (0, 'N/A', 'Disapproval table not found.')
        r, rolls = roll_dice('1d20')
        # match roll to first appropriate key
        chosen_key = None
        chosen_val: Any = None
        # sort by lower bound
        parsed: List[Tuple[Optional[int], Optional[int], str]] = []
        for k in tbl.keys():
            lo, hi = self._parse_result_key(k)
            parsed.append((lo, hi, k))
        def _key(x):
            lo, hi, _ = x
            return (lo if lo is not None else -10**9, hi if hi is not None else 10**9)
        parsed.sort(key=_key)
        for lo, hi, lab in parsed:
            ok = False
            if lo is None and hi is not None:
                ok = r <= hi
            elif lo is not None and hi is None:
                ok = r >= lo
            elif lo is not None and hi is not None:
                ok = lo <= r <= hi
            if ok:
                chosen_key = lab
                chosen_val = tbl.get(lab)
                break
        if chosen_key is None:
            chosen_key = str(r)
            chosen_val = tbl.get(chosen_key, 'No entry found.')
        text = chosen_val if isinstance(chosen_val, str) else (chosen_val.get('text') if isinstance(chosen_val, dict) else str(chosen_val))
        return (int(r), str(chosen_key), str(text or 'No entry'))

    # ---- Lay on Hands helpers ----
    def _class_hd_die(self, target: dict) -> str:
        """Return the HD die string for a target based on class (fallback 1d6)."""
        cls = str(target.get('class') or '').strip().lower()
        mapping = {
            'warrior': '1d12',
            'dwarf': '1d10',
            'thief': '1d6',
            'elf': '1d6',
            'halfling': '1d6',
            'wizard': '1d4',
            'mage': '1d4',
            'cleric': '1d8',
            'lv0': '1d4',
            'level0': '1d4',
            '0': '1d4',
        }
        return mapping.get(cls, '1d6')

    def _target_level(self, target: dict) -> int:
        try:
            return int(target.get('level', 1) or 1)
        except Exception:
            return 1

    def _heal_hp(self, target: dict, dice_count: int, die: str) -> Tuple[int, List[int]]:
        # Convert die like '1d8' into just the sides and roll dice_count of that die
        import re
        m = re.match(r"^1d(\d+)$", die, re.I)
        sides = int(m.group(1)) if m else 6
        total = 0
        rolls: List[int] = []
        for _ in range(max(0, int(dice_count))):
            r, rr = roll_dice(f"1d{sides}")
            total += int(r)
            rolls.extend(rr)
        # apply to target hp
        hp = target.get('hp') if isinstance(target.get('hp'), dict) else {'current': 0, 'max': 0}
        try:
            cur = int(hp.get('current', 0) or 0)
            mx = int(hp.get('max', cur) or cur)
        except Exception:
            cur, mx = 0, 0
        new_cur = min(mx, max(0, cur + total))
        target['hp'] = {'current': new_cur, 'max': mx}
        return total, rolls

    def _alignment_relation(self, cleric: dict, target: dict) -> str:
        def norm(a: Any) -> str:
            s = str(a or '').strip().lower()
            if s.startswith('law'): return 'lawful'
            if s.startswith('cha'): return 'chaotic'
            if s.startswith('neut'): return 'neutral'
            return s
        c = norm(cleric.get('alignment'))
        t = norm(target.get('alignment'))
        if c == t:
            return 'same'
        if (c == 'neutral' and t in {'lawful','chaotic'}) or (t == 'neutral' and c in {'lawful','chaotic'}):
            return 'adjacent'
        return 'opposed'

    def _dice_from_spell_check(self, total: int, relation: str) -> int:
        # Table per spec
        table = [
            (11, {'same': 0, 'adjacent': 0, 'opposed': 0}),
            (13, {'same': 2, 'adjacent': 1, 'opposed': 1}),
            (19, {'same': 3, 'adjacent': 2, 'opposed': 1}),
            (21, {'same': 4, 'adjacent': 3, 'opposed': 2}),
            (999, {'same': 5, 'adjacent': 4, 'opposed': 3}),
        ]
        for hi, row in table:
            if total <= hi:
                return int(row.get(relation, 0))
        return 0

    @app_commands.command(name="lay_on_hands", description="Cleric: Lay on Hands to heal HP or a condition. 1d20 + PER mod + CL. Failure increases disapproval.")
    @app_commands.describe(name="Cleric name", target="Target character name", condition="Optional condition to heal instead of HP: broken, organ, disease, paralysis, poison, blindness, deafness")
    async def lay_on_hands_slash(self, interaction: discord.Interaction, name: str, target: str, condition: Optional[str] = None):
        cleric = await self._load_record(name)
        if not cleric:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(cleric.get('class','')).strip().lower() != 'cleric':
            await interaction.response.send_message("Only clerics can lay on hands.", ephemeral=True)
            return
        tgt = await self._load_record(target)
        if not tgt:
            await interaction.response.send_message(f"Target '{target}' not found.", ephemeral=True)
            return
        # Roll spell check
        per_mod = self._ability_mod(cleric, 'PER')
        try:
            lvl = int(cleric.get('level', 1) or 1)
        except Exception:
            lvl = 1
        r, rolls = roll_dice('1d20')
        raw = int(rolls[0] if isinstance(rolls, list) and rolls else r)
        total = int(raw) + int(per_mod) + int(lvl)
        # Disapproval test
        try:
            dis_range = int(cleric.get('disapproval_range', 1) or 1)
        except Exception:
            dis_range = 1
        spells_data = self._load_spells_data()
        lines: List[str] = []
        header = f"{cleric.get('name', name)} (Cleric) lays on hands on {tgt.get('name', target)}"
        lines.append(header)
        lines.append(f"Roll: d20 {raw} + PER {per_mod:+} + CL {lvl} = {total}")
        if raw <= dis_range:
            new_range = max(1, dis_range + 1)
            cleric['disapproval_range'] = int(new_range)
            droll, dkey, dtext = self._roll_disapproval(spells_data)
            lines.append(f"❗ Disapproval triggered (raw {raw} ≤ range {dis_range}). Lay on Hands fails.")
            lines.append(f"Disapproval roll {droll} → {dkey}: {dtext}")
            lines.append(f"New disapproval range: {new_range}")
            await self._save_record(cleric.get('name', name), cleric)
            await interaction.response.send_message("\n".join(lines))
            return
        # Determine dice by alignment relation and check
        relation = self._alignment_relation(cleric, tgt)
        dice = self._dice_from_spell_check(total, relation)
        # Failure branch
        if dice <= 0:
            new_range = int(cleric.get('disapproval_range', 1) or 1) + 1
            cleric['disapproval_range'] = int(new_range)
            await self._save_record(cleric.get('name', name), cleric)
            lines.append(f"❌ Failure. Disapproval range increases to {new_range}.")
            await interaction.response.send_message("\n".join(lines))
            return
        # If a condition is specified, map to dice cost and apply if sufficient
        cond_map = {
            'broken': 1,
            'organ': 2,
            'disease': 2,
            'paralysis': 3,
            'poison': 3,
            'blindness': 4,
            'deafness': 4,
        }
        if condition:
            key = condition.strip().lower()
            if key in cond_map:
                need = cond_map[key]
                if dice >= need:
                    lines.append(f"✨ Condition healed: {key} (used {need} dice)")
                else:
                    lines.append(f"❌ Not enough healing power to cure {key} (need {need} dice, have {dice}). No effect.")
                await interaction.response.send_message("\n".join(lines))
                return
        # Otherwise, heal HP using target HD die, capped by target level/HD
        die = self._class_hd_die(tgt)
        cap = max(1, self._target_level(tgt))
        dice_to_roll = min(int(dice), int(cap))
        # Track pre-heal state for dying stabilization check
        had_dying = isinstance(tgt.get('dying'), dict)
        try:
            pre_cur_hp = int((tgt.get('hp') or {}).get('current', 0) or 0)
        except Exception:
            pre_cur_hp = 0
        healed, roll_list = self._heal_hp(tgt, dice_to_roll, die)
        # If healed from 0 while dying, apply permanent -1 STA and clear dying
        if had_dying:
            try:
                post_cur_hp = int((tgt.get('hp') or {}).get('current', 0) or 0)
            except Exception:
                post_cur_hp = 0
            if pre_cur_hp == 0 and post_cur_hp > 0:
                abl = tgt.setdefault('abilities', {})
                sta = abl.setdefault('STA', {})
                try:
                    mx = int(sta.get('max', sta.get('current', sta.get('score', 1)) or 1))
                except Exception:
                    mx = 1
                try:
                    cur = int(sta.get('current', mx) or mx)
                except Exception:
                    cur = mx
                new_max = max(1, mx - 1)
                new_cur = max(1, min(new_max, cur - 1))
                sta['max'] = int(new_max)
                sta['current'] = int(new_cur)
                # Recompute STA mod
                try:
                    from modules.utils import get_modifier  # type: ignore
                    sta['mod'] = int(get_modifier(int(new_cur)))
                except Exception:
                    pass
                tgt.pop('dying', None)
        await self._save_record(tgt.get('name', target), tgt)
        lines.append(f"✨ Healed {healed} HP using {dice_to_roll}×{die} (cap {cap}).")
        if had_dying and pre_cur_hp == 0 and ((tgt.get('hp') or {}).get('current', 0) or 0) > 0:
            lines.append("⚠️ Lasting injury: STA -1 (permanent)")
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="turn_unholy", description="Cleric: Turn unholy (1d20 + PER mod + caster level). Failure increases disapproval.")
    @app_commands.describe(name="Cleric name", target_hd="Target creature HD (affects DC)")
    async def turn_unholy_slash(self, interaction: discord.Interaction, name: str, target_hd: int):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(data.get('class','')).strip().lower() != 'cleric':
            await interaction.response.send_message("Only clerics can turn unholy.", ephemeral=True)
            return
        try:
            lvl = int(data.get('level', 1) or 1)
        except Exception:
            lvl = 1
        per_mod = self._ability_mod(data, 'PER')
        # roll
        r, rolls = roll_dice('1d20')
        raw = int(rolls[0] if isinstance(rolls, list) and rolls else r)
        total = int(raw) + int(lvl) + int(per_mod)
        # disapproval gate
        try:
            dis_range = int(data.get('disapproval_range', 1) or 1)
        except Exception:
            dis_range = 1
        spells_data = self._load_spells_data()
        lines: List[str] = []
        header = f"{data.get('name', name)} (Cleric) attempts to Turn Unholy"
        lines.append(header)
        lines.append(f"Roll: d20 {raw} + PER {per_mod:+} + CL {lvl} = {total}")
        if raw <= dis_range:
            # disapproval triggers; spell fails
            new_range = max(1, dis_range + 1)
            data['disapproval_range'] = int(new_range)
            # roll on table
            droll, dkey, dtext = self._roll_disapproval(spells_data)
            lines.append(f"❗ Disapproval triggered (raw {raw} ≤ range {dis_range}). Turn fails.")
            lines.append(f"Disapproval roll {droll} → {dkey}: {dtext}")
            lines.append(f"New disapproval range: {new_range}")
            await self._save_record(data.get('name', name), data)
            await interaction.response.send_message("\n".join(lines))
            return
        # determine DC: use prior convention DC=10+HD
        dc = 10 + int(target_hd)
        lines.append(f"DC: {dc}")
        if total >= dc:
            # success
            # Optionally mention known unholy targets
            unholy = data.get('unholy_targets') or []
            if isinstance(unholy, list) and unholy:
                lines.append(f"✨ Success. Affects unholy: {', '.join(unholy)}")
            else:
                lines.append("✨ Success.")
        else:
            # failure increases disapproval
            new_range = int(data.get('disapproval_range', 1) or 1) + 1
            data['disapproval_range'] = int(new_range)
            await self._save_record(data.get('name', name), data)
            lines.append(f"❌ Failure. Disapproval range increases to {new_range}.")
        await interaction.response.send_message("\n".join(lines))

    # ---- Autocomplete for name ----
    @turn_unholy_slash.autocomplete('name')
    async def turn_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items: List[app_commands.Choice[str]] = []
        try:
            for fname in os.listdir(SAVE_FOLDER):
                if not fname.endswith('.json'):
                    continue
                path = os.path.join(SAVE_FOLDER, fname)
                disp = None
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        disp = str(data.get('name') or '')
                except Exception:
                    disp = None
                if not disp:
                    disp = fname[:-5].replace('_', ' ')
                if cur and cur not in disp.lower():
                    continue
                items.append(app_commands.Choice(name=disp, value=disp))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    @lay_on_hands_slash.autocomplete('name')
    async def lay_name_ac(self, interaction: discord.Interaction, current: str):
        # Reuse name autocomplete
        return await self.turn_name_ac(interaction, current)

    @lay_on_hands_slash.autocomplete('target')
    async def lay_target_ac(self, interaction: discord.Interaction, current: str):
        # Suggest target names from SAVE_FOLDER
        return await self.turn_name_ac(interaction, current)

    # ---- Divine Aid ----
    @app_commands.command(name="divine_aid", description="Cleric: Request divine aid (extraordinary act). Adds +10 to future disapproval range.")
    @app_commands.describe(name="Cleric name", request="Brief description of the request", dc="Difficulty class (defaults to 10 for simple, higher for extraordinary)")
    async def divine_aid_slash(self, interaction: discord.Interaction, name: str, request: str, dc: int = 10):
        cleric = await self._load_record(name)
        if not cleric:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(cleric.get('class','')).strip().lower() != 'cleric':
            await interaction.response.send_message("Only clerics can beseech divine aid.", ephemeral=True)
            return
        per_mod = self._ability_mod(cleric, 'PER')
        try:
            lvl = int(cleric.get('level', 1) or 1)
        except Exception:
            lvl = 1
        # Spell check
        r, rolls = roll_dice('1d20')
        raw = int(rolls[0] if isinstance(rolls, list) and rolls else r)
        total = int(raw) + int(per_mod) + int(lvl)
        # Disapproval gate
        try:
            dis_range = int(cleric.get('disapproval_range', 1) or 1)
        except Exception:
            dis_range = 1
        spells_data = self._load_spells_data()
        lines: List[str] = []
        lines.append(f"{cleric.get('name', name)} (Cleric) beseeches divine aid: {request}")
        lines.append(f"Roll: d20 {raw} + PER {per_mod:+} + CL {lvl} = {total} vs DC {dc}")
        # This extraordinary act imparts a cumulative +10 penalty to future disapproval range
        new_dis = int(dis_range) + 10
        cleric['disapproval_range'] = int(new_dis)
        disapproval_triggered = False
        if raw <= dis_range:
            disapproval_triggered = True
            droll, dkey, dtext = self._roll_disapproval(spells_data)
            lines.append(f"❗ Disapproval triggered (raw {raw} ≤ range {dis_range}). Divine aid fails.")
            lines.append(f"Disapproval roll {droll} → {dkey}: {dtext}")
        else:
            if total >= int(dc):
                lines.append("✨ Divine aid granted at the judge's discretion.")
            else:
                # normal failure: increase disapproval by 1 more per rules consistency
                cleric['disapproval_range'] = int(new_dis + 1)
                lines.append(f"❌ Divine aid denied. Disapproval range increases to {cleric['disapproval_range']}.")
        # Persist range increase (and any additional increment on failure)
        await self._save_record(cleric.get('name', name), cleric)
        lines.append(f"Future disapproval range is now {cleric['disapproval_range']} (includes +10 penalty for asking).")
        await interaction.response.send_message("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(ClericCog(bot))
