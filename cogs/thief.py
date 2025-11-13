import os
import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from utils.dice import roll_dice  # type: ignore
from modules.utils import get_luck_current, consume_luck_and_save, get_modifier  # type: ignore


class ThiefCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- helpers ----
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

    def _ability_mod(self, data: dict, key: str) -> int:
        try:
            abl = data.get('abilities', {}) or {}
            v = abl.get(key)
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            if isinstance(v, (int, float, str)):
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    thief = app_commands.Group(name="thief", description="Thief utilities")

    @thief.command(name="skills", description="Show the full thief skill list for a character")
    @app_commands.describe(name="Character name")
    async def thief_skills(self, interaction: discord.Interaction, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Only thieves have this table
        if str(data.get('class', '')).strip().lower() != 'thief':
            await interaction.response.send_message("This character is not a Thief.", ephemeral=True)
            return
        # Viewing skills is allowed for any user (same visibility as /sheet)

        ts = data.get('thief_skills') if isinstance(data.get('thief_skills'), dict) else None
        raw_skills = (ts or {}).get('skills') if isinstance(ts, dict) else None
        skills = raw_skills if isinstance(raw_skills, dict) else {}
        lvl = (ts or {}).get('level') if isinstance(ts, dict) else data.get('level')
        align = (ts or {}).get('alignment') if isinstance(ts, dict) else data.get('alignment')
        if not skills:
            await interaction.response.send_message("No thief skills found. Level up the Thief to populate skills.", ephemeral=True)
            return

        # Render nicely
        title = f"{data.get('name', name)} — Thief Skills"
        desc_parts: list[str] = []
        desc_parts.append(f"Level: {lvl if lvl is not None else '—'} | Alignment: {align if align else '—'}")

        # Order keys for display
        order = [
            ('backstab', 'Backstab'),
            ('sneak_silently', 'Sneak Silently'),
            ('hide_in_shadows', 'Hide in Shadows'),
            ('pick_pocket', 'Pick Pocket'),
            ('climb_sheer_surfaces', 'Climb Sheer Surfaces'),
            ('pick_lock', 'Pick Lock'),
            ('find_trap', 'Find Trap'),
            ('disable_trap', 'Disable Trap'),
            ('forge_document', 'Forge Document'),
            ('disguise_self', 'Disguise Self'),
            ('read_languages', 'Read Languages'),
            ('handle_poison', 'Handle Poison'),
        ]
        rows: list[str] = []
        for key, label in order:
            if key in skills:
                try:
                    bonus = int(skills.get(key))
                    rows.append(f"• {label}: {bonus:+}")
                except Exception:
                    rows.append(f"• {label}: {skills.get(key)}")
        # Scroll casting die (string like d10, d12,...)
        sc = skills.get('cast_scroll_die')
        if sc:
            rows.append(f"• Cast Spell from Scroll: {sc}")

        embed = discord.Embed(title=title, description="\n".join(desc_parts))
        # Put rows in a field, chunk if too long
        body = "\n".join(rows) or "No skills recorded."
        if len(body) <= 1024:
            embed.add_field(name="Skills", value=body, inline=False)
        else:
            # split into chunks under 1024
            chunk = []
            size = 0
            i = 1
            for line in rows:
                if size + len(line) + 1 > 1000:
                    embed.add_field(name=f"Skills {i}", value="\n".join(chunk), inline=False)
                    chunk = []
                    size = 0
                    i += 1
                chunk.append(line)
                size += len(line) + 1
            if chunk:
                embed.add_field(name=f"Skills {i}", value="\n".join(chunk), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @thief.command(name="check", description="Roll a Thief skill with optional DC and Luck burn")
    @app_commands.describe(
        name="Character name",
        skill="Thief skill name (e.g., backstab, pick lock, hide in shadows)",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., tools, aid)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
    )
    async def thief_check(self,
        interaction: discord.Interaction,
        name: str,
        skill: str,
        dc: int | None = None,
        bonus: int | None = 0,
        burn: int | None = 0,
    ):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(data.get('class', '')).strip().lower() != 'thief':
            await interaction.response.send_message("This character is not a Thief.", ephemeral=True)
            return

        # Resolve skill bonus from data['thief_skills']
        ts = data.get('thief_skills') if isinstance(data.get('thief_skills'), dict) else None
        skills = (ts or {}).get('skills') if isinstance(ts, dict) else None
        # normalize key
        norm = ''.join(ch for ch in skill.lower() if ch.isalnum())
        candidates = {
            'backstab': 'backstab',
            'sneaksilently': 'sneak_silently',
            'hideinshadows': 'hide_in_shadows',
            'pickpocket': 'pick_pocket',
            'climbsheersurfaces': 'climb_sheer_surfaces',
            'picklock': 'pick_lock',
            'findtrap': 'find_trap',
            'disabletrap': 'disable_trap',
            'forgedocument': 'forge_document',
            'disguiseself': 'disguise_self',
            'readlanguages': 'read_languages',
            'handlepoison': 'handle_poison',
            'castspellfromscroll': 'cast_scroll_die',
            'castscroll': 'cast_scroll_die',
        }
        key = candidates.get(norm)
        # Fuzzy fallback: try to map by contains on known display names if not directly matched
        if not key:
            display_pairs = [
                ('backstab','backstab'),
                ('sneak silently','sneak_silently'),
                ('hide in shadows','hide_in_shadows'),
                ('pick pocket','pick_pocket'),
                ('climb sheer surfaces','climb_sheer_surfaces'),
                ('pick lock','pick_lock'),
                ('find trap','find_trap'),
                ('disable trap','disable_trap'),
                ('forge document','forge_document'),
                ('disguise self','disguise_self'),
                ('read languages','read_languages'),
                ('handle poison','handle_poison'),
                ('cast spell from scroll','cast_scroll_die'),
                ('cast scroll','cast_scroll_die'),
            ]
            low = skill.strip().lower()
            for disp, k in display_pairs:
                if low in disp or disp in low:
                    key = k
                    break
        if not key:
            await interaction.response.send_message("Unknown or unsupported thief skill.", ephemeral=True)
            return

        # Roll: thief skills are d20 checks with their skill bonus; Judge may add situational bonus
        die_expr = '1d20'
        roll, _ = roll_dice(die_expr)
        # Determine skill bonus (default 0 if table missing or key not present)
        skill_bonus = 0
        if isinstance(skills, dict) and key in skills and key != 'cast_scroll_die':
            try:
                skill_bonus = int(skills.get(key) or 0)
            except Exception:
                skill_bonus = 0
        b = int(bonus or 0)

        # Governing ability per provided table
        ability_map = {
            'backstab': 'AGI',
            'sneak_silently': 'AGI',
            'hide_in_shadows': 'AGI',
            'pick_pocket': 'AGI',
            'climb_sheer_surfaces': 'AGI',
            'pick_lock': 'AGI',
            'find_trap': 'INT',
            'disable_trap': 'AGI',
            'forge_document': 'INT',
            'disguise_self': 'PER',
            'read_languages': 'INT',
            'handle_poison': 'STA',
            'cast_scroll_die': 'LCK',  # special handling below
        }
        ab_code = ability_map.get(key)
        ab_mod = 0 if key == 'cast_scroll_die' else (self._ability_mod(data, ab_code) if ab_code else 0)

        total = int(roll) + int(skill_bonus) + int(ab_mod) + b

        # Luck burn applies and persists
        burn_used = 0
        luck_bonus = 0
        luck_rolls: list[int] = []
        if isinstance(burn, int) and burn and burn > 0:
            safe = name.strip().lower().replace(' ', '_')
            path = os.path.join(SAVE_FOLDER, f"{safe}.json")
            burn_used = int(consume_luck_and_save(data, int(burn), filename=path) or 0)
            if burn_used > 0:
                luck_die = str(data.get('luck_die') or 'd3')
                expr = f"{burn_used}{luck_die}"
                luck_bonus, luck_rolls = roll_dice(expr)
                total += int(luck_bonus)

        # Special handling for scroll casting: report die instead of bonus
        label_map = {
            'backstab': 'Backstab',
            'sneak_silently': 'Sneak Silently',
            'hide_in_shadows': 'Hide in Shadows',
            'pick_pocket': 'Pick Pocket',
            'climb_sheer_surfaces': 'Climb Sheer Surfaces',
            'pick_lock': 'Pick Lock',
            'find_trap': 'Find Trap',
            'disable_trap': 'Disable Trap',
            'forge_document': 'Forge Document',
            'disguise_self': 'Disguise Self',
            'read_languages': 'Read Languages',
            'handle_poison': 'Handle Poison',
            'cast_scroll_die': 'Cast Spell from Scroll',
        }
        display = label_map.get(key, skill.title())

        dc_text = f" vs DC {int(dc)}" if isinstance(dc, int) and dc > 0 else ""
        result_text = ""
        if isinstance(dc, int) and dc > 0:
            result_text = " — Success!" if total >= int(dc) else " — Fail"

        if key == 'cast_scroll_die':
            scroll_die = (
                (skills or {}).get('cast_scroll_die')
                or ((data.get('thief_skills') or {}).get('skills', {}) if isinstance(data.get('thief_skills'), dict) else {}).get('cast_scroll_die')
                or data.get('luck_die')
                or '—'
            )
            await interaction.response.send_message(
                f"📜 {data.get('name','Unknown')} {display}: {scroll_die} (no d20 roll; use this die for scroll casting).",
                ephemeral=True,
            )
            return

        parts = [
            f"🗝️ {data.get('name','Unknown')} {display} check{dc_text}:\n",
            f"• Roll: {roll} (1d20) + skill {skill_bonus:+}"
        ]
        if ab_code and key != 'cast_scroll_die':
            parts.append(f" + {ab_code} {ab_mod:+}")
        parts.append(f" + bonus {b:+}")
        if burn_used:
            ld = str(data.get('luck_die') or 'd3')
            parts.append(f" + LUCK ({burn_used}{ld} = {luck_bonus})")
        parts.append(f" = **{total}**{result_text}")

        # Append warning if luck ran out
        try:
            if burn_used and int(get_luck_current(data)) <= 0:
                parts.append("\nUh-oh! You're out of luck.")
        except Exception:
            pass

        await interaction.response.send_message("".join(parts))

    # ---- Autocomplete for name ----
    @thief_skills.autocomplete('name')
    @thief_check.autocomplete('name')
    async def name_ac(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                nm = fn[:-5]
                if q and q not in nm.lower():
                    continue
                # Prioritize thieves
                try:
                    with open(os.path.join(SAVE_FOLDER, fn), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if str(data.get('class', '')).strip().lower() != 'thief':
                        continue
                except Exception:
                    continue
                items.append(app_commands.Choice(name=nm, value=nm))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    @thief_check.autocomplete('skill')
    async def skill_ac(self, interaction: discord.Interaction, current: str):
        # Provide a friendly list of thief skills
        mapping = [
            ('backstab', 'Backstab'),
            ('sneak silently', 'Sneak Silently'),
            ('hide in shadows', 'Hide in Shadows'),
            ('pick pocket', 'Pick Pocket'),
            ('climb sheer surfaces', 'Climb Sheer Surfaces'),
            ('pick lock', 'Pick Lock'),
            ('find trap', 'Find Trap'),
            ('disable trap', 'Disable Trap'),
            ('forge document', 'Forge Document'),
            ('disguise self', 'Disguise Self'),
            ('read languages', 'Read Languages'),
            ('handle poison', 'Handle Poison'),
            ('cast scroll', 'Cast Spell from Scroll'),
        ]
        q = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        for key, label in mapping:
            text = f"{label}"
            if q and q not in key and q not in label.lower():
                continue
            items.append(app_commands.Choice(name=text, value=key))
            if len(items) >= 25:
                break
        return items


async def setup(bot: commands.Bot):
    await bot.add_cog(ThiefCog(bot))
