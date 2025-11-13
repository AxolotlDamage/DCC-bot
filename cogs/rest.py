import os
import json
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from modules.utils import get_luck_current, get_modifier  # type: ignore


class RestCog(commands.Cog):
    """Rest and recovery mechanics.

    Implements day-long rest healing and related resets.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- helpers ---
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

    async def _save_record(self, data: dict) -> bool:
        safe = str(data.get('name') or '').strip().lower().replace(' ', '_')
        if not safe:
            return False
        path = os.path.join(SAVE_FOLDER, f"{safe}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False

    def _hp_heal(self, data: dict, amount: int) -> int:
        """Heal up to 'amount' HP, returns actual healed."""
        if amount <= 0:
            return 0
        hp = data.get('hp')
        if not isinstance(hp, dict):
            # Initialize if missing
            cur = int(hp or 0)
            data['hp'] = {'current': int(cur), 'max': int(cur)}
            hp = data['hp']
        try:
            cur = int(hp.get('current', 0) or 0)
            mx = int(hp.get('max', cur) or cur)
        except Exception:
            cur, mx = 0, 0
        new_cur = min(mx, cur + int(amount))
        healed = max(0, new_cur - cur)
        data['hp'] = {'current': int(new_cur), 'max': int(mx)}
        return healed

    def _luck_recover(self, data: dict, amount: int) -> int:
        """Recover up to 'amount' Luck if model supports it, returns actual recovered."""
        if amount <= 0:
            return 0
        # Preferred: abilities.LCK.current/max
        abl = data.get('abilities') if isinstance(data.get('abilities'), dict) else None
        if isinstance(abl, dict) and isinstance(abl.get('LCK'), dict):
            lck = abl['LCK']
            try:
                cur = int(lck.get('current', lck.get('max', 0)) or 0)
                mx = int(lck.get('max', cur) or cur)
            except Exception:
                cur = int(lck.get('current', 0) or 0)
                mx = int(lck.get('max', cur) or cur)
            new_cur = min(mx, cur + int(amount))
            rec = max(0, new_cur - cur)
            lck['current'] = int(new_cur)
            return rec
        # Legacy: data.luck.current/max
        lk = data.get('luck') if isinstance(data.get('luck'), dict) else None
        if isinstance(lk, dict):
            try:
                cur = int(lk.get('current', lk.get('max', 0)) or 0)
                mx = int(lk.get('max', cur) or cur)
            except Exception:
                cur = int(lk.get('current', 0) or 0)
                mx = int(lk.get('max', cur) or cur)
            new_cur = min(mx, cur + int(amount))
            rec = max(0, new_cur - cur)
            lk['current'] = int(new_cur)
            return rec
        return 0

    def _recover_ability_scores(self, data: dict, per_day: int, days: int) -> dict:
        """Recover ability score loss (except Luck) at the same rate as HP: 1 per night or 2 per bed rest, per day.

        Returns a dict of ability: recovered_points.
        """
        recovered: dict[str, int] = {}
        if per_day <= 0 or days <= 0:
            return recovered
        total = int(per_day) * int(days)
        abl = data.get('abilities') if isinstance(data.get('abilities'), dict) else None
        if not isinstance(abl, dict):
            return recovered
        for key, blk in abl.items():
            if key == 'LCK':
                continue  # Luck does not naturally heal
            try:
                if isinstance(blk, dict):
                    cur = int(blk.get('current', blk.get('score', blk.get('max', 0)) or 0))
                    cap = blk.get('max', blk.get('score', cur))
                    cap = int(cap if cap is not None else cur)
                    if cur < cap:
                        new_cur = min(cap, cur + total)
                        got = max(0, new_cur - cur)
                        blk['current'] = int(new_cur)
                        recovered[key] = got
                else:
                    # Numeric-only representation; treat as both current and cap and do nothing
                    pass
            except Exception:
                continue
        return recovered

    # --- Commands ---
    rest = app_commands.Group(name="rest", description="Rest and recovery")

    @rest.command(name="day", description="Rest N day(s): heal HP, recover ability loss (not Luck), reset disapproval, recover Luck.")
    @app_commands.describe(
        name="Character name",
        days="Number of days to rest (>=1)",
        bed_rest="Day(s) of bed rest (2 HP and 2 ability per day instead of 1)",
        recover_luck="Recover Luck per day if class allows",
        reset_disapproval="Reset Cleric disapproval to 1 at dawn",
    )
    async def rest_day(
        self,
        interaction: discord.Interaction,
        name: str,
        days: int = 1,
        bed_rest: bool = False,
        recover_luck: bool = True,
        reset_disapproval: bool = True,
    ):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return

        d = max(1, int(days))
        # HP recovery model: base 1 HP/day; if bed rest, 2 HP/day
        per_day = 2 if bed_rest else 1
        hp_gain = per_day * d
        # Track pre-heal state for dying stabilization check
        pre_cur_hp = 0
        try:
            hpblk = data.get('hp') if isinstance(data.get('hp'), dict) else {}
            pre_cur_hp = int(hpblk.get('current', 0) or 0)
        except Exception:
            pre_cur_hp = 0
        healed = self._hp_heal(data, hp_gain)
        # If healed from 0 while dying, apply permanent -1 STA and clear dying
        stabilized_msg = None
        try:
            post_cur_hp = int((data.get('hp') or {}).get('current', 0) or 0)
            if pre_cur_hp == 0 and post_cur_hp > 0 and isinstance(data.get('dying'), dict):
                # Apply permanent -1 Stamina (reduce max and current, floor at 1)
                abl = data.setdefault('abilities', {})
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
                try:
                    sta['mod'] = int(get_modifier(int(new_cur)))
                except Exception:
                    pass
                data['dying'] = None
                data.pop('dying', None)
                stabilized_msg = "⚠️ Lasting injury: STA -1 (permanent)"
        except Exception:
            pass

        # Ability score recovery (except Luck) at same rate
        abil_recovered = self._recover_ability_scores(data, per_day, d)

        # Luck recovery for certain classes (toggle-able)
        luck_rec = 0
        if recover_luck:
            cls = str(data.get('class') or '').strip().lower()
            # Thief: recover Luck equal to level per night (up to max)
            if cls == "thief":
                try:
                    lvl = int(data.get('level', 1) or 1)
                except Exception:
                    lvl = 1
                luck_rec = self._luck_recover(data, lvl * d)
            # Halfling: recover Luck equal to level per night (up to natural max)
            elif cls == "halfling":
                try:
                    lvl = int(data.get('level', 1) or 1)
                except Exception:
                    lvl = 1
                luck_rec = self._luck_recover(data, lvl * d)

        # Reset Cleric disapproval at dawn/new day
        dis_before = data.get('disapproval_range')
        if reset_disapproval and str(data.get('class') or '').strip().lower() == 'cleric':
            data['disapproval_range'] = 1

        # Persist
        # Clear arcane 'lost' flags on rest (spells are refreshed after a new day)
        try:
            cls = str(data.get('class') or '').strip().lower()
            if cls in {'wizard','mage','elf'}:
                spells = data.get('spells') if isinstance(data.get('spells'), dict) else None
                if isinstance(spells, dict):
                    for lvl in (1,2,3,4,5):
                        key = f'level_{lvl}'
                        arr = spells.get(key)
                        if not isinstance(arr, list):
                            continue
                        for entry in arr:
                            if isinstance(entry, dict) and entry.get('lost'):
                                entry['lost'] = False
        except Exception:
            pass
        await self._save_record(data)

        # Build response
        parts: List[str] = []
        parts.append(f"🛌 {data.get('name', name)} rests for {d} day(s).")
        parts.append(f"❤️ HP: +{healed} (up to {hp_gain} possible)")
        if stabilized_msg:
            parts.append(stabilized_msg)
        if abil_recovered:
            # Present as e.g. STR +1, AGI +2
            def label(ab):
                return {
                    'STR': 'STR', 'AGI': 'AGI', 'STA': 'STA', 'INT': 'INT', 'PER': 'PER', 'LCK': 'LCK'
                }.get(ab, ab)
            ab_bits = [f"{label(k)} +{v}" for k, v in abil_recovered.items() if v]
            if ab_bits:
                parts.append("🧬 Abilities: " + ", ".join(ab_bits))
        if recover_luck and luck_rec:
            try:
                cur_luck = int(get_luck_current(data))
                parts.append(f"🍀 Luck: +{luck_rec} (now {cur_luck})")
            except Exception:
                parts.append(f"🍀 Luck: +{luck_rec}")
        if reset_disapproval and str(data.get('class') or '').strip().lower() == 'cleric':
            parts.append(f"🕯️ Disapproval reset to 1 (was {dis_before if dis_before is not None else '—'})")
        # Add a compact note when spells were refreshed
        try:
            if cls in {'wizard','mage','elf'}:
                parts.append("📘 Spells refreshed: lost spells are available again.")
        except Exception:
            pass
        await interaction.response.send_message("\n".join(parts))

    # --- Autocomplete for character name ---
    @rest_day.autocomplete('name')
    async def rest_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items: List[app_commands.Choice[str]] = []
        try:
            for fname in os.listdir(SAVE_FOLDER):
                if not fname.endswith('.json'):
                    continue
                disp = fname[:-5].replace('_', ' ')
                if cur and cur not in disp.lower():
                    continue
                items.append(app_commands.Choice(name=disp, value=disp))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items


async def setup(bot: commands.Bot):
    await bot.add_cog(RestCog(bot))
