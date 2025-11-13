import os
import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore


# DCC XP thresholds for levels 0-10 (inclusive)
LEVEL_THRESHOLDS = [0, 10, 50, 110, 190, 290, 410, 550, 710, 890, 1090]


def next_threshold(xp: int) -> Optional[int]:
    for th in LEVEL_THRESHOLDS:
        if xp < th:
            return th
    return None


class XPCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- File helpers ----
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

    xp = app_commands.Group(name="xp", description="Experience points commands")

    @xp.command(name="show", description="Show a character's XP and next threshold")
    async def xp_show(self, interaction: discord.Interaction, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        try:
            xp_val = int(data.get('xp', 0) or 0)
        except Exception:
            xp_val = 0
        try:
            lvl = int(data.get('level', 0) or 0)
        except Exception:
            lvl = 0
        nxt = next_threshold(xp_val)
        if nxt is None:
            text = f"{data.get('name', name)} — Level {lvl}, XP {xp_val} (max tier reached)"
        else:
            rem = max(0, nxt - xp_val)
            text = f"{data.get('name', name)} — Level {lvl}, XP {xp_val} (next at {nxt}, need {rem})"
        await interaction.response.send_message(text)

    @xp.command(name="add", description="Add XP to a character (owner or admin)")
    @app_commands.describe(amount="XP to add (can be negative)", note="Optional note to log")
    async def xp_add(self, interaction: discord.Interaction, name: str, amount: int, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Permissions: owner or admin
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        if not is_admin and str(data.get('owner')) != str(interaction.user.id):
            await interaction.response.send_message("You do not own this character.", ephemeral=True)
            return
        try:
            cur = int(data.get('xp', 0) or 0)
        except Exception:
            cur = 0
        new_val = max(0, int(cur) + int(amount))
        data['xp'] = int(new_val)
        if note:
            notes = data.setdefault('notes', {})
            log = notes.setdefault('xp_log', [])
            if isinstance(log, list):
                log.append({'delta': int(amount), 'new': int(new_val), 'by': int(interaction.user.id), 'note': note})
        await self._save_record(data.get('name', name), data)
        await interaction.response.send_message(f"✅ {data.get('name', name)} XP: {cur} → {new_val}")

    @xp.command(name="set", description="Set a character's XP to an absolute value (owner or admin)")
    @app_commands.describe(amount="New total XP", note="Optional note to log")
    async def xp_set(self, interaction: discord.Interaction, name: str, amount: int, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        if not is_admin and str(data.get('owner')) != str(interaction.user.id):
            await interaction.response.send_message("You do not own this character.", ephemeral=True)
            return
        try:
            cur = int(data.get('xp', 0) or 0)
        except Exception:
            cur = 0
        new_val = max(0, int(amount))
        data['xp'] = int(new_val)
        if note:
            notes = data.setdefault('notes', {})
            log = notes.setdefault('xp_log', [])
            if isinstance(log, list):
                log.append({'set': int(new_val), 'prev': int(cur), 'by': int(interaction.user.id), 'note': note})
        await self._save_record(data.get('name', name), data)
        await interaction.response.send_message(f"✅ {data.get('name', name)} XP set: {cur} → {new_val}")

    # ---- Autocomplete: character names ----
    @xp_show.autocomplete('name')
    @xp_add.autocomplete('name')
    @xp_set.autocomplete('name')
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
                items.append(app_commands.Choice(name=nm, value=nm))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items


async def setup(bot: commands.Bot):
    await bot.add_cog(XPCog(bot))
