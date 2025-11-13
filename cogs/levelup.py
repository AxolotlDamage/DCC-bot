import os
import json
import random
from typing import Optional, Iterable

import discord
from discord import app_commands
from discord.ext import commands

from modules.data_constants import WIZARD_LANGUAGE_TABLE, WEAPON_TABLE, DWARF_LANGUAGE_TABLE, ELF_LANGUAGE_TABLE, HALFLING_LANGUAGE_TABLE  # type: ignore

from core.config import SAVE_FOLDER  # type: ignore

# XP thresholds from DCC table (level -> required XP)
LEVEL_THRESHOLDS = [0, 10, 50, 110, 190, 290, 410, 550, 710, 890, 1090]

# Class hit dice mapping (per level)
CLASS_HD = {
    'cleric': '1d8',
    'warrior': '1d12',
    'mage': '1d4',      # alias for wizard
    'wizard': '1d4',
    'thief': '1d6',
    'dwarf': '1d10',
    'elf': '1d6',
    'halfling': '1d6',
}


class LevelUpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- helpers ----
    def _root_dir(self) -> str:
        # repo root assumed to be parent of this 'cogs' directory
        return os.path.dirname(os.path.dirname(__file__))

    async def _choose_weapon(self, interaction: discord.Interaction, prompt_title: str) -> str | None:
        """Interactive weapon selector. Returns the chosen weapon key (lowercased) or None on cancel/timeout.
        If total weapons <= 25, shows a single dropdown. Otherwise, provides paging with Prev/Next.
        """
        all_weapons = sorted([str(k) for k in WEAPON_TABLE.keys()])
        if not all_weapons:
            return None

        chosen: list[str] = []
        page = 0
        page_size = 25
        total_pages = max(1, (len(all_weapons) + page_size - 1) // page_size)

        class WeaponSelect(discord.ui.Select):
            def __init__(self, opts: list[str]):
                options = [discord.SelectOption(label=o, value=o) for o in opts]
                super().__init__(
                    placeholder="Choose luck weapon",
                    min_values=1,
                    max_values=1,
                    options=options[:25],
                )

            async def callback(self, itx: discord.Interaction):
                nonlocal chosen
                chosen = list(self.values)
                await itx.response.defer()

        class WeaponView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.done = False
                self._mount_select()

            def _mount_select(self):
                # Clear old select if re-rendering
                for item in list(self.children):
                    if isinstance(item, discord.ui.Select):
                        self.remove_item(item)
                start = page * page_size
                opts = all_weapons[start:start+page_size]
                self.add_item(WeaponSelect(opts))

            async def _repaint(self, itx: discord.Interaction):
                self._mount_select()
                await itx.response.edit_message(view=self)

            @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, disabled=(total_pages <= 1))
            async def prev(self, itx: discord.Interaction, button: discord.ui.Button):
                nonlocal page
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if total_pages > 1:
                    page = (page - 1) % total_pages
                    await self._repaint(itx)

            @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, disabled=(total_pages <= 1))
            async def next(self, itx: discord.Interaction, button: discord.ui.Button):
                nonlocal page
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if total_pages > 1:
                    page = (page + 1) % total_pages
                    await self._repaint(itx)

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if len(chosen) != 1:
                    await itx.response.send_message("Please pick one weapon.", ephemeral=True)
                    return
                self.done = True
                await itx.response.edit_message(content=f"Luck weapon selected: {chosen[0]}", view=None)
                self.stop()

            @discord.ui.button(label="Random", style=discord.ButtonStyle.secondary)
            async def rand(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                nonlocal chosen
                chosen = [random.choice(all_weapons)]
                self.done = True
                await itx.response.edit_message(content=f"Rolled random luck weapon: {chosen[0]}", view=None)
                self.stop()

        view = WeaponView()
        text = f"{prompt_title}: choose your fixed Luck weapon"
        try:
            await interaction.followup.send(text, view=view, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(text, view=view, ephemeral=True)
            except Exception:
                return None
        await view.wait()
        if not view.done:
            return None
        return (chosen[0] if chosen else None)

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

    def _get_sta_mod(self, data: dict) -> int:
        try:
            abl = data.get('abilities', {})
            # DCC uses Stamina (STA) for HP modifiers
            v = abl.get('STA')
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            # if numeric score, compute modifier
            from modules.utils import get_modifier  # type: ignore
            return int(get_modifier(int(v)))
        except Exception:
            return 0

    def _hp_fields(self, data: dict) -> tuple[int, int]:
        hp = data.get('hp')
        if isinstance(hp, dict):
            cur = int(hp.get('current', hp.get('max', 0)) or 0)
            mx = int(hp.get('max', cur) or cur)
            return cur, mx
        if isinstance(hp, int):
            return int(hp), int(hp)
        return 0, 0

    def _set_hp(self, data: dict, cur: int, mx: int) -> None:
        data['hp'] = {'current': int(max(0, cur)), 'max': int(max(1, mx))}

    def _ability_mod(self, data: dict, key: str) -> int:
        """Return the ability modifier for key (e.g., 'AGI','STA','PER')."""
        try:
            abl = data.get('abilities', {}) or {}
            v = abl.get(key)
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            if isinstance(v, (int, float, str)):
                from modules.utils import get_modifier  # type: ignore
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    # ---- languages helpers ----
    def _lang_from_table(self, table: dict) -> str | None:
        """Roll 1-100 and resolve a language from a table with int or range keys."""
        try:
            roll = random.randint(1, 100)
            for rng, lang in (table or {}).items():
                if isinstance(rng, int):
                    if roll == rng:
                        return str(lang) if lang is not None else None
                else:
                    try:
                        # assume range-like (e.g., range(1, 21))
                        if roll in rng:
                            return str(lang) if lang is not None else None
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def _recompute_saves(self, data: dict) -> None:
        """Recompute displayed saves using ability modifiers + class save bonuses + birth augur.

        This updates data['saves'] in-place. Class bonuses are taken from data['class_saves'].
        Birth augur effects that modify saves are re-applied using data['max_luck_mod'].
        """
        # Class bonuses
        cls = data.get('class_saves', {}) or {}
        # Ability modifiers
        agi = self._ability_mod(data, 'AGI')
        sta = self._ability_mod(data, 'STA')
        per = self._ability_mod(data, 'PER')
        # Base totals
        ref = int(cls.get('reflex', 0) or 0) + int(agi)
        fort = int(cls.get('fortitude', 0) or 0) + int(sta)
        will = int(cls.get('will', 0) or 0) + int(per)
        # Birth augur save effects
        try:
            aug = data.get('birth_augur', {}) or {}
            effect = str(aug.get('effect') or '').strip()
            luck_mod = int(data.get('max_luck_mod', 0) or 0)
            if luck_mod:
                if effect == 'Lucky sign':
                    ref += luck_mod; fort += luck_mod; will += luck_mod
                elif effect == 'Struck by lightning':
                    ref += luck_mod
                elif effect == 'Lived through famine':
                    fort += luck_mod
                elif effect == 'Resisted temptation':
                    will += luck_mod
        except Exception:
            pass
        data['saves'] = {
            'reflex': int(ref),
            'fortitude': int(fort),
            'will': int(will),
        }

    # ---- spells helpers ----
    def _load_spells_data(self) -> dict:
        try:
            path = os.path.join(self._root_dir(), 'Spells.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _cleric_spell_names(self, spells_data: dict, lvl: int) -> list[str]:
        # Spells.json structure: { "spells": { "Cleric Spells": { "level 1": { ... } } } }
        bucket = spells_data.get('spells', {}).get('Cleric Spells', {}).get(f'level {lvl}', {})
        if isinstance(bucket, dict):
            return list(bucket.keys())
        return []

    def _spell_payload(self, spells_data: dict, lvl: int, name: str) -> dict:
        data = spells_data.get('spells', {}).get('Cleric Spells', {}).get(f'level {lvl}', {}).get(name, {})
        if isinstance(data, dict):
            return {'name': name, **data}
        return {'name': name}

    # Wizard spell helpers (arcane)
    def _wizard_spell_names(self, spells_data: dict, lvl: int) -> list[str]:
        bucket = spells_data.get('spells', {}).get('Wizard Spells', {}).get(f'level {lvl}', {})
        if isinstance(bucket, dict):
            return list(bucket.keys())
        return []

    def _wizard_spell_payload(self, spells_data: dict, lvl: int, name: str) -> dict:
        data = spells_data.get('spells', {}).get('Wizard Spells', {}).get(f'level {lvl}', {}).get(name, {})
        if isinstance(data, dict):
            return {'name': name, **data}
        return {'name': name}

    def _mercurial_effect(self, spells_data: dict, roll: int) -> str:
        """Resolve a d100 roll on MercurialMagicTable in Spells.json and return the effect text."""
        mm = spells_data.get('MercurialMagicTable', {}) if isinstance(spells_data, dict) else {}
        table = mm.get('table', {}) if isinstance(mm, dict) else {}
        if not isinstance(table, dict):
            return "No mercurial table available."
        # exact match
        v = table.get(str(int(roll)))
        if isinstance(v, str):
            return v
        # support simple ranges like "41-60"
        for k, val in table.items():
            if not isinstance(k, str):
                continue
            ks = k.strip()
            if '-' in ks:
                try:
                    a, b = ks.split('-', 1)
                    lo, hi = int(a.strip()), int(b.strip())
                    if lo <= roll <= hi:
                        return str(val)
                except Exception:
                    continue
        # fallback to any stringable value
        return str(v) if v is not None else "Unknown mercurial result"

    async def _choose_spells(
        self,
        interaction: discord.Interaction,
        level_label: str,
        available: list[str],
        count: int,
        randomize: bool,
    ) -> list[str]:
        # Fallback: if count <= 0 or nothing available
        if count <= 0 or not available:
            return []
        # If not enough available, take all
        if len(available) <= count:
            return available[:]
        # Random path
        if randomize:
            return random.sample(available, k=count)

        # Interactive select (multi-select up to count)
        # Use a View with a Select and Confirm/Random buttons
        chosen: list[str] = []

        class SpellSelect(discord.ui.Select):
            def __init__(self, opts: Iterable[str], max_values: int):
                options = [discord.SelectOption(label=o, value=o) for o in opts]
                super().__init__(
                    placeholder=f"Pick {count} {level_label} spell(s)",
                    min_values=count,
                    max_values=count,
                    options=options[:25],  # discord cap per select
                )

            async def callback(self, itx: discord.Interaction):
                nonlocal chosen
                chosen = list(self.values)
                await itx.response.defer()  # wait for confirm

        class SpellView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.done = False

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if len(chosen) != count:
                    await itx.response.send_message(f"Please pick exactly {count}.", ephemeral=True)
                    return
                self.done = True
                await itx.response.edit_message(content=f"Selected {level_label}: {', '.join(chosen)}", view=None)
                self.stop()

            @discord.ui.button(label="Random", style=discord.ButtonStyle.secondary)
            async def random_btn(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                nonlocal chosen
                chosen = random.sample(available, k=count)
                self.done = True
                await itx.response.edit_message(content=f"Rolled random {level_label}: {', '.join(chosen)}", view=None)
                self.stop()

        view = SpellView()
        view.add_item(SpellSelect(available, count))
        try:
            await interaction.followup.send(
                f"Choose {count} {level_label} spell(s) or hit Random:",
                view=view,
                ephemeral=True,
            )
        except Exception:
            # try alternate path if followup not ready
            try:
                await interaction.response.send_message(
                    f"Choose {count} {level_label} spell(s) or hit Random:",
                    view=view,
                    ephemeral=True,
                )
            except Exception:
                # final fallback: random
                return random.sample(available, k=count)

        await view.wait()
        if not view.done:
            # timeout -> random
            return random.sample(available, k=count)
        return chosen

    # ---- deity helpers ----
    def _normalize_alignment(self, s: str | None) -> str:
        v = (s or '').strip().lower()
        if v in {'law', 'lawful'}:
            return 'lawful'
        if v in {'chaos', 'chaotic'}:
            return 'chaotic'
        return 'neutral' if v.startswith('neut') else v

    def _alignment_map(self) -> dict:
        return {
            'lawful': {
                'gods': [
                    'Shul', 'Klazath', 'Ulesh', 'Choranus', 'Daenthar', 'Gorhan', 'Justicia', 'Aristemis'
                ],
                'weapons': ['club', 'mace', 'sling', 'staff', 'warhammer'],
                'unholy': [
                    'undead', 'demons', 'devils', 'chaotic extraplanar creatures',
                    'basilisk', 'medusa', 'Chaos Primes', 'chaotic humanoids', 'chaotic dragons'
                ],
            },
            'neutral': {
                'gods': ['Amun Tor', 'Ildavir', 'Pelagia', 'Cthulhu'],
                'weapons': ['dagger', 'mace', 'sling', 'staff', 'sword'],  # any sword
                'unholy': [
                    'mundane animals', 'undead', 'demons', 'devils', 'basilisk', 'medusa',
                    'lycanthropes', 'otyughs', 'slimes'
                ],
            },
            'chaotic': {
                'gods': ['Ahriman', 'Hidden Lord', 'Azi Dahaka', 'Bobugbubilz', 'Cadixtat', 'Nimlurun', 'Malotoch'],
                'weapons': ['axe', 'bow', 'dagger', 'dart', 'flail'],  # any axe/bow
                'unholy': [
                    'angels', 'paladins', 'lawful dragons', 'Lords of Law', 'Lawful Primes', 'lawful humanoids'
                ],
            },
        }

    # ---- wizard patrons ----
    def _load_patrons_data(self) -> dict:
        """Load wizard patrons JSON from data/wizard_patrons.json."""
        try:
            path = os.path.join(self._root_dir(), 'data', 'wizard_patrons.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _patron_names(self, patrons_data: dict) -> list[str]:
        """Return all patron names present in the JSON, preserving file order.

        This shows every defined patron (Wizards and Elves share the list). The UI will cap at 25 choices.
        """
        arr = patrons_data.get('patrons') if isinstance(patrons_data, dict) else None
        if not isinstance(arr, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for p in arr:
            if not isinstance(p, dict):
                continue
            nm = p.get('name')
            if isinstance(nm, str):
                nm = nm.strip()
                if nm and nm.lower() not in seen:
                    out.append(nm)
                    seen.add(nm.lower())
        return out

    def _find_patron_entry(self, patrons_data: dict, name: str) -> dict | None:
        """Find a patron dict by name (case-insensitive)."""
        try:
            arr = patrons_data.get('patrons') if isinstance(patrons_data, dict) else None
            if not isinstance(arr, list):
                return None
            tgt = (name or '').strip().lower()
            for p in arr:
                if isinstance(p, dict) and isinstance(p.get('name'), str):
                    if p['name'].strip().lower() == tgt:
                        return p
        except Exception:
            return None
        return None

    def _patron_spell_names(self, patrons_data: dict, patron_name: str, lvl: int) -> list[str]:
        """Return names of patron-specific spells at a given level for the named patron."""
        try:
            entry = self._find_patron_entry(patrons_data, patron_name)
            if not isinstance(entry, dict):
                return []
            spells = entry.get('spells')
            if not isinstance(spells, dict):
                return []
            key = f'level_{int(lvl)}'
            arr = spells.get(key)
            if not isinstance(arr, list):
                return []
            out: list[str] = []
            for s in arr:
                if isinstance(s, dict) and isinstance(s.get('name'), str) and s.get('name').strip():
                    out.append(s['name'].strip())
            return out
        except Exception:
            return []

    def _patron_spell_payload(self, patrons_data: dict, patron_name: str, lvl: int, spell_name: str) -> dict:
        """Return a deep-ish copy of the patron spell entry payload for attaching to a character record."""
        import copy
        try:
            entry = self._find_patron_entry(patrons_data, patron_name)
            if not isinstance(entry, dict):
                return {'name': spell_name}
            spells = entry.get('spells')
            key = f'level_{int(lvl)}'
            arr = spells.get(key) if isinstance(spells, dict) else None
            if isinstance(arr, list):
                for s in arr:
                    if isinstance(s, dict) and str(s.get('name','')).strip().lower() == spell_name.strip().lower():
                        payload = copy.deepcopy(s)
                        payload['source'] = 'patron'
                        payload['patron'] = entry.get('name')
                        return payload
        except Exception:
            pass
        return {'name': spell_name, 'source': 'patron', 'patron': patron_name}

    def _grant_patron_spells(self, data: dict, allowed_levels: list[int]) -> list[str]:
        """Grant patron-specific spells up to allowed_levels. Returns summary strings per level.

        - Does nothing if the character has no patron or if the patron has no spells section.
        - Adds spells without counting them toward normal learned targets.
        - Idempotent: skips spells already known.
        """
        learned: list[str] = []
        try:
            patron_name = str(data.get('patron') or '').strip()
        except Exception:
            patron_name = ''
        if not patron_name:
            return learned
        patrons_data = self._load_patrons_data()
        if not patrons_data:
            return learned
        spells_bucket = data.setdefault('spells', {})
        spells_data = self._load_spells_data()  # used only if we need to fall back to core payloads (rare)
        for s_lvl in sorted({int(x) for x in allowed_levels if int(x) >= 1}):
            names = self._patron_spell_names(patrons_data, patron_name, s_lvl)
            if not names:
                continue
            key = f'level_{s_lvl}'
            known_list: list[dict] = list(spells_bucket.get(key, [])) if isinstance(spells_bucket.get(key), list) else []
            known_names = {str(s.get('name')).strip().lower() for s in known_list if isinstance(s, dict) and s.get('name')}
            added: list[str] = []
            for nm in names:
                lo = nm.strip().lower()
                if lo in known_names:
                    continue
                # build payload from patron JSON
                payload = self._patron_spell_payload(patrons_data, patron_name, s_lvl, nm)
                known_list.append(payload)
                known_names.add(lo)
                added.append(nm)
            if added:
                spells_bucket[key] = known_list
                learned.append(f"L{s_lvl}: {', '.join(added)}")
        return learned

    async def _choose_patron(
        self,
        interaction: discord.Interaction,
        names: list[str],
        randomize: bool,
    ) -> str | None:
        if not names:
            return None
        if randomize:
            return random.choice(names)
        selected: list[str] = []

        class PatronSelect(discord.ui.Select):
            def __init__(self, opts: Iterable[str]):
                options = [discord.SelectOption(label=o, value=o) for o in opts]
                super().__init__(
                    placeholder="Choose a patron",
                    min_values=1,
                    max_values=1,
                    options=options[:25],
                )

            async def callback(self, itx: discord.Interaction):
                nonlocal selected
                selected = list(self.values)
                await itx.response.defer()

        class PatronView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.done = False

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if len(selected) != 1:
                    await itx.response.send_message("Please pick one patron.", ephemeral=True)
                    return
                self.done = True
                await itx.response.edit_message(content=f"Patron selected: {selected[0]}", view=None)
                self.stop()

            @discord.ui.button(label="Random", style=discord.ButtonStyle.secondary)
            async def rand(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                nonlocal selected
                selected = [random.choice(names)]
                self.done = True
                await itx.response.edit_message(content=f"Patron rolled: {selected[0]}", view=None)
                self.stop()

        view = PatronView()
        view.add_item(PatronSelect(names))
        prompt = "Choose a Supernatural Patron (or press Random):"
        try:
            await interaction.followup.send(prompt, view=view, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(prompt, view=view, ephemeral=True)
            except Exception:
                return random.choice(names)
        await view.wait()
        if not view.done:
            return random.choice(names)
        return selected[0] if selected else None

    async def _choose_deity(
        self,
        interaction: discord.Interaction,
        alignment_key: str,
        deity_list: list[str],
        randomize: bool,
    ) -> str | None:
        if not deity_list:
            return None
        if randomize:
            return random.choice(deity_list)

        selected: list[str] = []

        class DeitySelect(discord.ui.Select):
            def __init__(self, opts: Iterable[str]):
                options = [discord.SelectOption(label=o, value=o) for o in opts]
                super().__init__(
                    placeholder=f"Choose a deity ({alignment_key.title()})",
                    min_values=1,
                    max_values=1,
                    options=options[:25],
                )

            async def callback(self, itx: discord.Interaction):
                nonlocal selected
                selected = list(self.values)
                await itx.response.defer()

        class DeityView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.done = False

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                if len(selected) != 1:
                    await itx.response.send_message("Please pick one deity.", ephemeral=True)
                    return
                self.done = True
                await itx.response.edit_message(content=f"Deity selected: {selected[0]}", view=None)
                self.stop()

            @discord.ui.button(label="Random", style=discord.ButtonStyle.secondary)
            async def rand(self, itx: discord.Interaction, button: discord.ui.Button):
                if itx.user.id != interaction.user.id:
                    await itx.response.send_message("Not your selection.", ephemeral=True)
                    return
                nonlocal selected
                selected = [random.choice(deity_list)]
                self.done = True
                await itx.response.edit_message(content=f"Deity rolled: {selected[0]}", view=None)
                self.stop()

        view = DeityView()
        view.add_item(DeitySelect(deity_list))
        prompt = f"Choose a deity for your {alignment_key.title()} cleric (or press Random):"
        try:
            await interaction.followup.send(prompt, view=view, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(prompt, view=view, ephemeral=True)
            except Exception:
                return random.choice(deity_list)
        await view.wait()
        if not view.done:
            return random.choice(deity_list)
        return selected[0] if selected else None

    async def _do_level_up(self, interaction: discord.Interaction, data: dict, class_key: str, note: Optional[str]):
        # Permissions: owner or admin
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        if not is_admin and str(data.get('owner')) != str(interaction.user.id):
            await interaction.response.send_message("You do not own this character.", ephemeral=True)
            return

        # Disallow multiclassing: if character already has a non-0-level class different from the requested one, block
        def _canon_class(s: str) -> str:
            v = (s or '').strip().lower()
            # normalize common placeholders for 0-level
            if v in {"lv0", "level0", "level 0", "0", "level-0"}:
                return "lv0"
            # unify mage->wizard
            if v == "mage":
                return "wizard"
            return v

        target = _canon_class(class_key)
        existing_raw = str(data.get('class') or '')
        existing = _canon_class(existing_raw)
        if existing and existing != "lv0" and existing != target:
            try:
                await interaction.response.send_message(
                    f"❌ Multiclassing is disabled. This character is {existing_raw or existing.title()}; use /levelup {existing} instead.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        # Compute threshold warning (non-blocking)
        try:
            level = int(data.get('level', 0) or 0)
        except Exception:
            level = 0
        try:
            xp_val = int(data.get('xp', 0) or 0)
        except Exception:
            xp_val = 0
        needed = None
        if 0 <= level + 1 < len(LEVEL_THRESHOLDS):
            needed = LEVEL_THRESHOLDS[level + 1]
        warn = None
        if needed is not None and xp_val < needed:
            warn = f"Warning: XP {xp_val} below next-level threshold {needed}."

    # Roll HP gain: class hit die + STA mod; minimum 1
        from utils.dice import roll_dice  # type: ignore
        die = CLASS_HD.get(class_key.lower()) or '1d6'
        roll, _ = roll_dice(die)
        sta_mod = self._get_sta_mod(data)
        hp_gain = max(1, int(roll) + int(sta_mod))

        # Update HP: add gain to max and current (assumption)
        cur_hp, max_hp = self._hp_fields(data)
        new_max = max_hp + hp_gain
        new_cur = min(new_max, cur_hp + hp_gain)
        self._set_hp(data, new_cur, new_max)

        # Update level and class
        data['level'] = int(level + 1)
        # Set canonical class names
        class_name = {
            'cleric': 'Cleric',
            'warrior': 'Warrior',
            'mage': 'Wizard',
            'wizard': 'Wizard',
            'thief': 'Thief',
            'dwarf': 'Dwarf',
            'elf': 'Elf',
            'halfling': 'Halfling',
        }.get(class_key.lower(), class_key.title())
        # Respect existing class if set, but if it's a 0-level placeholder (e.g., 'Lv0'), update to the chosen class
        existing_class = str(data.get('class') or '').strip()
        if not existing_class:
            data['class'] = class_name
        else:
            norm = existing_class.lower().replace(' ', '')
            if norm in {'lv0', 'level0', '0'}:
                data['class'] = class_name

        # Log note
        notes = data.setdefault('notes', {})
        logs = notes.setdefault('levelup_log', [])
        if isinstance(logs, list):
            logs.append({
                'from_level': level,
                'to_level': level + 1,
                'class': class_name,
                'hp_gain': hp_gain,
                'roll': str(die),
                'sta_mod': sta_mod,
                'by': int(interaction.user.id),
                'note': note or '',
            })

        # If leveling from 0 -> 1, grant training in currently equipped weapon (if any)
        trained_awarded: str | None = None
        try:
            if int(level) == 0:
                eq = str(data.get('weapon') or '').strip()
                if eq:
                    eq_key = eq.lower()
                    # Merge with any existing training and legacy proficiencies
                    wp: set[str] = set()
                    for w in (data.get('weapon_training') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in (data.get('weapon_proficiencies') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    if eq_key not in wp:
                        wp.add(eq_key)
                        trained_awarded = eq
                    # Write back (prefer weapon_training but mirror legacy key)
                    data['weapon_training'] = sorted([w for w in wp if w])
                    data['weapon_proficiencies'] = list(data['weapon_training'])
        except Exception:
            pass

        await self._save_record(data.get('name') or 'unknown', data)

        # Respond
        name = data.get('name') or 'Unnamed'
        msg = [f"✅ {name} leveled up: Level {level} → {level+1} ({class_name})"]
        msg.append(f"HP +{hp_gain} (new {new_cur}/{new_max})")
        if trained_awarded:
            msg.append(f"🗡️ Gained weapon training: {trained_awarded}")
        if warn:
            msg.append(warn)
        await interaction.response.send_message("\n".join(msg))

    levelup = app_commands.Group(name="levelup", description="Level up a character by class")

    # ---- Class subcommands ----
    @levelup.command(name="cleric", description="Level up a Cleric")
    async def levelup_cleric(
        self,
        interaction: discord.Interaction,
        name: str,
        note: str | None = None,
        randomize: bool | None = False,
        choose_deity: bool | None = False,
    ):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # First perform the base level-up (HP, level, log)
        await self._do_level_up(interaction, data, 'cleric', note)
        # Apply Cleric progression (attack bonus, crit, action dice, saves) by the new level
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        prev_level = max(0, lvl - 1)
        CLERIC_TABLE = {
            1: {"atk": 0, "crit": "1d8",  "crit_tbl": "III", "act": "1d20",          "ref": 0, "fort": 1, "will": 1},
            2: {"atk": 1, "crit": "1d8",  "crit_tbl": "III", "act": "1d20",          "ref": 0, "fort": 1, "will": 1},
            3: {"atk": 2, "crit": "1d10", "crit_tbl": "III", "act": "1d20",          "ref": 1, "fort": 1, "will": 2},
            4: {"atk": 2, "crit": "1d10", "crit_tbl": "III", "act": "1d20",          "ref": 1, "fort": 2, "will": 2},
            5: {"atk": 3, "crit": "1d12", "crit_tbl": "III", "act": "1d20",          "ref": 1, "fort": 2, "will": 3},
            6: {"atk": 4, "crit": "1d12", "crit_tbl": "III", "act": "1d20+1d14",    "ref": 2, "fort": 2, "will": 4},
            7: {"atk": 5, "crit": "1d14", "crit_tbl": "III", "act": "1d20+1d16",    "ref": 2, "fort": 3, "will": 4},
            8: {"atk": 5, "crit": "1d14", "crit_tbl": "III", "act": "1d20+1d20",    "ref": 2, "fort": 3, "will": 5},
            9: {"atk": 6, "crit": "1d16", "crit_tbl": "III", "act": "1d20+1d20",    "ref": 3, "fort": 3, "will": 5},
            10:{"atk": 7, "crit": "1d16", "crit_tbl": "III", "act": "1d20+1d20",    "ref": 3, "fort": 4, "will": 6},
        }
        row = CLERIC_TABLE.get(lvl)
        if row:
            # Initialize disapproval for brand-new Clerics at level 1
            new_disapproval_init = False
            if lvl == 1 and 'disapproval_range' not in data:
                data['disapproval_range'] = 1
                new_disapproval_init = True
            data['attack_bonus'] = int(row['atk'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            # Keep action_die as 1d20 for attack roll compatibility, but store full action dice string for reference
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            # Store class save progression separately (to avoid overwriting any computed base saves)
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            # Update displayed saves to reflect new class bonuses
            self._recompute_saves(data)
            await self._save_record(data.get('name') or name, data)
            # Summarize in a follow-up
            lines = [
                f"Cleric Level {lvl} features updated:",
                f"• Attack Bonus: +{row['atk']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            # If a title is already present on character, reflect it in the summary for clarity
            if data.get('title'):
                lines.append(f"• Title: {data.get('title')}")
            if new_disapproval_init:
                lines.append("• Disapproval Range: 1")
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                # If initial response not finished yet (rare timing), fallback to ephemeral addendum
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

        # Deity selection (if none set or choose_deity requested)
        try:
            alignment = self._normalize_alignment(str(data.get('alignment')))
        except Exception:
            alignment = 'neutral'
        deity_info = self._alignment_map().get(alignment) or self._alignment_map().get('neutral')
        need_deity = choose_deity or not str(data.get('god') or '').strip()
        deity_name: str | None = None
        if need_deity and deity_info:
            deity_name = await self._choose_deity(
                interaction,
                alignment,
                deity_info['gods'],
                bool(randomize),
            )
            if deity_name:
                data['god'] = deity_name
                # Weapon training: union deity grants with existing (supports legacy key)
                wp = set([w.lower() for w in deity_info['weapons']])
                # Prefer new key, fallback to legacy
                existing_list = data.get('weapon_training')
                if not isinstance(existing_list, list):
                    existing_list = data.get('weapon_proficiencies') or []
                if isinstance(existing_list, list):
                    for w in existing_list:
                        wp.add(str(w).lower())
                data['weapon_training'] = sorted(wp)
                # Unholy targets
                data['unholy_targets'] = list(deity_info['unholy'])
                # Log
                notes = data.setdefault('notes', {})
                dlog = notes.setdefault('deity_log', [])
                if isinstance(dlog, list):
                    dlog.append({'level': lvl, 'alignment': alignment, 'god': deity_name, 'by': int(interaction.user.id)})
                await self._save_record(data.get('name') or name, data)
                try:
                    await interaction.followup.send(
                        f"🛐 Deity: {deity_name}\n• Weapon training: {', '.join(deity_info['weapons'])}\n• Turn Unholy affects: {', '.join(deity_info['unholy'])}",
                        ephemeral=False,
                    )
                except Exception:
                    pass

        # Spell learning flow (choose or random)
        # Map of what to learn based on previous level (i.e., the level you came FROM)
        learn_plan: list[tuple[int, int]] = []  # list of (spell level, count)
        if prev_level == 0:
            learn_plan = [(1, 4)]
        elif prev_level == 1:
            learn_plan = [(1, 1)]
        elif prev_level == 2:
            learn_plan = [(2, 3)]
        elif prev_level == 3:
            learn_plan = [(1, 1), (2, 1)]
        elif prev_level == 4:
            learn_plan = [(2, 1), (3, 2)]
        elif prev_level == 5:
            learn_plan = [(1, 1), (3, 1)]
        elif prev_level == 6:
            learn_plan = [(2, 1), (3, 1), (4, 1)]

        if learn_plan:
            spells_data = self._load_spells_data()
            learned_summary: list[str] = []
            # Ensure container
            spells_bucket = data.setdefault('spells', {})

            for s_lvl, count in learn_plan:
                key = f'level_{s_lvl}'
                known_list: list[dict] = list(spells_bucket.get(key, [])) if isinstance(spells_bucket.get(key), list) else []
                known_names = {s.get('name') for s in known_list if isinstance(s, dict)}
                pool = [n for n in self._cleric_spell_names(spells_data, s_lvl) if n not in known_names]
                # Only prompt when there are actually available spells to learn at this level now
                if not pool or int(count or 0) <= 0:
                    continue
                chosen_names = await self._choose_spells(interaction, f'Level {s_lvl}', pool, count, bool(randomize))
                if not chosen_names:
                    continue
                # Add payloads
                for nm in chosen_names:
                    known_list.append(self._spell_payload(spells_data, s_lvl, nm))
                spells_bucket[key] = known_list
                learned_summary.append(f"L{s_lvl}: {', '.join(chosen_names)}")

            # Persist and log
            notes = data.setdefault('notes', {})
            slog = notes.setdefault('spells_learned', [])
            if isinstance(slog, list) and learned_summary:
                slog.append({'level': lvl, 'by': int(interaction.user.id), 'cleric': learned_summary})
            await self._save_record(data.get('name') or name, data)
            try:
                await interaction.followup.send("📖 Learned spells: " + "; ".join(learned_summary))
            except Exception:
                pass

    @levelup.command(name="warrior", description="Level up a Warrior")
    async def levelup_warrior(self, interaction: discord.Interaction, name: str, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Base level-up (HP, level, log)
        await self._do_level_up(interaction, data, 'warrior', note)
        # Apply Warrior progression per level (using deed die attack model)
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        # Table per user-provided progression
        WARRIOR_TABLE: dict[int, dict] = {
            1:  {"deed": "d3",     "crit": "1d12", "crit_tbl": "III", "threat": "19-20", "act": "1d20",             "ref": 1, "fort": 1, "will": 0},
            2:  {"deed": "d4",     "crit": "1d14", "crit_tbl": "III", "threat": "19-20", "act": "1d20",             "ref": 1, "fort": 1, "will": 0},
            3:  {"deed": "d5",     "crit": "1d16", "crit_tbl": "IV",  "threat": "19-20", "act": "1d20",             "ref": 1, "fort": 2, "will": 1},
            4:  {"deed": "d6",     "crit": "1d20", "crit_tbl": "IV",  "threat": "19-20", "act": "1d20",             "ref": 2, "fort": 2, "will": 1},
            5:  {"deed": "d7",     "crit": "1d24", "crit_tbl": "V",   "threat": "18-20", "act": "1d20+1d14",       "ref": 2, "fort": 3, "will": 1},
            6:  {"deed": "d8",     "crit": "1d30", "crit_tbl": "V",   "threat": "18-20", "act": "1d20+1d16",       "ref": 2, "fort": 4, "will": 2},
            7:  {"deed": "d10+1",  "crit": "1d30", "crit_tbl": "V",   "threat": "18-20", "act": "1d20+1d20",       "ref": 3, "fort": 4, "will": 2},
            8:  {"deed": "d10+2",  "crit": "2d20", "crit_tbl": "V",   "threat": "18-20", "act": "1d20+1d20",       "ref": 3, "fort": 5, "will": 2},
            9:  {"deed": "d10+3",  "crit": "2d20", "crit_tbl": "V",   "threat": "17-20", "act": "1d20+1d20",       "ref": 3, "fort": 5, "will": 3},
            10: {"deed": "d10+4",  "crit": "2d20", "crit_tbl": "V",   "threat": "17-20", "act": "1d20+1d20+1d14",  "ref": 4, "fort": 6, "will": 3},
        }
        TITLES_BY_LEVEL: dict[int, dict[str, str]] = {
            1: {"lawful": "Squire",   "chaotic": "Bandit",    "neutral": "Wildling"},
            2: {"lawful": "Champion", "chaotic": "Brigand",   "neutral": "Barbarian"},
            3: {"lawful": "Knight",   "chaotic": "Marauder",  "neutral": "Berserker"},
            4: {"lawful": "Cavalier", "chaotic": "Ravager",   "neutral": "Headman / Headwoman"},
            5: {"lawful": "Paladin",  "chaotic": "Reaver",    "neutral": "Chieftain"},
        }
        row = WARRIOR_TABLE.get(lvl)
        if row:
            # Primary combat stats
            data['attack_bonus'] = 0  # Warriors use deed die; no static bonus beyond other effects
            data['deed_die'] = str(row['deed'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            # Action dice: keep action_die = 1d20 for roll compatibility; store full string separately
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            # Threat range (for crit threat on d20)
            threat = str(row.get('threat') or '19-20')
            data['crit_threat'] = threat
            try:
                # parse like "19-20" → 19
                th_min = int(threat.split('-', 1)[0])
            except Exception:
                th_min = 20
            data['crit_threat_min'] = int(th_min)
            # Class saves progression; update displayed saves with ability mods and augur
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            self._recompute_saves(data)
            # Titles by alignment for early levels
            try:
                align = self._normalize_alignment(str(data.get('alignment')))
            except Exception:
                align = 'neutral'
            if lvl in TITLES_BY_LEVEL:
                tmap = TITLES_BY_LEVEL.get(lvl, {})
                ttl = tmap.get(align) or tmap.get('neutral')
                if ttl:
                    data['title'] = ttl
            # At Warrior level 1, grant default weapon training list and set hit die display
            if lvl == 1:
                default_weapons = [
                    'battleaxe','club','crossbow','dagger','dart','handaxe','javelin','longbow','longsword',
                    'mace','polearm','shortbow','short sword','sling','spear','staff','two-handed sword','warhammer'
                ]
                try:
                    wp: set[str] = set()
                    for w in (data.get('weapon_training') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in (data.get('weapon_proficiencies') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in default_weapons:
                        wp.add(w)
                    data['weapon_training'] = sorted([w for w in wp if w])
                    data['weapon_proficiencies'] = list(data['weapon_training'])
                except Exception:
                    pass
                # Set hit die display
                data['hit_die'] = '1d12'
            # At first level, prompt for fixed Luck weapon if not set yet
            if lvl == 1 and not data.get('warrior_luck_weapon'):
                sel = await self._choose_weapon(interaction, "Warrior Level 1")
                if sel:
                    lck_mod = self._ability_mod(data, 'LCK')
                    data['warrior_luck_weapon'] = sel.strip().lower()
                    data['warrior_luck_weapon_mod'] = int(lck_mod)
                await self._save_record(data.get('name') or name, data)
            else:
                await self._save_record(data.get('name') or name, data)
            # Summary message
            lines = [
                f"Warrior Level {lvl} features updated:",
                f"• Deed Die: {row['deed']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']} (threat {row['threat']})",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            ttl_disp = data.get('title')
            if ttl_disp and lvl in TITLES_BY_LEVEL:
                lines.append(f"• Title: {ttl_disp}")
            if lvl == 1 and data.get('weapon_training'):
                lines.append("• Weapon Training: " + ", ".join(data['weapon_training'])[:900])
            # Report Luck weapon status
            if lvl == 1:
                try:
                    if data.get('warrior_luck_weapon'):
                        lines.append(f"• Luck Weapon: {data.get('warrior_luck_weapon')} ({int(data.get('warrior_luck_weapon_mod',0)):+} to attack; fixed at first level)")
                    else:
                        lines.append("• Luck Weapon: not set — use /set luck_weapon to choose any weapon type (fixed at first level)")
                except Exception:
                    pass
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

    # Internal implementation for Wizard level-up (formerly exposed as 'mage')
    async def _levelup_mage_impl(self, interaction: discord.Interaction, name: str, note: str | None = None, randomize: bool | None = False):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        await self._do_level_up(interaction, data, 'mage', note)
        # Apply Wizard progression per level (from provided CSV)
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        # Wizard table derived from CSV (crit table I throughout)
        WIZ: dict[int, dict] = {
            1:  {"atk": 0, "crit": "1d6",  "crit_tbl": "I",  "act": "1d20",            "ref": 0, "fort": 0, "will": 1, "known": {1:4, 2:0, 3:0, 4:0, 5:0}},
            2:  {"atk": 1, "crit": "1d6",  "crit_tbl": "I",  "act": "1d20",            "ref": 0, "fort": 0, "will": 1, "known": {1:5, 2:0, 3:0, 4:0, 5:0}},
            3:  {"atk": 1, "crit": "1d8",  "crit_tbl": "I",  "act": "1d20",            "ref": 1, "fort": 1, "will": 2, "known": {1:5, 2:3, 3:0, 4:0, 5:0}},
            4:  {"atk": 2, "crit": "1d8",  "crit_tbl": "I",  "act": "1d20",            "ref": 1, "fort": 1, "will": 2, "known": {1:6, 2:4, 3:0, 4:0, 5:0}},
            5:  {"atk": 2, "crit": "1d10", "crit_tbl": "I",  "act": "1d20+1d14",      "ref": 1, "fort": 2, "will": 3, "known": {1:6, 2:5, 3:2, 4:0, 5:0}},
            6:  {"atk": 3, "crit": "1d10", "crit_tbl": "I",  "act": "1d20+1d16",      "ref": 1, "fort": 2, "will": 4, "known": {1:7, 2:5, 3:3, 4:0, 5:0}},
            7:  {"atk": 4, "crit": "1d12", "crit_tbl": "I",  "act": "1d20+1d20",      "ref": 2, "fort": 2, "will": 5, "known": {1:7, 2:6, 3:4, 4:1, 5:0}},
            8:  {"atk": 4, "crit": "1d12", "crit_tbl": "I",  "act": "1d20+1d20",      "ref": 2, "fort": 3, "will": 6, "known": {1:8, 2:6, 3:5, 4:2, 5:0}},
            9:  {"atk": 5, "crit": "1d14", "crit_tbl": "I",  "act": "1d20+1d20",      "ref": 2, "fort": 3, "will": 7, "known": {1:8, 2:7, 3:5, 4:3, 5:1}},
            10: {"atk": 6, "crit": "1d16", "crit_tbl": "I",  "act": "1d20+1d20",      "ref": 3, "fort": 3, "will": 8, "known": {1:9, 2:7, 3:6, 4:4, 5:2}},
        }
        row = WIZ.get(lvl)
        if row:
            data['attack_bonus'] = int(row['atk'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            # Set hit die display at level 1
            if lvl == 1:
                data['hit_die'] = '1d4'
            # Titles per alignment for Wizard levels 1..5
            try:
                align = self._normalize_alignment(str(data.get('alignment')))
            except Exception:
                align = 'neutral'
            WIZ_TITLES = {
                1: {"lawful": "Astrologer",    "chaotic": "Shaman",   "neutral": "Philosopher"},
                2: {"lawful": "Evoker",       "chaotic": "Necromancer", "neutral": "Magician"},
                3: {"lawful": "Theurgist",    "chaotic": "Diabolist", "neutral": "Enchanter"},
                4: {"lawful": "Thaumaturgist", "chaotic": "Witch",    "neutral": "Wizard"},
                5: {"lawful": "Elementalist",  "chaotic": "Warlock",  "neutral": "Sorcerer"},
            }
            if lvl in WIZ_TITLES:
                tmap = WIZ_TITLES.get(lvl, {})
                ttl = tmap.get(align) or tmap.get('neutral')
                if ttl:
                    data['title'] = ttl
            # Recompute displayed saves with abilities and augur
            self._recompute_saves(data)
            await self._save_record(data.get('name') or name, data)
            # At Wizard level 1: choose a Supernatural Patron (from data/wizard_patrons.json) if not already set
            if lvl == 1 and not str(data.get('patron') or '').strip():
                p_data = self._load_patrons_data()
                names = self._patron_names(p_data)
                chosen_patron = await self._choose_patron(interaction, names, bool(randomize)) if names else None
                if chosen_patron:
                    data['patron'] = chosen_patron
                    # Log selection
                    notes = data.setdefault('notes', {})
                    plog = notes.setdefault('patron_log', [])
                    if isinstance(plog, list):
                        plog.append({'level': lvl, 'patron': chosen_patron, 'by': int(interaction.user.id)})
                    # Ensure 'Invoke patron' is known at level 1 for Wizards when a patron is chosen
                    try:
                        spells_data = self._load_spells_data()
                        spells_bucket = data.setdefault('spells', {})
                        key1 = 'level_1'
                        known_list_1: list[dict] = list(spells_bucket.get(key1, [])) if isinstance(spells_bucket.get(key1), list) else []
                        known_names_1 = {str(s.get('name')).strip().lower() for s in known_list_1 if isinstance(s, dict) and s.get('name')}
                        if 'invoke patron' not in known_names_1:
                            entry = self._wizard_spell_payload(spells_data, 1, 'Invoke patron')
                            known_list_1.append(entry)
                            spells_bucket[key1] = known_list_1
                    except Exception:
                        pass
                    await self._save_record(data.get('name') or name, data)
                    try:
                        await interaction.followup.send(f"🔮 Patron: {chosen_patron}")
                    except Exception:
                        pass
            # At Wizard level 1: grant languages automatically (2 × INT modifier)
            if lvl == 1:
                try:
                    int_mod = int(self._ability_mod(data, 'INT'))
                except Exception:
                    int_mod = 0
                rolls = max(0, int_mod * 2)
                if rolls > 0:
                    table = WIZARD_LANGUAGE_TABLE
                    # existing known languages (case-insensitive uniqueness)
                    known_raw = list(data.get('languages', []) or [])
                    known_lower = {str(x).lower() for x in known_raw}
                    new_langs: list[str] = []
                    # alignment mapping for special entry
                    align_key = self._normalize_alignment(str(data.get('alignment')))
                    align_lang = {'lawful': 'Law', 'chaotic': 'Chaos'}.get(align_key, 'Neutrality')
                    # attempt to pick without duplicates
                    safety = 0
                    while rolls > 0 and table and safety < 300:
                        safety += 1
                        lang = self._lang_from_table(table)
                        if not lang:
                            continue
                        if lang == 'by_alignment':
                            lang = align_lang
                        lo = str(lang).lower()
                        if lo in known_lower:
                            continue
                        known_lower.add(lo)
                        new_langs.append(str(lang))
                        rolls -= 1
                    if new_langs:
                        merged = list(known_raw)
                        # preserve original casing for existing; append new
                        for lg in new_langs:
                            if lg.lower() not in {x.lower() for x in merged}:
                                merged.append(lg)
                        data['languages'] = sorted(merged)
                        await self._save_record(data.get('name') or name, data)
                        try:
                            await interaction.followup.send(
                                f"🗣️ Languages learned (Wizard L1): {', '.join(new_langs)}",
                                ephemeral=True,
                            )
                        except Exception:
                            pass
            # Ensure spells known match CSV targets (fill with random if needed),
            # modified by INT-based adjustments and gated by maximum spell level
            spells_data = self._load_spells_data()
            learned: list[str] = []
            new_entries: list[dict] = []
            spells_bucket = data.setdefault('spells', {})
            # Compute INT score and table-based adjustments for number of known spells (no level gating)
            try:
                abl = data.get('abilities', {}).get('INT', {}) or {}
                int_score = int(abl.get('current', abl.get('score', 0)) or 0)
            except Exception:
                int_score = 0
            try:
                from modules.utils import wizard_spells_known_adjustment  # type: ignore
                int_adj = int(wizard_spells_known_adjustment(int_score))
            except Exception:
                int_adj = 0
            for s_lvl in (1,2,3,4,5):
                # Only offer levels that this progression row can learn (per CSV targets),
                # then apply INT-based adjustment; do not reduce below 1 if base target > 0
                base_target = int(row['known'].get(s_lvl, 0))
                if base_target <= 0:
                    continue
                target = base_target + int_adj
                if target < 1:
                    target = 1
                if target <= 0:
                    continue
                key = f'level_{s_lvl}'
                known_list: list[dict] = list(spells_bucket.get(key, [])) if isinstance(spells_bucket.get(key), list) else []
                have = len([1 for s in known_list if isinstance(s, dict)])
                need = max(0, target - have)
                if need <= 0:
                    continue
                pool_all = self._wizard_spell_names(spells_data, s_lvl)
                known_names = {str(s.get('name')).strip() for s in known_list if isinstance(s, dict)}
                pool = [nm for nm in pool_all if nm not in known_names]
                # Interactive selection (or random fallback)
                chosen_names = await self._choose_spells(interaction, f'Level {s_lvl}', pool, need, bool(randomize))
                pick = chosen_names[:]
                for nm in chosen_names:
                    entry = self._wizard_spell_payload(spells_data, s_lvl, nm)
                    known_list.append(entry)
                    new_entries.append(entry)
                spells_bucket[key] = known_list
                if pick:
                    learned.append(f"L{s_lvl}: {', '.join(pick)}")
            # After core learning, grant any patron-specific spells available at current level (does not count against picks)
            try:
                allowed_levels = [lvl for lvl, cnt in row.get('known', {}).items() if int(cnt or 0) > 0]
                patron_added = self._grant_patron_spells(data, allowed_levels)
                if patron_added:
                    learned.append("Patron: " + "; ".join(patron_added))
                    await self._save_record(data.get('name') or name, data)
            except Exception:
                pass
            # Ask to apply Mercurial Magic to newly learned spells
            if new_entries:
                class AskMercurial(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=90)
                        self.choice: str | None = None
                        self.done = False

                    @discord.ui.button(label="Apply Mercurial Magic", style=discord.ButtonStyle.primary)
                    async def apply(self, itx: discord.Interaction, button: discord.ui.Button):
                        if itx.user.id != interaction.user.id:
                            await itx.response.send_message("Not your action.", ephemeral=True)
                            return
                        self.choice = 'yes'
                        self.done = True
                        await itx.response.edit_message(content="Applying Mercurial Magic…", view=None)
                        self.stop()

                    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
                    async def skip(self, itx: discord.Interaction, button: discord.ui.Button):
                        if itx.user.id != interaction.user.id:
                            await itx.response.send_message("Not your action.", ephemeral=True)
                            return
                        self.choice = 'no'
                        self.done = True
                        await itx.response.edit_message(content="Skipped Mercurial Magic.", view=None)
                        self.stop()

                ask = AskMercurial()
                prompt = "Apply Mercurial Magic to newly learned spells? (rolls 1d100 per spell; Wizard Luck mod applies)"
                try:
                    await interaction.followup.send(prompt, view=ask, ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(prompt, view=ask, ephemeral=True)
                    except Exception:
                        ask.done = True
                        ask.choice = 'no'
                await ask.wait()
                if ask.choice == 'yes':
                    results: list[str] = []
                    for entry in new_entries:
                        try:
                            nm = str(entry.get('name'))
                            lck_mod = int(self._ability_mod(data, 'LCK'))

                            def clamp(v: int) -> int:
                                return 1 if v < 1 else (100 if v > 100 else v)

                            def resolve_once(mode: str) -> tuple[int, str, str]:
                                """Return (adj_roll, effect_text, raw_repr) for a single mercurial roll.
                                mode: 'd100' or '4d20' (the latter applies Luck in +/-10 increments).
                                """
                                if mode == '4d20':
                                    a = random.randint(1, 20)
                                    b = random.randint(1, 20)
                                    c = random.randint(1, 20)
                                    d = random.randint(1, 20)
                                    total = a + b + c + d
                                    adj = clamp(int(total + (lck_mod * 10)))
                                    eff = self._mercurial_effect(spells_data, adj)
                                    raw = f"4d20=({a}+{b}+{c}+{d})={total}; luck*10={lck_mod*10:+}"
                                    return adj, eff, raw
                                else:  # 'd100'
                                    r0 = random.randint(1, 100)
                                    adj = clamp(int(r0 + lck_mod))
                                    eff = self._mercurial_effect(spells_data, adj)
                                    raw = f"1d100={r0}; luck={lck_mod:+}"
                                    return adj, eff, raw

                            # Initial mercurial roll (standard d100 path with Luck mod by +/-1)
                            r0 = random.randint(1, 100)
                            adj0 = clamp(int(r0 + lck_mod))
                            eff0 = self._mercurial_effect(spells_data, adj0)

                            # Handle special reroll directives for 99 and 100
                            if adj0 == 99:
                                r1, e1, raw1 = resolve_once('d100')
                                r2, e2, raw2 = resolve_once('d100')
                                comb_effect = (
                                    "Roll again twice — results:\n"
                                    f"  • {r1}: {e1}\n"
                                    f"  • {r2}: {e2}"
                                )
                                entry['mercurial'] = {
                                    'roll': '99→2×',
                                    'effect': comb_effect,
                                    'luck_mod': lck_mod,
                                    'raw': f"1d100={r0}; luck={lck_mod:+}",
                                    'chain': [
                                        {'mode': 'd100', 'roll': r1, 'effect': e1, 'raw': raw1},
                                        {'mode': 'd100', 'roll': r2, 'effect': e2, 'raw': raw2},
                                    ]
                                }
                                suffix = f" (Luck {lck_mod:+})" if lck_mod else ""
                                results.append(f"• {nm}: 99→2×{suffix} — {e1[:90]} | {e2[:90]}")
                            elif adj0 == 100:
                                r1, e1, raw1 = resolve_once('4d20')
                                r2, e2, raw2 = resolve_once('4d20')
                                comb_effect = (
                                    "Roll again twice (special 4d20±Luck×10) — results:\n"
                                    f"  • {r1}: {e1}\n"
                                    f"  • {r2}: {e2}"
                                )
                                entry['mercurial'] = {
                                    'roll': '100→2×4d20',
                                    'effect': comb_effect,
                                    'luck_mod': lck_mod,
                                    'raw': f"1d100={r0}; luck={lck_mod:+}",
                                    'chain': [
                                        {'mode': '4d20', 'roll': r1, 'effect': e1, 'raw': raw1},
                                        {'mode': '4d20', 'roll': r2, 'effect': e2, 'raw': raw2},
                                    ]
                                }
                                suffix = f" (Luck {lck_mod:+})" if lck_mod else ""
                                results.append(f"• {nm}: 100→2×4d20{suffix} — {e1[:90]} | {e2[:90]}")
                            else:
                                entry['mercurial'] = {
                                    'roll': adj0,
                                    'effect': eff0,
                                    'luck_mod': lck_mod,
                                    'raw': f"1d100={r0}; luck={lck_mod:+}",
                                }
                                suffix = f" (Luck {lck_mod:+})" if lck_mod else ""
                                results.append(f"• {nm}: {adj0}{suffix} — {eff0[:180]}")
                        except Exception:
                            continue
                    await self._save_record(data.get('name') or name, data)
                    if results:
                        try:
                            await interaction.followup.send("Mercurial Magic applied:\n" + "\n".join(results), ephemeral=True)
                        except Exception:
                            pass
            if learned:
                notes = data.setdefault('notes', {})
                wlog = notes.setdefault('spells_learned', [])
                if isinstance(wlog, list):
                    wlog.append({'level': lvl, 'by': int(interaction.user.id), 'wizard': learned})
                await self._save_record(data.get('name') or name, data)
            # Summary output
            lines = [
                f"Wizard Level {lvl} features updated:",
                f"• Attack Bonus: +{row['atk']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            if learned:
                lines.append("• Spells known updated: " + "; ".join(learned))
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

    @levelup.command(name="wizard", description="Level up a Wizard")
    async def levelup_wizard(self, interaction: discord.Interaction, name: str, note: str | None = None, randomize: bool | None = False):
        # Reuse shared implementation (mage alias removed as a public command)
        await self._levelup_mage_impl(interaction, name, note, randomize)

    @app_commands.command(name="wizard_summary", description="Preview a Wizard's progression and spells known")
    async def wizard_summary(self, interaction: discord.Interaction, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        cls = str(data.get('class') or '').strip().lower()
        if cls not in {'wizard','mage','elf'}:
            await interaction.response.send_message("Character is not an arcane caster (Wizard/Mage/Elf).", ephemeral=True)
            return
        try:
            lvl = int(data.get('level', 0) or 0)
        except Exception:
            lvl = 0
        atk = data.get('attack_bonus')
        crit_die = data.get('crit_die')
        crit_tbl = data.get('crit_table')
        act = data.get('action_dice') or data.get('action_die')
        saves = data.get('saves') or {}
        lines = [
            f"{data.get('name') or name} ({str(data.get('class') or '').title()}) — Level {lvl}",
            f"• Attack Bonus: {('+' if int(atk or 0) >= 0 else '')}{int(atk or 0)}",
            f"• Crit: {crit_die or 'n/a'} / {crit_tbl or 'n/a'}",
            f"• Action Dice: {act or 'n/a'}",
            f"• Saves: Ref {int((saves.get('reflex') or 0)):+}, Fort {int((saves.get('fortitude') or 0)):+}, Will {int((saves.get('will') or 0)):+}",
        ]
        # Spells known by level (counts and first few names)
        spells = data.get('spells') or {}
        for s_lvl in (1,2,3,4,5):
            key = f'level_{s_lvl}'
            arr = spells.get(key) or []
            names = [str(s.get('name')) for s in arr if isinstance(s, dict) and s.get('name')]
            disp = ', '.join(names[:8]) + (' …' if len(names) > 8 else '') if names else '—'
            lines.append(f"• L{s_lvl} Known: {len(names)} — {disp}")
        try:
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
        except Exception:
            pass

    @levelup.command(name="thief", description="Level up a Thief")
    async def levelup_thief(self, interaction: discord.Interaction, name: str, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Base level-up (HP, level, log)
        await self._do_level_up(interaction, data, 'thief', note)
        # Apply Thief progression per level
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        THIEF_TABLE = {
            1: {"atk": 0, "crit": "1d10", "crit_tbl": "II",  "act": "1d20",          "luck": "d3",  "ref": 1, "fort": 1, "will": 0},
            2: {"atk": 1, "crit": "1d12", "crit_tbl": "II",  "act": "1d20",          "luck": "d4",  "ref": 1, "fort": 1, "will": 0},
            3: {"atk": 2, "crit": "1d14", "crit_tbl": "II",  "act": "1d20",          "luck": "d5",  "ref": 2, "fort": 1, "will": 1},
            4: {"atk": 2, "crit": "1d16", "crit_tbl": "II",  "act": "1d20",          "luck": "d6",  "ref": 2, "fort": 2, "will": 1},
            5: {"atk": 3, "crit": "1d20", "crit_tbl": "II",  "act": "1d20",          "luck": "d7",  "ref": 3, "fort": 2, "will": 1},
            6: {"atk": 4, "crit": "1d24", "crit_tbl": "II",  "act": "1d20+1d14",    "luck": "d8",  "ref": 4, "fort": 2, "will": 2},
            7: {"atk": 5, "crit": "1d30", "crit_tbl": "II",  "act": "1d20+1d16",    "luck": "d10", "ref": 4, "fort": 3, "will": 2},
            8: {"atk": 5, "crit": "1d30+2", "crit_tbl": "II","act": "1d20+1d20",    "luck": "d12", "ref": 5, "fort": 3, "will": 2},
            9: {"atk": 6, "crit": "1d30+4", "crit_tbl": "II","act": "1d20+1d20",    "luck": "d14", "ref": 5, "fort": 3, "will": 3},
            10:{"atk": 7, "crit": "1d30+6", "crit_tbl": "II","act": "1d20+1d20",    "luck": "d16", "ref": 6, "fort": 4, "will": 3},
        }
        row = THIEF_TABLE.get(lvl)
        if row:
            data['attack_bonus'] = int(row['atk'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            # Keep action_die simple for attack roll compatibility; store full action dice for reference
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            data['luck_die'] = str(row['luck'])
            # Class saves progression
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            # Recompute displayed saves
            self._recompute_saves(data)
            # Titles per alignment for Thief levels 1..5
            try:
                align = self._normalize_alignment(str(data.get('alignment')))
            except Exception:
                align = 'neutral'
            THIEF_TITLES = {
                1: {"lawful": "Bravo",      "chaotic": "Thug",        "neutral": "Beggar"},
                2: {"lawful": "Apprentice", "chaotic": "Murderer",    "neutral": "Cutpurse"},
                3: {"lawful": "Rogue",      "chaotic": "Cutthroat",   "neutral": "Burglar"},
                4: {"lawful": "Capo",       "chaotic": "Executioner", "neutral": "Robber"},
                5: {"lawful": "Boss",       "chaotic": "Assassin",    "neutral": "Swindler"},
            }
            if lvl in THIEF_TITLES:
                tmap = THIEF_TITLES.get(lvl, {})
                ttl = tmap.get(align) or tmap.get('neutral')
                if ttl:
                    data['title'] = ttl
            await self._save_record(data.get('name') or name, data)
            # Summary
            lines = [
                f"Thief Level {lvl} features updated:",
                f"• Attack Bonus: +{row['atk']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Luck Die: {row['luck']}",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            if data.get('title'):
                lines.append(f"• Title: {data.get('title')}")
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

        # Thieves' Cant: ensure thieves learn the cant (spoken, no script). Grant at Thief level 1; be idempotent.
        try:
            langs = data.get('languages')
            # normalize container
            if not isinstance(langs, list):
                if langs is None:
                    langs = []
                else:
                    langs = [str(langs)]
            def _norm(s: str) -> str:
                return str(s or '').strip().lower().replace('’', "'")
            target = "thieves' cant"
            has_cant = any(_norm(x) == target for x in langs)
            # Add when first becoming a Thief (level 1) or if missing for existing Thieves
            if not has_cant and str(data.get('class','')).strip().lower() == 'thief':
                langs.append("Thieves' Cant")
                data['languages'] = langs
                await self._save_record(data.get('name') or name, data)
                try:
                    await interaction.followup.send("🗣️ Language gained: Thieves' Cant")
                except Exception:
                    pass
        except Exception:
            pass

        # Apply Thief skills by alignment and level (Table 1-9)
        try:
            align = self._normalize_alignment(str(data.get('alignment')))
        except Exception:
            align = 'neutral'
        idx = max(0, min(9, (lvl or 1) - 1))
        # Helper to pick from arrays by alignment
        def pick(arr_law, arr_neu, arr_cha):
            if align == 'lawful':
                return arr_law[idx]
            if align == 'chaotic':
                return arr_cha[idx]
            return arr_neu[idx]
        # Arrays per skill (lawful, neutral, chaotic)
        seq_1_3_5_7_8_9_10_11_12_13 = [1,3,5,7,8,9,10,11,12,13]
        seq_3_5_7_8_9_11_12_13_14_15 = [3,5,7,8,9,11,12,13,14,15]
        seq_0_to_9 = [0,0,1,2,3,4,5,6,7,8]
        seq_0_to_9_plus1 = [0,1,2,3,4,5,6,7,8,9]
        scroll_law = ['d10','d10','d12','d12','d14','d14','d16','d16','d20','d20']
        scroll_neu = ['d8','d10','d10','d12','d12','d14','d14','d16','d16','d20']
        scroll_cha = ['d6','d8','d10','d10','d12','d12','d14','d14','d16','d16']
        skills = {
            'backstab': pick(seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13),
            'sneak_silently': pick(seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13),
            'hide_in_shadows': pick(seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15),
            'pick_pocket': pick(seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13),
            'climb_sheer_surfaces': pick(seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15),
            'pick_lock': pick(seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13, seq_1_3_5_7_8_9_10_11_12_13),
            'find_trap': pick(seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15),
            'disable_trap': pick(seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15, seq_3_5_7_8_9_11_12_13_14_15),
            'forge_document': pick(seq_0_to_9, seq_0_to_9, seq_0_to_9),
            'disguise_self': pick(seq_0_to_9_plus1, seq_0_to_9_plus1, seq_0_to_9_plus1),
            'read_languages': pick(seq_0_to_9, seq_0_to_9, seq_0_to_9),
            'handle_poison': pick(seq_0_to_9_plus1, seq_0_to_9_plus1, seq_0_to_9_plus1),
            'cast_scroll_die': pick(scroll_law, scroll_neu, scroll_cha),
        }
        data['thief_skills'] = {
            'alignment': align,
            'level': lvl,
            'skills': skills,
        }
        await self._save_record(data.get('name') or name, data)
        try:
            await interaction.followup.send(
                f"🔐 Thief skills updated ({align.title()}): backstab +{skills['backstab']}, sneak +{skills['sneak_silently']}, hide +{skills['hide_in_shadows']}, scroll {skills['cast_scroll_die']}"
            )
        except Exception:
            pass

    @levelup.command(name="dwarf", description="Level up a Dwarf")
    async def levelup_dwarf(self, interaction: discord.Interaction, name: str, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Base level-up (HP, level, log)
        await self._do_level_up(interaction, data, 'dwarf', note)
        # Apply Dwarf progression per level (DCC-style: uses Mighty Deeds like Warriors; no expanded threat by default)
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        # Dwarf progression table (per provided spec)
        DWARF_TABLE: dict[int, dict] = {
            1:  {"deed": "d3",       "crit": "1d10", "crit_tbl": "III", "act": "1d20",             "ref": 1, "fort": 1, "will": 1},
            2:  {"deed": "d4",       "crit": "1d12", "crit_tbl": "III", "act": "1d20",             "ref": 1, "fort": 1, "will": 1},
            3:  {"deed": "d5",       "crit": "1d14", "crit_tbl": "III", "act": "1d20",             "ref": 1, "fort": 2, "will": 1},
            4:  {"deed": "d6",       "crit": "1d16", "crit_tbl": "IV",  "act": "1d20",             "ref": 2, "fort": 2, "will": 2},
            5:  {"deed": "d7",       "crit": "1d20", "crit_tbl": "IV",  "act": "1d20+1d14",       "ref": 2, "fort": 3, "will": 2},
            6:  {"deed": "d8",       "crit": "1d24", "crit_tbl": "V",   "act": "1d20+1d16",       "ref": 2, "fort": 4, "will": 2},
            7:  {"deed": "d10+1",    "crit": "1d30", "crit_tbl": "V",   "act": "1d20+1d20",       "ref": 3, "fort": 4, "will": 3},
            8:  {"deed": "d10+2",    "crit": "1d30", "crit_tbl": "V",   "act": "1d20+1d20",       "ref": 3, "fort": 5, "will": 3},
            9:  {"deed": "d10+3",    "crit": "2d20", "crit_tbl": "V",   "act": "1d20+1d20",       "ref": 3, "fort": 5, "will": 3},
            10: {"deed": "d10+4",    "crit": "2d20", "crit_tbl": "V",   "act": "1d20+1d20+1d14",  "ref": 4, "fort": 6, "will": 4},
        }
        row = DWARF_TABLE.get(lvl)
        if row:
            # Dwarves use Mighty Deeds; keep static attack bonus at 0 (deed adds to attack/damage)
            data['attack_bonus'] = 0
            data['deed_die'] = str(row['deed'])
            # Crit die and table per provided progression
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            # Action dice: keep action_die = 1d20 for compatibility; store full multi-die string separately
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            # Saves: store class bonuses and recompute displayed saves with abilities and augur
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            self._recompute_saves(data)
            # Titles by alignment for Dwarf levels 1..5
            try:
                align = self._normalize_alignment(str(data.get('alignment')))
            except Exception:
                align = 'neutral'
            # Dwarf titles by alignment (levels 1-4) — per provided table
            DWARF_TITLES: dict[int, dict[str, str]] = {
                1: {"lawful": "Agent",     "chaotic": "Rebel",       "neutral": "Apprentice"},
                2: {"lawful": "Broker",    "chaotic": "Dissident",   "neutral": "Novice"},
                3: {"lawful": "Delegate",  "chaotic": "Exile",       "neutral": "Journeyer"},
                4: {"lawful": "Envoy",     "chaotic": "Iconoclast",  "neutral": "Crafter"},
            }
            if lvl in DWARF_TITLES:
                tmap = DWARF_TITLES.get(lvl, {})
                ttl = tmap.get(align) or tmap.get('neutral')
                if ttl:
                    data['title'] = ttl
            # At Dwarf level 1, set hit die display and grant core weapon training set (idempotent union)
            if lvl == 1:
                data['hit_die'] = '1d10'
                # Racial traits: Dwarves are slow (20 ft) and have infravision 60'
                try:
                    data['speed'] = 20
                except Exception:
                    pass
                try:
                    data['infravision'] = 60
                except Exception:
                    pass
                # Do not auto-set Luck weapon; allow the player to choose any weapon type via /set luck_weapon
                try:
                    default_weapons = [
                        'battleaxe','hand axe','warhammer','mace','club','short sword','longsword','staff'
                    ]
                    wp: set[str] = set()
                    for w in (data.get('weapon_training') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in (data.get('weapon_proficiencies') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in default_weapons:
                        wp.add(w)
                    data['weapon_training'] = sorted([w for w in wp if w])
                    data['weapon_proficiencies'] = list(data['weapon_training'])
                except Exception:
                    pass
            # At first level, prompt for fixed Luck weapon if not set yet
            if lvl == 1 and not data.get('dwarf_luck_weapon'):
                sel = await self._choose_weapon(interaction, "Dwarf Level 1")
                if sel:
                    lck_mod = self._ability_mod(data, 'LCK')
                    data['dwarf_luck_weapon'] = sel.strip().lower()
                    data['dwarf_luck_weapon_mod'] = int(lck_mod)
                await self._save_record(data.get('name') or name, data)
            else:
                await self._save_record(data.get('name') or name, data)
            # Summary
            lines = [
                f"Dwarf Level {lvl} features updated:",
                f"• Deed Die: {row['deed']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            if lvl == 1:
                lines.append("• Speed: 20 ft (racial)")
                lines.append("• Infravision: 60’ (racial)")
                try:
                    if data.get('dwarf_luck_weapon'):
                        lines.append(f"• Luck Weapon: {data.get('dwarf_luck_weapon')} ({int(data.get('dwarf_luck_weapon_mod',0)):+} to attack; fixed at first level)")
                    else:
                        lines.append("• Luck Weapon: not set — use /set luck_weapon to choose any weapon type (fixed at first level)")
                except Exception:
                    pass
                # Languages at 1st level: Common, Dwarf, plus 1 random + INT modifier additional from dwarf table
                try:
                    langs = list(data.get('languages') or [])
                    # ensure list of strings
                    langs = [str(x) for x in langs if isinstance(x, (str, int))]
                except Exception:
                    langs = []
                # Always include Common and Dwarf
                def _have(lang: str) -> bool:
                    try:
                        return lang.lower() in {x.lower() for x in langs}
                    except Exception:
                        return False
                if not _have('Common'):
                    langs.append('Common')
                if not _have('Dwarf'):
                    langs.append('Dwarf')
                # Revised: Demi-human (Dwarf) gets ONLY one additional language beyond Common + Dwarf (ignoring INT mod)
                rolls = 1
                gained: list[str] = []
                for _ in range(rolls):
                    lang = self._lang_from_table(DWARF_LANGUAGE_TABLE)
                    if lang == 'by_alignment':
                        al = self._normalize_alignment(str(data.get('alignment')))
                        if al == 'lawful':
                            lang = 'Law'
                        elif al == 'chaotic':
                            lang = 'Chaos'
                        else:
                            lang = 'Neutrality'
                    if isinstance(lang, str) and lang:
                        # avoid duplicates
                        if not _have(lang):
                            langs.append(lang)
                            gained.append(lang)
                try:
                    data['languages'] = sorted(langs)
                    await self._save_record(data.get('name') or name, data)
                    if gained:
                        lines.append("• Languages: " + ", ".join(gained))
                    else:
                        lines.append("• Languages: (no new; Common and Dwarf ensured)")
                except Exception:
                    pass
            if data.get('title'):
                lines.append(f"• Title: {data.get('title')}")
            if lvl == 1 and data.get('weapon_training'):
                lines.append("• Weapon Training: " + ", ".join(data['weapon_training'])[:900])
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

    @levelup.command(name="elf", description="Level up an Elf")
    async def levelup_elf(self, interaction: discord.Interaction, name: str, note: str | None = None, randomize: bool | None = False):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        await self._do_level_up(interaction, data, 'elf', note)
        # Apply Elf progression (attack, crit, action dice, saves) and caster flow (patron, spells)
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        # Elf progression table (Crit Table II, martial-ish action dice, arcane caster)
        ELF_TABLE: dict[int, dict] = {
            # atk, crit, crit_tbl, act, ref, fort, will, known (by spell level), max_lvl
            1:  {"atk": 1, "crit": "1d6",  "crit_tbl": "II", "act": "1d20",             "ref": 1, "fort": 0, "will": 1, "known": {1:3, 2:0, 3:0, 4:0, 5:0}, "max": 1},
            2:  {"atk": 1, "crit": "1d6",  "crit_tbl": "II", "act": "1d20",             "ref": 1, "fort": 0, "will": 1, "known": {1:4, 2:0, 3:0, 4:0, 5:0}, "max": 1},
            3:  {"atk": 2, "crit": "1d8",  "crit_tbl": "II", "act": "1d20",             "ref": 1, "fort": 1, "will": 2, "known": {1:4, 2:2, 3:0, 4:0, 5:0}, "max": 2},
            4:  {"atk": 2, "crit": "1d8",  "crit_tbl": "II", "act": "1d20",             "ref": 2, "fort": 1, "will": 2, "known": {1:5, 2:3, 3:0, 4:0, 5:0}, "max": 2},
            5:  {"atk": 3, "crit": "1d10", "crit_tbl": "II", "act": "1d20+1d14",       "ref": 2, "fort": 2, "will": 3, "known": {1:5, 2:4, 3:1, 4:0, 5:0}, "max": 3},
            6:  {"atk": 3, "crit": "1d10", "crit_tbl": "II", "act": "1d20+1d16",       "ref": 2, "fort": 2, "will": 4, "known": {1:6, 2:4, 3:2, 4:0, 5:0}, "max": 3},
            7:  {"atk": 4, "crit": "1d12", "crit_tbl": "II", "act": "1d20+1d20",       "ref": 3, "fort": 2, "will": 5, "known": {1:6, 2:5, 3:3, 4:1, 5:0}, "max": 4},
            8:  {"atk": 4, "crit": "1d12", "crit_tbl": "II", "act": "1d20+1d20",       "ref": 3, "fort": 3, "will": 5, "known": {1:7, 2:5, 3:4, 4:2, 5:0}, "max": 4},
            9:  {"atk": 5, "crit": "1d14", "crit_tbl": "II", "act": "1d20+1d20",       "ref": 3, "fort": 3, "will": 6, "known": {1:7, 2:6, 3:4, 4:3, 5:1}, "max": 5},
            10: {"atk": 5, "crit": "1d16", "crit_tbl": "II", "act": "1d20+1d20+1d14",  "ref": 4, "fort": 3, "will": 6, "known": {1:8, 2:6, 3:5, 4:4, 5:2}, "max": 5},
        }
        row = ELF_TABLE.get(lvl)
        learned: list[str] = []
        new_entries: list[dict] = []
        if row:
            data['attack_bonus'] = int(row['atk'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            # Titles for Elves (non-alignment-based)
            ELF_TITLES: dict[int, str] = {
                1: 'Wanderer',
                2: 'Seer',
                3: 'Quester',
                4: 'Savant',
                5: 'Elder',
            }
            if lvl in ELF_TITLES:
                data['title'] = ELF_TITLES[lvl]
            # Hit die at 1st level
            if lvl == 1:
                data['hit_die'] = '1d6'
                # Grant core weapon training set for Elves
                try:
                    default_weapons = [
                        'dagger','javelin','longbow','longsword','shortbow','short sword','staff','spear','two-handed sword'
                    ]
                    wp: set[str] = set()
                    for w in (data.get('weapon_training') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in (data.get('weapon_proficiencies') or []):
                        try:
                            wp.add(str(w).strip().lower())
                        except Exception:
                            continue
                    for w in default_weapons:
                        wp.add(w)
                    data['weapon_training'] = sorted([w for w in wp if w])
                    data['weapon_proficiencies'] = list(data['weapon_training'])
                except Exception:
                    pass
                # Racial traits
                try:
                    # Infravision 60'
                    data['infravision'] = 60
                except Exception:
                    pass
                # Immunities: magical sleep and paralysis
                try:
                    imm = data.get('immunities')
                    if not isinstance(imm, list):
                        imm = []
                    base = {str(x).strip().lower() for x in imm}
                    for t in ("magical sleep", "paralysis"):
                        if t not in base:
                            imm.append(t)
                            base.add(t)
                    data['immunities'] = imm
                except Exception:
                    pass
                # Vulnerability to iron: store a normalized rule description; merge into list
                try:
                    vul = data.get('vulnerabilities')
                    if not isinstance(vul, list):
                        vul = []
                    desc = (
                        "Sensitivity to iron: prolonged direct contact causes 1 hp damage per day; "
                        "cannot wear iron armor or bear iron weapons for extended periods; uncomfortable near iron."
                    )
                    if all(str(v).strip().lower() != desc.strip().lower() for v in vul):
                        vul.append(desc)
                    data['vulnerabilities'] = vul
                except Exception:
                    pass
                # Heightened senses for secret doors
                try:
                    senses = data.get('senses')
                    if not isinstance(senses, dict):
                        senses = {}
                    senses['secret_doors_bonus'] = 4
                    senses['secret_doors_auto_check_range_ft'] = 10
                    data['senses'] = senses
                except Exception:
                    pass
            # Update displayed saves with abilities and augur
            self._recompute_saves(data)
            await self._save_record(data.get('name') or name, data)
            # Patron selection at level 1 if not already set
            if lvl == 1 and not str(data.get('patron') or '').strip():
                p_data = self._load_patrons_data()
                names = self._patron_names(p_data)
                chosen_patron = await self._choose_patron(interaction, names, bool(randomize)) if names else None
                if chosen_patron:
                    data['patron'] = chosen_patron
                    notes = data.setdefault('notes', {})
                    plog = notes.setdefault('patron_log', [])
                    if isinstance(plog, list):
                        plog.append({'level': lvl, 'patron': chosen_patron, 'by': int(interaction.user.id)})
                    await self._save_record(data.get('name') or name, data)
                    try:
                        await interaction.followup.send(f"🔮 Patron: {chosen_patron}")
                    except Exception:
                        pass
            # Ensure spells known meet targets, adjusted by INT and gated by max spell level
            spells_data = self._load_spells_data()
            spells_bucket = data.setdefault('spells', {})
            # Elves automatically receive Patron bond and Invoke patron at 1st level (in addition to other spells)
            if lvl == 1:
                ensure_names = ["Patron bond", "Invoke patron"]
                key1 = 'level_1'
                known_list_1: list[dict] = list(spells_bucket.get(key1, [])) if isinstance(spells_bucket.get(key1), list) else []
                known_names_1 = {str(s.get('name')).strip().lower() for s in known_list_1 if isinstance(s, dict)}
                ensured_any = False
                for nm in ensure_names:
                    if nm.strip().lower() not in known_names_1:
                        # Use wizard payload shape if available; otherwise minimal entry
                        try:
                            entry = self._wizard_spell_payload(spells_data, 1, nm)
                        except Exception:
                            entry = {'name': nm}
                        known_list_1.append(entry)
                        new_entries.append(entry)
                        ensured_any = True
                if ensured_any:
                    spells_bucket[key1] = known_list_1
                    learned.append("L1: " + ", ".join(ensure_names))
            try:
                abl = data.get('abilities', {}).get('INT', {}) or {}
                int_score = int(abl.get('current', abl.get('score', 0)) or 0)
            except Exception:
                int_score = 0
            try:
                from modules.utils import wizard_spells_known_adjustment  # type: ignore
                int_adj = int(wizard_spells_known_adjustment(int_score))
            except Exception:
                int_adj = 0
            max_lvl = int(row.get('max', 1))
            for s_lvl in (1,2,3,4,5):
                base_target = int(row['known'].get(s_lvl, 0))
                if base_target <= 0:
                    continue
                if s_lvl > max_lvl:
                    continue
                target = base_target + int_adj
                if target < 1:
                    target = 1
                key = f'level_{s_lvl}'
                known_list: list[dict] = list(spells_bucket.get(key, [])) if isinstance(spells_bucket.get(key), list) else []
                have = len([1 for s in known_list if isinstance(s, dict)])
                need = max(0, target - have)
                if need <= 0:
                    continue
                pool_all = self._wizard_spell_names(spells_data, s_lvl)
                known_names = {str(s.get('name')).strip() for s in known_list if isinstance(s, dict)}
                pool = [nm for nm in pool_all if nm not in known_names]
                # Interactive choose up to 'need' spells (random fallback when not interactive)
                chosen_names = await self._choose_spells(interaction, f'Level {s_lvl}', pool, need, False)
                pick = chosen_names[:]
                for nm in chosen_names:
                    entry = self._wizard_spell_payload(spells_data, s_lvl, nm)
                    known_list.append(entry)
                    new_entries.append(entry)
                spells_bucket[key] = known_list
                if pick:
                    learned.append(f"L{s_lvl}: {', '.join(pick)}")
            # Grant patron-specific spells now (so they can be selectable as Favorite Spell at L1)
            try:
                allowed_levels = [lvl for lvl, cnt in row.get('known', {}).items() if int(cnt or 0) > 0 and (lvl <= max_lvl)]
                patron_added = self._grant_patron_spells(data, allowed_levels)
                if patron_added:
                    learned.append("Patron: " + "; ".join(patron_added))
                    await self._save_record(data.get('name') or name, data)
            except Exception:
                pass
            # Elves do not receive Mercurial Magic
            # After choosing spells (at 1st level), prompt to choose a Favorite Spell (fixed Luck applies)
            fav_selected: str | None = None
            if lvl == 1 and not str(data.get('elf_favorite_spell') or '').strip():
                # Build candidate list from spells learned this session; fallback to all known L1 spells
                cand: list[str] = []
                try:
                    for e in new_entries:
                        nm = str((e or {}).get('name') or '').strip()
                        if nm:
                            cand.append(nm)
                except Exception:
                    cand = []
                if not cand:
                    try:
                        arr = data.get('spells', {}).get('level_1', [])
                        if isinstance(arr, list):
                            for e in arr:
                                nm = str((e or {}).get('name') or '').strip()
                                if nm:
                                    cand.append(nm)
                    except Exception:
                        pass
                # Dedup and cap to 25 for the select control
                uniq = []
                seen = set()
                for x in cand:
                    k = x.lower()
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(x)
                if uniq:
                    if bool(randomize):
                        fav_selected = random.choice(uniq)
                    else:
                        chosen: list[str] = []

                        class FavSelect(discord.ui.Select):
                            def __init__(self, opts: Iterable[str]):
                                options = [discord.SelectOption(label=o, value=o) for o in opts]
                                super().__init__(
                                    placeholder="Choose favorite spell (Luck applies)",
                                    min_values=1,
                                    max_values=1,
                                    options=options[:25],
                                )

                            async def callback(self, itx: discord.Interaction):
                                nonlocal chosen
                                chosen = list(self.values)
                                await itx.response.defer()

                        class FavView(discord.ui.View):
                            def __init__(self):
                                super().__init__(timeout=120)
                                self.done = False

                            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
                            async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
                                if itx.user.id != interaction.user.id:
                                    await itx.response.send_message("Not your selection.", ephemeral=True)
                                    return
                                if len(chosen) != 1:
                                    await itx.response.send_message("Please pick one spell.", ephemeral=True)
                                    return
                                self.done = True
                                await itx.response.edit_message(content=f"Favorite spell selected: {chosen[0]}", view=None)
                                self.stop()

                            @discord.ui.button(label="Random", style=discord.ButtonStyle.secondary)
                            async def random_btn(self, itx: discord.Interaction, button: discord.ui.Button):
                                if itx.user.id != interaction.user.id:
                                    await itx.response.send_message("Not your selection.", ephemeral=True)
                                    return
                                nonlocal chosen
                                chosen = [random.choice(uniq)]
                                self.done = True
                                await itx.response.edit_message(content=f"Rolled random favorite spell: {chosen[0]}", view=None)
                                self.stop()

                        view = FavView()
                        view.add_item(FavSelect(uniq))
                        sent = False
                        try:
                            await interaction.followup.send(
                                "Pick your Favorite Spell (Luck modifier applies to spell checks for this spell; fixed at 1st level)",
                                view=view,
                                ephemeral=True,
                            )
                            sent = True
                        except Exception:
                            try:
                                await interaction.response.send_message(
                                    "Pick your Favorite Spell (Luck modifier applies to spell checks for this spell; fixed at 1st level)",
                                    view=view,
                                    ephemeral=True,
                                )
                                sent = True
                            except Exception:
                                fav_selected = random.choice(uniq)
                        if sent:
                            await view.wait()
                            if view.done and chosen:
                                fav_selected = chosen[0]
                            elif not view.done:
                                fav_selected = random.choice(uniq)
                if fav_selected:
                    data['elf_favorite_spell'] = fav_selected
                    try:
                        lck_mod_now = int(self._ability_mod(data, 'LCK'))
                    except Exception:
                        lck_mod_now = 0
                    data['elf_favorite_spell_luck_mod'] = int(lck_mod_now)
                    await self._save_record(data.get('name') or name, data)
                    try:
                        await interaction.followup.send(f"⭐ Favorite Spell set: {fav_selected} (Luck {lck_mod_now:+} to spell checks; fixed)", ephemeral=True)
                    except Exception:
                        pass
            # Summary
            lines = [
                f"Elf Level {lvl} features updated:",
                f"• Attack Bonus: +{row['atk']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
            ]
            if data.get('title'):
                lines.append(f"• Title: {data.get('title')}")
            if learned:
                lines.append("• Spells known updated: " + "; ".join(learned))
            if lvl == 1 and data.get('weapon_training'):
                lines.append("• Weapon Training: " + ", ".join(data['weapon_training'])[:900])
            if lvl == 1:
                lines.append("• Infravision: 60’")
                lines.append("• Immunities: magical sleep; paralysis")
                lines.append("• Vulnerability: sensitivity to iron (1 hp/day of direct contact; cannot wear iron armor or bear iron weapons for long)")
                lines.append("• Heightened Senses: +4 to detect secret doors; automatic check within 10’ when passing")
                if data.get('elf_favorite_spell') is not None:
                    try:
                        fav = str(data.get('elf_favorite_spell') or '')
                        lmod = int(data.get('elf_favorite_spell_luck_mod', 0) or 0)
                        lines.append(f"• Favorite Spell: {fav} (Luck {lmod:+} to spell checks; fixed at 1st level)")
                    except Exception:
                        pass
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass

        # First-level languages for Elves (Common + Elf + 1 + INT mod additional)
        if lvl == 1:
            try:
                langs = list(data.get('languages') or [])
                langs = [str(x) for x in langs if isinstance(x, (str, int))]
            except Exception:
                langs = []
            have = {x.lower() for x in langs}
            if 'common' not in have:
                langs.append('Common')
                have.add('common')
            if 'elf' not in have:
                langs.append('Elf')
                have.add('elf')
            gained: list[str] = []
            try:
                int_mod = int(self._ability_mod(data, 'INT'))
            except Exception:
                int_mod = 0
            # Revised: Elf gets ONLY one additional language beyond Common + Elf (ignoring INT mod)
            rolls = 1
            safety = 0
            while rolls > 0 and safety < 300:
                safety += 1
                lang = self._lang_from_table(ELF_LANGUAGE_TABLE)
                if not lang:
                    continue
                if lang == 'by_alignment':
                    al = self._normalize_alignment(str(data.get('alignment')))
                    lang = {'lawful': 'Law', 'chaotic': 'Chaos'}.get(al, 'Neutrality')
                if isinstance(lang, str) and lang:
                    lo = lang.lower()
                    if lo in have:
                        continue
                    langs.append(lang)
                    gained.append(lang)
                    have.add(lo)
                    rolls -= 1
            data['languages'] = sorted(langs)
            await self._save_record(data.get('name') or name, data)
            if gained:
                try:
                    await interaction.followup.send(f"🗣️ Languages learned (Elf L1): {', '.join(gained)}", ephemeral=True)
                except Exception:
                    pass

    @levelup.command(name="halfling", description="Level up a Halfling")
    async def levelup_halfling(self, interaction: discord.Interaction, name: str, note: str | None = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        await self._do_level_up(interaction, data, 'halfling', note)
        # Apply Halfling progression (attack, crit, action dice, saves, luck die)
        try:
            lvl = int((data.get('level', 0) or 0))
        except Exception:
            lvl = 0
        # Table 1-18 (Halfling): attack bonus, crit die, crit table III, action dice, Luck die, and save bonuses
        # Halfling progression per provided spec (Attack, Crit Die/Table III, Action Dice, Saves)
        HALFLING_TABLE: dict[int, dict] = {
            1:  {"atk": 1, "crit": "1d8",  "crit_tbl": "III", "act": "1d20",            "luck": "d3",  "ref": 1, "fort": 1, "will": 1, "sneak_hide": 3},
            2:  {"atk": 2, "crit": "1d8",  "crit_tbl": "III", "act": "1d20",            "luck": "d4",  "ref": 1, "fort": 1, "will": 1, "sneak_hide": 5},
            3:  {"atk": 2, "crit": "1d10", "crit_tbl": "III", "act": "1d20",            "luck": "d5",  "ref": 2, "fort": 1, "will": 2, "sneak_hide": 7},
            4:  {"atk": 3, "crit": "1d10", "crit_tbl": "III", "act": "1d20",            "luck": "d6",  "ref": 2, "fort": 2, "will": 2, "sneak_hide": 8},
            5:  {"atk": 4, "crit": "1d12", "crit_tbl": "III", "act": "1d20",            "luck": "d7",  "ref": 3, "fort": 2, "will": 3, "sneak_hide": 9},
            6:  {"atk": 5, "crit": "1d12", "crit_tbl": "III", "act": "1d20+1d14",     "luck": "d8",  "ref": 4, "fort": 2, "will": 4, "sneak_hide": 11},
            7:  {"atk": 5, "crit": "1d14", "crit_tbl": "III", "act": "1d20+1d16",     "luck": "d10", "ref": 4, "fort": 3, "will": 4, "sneak_hide": 12},
            8:  {"atk": 6, "crit": "1d14", "crit_tbl": "III", "act": "1d20+1d20",     "luck": "d12", "ref": 5, "fort": 3, "will": 5, "sneak_hide": 13},
            9:  {"atk": 7, "crit": "1d16", "crit_tbl": "III", "act": "1d20+1d20",     "luck": "d14", "ref": 5, "fort": 3, "will": 5, "sneak_hide": 14},
            10: {"atk": 8, "crit": "1d16", "crit_tbl": "III", "act": "1d20+1d20",     "luck": "d16", "ref": 6, "fort": 4, "will": 6, "sneak_hide": 15},
        }
        row = HALFLING_TABLE.get(lvl)
        if row:
            data['attack_bonus'] = int(row['atk'])
            data['crit_die'] = str(row['crit'])
            data['crit_table'] = str(row['crit_tbl'])
            # Keep 'action_die' as 1d20 for attack roll compatibility; store full action dice in 'action_dice'
            data['action_die'] = '1d20'
            data['action_dice'] = str(row['act'])
            # Halfling Luck die scales by level
            data['luck_die'] = str(row['luck'])
            # Class save bonuses
            data['class_saves'] = {
                'reflex': int(row['ref']),
                'fortitude': int(row['fort']),
                'will': int(row['will']),
            }
            # Recompute total saves (ability + class + augur)
            self._recompute_saves(data)
            # Halfling titles (all alignments share the same title), levels 1..5
            HALFLING_TITLES_SIMPLE: dict[int, str] = {
                1: "Wanderer",
                2: "Explorer",
                3: "Collector",
                4: "Accumulator",
                5: "Wise one",
            }
            if lvl in HALFLING_TITLES_SIMPLE:
                data['title'] = HALFLING_TITLES_SIMPLE[lvl]
            # First-level racial fixtures
            if lvl == 1:
                data['hit_die'] = '1d6'
                # Typical demi-human movement and senses
                try:
                    data['speed'] = 20
                except Exception:
                    pass
                # Halfling infravision: 30'
                try:
                    cur_inf = int(data.get('infravision') or 0)
                except Exception:
                    cur_inf = 0
                if cur_inf < 30:
                    data['infravision'] = 30
                # Flag for Halfling TWF behavior (handled in combat logic)
                try:
                    traits = data.get('traits') if isinstance(data.get('traits'), list) else []
                    label = 'Two-weapon fighting (AGI 16 min; -1 die on both attacks)'
                    low = label.strip().lower()
                    if not any(str(t).strip().lower() == low for t in traits):
                        traits.append(label)
                    data['traits'] = traits
                except Exception:
                    pass
            await self._save_record(data.get('name') or name, data)
            # Summary output
            lines = [
                f"Halfling Level {lvl} features updated:",
                f"• Attack Bonus: +{row['atk']}",
                f"• Crit: {row['crit']} / {row['crit_tbl']}",
                f"• Action Dice: {row['act']} (action_die kept as 1d20 for now)",
                f"• Luck Die: {row['luck']}",
                f"• Saves (class bonus): Ref {row['ref']:+}, Fort {row['fort']:+}, Will {row['will']:+}",
                f"• Sneak & Hide: +{row.get('sneak_hide', 0)}",
            ]
            try:
                if data.get('title'):
                    lines.append(f"• Title: {data.get('title')}")
            except Exception:
                pass
            if lvl == 1:
                lines.append("• Trait: Two-weapon fighting (AGI 16 min; -1 die on both attacks)")
                try:
                    if int(data.get('infravision') or 0) >= 30:
                        lines.append("• Infravision: 30’ (can see in the dark up to 30’)")
                except Exception:
                    pass
            try:
                await interaction.followup.send("\n".join(lines))
            except Exception:
                try:
                    await interaction.response.send_message("\n".join(lines), ephemeral=True)
                except Exception:
                    pass
        # Grant a language at first level (ensure Common and Halfling, then add 1 from HALFLING_LANGUAGE_TABLE)
        if lvl == 1:
            try:
                langs = list(data.get('languages') or [])
                langs = [str(x) for x in langs if isinstance(x, (str, int))]
            except Exception:
                langs = []
            have = {x.lower() for x in langs}
            if 'common' not in have:
                langs.append('Common')
                have.add('common')
            if 'halfling' not in have:
                langs.append('Halfling')
                have.add('halfling')
            gained: list[str] = []
            # Demi-humans: one additional language beyond Common+racial+INT-mod languages
            try:
                int_mod = int(self._ability_mod(data, 'INT'))
            except Exception:
                int_mod = 0
            # Revised: Halfling gets ONLY one additional language beyond Common + Halfling (ignoring INT mod)
            rolls = 1
            safety = 0
            while rolls > 0 and safety < 300:
                safety += 1
                lang = self._lang_from_table(HALFLING_LANGUAGE_TABLE)
                if not lang:
                    continue
                if lang == 'by_alignment':
                    al = self._normalize_alignment(str(data.get('alignment')))
                    lang = {'lawful': 'Law', 'chaotic': 'Chaos'}.get(al, 'Neutrality')
                if isinstance(lang, str) and lang:
                    lo = lang.lower()
                    if lo in have:
                        continue
                    langs.append(lang)
                    gained.append(lang)
                    have.add(lo)
                    rolls -= 1
            data['languages'] = sorted(langs)
            await self._save_record(data.get('name') or name, data)
            if gained:
                try:
                    await interaction.followup.send(f"🗣️ Languages learned (Halfling L1): {', '.join(gained)}", ephemeral=True)
                except Exception:
                    pass

    # ---- Admin utility: set a Warrior's fixed luck weapon retroactively ----
    @app_commands.command(name="set_warrior_luck_weapon", description="Admin: set a Warrior's fixed luck weapon and modifier")
    @app_commands.describe(
        name="Character name",
        weapon="Exact weapon key (e.g., 'longsword', 'short sword')",
        mod="Luck modifier to fix (defaults to current LCK mod)",
        force="Set even if the weapon is not in inventory"
    )
    async def set_warrior_luck_weapon(self, interaction: discord.Interaction, name: str, weapon: str, mod: Optional[int] = None, force: Optional[bool] = False):
        # Admin/owner check
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        if not is_admin:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(data.get('class','')).strip().lower() != 'warrior':
            await interaction.response.send_message("Target is not a Warrior.", ephemeral=True)
            return
        wkey = (weapon or '').strip().lower()
        if not wkey:
            await interaction.response.send_message("Provide a weapon name.", ephemeral=True)
            return
        # Optionally enforce inventory presence
        if not force:
            inv = [str((it.get('name') if isinstance(it, dict) else it) or '').strip().lower() for it in (data.get('inventory') or [])]
            if wkey not in inv:
                await interaction.response.send_message("Weapon not found in character inventory. Use force:true to override.", ephemeral=True)
                return
        # Compute or accept provided modifier
        if mod is None:
            lmod = self._ability_mod(data, 'LCK')
        else:
            try:
                lmod = int(mod)
            except Exception:
                await interaction.response.send_message("Modifier must be an integer.", ephemeral=True)
                return
        data['warrior_luck_weapon'] = wkey
        data['warrior_luck_weapon_mod'] = int(lmod)
        await self._save_record(data.get('name') or name, data)
        await interaction.response.send_message(f"✅ Luck weapon set: {wkey} ({lmod:+}). It will auto-apply on attacks with this weapon.", ephemeral=True)

    @app_commands.command(name="set_dwarf_luck_weapon", description="Admin: set a Dwarf's fixed luck weapon and modifier")
    @app_commands.describe(
        name="Character name",
        weapon="Exact weapon key (e.g., 'longsword', 'short sword')",
        mod="Luck modifier to fix (defaults to current LCK mod)",
        force="Set even if the weapon is not in inventory"
    )
    async def set_dwarf_luck_weapon(self, interaction: discord.Interaction, name: str, weapon: str, mod: Optional[int] = None, force: Optional[bool] = False):
        # Admin/owner check
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        if not is_admin:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        if str(data.get('class','')).strip().lower() != 'dwarf':
            await interaction.response.send_message("Target is not a Dwarf.", ephemeral=True)
            return
        wkey = (weapon or '').strip().lower()
        if not wkey:
            await interaction.response.send_message("Provide a weapon name.", ephemeral=True)
            return
        # Optionally enforce inventory presence
        if not force:
            inv = [str((it.get('name') if isinstance(it, dict) else it) or '').strip().lower() for it in (data.get('inventory') or [])]
            if wkey not in inv:
                await interaction.response.send_message("Weapon not found in character inventory. Use force:true to override.", ephemeral=True)
                return
        # Compute or accept provided modifier
        if mod is None:
            lmod = self._ability_mod(data, 'LCK')
        else:
            try:
                lmod = int(mod)
            except Exception:
                await interaction.response.send_message("Modifier must be an integer.", ephemeral=True)
                return
        data['dwarf_luck_weapon'] = wkey
        data['dwarf_luck_weapon_mod'] = int(lmod)
        await self._save_record(data.get('name') or name, data)
        await interaction.response.send_message(f"✅ Dwarf luck weapon set: {wkey} ({lmod:+}). It will auto-apply on attacks with this weapon.", ephemeral=True)

    # ---- Autocomplete for name on each subcommand ----
    @levelup_cleric.autocomplete('name')
    @levelup_warrior.autocomplete('name')
    @levelup_wizard.autocomplete('name')
    @levelup_thief.autocomplete('name')
    @levelup_dwarf.autocomplete('name')
    @levelup_elf.autocomplete('name')
    @levelup_halfling.autocomplete('name')
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
    await bot.add_cog(LevelUpCog(bot))
