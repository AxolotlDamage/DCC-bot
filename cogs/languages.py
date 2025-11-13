from __future__ import annotations
import os
import json
import random
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from modules.data_constants import (
    DWARF_LANGUAGE_TABLE,
    ELF_LANGUAGE_TABLE,
    HALFLING_LANGUAGE_TABLE,
    LV0_LANGUAGE_TABLE,
    WIZARD_LANGUAGE_TABLE,
)  # type: ignore


def _char_path(name: str) -> str:
    safe = name.lower().strip().replace(' ', '_')
    return os.path.join(SAVE_FOLDER, f"{safe}.json")


def _get_languages_from_table(table: dict) -> Optional[str]:
    roll = random.randint(1, 100)
    for rng, lang in table.items():
        if isinstance(rng, int):
            if roll == rng:
                return lang
        else:
            # assume range-like
            try:
                if roll in rng:
                    return lang
            except Exception:
                continue
    return None


class LanguagesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _learn_impl(self, interaction: discord.Interaction, name: str):
        data = await self._load_character(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        owner_id = str(data.get('owner')) if data.get('owner') is not None else None
        if owner_id and owner_id != str(interaction.user.id):
            await interaction.response.send_message("🚫 You do not own this character.", ephemeral=True)
            return

        cls = str(data.get('class') or '').strip()
        alignment = str(data.get('alignment') or '').strip().lower()
        abilities = data.get('abilities', {}) or {}
        def _mod(key: str) -> int:
            try:
                v = abilities.get(key, {})
                if isinstance(v, dict):
                    return int(v.get('mod', 0))
                return int(v)
            except Exception:
                return 0
        int_mod = _mod('INT')
        lck_mod = abs(_mod('LCK'))
        augur = str(data.get('birth_augur', {}).get('sign') or '').strip().lower()
        occupation = str(data.get('occupation') or '').strip().lower()

        # Determine rolls and table
        rolls = 0
        bonus_langs: List[str] = []
        table = None
        if cls == 'Lv0':
            rolls = int_mod
            if 'birdsong' in augur:
                rolls += lck_mod
            rolls = max(1, rolls)  # at least native tongue
            dwarf = ('dwarven' in occupation) or ('dwarf' in cls.lower())
            elf = ('elven' in occupation) or ('elf' in cls.lower())
            halfling = ('halfling' in occupation) or ('halfling' in cls.lower())
            if dwarf:
                table = DWARF_LANGUAGE_TABLE
                bonus_langs.append('Dwarf')
            elif elf:
                table = ELF_LANGUAGE_TABLE
                bonus_langs.append('Elven')
            elif halfling:
                table = HALFLING_LANGUAGE_TABLE
                bonus_langs.append('Halfling')
            else:
                table = LV0_LANGUAGE_TABLE
        elif cls.lower() == 'wizard':
            rolls = int_mod * 2
            table = WIZARD_LANGUAGE_TABLE
        else:
            await interaction.response.send_message("🧠 Only level 0 and wizards learn languages this way.", ephemeral=True)
            return

        # Known languages (case-insensitive set)
        known_langs_raw: List[str] = data.get('languages', []) or []
        known_lower = set(l.lower() for l in known_langs_raw)
        new_langs: List[str] = []

        # If no options remain and no bonus left, exit early
        available = set(lang for rng, lang in (table or {}).items() if str(lang).lower() not in known_lower)
        bonus_lower = set(b.lower() for b in bonus_langs)
        if not available and not (bonus_lower - known_lower):
            await interaction.response.send_message(f"📘 {data.get('name', name)} already knows all possible languages from this table.", ephemeral=True)
            return

        while rolls > 0 and table:
            lang = _get_languages_from_table(table)
            if not lang:
                continue
            if lang == 'by_alignment':
                if alignment == 'lawful':
                    lang = 'Law'
                elif alignment == 'chaotic':
                    lang = 'Chaos'
                elif alignment == 'neutral':
                    lang = 'Neutrality'
                else:
                    # unknown alignment, skip this pick
                    continue
            lo = str(lang).lower()
            if lo not in known_lower:
                known_lower.add(lo)
                new_langs.append(lang)
                rolls -= 1

        for bl in bonus_langs:
            lo = bl.lower()
            if lo not in known_lower:
                known_lower.add(lo)
                new_langs.append(bl)

        # Merge preserving original case of existing entries
        out = list(known_langs_raw)
        existing_lower = set(l.lower() for l in out)
        for lang in new_langs:
            if lang.lower() not in existing_lower:
                out.append(lang)
                existing_lower.add(lang.lower())
        data['languages'] = sorted(out)
        await self._save_character(name, data)

        if new_langs:
            await interaction.response.send_message(f"🗣️ {data.get('name', name)} learned: {', '.join(new_langs)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"📘 {data.get('name', name)} already knows all possible languages from this table.", ephemeral=True)

    async def _load_character(self, name: str) -> Optional[dict]:
        path = _char_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    async def _save_character(self, name: str, data: dict) -> None:
        path = _char_path(name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    @app_commands.command(name="lang", description="Learn or modify languages for a character")
    @app_commands.describe(name="Character name, e.g., Char1", op="Optional: +elvish to add, -elvish to remove")
    async def lang(self, interaction: discord.Interaction, name: str, op: Optional[str] = None):
        if op and (op.startswith('+') or op.startswith('-')):
            data = await self._load_character(name)
            if not data:
                await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
                return
            owner_id = str(data.get('owner')) if data.get('owner') is not None else None
            if owner_id and owner_id != str(interaction.user.id):
                await interaction.response.send_message("🚫 You do not own this character.", ephemeral=True)
                return
            lang = op[1:].strip()
            langs = list(data.get('languages', []) or [])
            if op.startswith('+'):
                if lang not in langs:
                    langs.append(lang)
                data['languages'] = sorted(langs)
                await self._save_character(name, data)
                await interaction.response.send_message(f"🗣️ Added language: {lang}", ephemeral=True)
                return
            if op.startswith('-'):
                langs = [x for x in langs if str(x).lower() != lang.lower()]
                data['languages'] = sorted(langs)
                await self._save_character(name, data)
                await interaction.response.send_message(f"🗣️ Removed language: {lang}", ephemeral=True)
                return
        await self._learn_impl(interaction, name)


async def setup(bot: commands.Bot):
    await bot.add_cog(LanguagesCog(bot))
