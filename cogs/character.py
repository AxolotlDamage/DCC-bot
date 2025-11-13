import asyncio
import json
import os
import random
import re
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from models.character import Character  # type: ignore
from storage.files import async_load_json, async_save_json  # type: ignore
from modules.utils import get_modifier, ABILITY_ORDER, ability_name, ability_emoji, character_trained_weapons, apply_condition, get_luck_current  # type: ignore
from utils.dice import roll_dice  # type: ignore

CHAR_EXT = '.json'

class CharacterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Helpers
    def _char_path(self, name: str) -> Path:
        safe = name.lower().strip().replace(' ', '_')
        return Path(SAVE_FOLDER) / f"{safe}{CHAR_EXT}"

    async def _load_character(self, name: str) -> Optional[Character]:
        path = self._char_path(name)
        if not path.exists():
            return None
        data = await async_load_json(path)
        return Character.from_dict(data)

    async def _save_character(self, char: Character):
        path = self._char_path(char.name)
        await async_save_json(path, char.to_dict())

    # Raw JSON helpers for editing
    async def _load_record(self, name: str) -> Optional[dict]:
        path = self._char_path(name)
        if not path.exists():
            return None
        try:
            with path.open('r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    async def _save_record(self, name: str, data: dict) -> bool:
        path = self._char_path(name)
        try:
            with path.open('w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            return True
        except Exception:
            return False

    def _build_sheet_embed(self, data: dict) -> discord.Embed:
        # Build display name as: Title Name Occupation (for Clerics by alignment/level), then append level/class
        name = str(data.get('name', 'Unnamed')).strip() or 'Unnamed'
        occupation = str(data.get('occupation') or '').strip()
        char_class = str(data.get('class') or '').strip()
        try:
            level = int(data.get('level', 0) or 0)
        except Exception:
            level = 0
        # Prefer an explicit character title if one is stored (e.g., Warrior 'Squire').
        honorific = str(data.get('title') or '').strip()
        cls_key = char_class.lower()
        # If no explicit title, fall back to class-based mappings for Cleric/Thief.
        if not honorific and cls_key in {'cleric', 'thief'} and level >= 1:
            # Titles by level and alignment
            cleric_titles = {
                1: {'law': 'Acolyte', 'chaos': 'Zealot', 'neutral': 'Witness'},
                2: {'law': 'Heathen-slayer', 'chaos': 'Convert', 'neutral': 'Pupil'},
                3: {'law': 'Brother', 'chaos': 'Cultist', 'neutral': 'Chronicler'},
                4: {'law': 'Curate', 'chaos': 'Apostle', 'neutral': 'Judge'},
                5: {'law': 'Father', 'chaos': 'High priest', 'neutral': 'Druid'},
            }
            thief_titles = {
                1: {'law': 'Bravo', 'chaos': 'Thug', 'neutral': 'Beggar'},
                2: {'law': 'Apprentice', 'chaos': 'Murderer', 'neutral': 'Cutpurse'},
                3: {'law': 'Rogue', 'chaos': 'Cutthroat', 'neutral': 'Burglar'},
                4: {'law': 'Capo', 'chaos': 'Executioner', 'neutral': 'Robber'},
                5: {'law': 'Boss', 'chaos': 'Assassin', 'neutral': 'Swindler'},
            }
            raw_align = str(data.get('alignment') or '').lower()
            align_key = 'neutral'
            if 'law' in raw_align:
                align_key = 'law'
            elif 'chaos' in raw_align or 'chaotic' in raw_align:
                align_key = 'chaos'
            table = cleric_titles if cls_key == 'cleric' else thief_titles
            honorific = table.get(min(level, 5), {}).get(align_key, '')
        display_name = " ".join([s for s in [honorific, name, occupation] if s]).strip()
        title = f"{display_name} — L{level} {char_class}".strip()
        emb = discord.Embed(title=title)
        # Familiar special-case: render a compact familiar sheet and return early
        if cls_key == 'familiar':
            fam = ((data.get('notes') or {}).get('familiar') or {})
            # HP block
            hp_field = data.get('hp', {})
            if isinstance(hp_field, dict):
                cur_hp = hp_field.get('current', hp_field.get('max', 0))
                max_hp = hp_field.get('max', hp_field.get('current', 0))
            else:
                cur_hp = hp_field; max_hp = hp_field
            try:
                cur_hp = int(cur_hp or 0); max_hp = int(max_hp or 0)
            except Exception:
                pass
            atk_bonus = str(data.get('attack_bonus', data.get('attack', '+0')))
            ac_val = data.get('ac', 10)
            emb.add_field(
                name="🐾 Familiar",
                value=(
                    f"Type: {fam.get('type','—')}\n"
                    f"Intelligence: {fam.get('intelligence_score','—')}\n"
                    f"Master: {fam.get('master','—')}"
                ),
                inline=False,
            )
            emb.add_field(name="❤️ HP / 🛡️ AC", value=f"{cur_hp}/{max_hp} / {ac_val}", inline=True)
            emb.add_field(name="⚔️ Attack Bonus", value=str(atk_bonus), inline=True)
            form = fam.get('creature_form','—'); benefit = fam.get('creature_benefit','') or '—'
            emb.add_field(name="🌀 Form", value=f"{form}\nBenefit: {benefit}", inline=False)
            emb.add_field(name="🎭 Personality", value=str(fam.get('personality','—')), inline=True)
            # If the familiar has a damage entry, surface first natural attack
            attacks = data.get('attacks') if isinstance(data.get('attacks'), list) else []
            if attacks:
                first = attacks[0]
                if isinstance(first, dict):
                    dmg = first.get('damage') or '—'
                    emb.add_field(name="🦴 Natural Attack", value=str(dmg), inline=True)
            return emb
        abil = data.get('abilities', {}) or {}
        order = list(ABILITY_ORDER)
        lines = []
        for k in order:
            v = abil.get(k, {})
            if isinstance(v, dict):
                try:
                    cur = int(v.get('current', v.get('max', v.get('score', 0)) or 0))
                except Exception:
                    cur = 0
                try:
                    mx = int(v.get('max', cur))
                except Exception:
                    mx = cur
                try:
                    mod = int(v.get('mod', 0))
                except Exception:
                    mod = 0
            else:
                try:
                    cur = int(v)
                except Exception:
                    cur = 0
                mx = cur
                mod = 0
            label = ability_name(k)
            emoji = ability_emoji(k)
            lines.append(f"{emoji} {label}: {cur}/{mx} ({mod:+})")
        emb.add_field(name="🧬 Abilities", value="\n".join(lines), inline=False)
        hp = data.get('hp', {})
        if isinstance(hp, dict):
            cur_hp = hp.get('current', hp.get('max', 0))
            max_hp = hp.get('max', hp.get('current', 0))
            status_suffix = ''
            if int(cur_hp or 0) == 0:
                if data.get('dead'):
                    status_suffix = ' (DEAD)'
                elif isinstance(data.get('dying'), dict):
                    rem = data.get('dying', {}).get('remaining_turns')
                    if rem is not None:
                        status_suffix = f" (DYING {int(rem)} turns left)"
                    else:
                        status_suffix = ' (DYING)'
            hp_text = f"{cur_hp}/{max_hp}{status_suffix}"
        else:
            hp_text = str(hp)
        ac = data.get('ac', 10)
        saves = data.get('saves', {}) or {}
        sv_text = f"R {int(saves.get('reflex',0)):+} / F {int(saves.get('fortitude',0)):+} / W {int(saves.get('will',0)):+}"
        emb.add_field(name="❤️ HP", value=str(hp_text), inline=True)
        emb.add_field(name="🛡️ AC", value=str(ac), inline=True)
        emb.add_field(name="🛡️ Saves", value=sv_text, inline=True)
        # Cleric Disapproval (if applicable)
        try:
            cls = str(data.get('class') or '').strip().lower()
        except Exception:
            cls = ''
        if cls == 'cleric':
            def _int_or_none(x):
                try:
                    return int(x)
                except Exception:
                    return None
            dis_val = None
            # Common keys where disapproval may be stored
            for key in ('disapproval_range', 'disapproval', 'disapprovalRange'):
                v = data.get(key)
                if v is None and isinstance(data.get('notes'), dict):
                    v = data['notes'].get(key)
                if isinstance(v, dict):
                    # support possible structure like {'current': n}
                    dis_val = _int_or_none(v.get('current')) or _int_or_none(v.get('max')) or _int_or_none(v.get('min'))
                elif v is not None:
                    dis_val = _int_or_none(v)
                if dis_val is not None:
                    break
            emb.add_field(name="🕯️ Disapproval", value=str(dis_val) if dis_val is not None else '—', inline=True)
        # Thief skills (compact summary)
        if cls == 'thief':
            ts = data.get('thief_skills') or {}
            skills = ts.get('skills') if isinstance(ts, dict) else None
            if isinstance(skills, dict) and skills:
                # Pick a compact set of commonly referenced skills
                def _fmt_bonus(v):
                    try:
                        return f"{int(v):+}"
                    except Exception:
                        return str(v)
                parts = []
                # order of display
                key_map = [
                    ('backstab', 'Backstab'),
                    ('sneak_silently', 'Sneak'),
                    ('hide_in_shadows', 'Hide'),
                    ('pick_lock', 'Pick lock'),
                    ('find_trap', 'Find trap'),
                    ('disable_trap', 'Disable'),
                    ('climb_sheer_surfaces', 'Climb'),
                    ('pick_pocket', 'Pick pocket'),
                ]
                for key, label in key_map:
                    if key in skills:
                        parts.append(f"{label} {_fmt_bonus(skills.get(key))}")
                # Scroll casting die
                sc = skills.get('cast_scroll_die')
                if sc:
                    parts.append(f"Scroll {sc}")
                if parts:
                    value = ", ".join(parts)
                    emb.add_field(name="🗝️ Thief Skills", value=value[:1024], inline=False)
        # Alignment display
        alignment = data.get('alignment') or '—'
        emb.add_field(name="⚖️ Alignment", value=str(alignment), inline=True)
        # Patron (Wizards)
        patron = data.get('patron') or '—'
        emb.add_field(name="🔮 Patron", value=str(patron), inline=True)
        # Occupation (Lv0 and stored characters)
        occupation = data.get('occupation') or '—'
        emb.add_field(name="🧱 Occupation", value=str(occupation), inline=True)
        emb.add_field(name="⚡ Init", value=f"{int(data.get('initiative',0)):+}", inline=True)
        # Display effective speed: base speed minus armor/shield speed penalties
        try:
            base_speed = int(data.get('speed', 30) or 30)
        except Exception:
            base_speed = 30
        eff_speed = base_speed
        try:
            from modules.data_constants import ARMOR_TABLE  # type: ignore
            armor_key = str(data.get('armor', 'unarmored') or 'unarmored').lower()
            armor_entry = ARMOR_TABLE.get(armor_key)
            if isinstance(armor_entry, dict):
                pen = int(armor_entry.get('speed_penalty', 0) or 0)
                eff_speed += pen
            if bool(data.get('shield')):
                sp = int(ARMOR_TABLE.get('shield', {}).get('speed_penalty', 0) or 0)
                eff_speed += sp
        except Exception:
            pass
        emb.add_field(name="🏃 Speed", value=f"{int(eff_speed)}", inline=True)
        # Vision/Senses
        try:
            infr = int(data.get('infravision') or 0)
        except Exception:
            infr = 0
        cls_lower = str(data.get('class','') or '').strip().lower()
        if cls_lower == 'halfling' and infr < 30:
            # Fallback for legacy characters: ensure halfling infravision
            infr = 30
        if infr:
            if cls_lower == 'halfling':
                desc = "Halflings dwell in pleasant homes carved from the sod beneath hills; can see in the dark up to 30’."
                emb.add_field(name="👁️ Vision", value=f"Infravision {infr}’\n{desc}", inline=True)
            else:
                emb.add_field(name="👁️ Vision", value=f"Infravision {infr}’", inline=True)
        atk_bonus = int(data.get('attack_bonus', data.get('attack', 0)) or 0)
        action_die = data.get('action_die', '1d20')
        crit = data.get('crit_die', '1d4'); crit_tbl = data.get('crit_table', 'I')
        fumble = data.get('fumble_die', 'd4')
        deed = data.get('deed_die')
        threat = data.get('crit_threat')
        atk_lines = [
            f"Bonus {atk_bonus:+}",
            f"Action {action_die}",
        ]
        if data.get('action_dice'):
            atk_lines.append(f"Action Dice {data.get('action_dice')}")
        if deed:
            atk_lines.append(f"Deed {deed}")
        # Warrior/Dwarf Luck weapon bonus display (fixed at first level)
        try:
            lw = None; lmod = 0
            if str(data.get('class','')).strip().lower() == 'warrior':
                lw = str(data.get('warrior_luck_weapon') or '').strip()
                lmod = int(data.get('warrior_luck_weapon_mod', 0) or 0)
            elif str(data.get('class','')).strip().lower() == 'dwarf':
                lw = str(data.get('dwarf_luck_weapon') or '').strip()
                lmod = int(data.get('dwarf_luck_weapon_mod', 0) or 0)
            if lw:
                atk_lines.append(f"Luck Weapon {lw} ({lmod:+})")
        except Exception:
            pass
        atk_lines.append(f"Crit {crit} ({crit_tbl})" + (f" [{threat}]" if threat else ""))
        atk_lines.append(f"Fumble {fumble}")
        emb.add_field(name="⚔️ Attack", value="\n".join(atk_lines), inline=True)
        # Halfling Sneak & Hide bonus (base from progression + Agility modifier)
        try:
            if str(data.get('class','')).strip().lower() == 'halfling':
                # Base stored or infer from level
                sh_base = None
                if 'sneak_hide' in data:
                    try:
                        sh_base = int(data.get('sneak_hide') or 0)
                    except Exception:
                        sh_base = None
                if sh_base is None:
                    lvl = int(data.get('level',0) or 0)
                    HALFLING_SNEAK_HIDE = {1:3,2:5,3:7,4:8,5:9,6:11,7:12,8:13,9:14,10:15}
                    sh_base = HALFLING_SNEAK_HIDE.get(lvl, 0)
                # Agility modifier
                agi_mod = 0
                abil = data.get('abilities') or {}
                agi = abil.get('AGI')
                if isinstance(agi, dict):
                    try:
                        # Prefer explicit mod if present; otherwise compute from current/score
                        if 'mod' in agi and agi.get('mod') is not None:
                            agi_mod = int(agi.get('mod') or 0)
                        else:
                            score = None
                            for key in ('current','score','max'):
                                if key in agi and agi.get(key) is not None:
                                    score = int(agi.get(key) or 0); break
                            if score is not None:
                                from modules.utils import get_modifier  # type: ignore
                                agi_mod = int(get_modifier(score))
                    except Exception:
                        agi_mod = 0
                elif isinstance(agi, (int,float)):
                    try:
                        from modules.utils import get_modifier  # type: ignore
                        agi_mod = int(get_modifier(int(agi)))
                    except Exception:
                        agi_mod = 0
                total = sh_base + agi_mod
                # Include breakdown for clarity
                breakdown = f"+{total} (base {sh_base} AGI {agi_mod:+})"
                emb.add_field(name="🕵️ Sneak & Hide", value=breakdown, inline=True)
                # Luck die display (Halfling only per request)
                luck_die = data.get('luck_die') or 'd3'
                # Current / Max Luck values (from luck block or abilities LCK)
                cur_luck = None; max_luck = None
                luck_blk = data.get('luck') if isinstance(data.get('luck'), dict) else {}
                try:
                    if luck_blk:
                        cur_luck = int(luck_blk.get('current', luck_blk.get('max', 0)) or 0)
                        max_luck = int(luck_blk.get('max', cur_luck) or cur_luck)
                    else:
                        lck_abil = (data.get('abilities') or {}).get('LCK')
                        if isinstance(lck_abil, dict):
                            cur_luck = int(lck_abil.get('current', lck_abil.get('max', lck_abil.get('score', 0)) or 0))
                            max_luck = int(lck_abil.get('max', cur_luck) or cur_luck)
                except Exception:
                    cur_luck = None; max_luck = None
                luck_val_txt = f"{cur_luck}/{max_luck}" if (cur_luck is not None and max_luck is not None) else "—"
                emb.add_field(
                    name="🍀 Luck",
                    value=f"Die {luck_die} | Luck {luck_val_txt}\nSelf-burn: +2/pt; Donor: +1/pt; Nightly recovery = level",
                    inline=True
                )
        except Exception:
            pass
        weapon = data.get('weapon', '—')
        armor = data.get('armor', 'unarmored')
        shield = "yes" if data.get('shield') else "no"
        emb.add_field(name="🎒 Gear", value=f"Weapon: {weapon}\nArmor: {armor} (shield: {shield})", inline=False)
        # Weapon training (union of class defaults, per-character training, legacy, and Lv0 starting weapon)
        try:
            trained = sorted(character_trained_weapons(data))
            wt_text = ", ".join(trained) if trained else "None"
            wt_text = wt_text[:1024]
            emb.add_field(name="🗡️ Weapon Training", value=wt_text, inline=False)
        except Exception:
            pass
        inv = data.get('inventory', []) or []
        if inv:
            # Render structured entries nicely: name xqty — note
            def _fmt_item(it):
                try:
                    if isinstance(it, dict):
                        nm = str(it.get('name') or it.get('item') or '').strip()
                        if not nm:
                            return None
                        qty = int(it.get('qty', 1) or 1)
                        note = str(it.get('note') or '').strip()
                        base = f"{nm}{(' x'+str(qty)) if qty and qty>1 else ''}"
                        return base + (f" — {note}" if note else '')
                    return str(it)
                except Exception:
                    return str(it)
            lines = [s for s in (_fmt_item(x) for x in inv) if s]
            if lines:
                text = ", ".join(lines)[:1024]
                emb.add_field(name="📦 Inventory", value=text, inline=False)
        # Augur with Luck modifier based on Max Luck (doesn't change with current Luck)
        aug = data.get('birth_augur', {}) or {}
        # Determine the Luck modifier from Max Luck
        luck_mod = None
        try:
            # Preferred: stored at creation
            if 'max_luck_mod' in data and data.get('max_luck_mod') is not None:
                luck_mod = int(data.get('max_luck_mod'))
            else:
                # Fallbacks: compute from abilities or luck containers using their max values
                lck = (abil or {}).get('LCK')
                if isinstance(lck, dict):
                    # Prefer explicit max, otherwise score/current
                    for key in ('max', 'score', 'current'):
                        if key in lck:
                            luck_mod = int(get_modifier(int(lck.get(key) or 0)))
                            break
                elif lck is not None:
                    luck_mod = int(get_modifier(int(lck)))
                else:
                    luck_blk = data.get('luck')
                    if isinstance(luck_blk, dict) and ('max' in luck_blk):
                        luck_mod = int(get_modifier(int(luck_blk.get('max') or 0)))
        except Exception:
            luck_mod = None
        aug_text = f"{aug.get('sign','—')} — {aug.get('effect','—')}"
        if luck_mod is not None:
            aug_text += f"\nLuck modifier (from Max Luck): {luck_mod:+}"
        emb.add_field(name="✨ Augur", value=aug_text, inline=False)
        # Spellbook (Wizard/Mage/Elf) — cleaner per-level layout with bullets and mercurial snippets
        try:
            spells = data.get('spells') or {}
            has_any = any(spells.get(f'level_{i}') for i in range(1, 6))
            if has_any:
                import re as _re
                def _short(txt: str, n: int = 80) -> str:
                    s = _re.sub(r"\s+", " ", str(txt or '').strip())
                    return s[:n] + ("…" if len(s) > n else "")
                # One embed field per level for readability
                for s_lvl in (1,2,3,4,5):
                    arr = spells.get(f'level_{s_lvl}') or []
                    if not isinstance(arr, list) or not arr:
                        continue
                    lines: list[str] = []
                    total = 0
                    for entry in arr:
                        if not isinstance(entry, dict):
                            continue
                        nm = str(entry.get('name') or '').strip()
                        mm = entry.get('mercurial') if isinstance(entry.get('mercurial'), dict) else None
                        if mm and ('roll' in mm):
                            r = mm.get('roll')
                            eff = _short(mm.get('effect') or '', 72)
                            item = f"• {nm} — M{r}: {eff}"
                        else:
                            item = f"• {nm}"
                        # Enforce Discord field value limit of 1024
                        if sum(len(x) + 1 for x in lines) + len(item) > 1024:
                            break
                        lines.append(item)
                    omitted = max(0, len(arr) - len(lines))
                    if omitted:
                        more = f"… and {omitted} more"
                        # If we can fit the 'more' line, append it
                        if sum(len(x) + 1 for x in lines) + len(more) <= 1024:
                            lines.append(more)
                    emb.add_field(name=f"📜 Spells — Level {s_lvl} ({len(arr)})", value="\n".join(lines) if lines else "—", inline=False)
        except Exception:
            pass
        coins = data.get('coins') if isinstance(data.get('coins'), dict) else None
        if coins:
            cp = int(coins.get('cp', 0) or 0); sp = int(coins.get('sp', 0) or 0); gp = int(coins.get('gp', 0) or 0)
            coins_text = f"cp {cp}, sp {sp}, gp {gp}"
        else:
            cp = int(data.get('cp', 0) or 0)
            coins_text = f"cp {cp}"
        langs = data.get('languages', []) or []
        emb.add_field(name="💰 Coins / 🗣️ Languages", value=f"{coins_text} / {', '.join(langs) if langs else '—'}", inline=False)
        return emb

    # Commands
    @commands.command(name='create')
    async def create_character(self, ctx: commands.Context, *, name: str):
        if await self._load_character(name):
            await ctx.reply(f"Character '{name}' already exists.")
            return
        char = Character(name=name)
        await self._save_character(char)
        await ctx.reply(f"Created character '{char.name}'.")
        try:
            await ctx.reply(
                "You've created a character! Remember to set alignment with `/set alignment` and then set languages with `/lang`"
            )
        except Exception:
            pass

    @commands.command(name='delete')
    async def delete_character(self, ctx: commands.Context, *, name: str):
        path = self._char_path(name)
        if not path.exists():
            await ctx.reply(f"Character '{name}' not found.")
            return
        path.unlink(missing_ok=True)
        await ctx.reply(f"Deleted character '{name}'.")

    @commands.command(name='sheet')
    async def sheet_character(self, ctx: commands.Context, *, name: str):
        char = await self._load_character(name)
        if not char:
            await ctx.reply(f"Character '{name}' not found.")
            return
        embed = discord.Embed(title=f"{char.name} Sheet")
        try:
            cur_hp = char.current_hp()
        except Exception:
            cur_hp = 0
        try:
            max_hp = cur_hp
            if isinstance(char.hp, dict):
                max_hp = int(char.hp.get('max', max_hp) or max_hp)
        except Exception:
            max_hp = cur_hp
        embed.add_field(name='HP', value=f"{cur_hp}/{max_hp}")
        embed.add_field(name='Level', value=str(char.level or 0))
        await ctx.reply(embed=embed)

    # --- Slash: 0-level generator ---
    # Keep dice parsing consistent with /roll (supports dlN, k[h|l]N, step +/-d, modifiers)
    DCC_CHAIN = [3,4,5,6,7,8,10,12,14,16,20,24,30]
    ROLL_PATTERN = re.compile(
        r"^\s*([0-9]*)d(\d+)((?:[+-]d\d*)*)(?:(k|dl)([hl]?\d+|[hl]))?(?:([+-])\s*(\d+))?\s*$",
        re.IGNORECASE,
    )

    def _parse_expr(self, text: str):
        m = self.ROLL_PATTERN.match(text)
        if not m:
            return None
        count_raw, sides_raw, steps_cluster, mode_raw, mode_arg_raw, sign, mod_raw = m.groups()
        count = int(count_raw) if count_raw else 1
        sides = int(sides_raw)
        original_sides = sides
        if steps_cluster and sides in self.DCC_CHAIN:
            step_tokens = re.findall(r"([+-])d(\d*)", steps_cluster)
            net_steps = 0
            for sign_tok, num_tok in step_tokens:
                magnitude = int(num_tok) if num_tok else 1
                net_steps += magnitude if sign_tok == '+' else -magnitude
            if net_steps != 0:
                idx = self.DCC_CHAIN.index(sides)
                new_idx = max(0, min(len(self.DCC_CHAIN)-1, idx + net_steps))
                sides = self.DCC_CHAIN[new_idx]
        mode = mode_raw.lower() if mode_raw else None
        mode_arg = mode_arg_raw.lower() if mode_arg_raw else None
        if mode == 'k' and mode_arg:
            if mode_arg in ('h','l'):
                pass
            elif (mode_arg[0] in ('h','l')) and mode_arg[1:].isdigit():
                pass
            elif mode_arg.isdigit():
                pass
            else:
                return None
        if mode == 'dl' and mode_arg and not mode_arg.isdigit():
            return None
        mod = int(mod_raw) if (mod_raw) else None
        return (count, sides, mode, mode_arg, sign, mod, original_sides)

    def _apply_mode(self, rolls, mode, mode_arg):
        if not mode:
            return rolls, []
        sorted_pairs = sorted(enumerate(rolls), key=lambda x: x[1])
        indices = [i for i,_ in sorted_pairs]
        if mode == 'k':
            if mode_arg and mode_arg.startswith('l'):
                n = 1
                tail = mode_arg[1:]
                if tail.isdigit(): n = int(tail)
                kept_indices = indices[:n]
            elif mode_arg and mode_arg.startswith('h'):
                n = 1
                tail = mode_arg[1:]
                if tail.isdigit(): n = int(tail)
                kept_indices = indices[-n:]
            elif mode_arg and mode_arg.isdigit():
                n = int(mode_arg)
                kept_indices = indices[-n:]
            else:
                kept_indices = indices[-1:]
            kept_set = set(kept_indices)
            kept = [rolls[i] for i in range(len(rolls)) if i in kept_set]
            dropped = [rolls[i] for i in range(len(rolls)) if i not in kept_set]
            return kept, dropped
        if mode == 'dl':
            n = int(mode_arg) if (mode_arg and mode_arg.isdigit()) else 1
            if n >= len(rolls):
                n = len(rolls) - 1
            drop_indices = set(indices[:n])
            kept = [rolls[i] for i in range(len(rolls)) if i not in drop_indices]
            dropped = [rolls[i] for i in range(len(rolls)) if i in drop_indices]
            return kept, dropped
        return rolls, []

    def _roll_total(self, expr: str) -> tuple[int, list[int], list[int]]:
        parsed = self._parse_expr(expr)
        if not parsed:
            raise ValueError(f"Invalid dice spec: {expr}")
        count, sides, mode, mode_arg, sign, mod, _orig = parsed
        base = [random.randint(1, sides) for _ in range(count or 1)]
        kept, dropped = self._apply_mode(base, mode, mode_arg)
        subtotal = sum(kept)
        if sign and mod:
            subtotal = subtotal + (mod if sign == '+' else -mod)
        return int(subtotal), kept, dropped

    def _next_char_name(self) -> str:
        # Pick the next available CharN based on files in SAVE_FOLDER, case-insensitive.
        Path(SAVE_FOLDER).mkdir(parents=True, exist_ok=True)
        import re
        pat = re.compile(r"^char(\d+)\.json$", re.IGNORECASE)
        used: list[int] = []
        try:
            for f in os.listdir(SAVE_FOLDER):
                m = pat.match(f)
                if m:
                    try:
                        used.append(int(m.group(1)))
                    except Exception:
                        continue
        except Exception:
            pass
        nxt = max(used, default=0) + 1
        # Ensure we don't overwrite if something else is present; bump until filename is free
        while True:
            candidate = f"Char{nxt}"
            safe = candidate.lower().strip().replace(' ', '_')
            p = Path(SAVE_FOLDER) / f"{safe}{CHAR_EXT}"
            if not p.exists():
                return candidate
            nxt += 1

    @app_commands.command(name="create", description="Create a random level 0 DCC character")
    @app_commands.describe(expression="Ability roll expression (e.g., 3d6, 4d6dl1, 4d6kh3). Default 3d6.")
    async def create_lv0(self, interaction: discord.Interaction, expression: Optional[str] = None):
        spec = (expression or "3d6").strip()
        # Validate once
        try:
            _ = self._parse_expr(spec)
            if _ is None:
                raise ValueError()
        except Exception:
            await interaction.response.send_message(f"Invalid expression '{spec}'. Try '3d6' or '4d6dl1'.", ephemeral=True)
            return
        # Roll abilities
        abilities_order = ["STR","AGI","STA","INT","PER","LCK"]
        stats = {}
        mods = {}
        rolls_detail = {}
        for ab in abilities_order:
            total, kept, dropped = self._roll_total(spec)
            stats[ab] = int(total)
            mods[ab] = int(get_modifier(total))
            rolls_detail[ab] = {"kept": kept, "dropped": dropped}
        max_luck_mod = mods["LCK"]
        # Occupation
        occ_path = Path(__file__).resolve().parents[1] / 'occupations_full.json'
        try:
            with occ_path.open('r', encoding='utf-8') as f:
                occ_data = json.load(f)
        except Exception:
            occ_data = {}
        # Choose 1..100 key
        if occ_data:
            k = str(random.randint(1, 100))
            occ = occ_data.get(k, {})
            occupation = occ.get('name', 'Gongfarmer')
            weapon = occ.get('weapon', 'club (1d4)')
            goods = occ.get('goods', 'sack of night soil')
        else:
            occupation = 'Gongfarmer'
            weapon = 'trowel (1d4)'
            goods = 'sack of night soil'
        weapon_name = weapon.split(' (')[0] if isinstance(weapon, str) else str(weapon)
        inventory = [weapon_name, goods]
        # Augur
        aug_path = Path(__file__).resolve().parents[1] / 'auguries.json'
        try:
            with aug_path.open('r', encoding='utf-8') as f:
                aug = json.load(f)
        except Exception:
            aug = {}
        if aug:
            sign, effect = random.choice(list(aug.items()))
        else:
            sign, effect = ("Harsh winter", "All attack rolls")
        # Base derived
        hp = max(1, random.randint(1, 4) + mods["STA"])  # d4 + STA
        ac = 10 + mods["AGI"]
        reflex = mods["AGI"]
        fort = mods["STA"]
        will = mods["PER"]
        initiative = mods["AGI"]
        speed = 30
        attack = 0
        # Apply augur via luck mod
        if effect in ["Harsh winter", "Pack hunter"]:
            attack += max_luck_mod
        if effect == "Lucky sign":
            reflex += max_luck_mod; fort += max_luck_mod; will += max_luck_mod
        if effect == "Struck by lightning":
            reflex += max_luck_mod
        if effect == "Lived through famine":
            fort += max_luck_mod
        if effect == "Resisted temptation":
            will += max_luck_mod
        if effect == "Charmed house":
            ac += max_luck_mod
        if effect == "Speed of the cobra":
            initiative += max_luck_mod
        if effect == "Bountiful harvest":
            hp += max_luck_mod
        if effect == "Wild child":
            speed += max_luck_mod * 5
        extra_langs = max_luck_mod if effect == "Birdsong" else 0
        # Languages
        languages = []
        if extra_langs > 0:
            known_languages = ["Elvish","Dwarvish","Halfling","Draconic","Infernal","Celestial","Goblin","Orc"]
            pick = max(0, min(len(known_languages), extra_langs))
            if pick:
                languages = random.sample(known_languages, pick)
        # Coin
        cp = sum(random.randint(1, 12) for _ in range(5))
        # Name and alignment
        character_name = self._next_char_name()
        alignment = random.choice(["Lawful","Neutral","Chaotic"])
        # Build full record (raw JSON to preserve wider schema)
        record = {
            "name": character_name,
            "alignment": alignment,
            "class": "Lv0",
            "level": 0,
            "owner": interaction.user.id,
            "occupation": occupation,
            "weapon": weapon_name,
            "inventory": inventory,
            "birth_augur": {"sign": sign, "effect": effect},
            "max_luck_mod": max_luck_mod,
            "hp": {"current": hp, "max": hp},
            "luck": {"current": int(stats.get("LCK", 0)), "max": int(stats.get("LCK", 0))},
            "ac": ac,
            "saves": {"reflex": reflex, "fortitude": fort, "will": will},
            "initiative": initiative,
            "speed": speed,
            "attack": attack,
            "attack_bonus": 0,
            "cp": cp,
            "abilities": {k: {"max": int(stats[k]), "current": int(stats[k]), "mod": int(mods[k])} for k in stats},
            "armor": "unarmored",
            "shield": False,
            "fumble_die": "d4",
            "action_die": "1d20",
            "crit_die": "1d4",
            "crit_table": "I",
            "languages": languages
        }
        # Save
        out_path = self._char_path(character_name)
        try:
            with out_path.open('w', encoding='utf-8') as f:
                json.dump(record, f, indent=4)
        except Exception as e:
            await interaction.response.send_message(f"Failed to save character: {e}", ephemeral=True)
            return
        # Reply with summary and rolls
        lines = [
            f"✅ New level 0 character: **{character_name}**",
            f"Occupation: {occupation}  |  Weapon: {weapon}",
            f"HP {hp}, AC {ac}, Init {initiative:+}, Saves R/F/W {reflex:+}/{fort:+}/{will:+}",
            f"Augur: {sign} — {effect}",
            f"CP: {cp}",
            "Stats: " + ", ".join(
                f"{ability_emoji(ab)} {ability_name(ab)} {stats[ab]} ({mods[ab]:+})".strip()
                for ab in abilities_order
            ),
        ]
        # compact roll details per stat
        detail = " | ".join(
            f"{ab}:{'+'.join(map(str, rolls_detail[ab]['kept']))}"
            + (f" dl({'+'.join(map(str, rolls_detail[ab]['dropped']))})" if rolls_detail[ab]['dropped'] else "")
            for ab in abilities_order
        )
        lines.append("Rolls (" + spec + "): " + detail)
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        try:
            sheet_embed = self._build_sheet_embed(record)
            await interaction.followup.send(embed=sheet_embed, ephemeral=True)
        except Exception:
            pass
        # Friendly reminder after creation
        try:
            await interaction.followup.send(
                "You've created a character! Remember to set alignment with `/set alignment` and then set languages with `/lang`",
                ephemeral=True,
            )
        except Exception:
            pass

    @app_commands.command(name="sheet", description="Show a character sheet")
    @app_commands.describe(name="Character name, e.g., Char1")
    async def sheet_slash(self, interaction: discord.Interaction, name: str):
        path = self._char_path(name)
        if not path.exists():
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        try:
            with path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            await interaction.response.send_message(f"Failed to load character: {e}", ephemeral=True)
            return
        emb = self._build_sheet_embed(data)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # --- Slash: set commands ---
    set = app_commands.Group(name="set", description="Set character fields")
    equip = app_commands.Group(name="equip", description="Equip gear (weapon/armor/shield)")
    inv = app_commands.Group(name="inv", description="Manage inventory (add/remove/set/list)")
    training = app_commands.Group(name="training", description="Manage weapon training (add/remove)", parent=set)

    def _check_owner(self, interaction: discord.Interaction, data: dict) -> Optional[str]:
        owner = data.get('owner')
        if owner is None:
            return None
        if str(owner) != str(interaction.user.id):
            return "You do not own this character."
        return None

    # --- Equip helpers ---
    def _recompute_defense(self, data: dict):
        from modules.data_constants import ARMOR_TABLE
        agi_mod = 0
        try:
            abil = data.get('abilities', {})
            v = abil.get('AGI', {})
            if isinstance(v, dict):
                agi_mod = int(v.get('mod', 0))
            else:
                from modules.utils import get_modifier
                agi_mod = int(get_modifier(int(v)))
        except Exception:
            agi_mod = 0
        armor_name = str(data.get('armor', 'unarmored') or 'unarmored').lower()
        shield_on = bool(data.get('shield'))
        base_ac = 10 + agi_mod
        fumble_die = data.get('fumble_die', 'd4')
        armor_entry = ARMOR_TABLE.get(armor_name)
        if isinstance(armor_entry, dict):
            base_ac += int(armor_entry.get('ac_bonus', 0) or 0)
            fumble_die = armor_entry.get('fumble_die') or fumble_die
        if shield_on:
            base_ac += int(ARMOR_TABLE.get('shield', {}).get('ac_bonus', 0) or 1)
        data['ac'] = int(base_ac)
        if fumble_die:
            data['fumble_die'] = str(fumble_die)

    # --- Inventory helpers ---
    def _normalize_inventory(self, data: dict) -> list[dict]:
        raw = data.get('inventory') or []
        out: list[dict] = []
        for it in raw if isinstance(raw, list) else []:
            try:
                if isinstance(it, dict):
                    nm = str(it.get('name') or it.get('item') or '').strip()
                    if not nm:
                        continue
                    qty = int(it.get('qty', 1) or 1)
                    note = str(it.get('note') or '').strip() or None
                    rec = {'name': nm, 'qty': max(0, qty)}
                    if note:
                        rec['note'] = note
                    # Preserve custom weapon metadata and tags if present
                    if 'weapon' in it and isinstance(it['weapon'], dict):
                        rec['weapon'] = it['weapon']
                    if 'tags' in it and isinstance(it['tags'], (list, tuple)):
                        rec['tags'] = list(it['tags'])
                    out.append(rec)
                else:
                    nm = str(it).strip()
                    if nm:
                        out.append({'name': nm, 'qty': 1})
            except Exception:
                continue
        data['inventory'] = out
        return out

    def _find_item_index(self, inv: list[dict], item_name: str) -> int:
        key = (item_name or '').strip().lower()
        for i, it in enumerate(inv):
            try:
                if isinstance(it, dict) and (it.get('name','').strip().lower() == key):
                    return i
            except Exception:
                continue
        return -1

    # --- Looting ---
    @app_commands.command(name="loot", description="Transfer all items from a dead character's inventory into another character's inventory")
    @app_commands.describe(looter="Your character (receives items)", target="Dead character to loot from")
    async def loot(self, interaction: discord.Interaction, looter: str, target: str):
        # Load looter and check ownership
        recv = await self._load_record(looter)
        if not recv:
            await interaction.response.send_message(f"Character '{looter}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, recv)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        # Load target and validate dead status
        src = await self._load_record(target)
        if not src:
            await interaction.response.send_message(f"Character '{target}' not found.", ephemeral=True)
            return
        if not bool(src.get('dead')):
            await interaction.response.send_message("Target is not marked as dead. You can only loot dead characters.", ephemeral=True)
            return
        # Normalize both inventories
        recv_inv = self._normalize_inventory(recv)
        src_inv = self._normalize_inventory(src)
        if not src_inv:
            await interaction.response.send_message(f"{target} has nothing in their inventory.", ephemeral=True)
            return
        transferred: list[str] = []
        for it in src_inv:
            try:
                nm = str(it.get('name') or '').strip()
                if not nm:
                    continue
                qty = int(it.get('qty', 0) or 0)
                if qty <= 0:
                    continue
                idx = self._find_item_index(recv_inv, nm)
                if idx >= 0:
                    # Merge quantities and metadata conservatively
                    dest = recv_inv[idx]
                    dest['qty'] = int(dest.get('qty', 0) or 0) + qty
                    # If dest missing metadata, adopt from source
                    if 'weapon' not in dest and isinstance(it.get('weapon'), dict):
                        dest['weapon'] = dict(it['weapon'])
                    if 'tags' in it and isinstance(it['tags'], list):
                        dt = dest.get('tags') or []
                        if not isinstance(dt, list):
                            dt = []
                        # Merge unique tags
                        present = {str(x).lower() for x in dt}
                        for t in it['tags']:
                            if str(t).lower() not in present:
                                dt.append(t)
                                present.add(str(t).lower())
                        dest['tags'] = dt
                    if 'note' not in dest and it.get('note'):
                        dest['note'] = it.get('note')
                else:
                    # Take the item wholesale
                    recv_inv.append(dict(it))
                transferred.append(f"{nm} x{qty}")
            except Exception:
                continue
        # Also ensure the dead character's equipped weapon is transferred (if not already in inventory)
        try:
            wkey = str(src.get('weapon') or '').strip()
            if wkey:
                # Did we already transfer this weapon via inventory copy?
                already = any(str(x.get('name','')).strip().lower() == wkey.lower() for x in recv_inv)
                if not already:
                    recv_inv.append({'name': wkey, 'qty': 1})
                    transferred.append(f"{wkey} x1 (equipped)")
                # Unequip from source
                src.pop('weapon', None)
        except Exception:
            pass
        # Clear source inventory (all taken)
        src['inventory'] = []
        # Persist changes
        ok1 = await self._save_record(looter, recv)
        ok2 = await self._save_record(target, src)
        if not (ok1 and ok2):
            await interaction.response.send_message("Failed to save updated inventories.", ephemeral=True)
            return
        if not transferred:
            await interaction.response.send_message(f"Nothing to loot from {target}.", ephemeral=True)
            return
        items_text = ", ".join(transferred)
        await interaction.response.send_message(
            f"🪙 Looted from {target} → {looter}: {items_text}",
            ephemeral=False
        )

    @loot.autocomplete('looter')
    async def loot_looter_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                nm = fn[:-5].replace('_',' ')
                if cur and cur not in nm.lower():
                    continue
                items.append(app_commands.Choice(name=nm, value=nm))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    @loot.autocomplete('target')
    async def loot_target_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                path = os.path.join(SAVE_FOLDER, fn)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        rec = json.load(f)
                except Exception:
                    continue
                if not bool(rec.get('dead')):
                    continue
                nm = rec.get('name') or fn[:-5].replace('_',' ')
                if cur and cur not in str(nm).lower():
                    continue
                items.append(app_commands.Choice(name=str(nm), value=str(nm)))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    # ---- Spellbook helpers ----
    def _load_spells_data(self) -> dict:
        try:
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Spells.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _bucket_for_char(self, data: dict) -> Optional[str]:
        cls = str(data.get('class') or '').strip().lower()
        if cls in {'wizard', 'mage', 'elf'}:
            return 'Wizard Spells'
        if cls in {'cleric'}:
            return 'Cleric Spells'
        return None

    def _lookup_spell_blob(self, spells: dict, name: str, bucket_hint: Optional[str], level_hint: Optional[int]) -> tuple[Optional[str], Optional[int], dict]:
        nm_key = str(name or '').strip()
        if bucket_hint and level_hint in (1,2,3,4,5):
            blob = (
                spells.get('spells', {})
                      .get(bucket_hint, {})
                      .get(f'level {int(level_hint)}', {})
                      .get(nm_key, {})
                or {}
            )
            if blob:
                return bucket_hint, int(level_hint), blob
        # Fall back: search both buckets & levels for exact name
        for b in ['Wizard Spells','Cleric Spells']:
            for lv in (1,2,3,4,5):
                pool = spells.get('spells', {}).get(b, {}).get(f'level {lv}', {}) or {}
                for nm in pool.keys():
                    if str(nm).strip().lower() == nm_key.lower():
                        return b, lv, pool.get(nm, {})
        return None, None, {}

    def _build_spellbook_embed(self, data: dict, spells: dict, level: int, *, mode: str = 'summary') -> discord.Embed:
        name = str(data.get('name') or 'Unnamed').strip() or 'Unnamed'
        cls = str(data.get('class') or '').strip()
        emb = discord.Embed(title=f"{name} — Spellbook (Level {level})", description=cls)
        bucket_hint = self._bucket_for_char(data)
        level_arr = (data.get('spells', {}) or {}).get(f'level_{int(level)}') or []
        if not isinstance(level_arr, list) or not level_arr:
            emb.description = (emb.description or '') + "\nNo spells at this level."
            return emb

        def _txt(v):
            if isinstance(v, dict):
                return str(v.get('text') or '')
            if isinstance(v, list):
                return "\n\n".join(str(x) for x in v)
            return str(v or '')

        import re as _re
        def _short(s: str, n: int = 160) -> str:
            s = _re.sub(r"\s+", " ", s.strip())
            return s[:n] + ("…" if len(s) > n else "")

        field_count = 0
        for entry in level_arr:
            if not isinstance(entry, dict):
                continue
            nm = str(entry.get('name') or '').strip()
            if not nm:
                continue
            _, _, blob = self._lookup_spell_blob(spells, nm, bucket_hint, level)
            rng = _txt(blob.get('range')); dur = _txt(blob.get('duration'))
            cast = _txt(blob.get('casting_time')); save = _txt(blob.get('save'))
            flags = []
            if 'corruption' in blob: flags.append('Corruption')
            if 'misfire' in blob: flags.append('Misfire')
            if 'manifestation' in blob: flags.append('Manifestation')
            mm = entry.get('mercurial') if isinstance(entry.get('mercurial'), dict) else None
            mm_line = None
            if mm and ('roll' in mm or 'effect' in mm):
                r = mm.get('roll')
                eff = str(mm.get('effect') or '')
                mm_line = f"M{r}: " + (eff if mode == 'full' else _short(eff, 180))
            parts = []
            if mm_line:
                parts.append(mm_line)
            if rng: parts.append(f"Range: {rng}")
            if dur: parts.append(f"Duration: {dur}")
            if cast: parts.append(f"Casting Time: {cast}")
            if save: parts.append(f"Save: {save}")
            if flags: parts.append("Tables: " + ", ".join(flags))
            val = "\n".join(parts) if parts else "—"
            emb.add_field(name=nm, value=val[:1024], inline=False)
            field_count += 1
            if field_count >= 25:
                emb.add_field(name="(more)", value="… output truncated to first 25 spells for Discord limits", inline=False)
                break
        return emb

    class SpellbookView(discord.ui.View):
        def __init__(self, parent: 'CharacterCog', data: dict, spells: dict, levels: list[int], *, level: int, mode: str = 'summary', timeout: Optional[float] = 300):
            super().__init__(timeout=timeout)
            self.parent = parent
            self.data = data
            self.spells = spells
            self.levels = levels
            self.level = level
            self.mode = mode
            opts = [discord.SelectOption(label=f"Level {lv}", value=str(lv), default=(lv==level)) for lv in levels]
            self.level_select = discord.ui.Select(placeholder="Choose level", options=opts, min_values=1, max_values=1)
            self.level_select.callback = self.on_select
            self.add_item(self.level_select)

        async def on_select(self, interaction: discord.Interaction):
            try:
                val = int(self.level_select.values[0])
            except Exception:
                val = self.level
            self.level = val
            # Update defaults so the select reflects the current selection and allows re-selecting previous values
            try:
                for opt in self.level_select.options:
                    try:
                        opt.default = (int(opt.value) == self.level)
                    except Exception:
                        opt.default = (str(opt.value) == str(self.level))
            except Exception:
                pass
            emb = self.parent._build_spellbook_embed(self.data, self.spells, self.level, mode=self.mode)
            await interaction.response.edit_message(embed=emb, view=self)

        @discord.ui.button(label="Summary", style=discord.ButtonStyle.primary)
        async def summary_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.mode == 'summary':
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.mode = 'summary'
            emb = self.parent._build_spellbook_embed(self.data, self.spells, self.level, mode=self.mode)
            await interaction.response.edit_message(embed=emb, view=self)

        @discord.ui.button(label="Full text", style=discord.ButtonStyle.secondary)
        async def full_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.mode == 'full':
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.mode = 'full'
            emb = self.parent._build_spellbook_embed(self.data, self.spells, self.level, mode=self.mode)
            await interaction.response.edit_message(embed=emb, view=self)

    # --- Inventory commands ---
    @inv.command(name="list", description="Show a character's inventory")
    @app_commands.describe(name="Character name")
    async def inv_list(self, interaction: discord.Interaction, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        inv = self._normalize_inventory(data)
        if not inv:
            await interaction.response.send_message("📦 Inventory is empty.", ephemeral=True)
            return
        lines = []
        for it in inv:
            nm = it.get('name'); qty = int(it.get('qty',1) or 1); note = it.get('note')
            base = f"{nm}{(' x'+str(qty)) if qty>1 else ''}"
            lines.append(base + (f" — {note}" if note else ''))
        await interaction.response.send_message("\n".join(f"• {x}" for x in lines), ephemeral=True)

    @inv_list.autocomplete("name")
    async def inv_list_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @inv.command(name="add", description="Add an item to inventory (increments if it exists)")
    @app_commands.describe(name="Character name", item="Item name", qty="Quantity (default 1)", note="Optional note")
    async def inv_add(self, interaction: discord.Interaction, name: str, item: str, qty: int = 1, note: Optional[str] = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        inv = self._normalize_inventory(data)
        qty = max(1, int(qty or 1))
        nm = (item or '').strip()
        if not nm:
            await interaction.response.send_message("Item name is required.", ephemeral=True)
            return
        idx = self._find_item_index(inv, nm)
        if idx >= 0:
            inv[idx]['qty'] = int(inv[idx].get('qty', 1)) + qty
            if note:
                inv[idx]['note'] = note
        else:
            rec = {'name': nm, 'qty': qty}
            if note:
                rec['note'] = note
            inv.append(rec)
        data['inventory'] = inv
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"➕ Added {nm} x{qty}.", ephemeral=True)

    @inv_add.autocomplete("name")
    async def inv_add_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @inv_add.autocomplete("item")
    async def inv_add_item_ac(self, interaction: discord.Interaction, current: str):
        # Suggest from equipment and common gear/weapon/armor keys
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
        try:
            from modules.data_constants import EQUIPMENT_TABLE, WEAPON_TABLE, ARMOR_TABLE
            pool = []
            try:
                pool.extend(EQUIPMENT_TABLE)
            except Exception:
                pass
            try:
                pool.extend(list(WEAPON_TABLE.keys()))
            except Exception:
                pass
            try:
                pool.extend([k for k in ARMOR_TABLE.keys() if k != 'shield'])
            except Exception:
                pass
            seen = set()
            for k in pool:
                s = str(k)
                kl = s.lower()
                if kl in seen:
                    continue
                if cur and cur not in kl:
                    continue
                seen.add(kl)
                choices.append(app_commands.Choice(name=s, value=s))
                if len(choices) >= 25:
                    break
        except Exception:
            pass
        return choices

    @inv.command(name="remove", description="Remove or decrement an item from inventory")
    @app_commands.describe(name="Character name", item="Item name", qty="Quantity to remove (default 1)")
    async def inv_remove(self, interaction: discord.Interaction, name: str, item: str, qty: int = 1):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        inv = self._normalize_inventory(data)
        nm = (item or '').strip()
        idx = self._find_item_index(inv, nm)
        if idx < 0:
            await interaction.response.send_message(f"❌ Item '{nm}' not found.", ephemeral=True)
            return
        qty = max(1, int(qty or 1))
        inv[idx]['qty'] = max(0, int(inv[idx].get('qty',1)) - qty)
        if inv[idx]['qty'] <= 0:
            inv.pop(idx)
        data['inventory'] = inv
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"➖ Removed {nm} x{qty}.", ephemeral=True)

    @inv_remove.autocomplete("name")
    async def inv_remove_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @inv_remove.autocomplete("item")
    async def inv_remove_item_ac(self, interaction: discord.Interaction, current: str):
        # Suggest from current character inventory if provided via focused options
        cur = (current or '').lower()
        # Try to infer the 'name' option value from the interaction
        target_name = None
        try:
            for opt in interaction.data.get('options', []):
                if opt.get('name') == 'name':
                    target_name = opt.get('value')
                    break
        except Exception:
            pass
        items: list[app_commands.Choice[str]] = []
        inv_list = []
        if target_name:
            data = await self._load_record(str(target_name))
            if data:
                inv_list = self._normalize_inventory(data)
        for it in inv_list:
            try:
                nm = str(it.get('name') or '')
            except Exception:
                nm = ''
            if not nm:
                continue
            if cur and cur not in nm.lower():
                continue
            items.append(app_commands.Choice(name=nm, value=nm))
            if len(items) >= 25:
                break
        return items

    @inv.command(name="set", description="Set an item's quantity (0 to remove)")
    @app_commands.describe(name="Character name", item="Item name", qty="Quantity to set (0 removes)", note="Optional note")
    async def inv_set(self, interaction: discord.Interaction, name: str, item: str, qty: int, note: Optional[str] = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        inv = self._normalize_inventory(data)
        nm = (item or '').strip()
        if nm == '':
            await interaction.response.send_message("Item name is required.", ephemeral=True)
            return
        qty = max(0, int(qty))
        idx = self._find_item_index(inv, nm)
        if qty == 0:
            if idx >= 0:
                inv.pop(idx)
        else:
            if idx >= 0:
                inv[idx]['qty'] = qty
                if note is not None:
                    if note:
                        inv[idx]['note'] = note
                    else:
                        inv[idx].pop('note', None)
            else:
                rec = {'name': nm, 'qty': qty}
                if note:
                    rec['note'] = note
                inv.append(rec)
        data['inventory'] = inv
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"📦 Set {nm} to x{qty}.", ephemeral=True)

    @inv_set.autocomplete("name")
    async def inv_set_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @inv_set.autocomplete("item")
    async def inv_set_item_ac(self, interaction: discord.Interaction, current: str):
        return await self.inv_remove_item_ac(interaction, current)

    # --- Custom weapon creation ---
    @inv.command(name="weapon_custom", description="Create a custom weapon and add it to inventory")
    @app_commands.describe(name="Character name")
    async def inv_weapon_custom(self, interaction: discord.Interaction, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return

        # Modal for weapon details (nested so we can capture cog and name)
        cog = self
        char_name = name

        class CustomWeaponModal(discord.ui.Modal, title="New Custom Weapon"):
            weapon_name = discord.ui.TextInput(label="Weapon name", placeholder="e.g., ghostblade", required=True, max_length=40)
            damage = discord.ui.TextInput(label="Damage dice", placeholder="e.g., 1d6 or 1d6+1", required=True, max_length=20)
            ability = discord.ui.TextInput(label="Ability used (optional)", placeholder="STR, AGI, STA, INT, PER, LCK or leave blank for none", required=False, max_length=8)
            attack_bonus = discord.ui.TextInput(label="Attack bonus (signed)", placeholder="e.g., +1 or 0", required=True, max_length=6)
            tags = discord.ui.TextInput(label="Tags (comma-separated)", placeholder="e.g., melee,thrown,mounted,versatile", required=False, max_length=200)

            async def on_submit(self, modal_inter: discord.Interaction):
                try:
                    nm = str(self.weapon_name.value).strip()
                    dmg = str(self.damage.value).strip()
                    abil = str(self.ability.value or '').strip().upper()
                    ab = str(self.attack_bonus.value).strip()
                    try:
                        ab_val = int(ab)
                    except Exception:
                        ab_val = 0
                    tags_text = str(self.tags.value or '').strip()
                    tag_list = [t.strip() for t in tags_text.split(',') if t.strip()] if tags_text else []
                    note = None  # add notes later via inventory set/note command

                    # Persist into inventory as a structured item
                    rec = {
                        'name': nm,
                        'qty': 1,
                        'note': note or 'custom weapon',
                        'weapon': {
                            'custom': True,
                            'damage': dmg,
                            # Accept any ability code or none
                            'ability': abil if abil else None,
                            'attack_bonus': ab_val,
                            'tags': tag_list,
                        }
                    }
                    d2 = await cog._load_record(char_name)
                    inv = cog._normalize_inventory(d2)
                    inv.append(rec)
                    d2['inventory'] = inv
                    ok = await cog._save_record(char_name, d2)
                    if not ok:
                        await modal_inter.response.send_message("Failed to save.", ephemeral=True)
                        return
                    await modal_inter.response.send_message(f"🛠️ Added custom weapon '{nm}' to inventory. You can add a note later with /inv set.", ephemeral=True)
                except Exception as e:
                    try:
                        await modal_inter.response.send_message(f"Error: {e}", ephemeral=True)
                    except Exception:
                        pass

        await interaction.response.send_modal(CustomWeaponModal())

    @inv.command(name="qty", description="Adjust an item's quantity by +/-N (e.g., +3, -2)")
    @app_commands.describe(name="Character name", item="Item name", value="Signed number like +3 or -2")
    async def inv_qty(self, interaction: discord.Interaction, name: str, item: str, value: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        nm = (item or '').strip()
        if not nm:
            await interaction.response.send_message("Item name is required.", ephemeral=True)
            return
        s = (value or '').strip().replace(' ', '')
        if not s or s[0] not in ['+','-']:
            await interaction.response.send_message("Value must be a signed integer like +3 or -2.", ephemeral=True)
            return
        try:
            delta = int(s)
        except Exception:
            await interaction.response.send_message("Invalid number format. Use +N or -N.", ephemeral=True)
            return
        inv = self._normalize_inventory(data)
        idx = self._find_item_index(inv, nm)
        if idx < 0:
            if delta <= 0:
                await interaction.response.send_message(f"❌ Item '{nm}' not found.", ephemeral=True)
                return
            inv.append({'name': nm, 'qty': delta})
        else:
            inv[idx]['qty'] = max(0, int(inv[idx].get('qty',1)) + delta)
            if inv[idx]['qty'] <= 0:
                inv.pop(idx)
        data['inventory'] = inv
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"📦 {nm} quantity adjusted by {delta:+}.", ephemeral=True)

    @inv_qty.autocomplete("name")
    async def inv_qty_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @inv_qty.autocomplete("item")
    async def inv_qty_item_ac(self, interaction: discord.Interaction, current: str):
        return await self.inv_remove_item_ac(interaction, current)

    # --- Delta parser for quick adjustments ---
    def _parse_delta(self, raw: str) -> tuple[int, str]:
        text = (raw or '').strip().replace(' ', '')
        if not text:
            raise ValueError("Empty delta")
        sign = +1
        if text[0] in ['+','-']:
            if text[0] == '-':
                sign = -1
            text = text[1:]
        if not text:
            raise ValueError("No delta value")
        detail = ''
        if 'd' in text.lower():
            total, kept, dropped = self._roll_total(text)
            delta = sign * int(total)
            kept_str = '+'.join(map(str, kept)) if kept else '0'
            dd = f"{text} => {kept_str}"
            detail = f"({('+' if sign>0 else '-')}{dd} = {delta:+})"
            return delta, detail
        # plain integer
        if not re.match(r"^\d+$", text):
            raise ValueError("Invalid delta format")
        delta = sign * int(text)
        detail = f"({delta:+})"
        return delta, detail

    # --- Quick adjust commands ---
    @app_commands.command(name="hp", description="Adjust current HP by +NdX or +/-int")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def hp_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        hp = data.get('hp', {}) if isinstance(data.get('hp'), dict) else {}
        cur = int(hp.get('current', 0) or 0)
        mx = hp.get('max')
        try:
            mx = int(mx) if mx is not None else None
        except Exception:
            mx = None
        new_cur = cur + delta
        if mx is not None:
            new_cur = max(0, min(int(mx), int(new_cur)))
        else:
            new_cur = max(0, int(new_cur))
        # Apply new HP
        hp['current'] = int(new_cur)
        if mx is not None:
            hp['max'] = int(mx)
        data['hp'] = hp
        # If adjusted from 0 to >0 while dying, apply permanent -1 STA and clear dying
        stabilized_note = ""
        try:
            if cur == 0 and new_cur > 0 and isinstance(data.get('dying'), dict):
                abl = data.setdefault('abilities', {})
                sta = abl.setdefault('STA', {})
                try:
                    mx_sta = int(sta.get('max', sta.get('current', sta.get('score', 1)) or 1))
                except Exception:
                    mx_sta = 1
                try:
                    cur_sta = int(sta.get('current', mx_sta) or mx_sta)
                except Exception:
                    cur_sta = mx_sta
                new_max_sta = max(1, mx_sta - 1)
                new_cur_sta = max(1, min(new_max_sta, cur_sta - 1))
                sta['max'] = int(new_max_sta)
                sta['current'] = int(new_cur_sta)
                try:
                    from modules.utils import get_modifier
                    sta['mod'] = int(get_modifier(int(new_cur_sta)))
                except Exception:
                    pass
                data.pop('dying', None)
                stabilized_note = "\n⚠️ Lasting injury: STA -1 (permanent)"
        except Exception:
            pass
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        after = f"{hp.get('current','?')}/{hp.get('max','?') if 'max' in hp else '?'}"
        await interaction.response.send_message(f"❤️ HP {cur} → {hp.get('current')} {detail}\nNow: {after}{stabilized_note}", ephemeral=True)

    @hp_adjust.autocomplete("name")
    async def hp_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items = []
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

    # Group for confirmation actions
    confirm = app_commands.Group(name="confirm", description="Confirm outcomes (e.g., death) and edge-case rulings")

    # --- Confirm death (aka recovering the body) ---
    @confirm.command(name="death", description="If the body is recovered within 1 hour: Luck check to awaken at 1 HP; apply groggy (-4 for 1 hour) and a random -1 to STR/AGI/STA.")
    @app_commands.describe(target="Dead character name to attempt recovery on", rescuer="Who recovered the body (for narration)", override_time="Bypass the 1 hour limit (GM discretion)")
    async def confirm_death(self, interaction: discord.Interaction, target: str, rescuer: Optional[str] = None, override_time: Optional[bool] = False):
        data = await self._load_record(target)
        if not data:
            await interaction.response.send_message(f"Character '{target}' not found.", ephemeral=True)
            return
        # Must be marked dead
        if not bool(data.get('dead')):
            await interaction.response.send_message("Target is not marked as dead.", ephemeral=True)
            return
        # Enforce 1-hour window if we have a timestamp, unless overridden
        too_late = False
        try:
            import time as _t
            ts = data.get('time_of_death')
            if ts is not None and not bool(override_time):
                elapsed = int(_t.time()) - int(ts)
                if elapsed > 3600:
                    too_late = True
        except Exception:
            too_late = False
        if too_late and not bool(override_time):
            await interaction.response.send_message("More than one hour has passed since death; recovery attempt not allowed (use override_time to bypass).", ephemeral=True)
            return
        # Luck check: roll 1d20; success if <= current Luck score
        d20, _ = roll_dice('1d20')
        try:
            threshold = int(get_luck_current(data))
        except Exception:
            # fallback to abilities
            lck = (data.get('abilities') or {}).get('LCK') or {}
            try:
                threshold = int(lck.get('current', lck.get('max', lck.get('score', 0)) or 0))
            except Exception:
                threshold = 0
        success = int(d20) <= int(threshold)
        header = f"🪦 Recovering the body of {data.get('name','Unknown')}"
        resc_txt = f" by {rescuer}" if rescuer else ""
        if not success:
            await interaction.response.send_message(
                f"{header}{resc_txt}: Luck check {int(d20)} vs {int(threshold)} — FAIL. The character remains dead.",
                ephemeral=False
            )
            return
        # Success: not truly dead — set HP to 1, clear dead/dying, apply groggy and permanent -1 to random physical ability
        # HP -> 1
        hp = data.get('hp', {}) if isinstance(data.get('hp'), dict) else {}
        hp['current'] = 1
        if 'max' not in hp:
            try:
                hp['max'] = max(1, int(hp.get('max', 1) or 1))
            except Exception:
                hp['max'] = 1
        data['hp'] = hp
        # Clear death/dying flags
        data.pop('dying', None)
        data.pop('dead', None)
        # Permanent injury: -1 to STR, AGI, or STA (random), affecting both current and max
        phys = ['STR','AGI','STA']
        import random as _rand
        inj = _rand.choice(phys)
        try:
            abil = data.setdefault('abilities', {})
            blk = abil.setdefault(inj, {})
            # Determine current and max
            try:
                cur = int(blk.get('current', blk.get('max', blk.get('score', 1)) or 1))
            except Exception:
                cur = 1
            try:
                mx = int(blk.get('max', cur) or cur)
            except Exception:
                mx = cur
            new_max = max(1, mx - 1)
            new_cur = max(1, min(new_max, cur - 1))
            blk['max'] = int(new_max)
            blk['current'] = int(new_cur)
            try:
                blk['mod'] = int(get_modifier(int(new_cur)))
            except Exception:
                pass
            abil[inj] = blk
            data['abilities'] = abil
        except Exception:
            inj = 'STA'  # fallback label
        # Apply groggy condition for 1 hour (-4 to all rolls)
        try:
            import time as _t
            apply_condition(data, 'groggy', {'expires': int(_t.time()) + 3600})
        except Exception:
            pass
        ok = await self._save_record(target, data)
        if not ok:
            await interaction.response.send_message("Failed to save recovery changes.", ephemeral=True)
            return
        inj_label = ability_name(inj)
        await interaction.response.send_message(
            f"{header}{resc_txt}: Luck check {int(d20)} vs {int(threshold)} — SUCCESS!\n"
            f"• HP set to 1; death averted.\n"
            f"• Permanent injury: -1 {inj_label}.\n"
            f"• Groggy for 1 hour (−4 to all attack/check/save rolls).",
            ephemeral=False
        )

    @confirm_death.autocomplete('target')
    async def confirm_death_target_ac(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                path = os.path.join(SAVE_FOLDER, fn)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        rec = json.load(f)
                except Exception:
                    continue
                if not rec.get('dead'):
                    continue
                disp = str(rec.get('name') or fn[:-5].replace('_',' '))
                if q and q not in disp.lower():
                    continue
                items.append(app_commands.Choice(name=disp, value=disp))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    def _adjust_ability(self, data: dict, code: str, delta: int) -> tuple[int, int, int]:
        abil = data.setdefault('abilities', {})
        slot = abil.setdefault(code, {})
        cur = 0
        try:
            cur = int(slot.get('current', slot.get('max', slot.get('score', 0)) or 0))
        except Exception:
            cur = 0
        mx = None
        try:
            if 'max' in slot:
                mx = int(slot.get('max'))
        except Exception:
            mx = None
        new_cur = cur + int(delta)
        # Allow increases beyond current max by expanding max (so permanent/stat boosts work).
        # Decreases still floor at 1.
        if mx is not None:
            if new_cur > mx:
                # Only raise max on increases; decreases leave max untouched
                if 'prev_max' not in slot:
                    slot['prev_max'] = mx
                slot['max'] = int(new_cur)
            # Clamp floor at 1 but do NOT reduce max when new_cur < 1
            new_cur = max(1, int(new_cur))
        else:
            new_cur = max(1, int(new_cur))
        slot['current'] = int(new_cur)
        try:
            slot['mod'] = int(get_modifier(int(new_cur)))
        except Exception:
            pass
        abil[code] = slot
        data['abilities'] = abil
        return int(cur), int(new_cur), int(slot.get('mod', 0))

    async def _maybe_confirm_raise_max(self, interaction: discord.Interaction, data: dict, name: str, code: str, delta: int, detail: str) -> bool:
        """If applying delta would push current above max, prompt user to confirm raising max.
        Returns True if a confirmation UI was sent (caller should return early), False to proceed normally.
        """
        try:
            abil = (data or {}).get('abilities', {})
            slot = abil.get(code, {}) if isinstance(abil, dict) else {}
            cur = int(slot.get('current', slot.get('max', slot.get('score', 0)) or 0))
            mx = slot.get('max')
            mx = int(mx) if mx is not None else None
        except Exception:
            cur, mx = 0, None
        new_cur = int(cur) + int(delta)
        # Only confirm when increasing past an existing max
        if int(delta) > 0 and mx is not None and new_cur > mx:
            code_up = code.upper()
            from modules.utils import ability_emoji, ability_name
            em = ability_emoji(code_up)
            nm = ability_name(code_up)
            msg = (
                f"⚠️ You are about to increase {em} {nm} above max.\n"
                f"Current {cur}, Max {mx} → New {new_cur} {detail}.\n"
                f"This will change your max score to {new_cur}. Are you sure you want to proceed?"
            )

            # Inline view for confirmation
            cog = self
            class ConfirmRaiseMaxView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)

                @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
                async def yes(self, btn_inter: discord.Interaction, button: discord.ui.Button):
                    # Apply change and raise max
                    rec = await cog._load_record(name)
                    if not rec:
                        await btn_inter.response.edit_message(content="Character not found anymore.", view=None)
                        return
                    owner_msg = cog._check_owner(btn_inter, rec)
                    if owner_msg:
                        await btn_inter.response.edit_message(content=f"🚫 {owner_msg}", view=None)
                        return
                    before, after, mod = cog._adjust_ability(rec, code_up, int(delta))
                    ok = await cog._save_record(name, rec)
                    # Disable buttons
                    for child in self.children:
                        if isinstance(child, discord.ui.Button):
                            child.disabled = True
                    from modules.utils import ability_emoji as _e, ability_name as _n
                    em2 = _e(code_up); nm2 = _n(code_up)
                    if ok:
                        await btn_inter.response.edit_message(
                            content=f"✅ {em2} {nm2} {before} → {after} {detail} (mod {mod:+})\n— Applied.", view=self
                        )
                        try:
                            await btn_inter.followup.send("Applied.", ephemeral=True)
                        except Exception:
                            pass
                    else:
                        await btn_inter.response.edit_message(content="Failed to save.\n— Not applied.", view=self)
                        try:
                            await btn_inter.followup.send("Not applied (save failed).", ephemeral=True)
                        except Exception:
                            pass

                @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
                async def no(self, btn_inter: discord.Interaction, button: discord.ui.Button):
                    # Cancel without changes
                    for child in self.children:
                        if isinstance(child, discord.ui.Button):
                            child.disabled = True
                    await btn_inter.response.edit_message(content="❎ Canceled. No changes made.\n— Not applied.", view=self)
                    try:
                        await btn_inter.followup.send("Not applied.", ephemeral=True)
                    except Exception:
                        pass

            await interaction.response.send_message(msg, view=ConfirmRaiseMaxView(), ephemeral=True)
            return True
        return False

    def _ability_name_ac(self, current: str):
        cur = (current or '').lower()
        items = []
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

    # STR
    @app_commands.command(name="str", description="Adjust Strength (str) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def str_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        # Confirm if this would raise max
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'STR', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'STR', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('STR')} {ability_name('STR')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @str_adjust.autocomplete("name")
    async def str_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    # AGI (alias DEX)
    @app_commands.command(name="agi", description="Adjust Agility (agi) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def agi_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'AGI', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'AGI', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('AGI')} {ability_name('AGI')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @agi_adjust.autocomplete("name")
    async def agi_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    # STA
    @app_commands.command(name="sta", description="Adjust Stamina (sta) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def sta_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'STA', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'STA', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('STA')} {ability_name('STA')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @sta_adjust.autocomplete("name")
    async def sta_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    # INT
    @app_commands.command(name="int", description="Adjust Intelligence (int) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def int_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'INT', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'INT', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('INT')} {ability_name('INT')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @int_adjust.autocomplete("name")
    async def int_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    # PER
    @app_commands.command(name="per", description="Adjust Personality (per) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def per_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'PER', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'PER', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('PER')} {ability_name('PER')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @per_adjust.autocomplete("name")
    async def per_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    # LCK
    @app_commands.command(name="lck", description="Adjust Luck (lck) current by +NdX or +/-int (increases can raise max)")
    @app_commands.describe(value="Delta (e.g., +1d4, -2, +5)", name="Character name")
    async def lck_adjust(self, interaction: discord.Interaction, value: str, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        try:
            delta, detail = self._parse_delta(value)
        except Exception:
            await interaction.response.send_message("Invalid delta. Try '+1d4', '-2', or '+5'.", ephemeral=True)
            return
        sent = await self._maybe_confirm_raise_max(interaction, data, name, 'LCK', delta, detail)
        if sent:
            return
        before, after, mod = self._adjust_ability(data, 'LCK', delta)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"{ability_emoji('LCK')} {ability_name('LCK')} {before} → {after} {detail} (mod {mod:+})", ephemeral=True)

    @lck_adjust.autocomplete("name")
    async def lck_adjust_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @equip.command(name="weapon", description="Equip a weapon by name")
    @app_commands.describe(name="Character name", weapon="Weapon name (e.g., dagger, spear)")
    async def equip_weapon(self, interaction: discord.Interaction, name: str, weapon: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        from modules.data_constants import WEAPON_TABLE
        key = weapon.strip().lower()
        inv = self._normalize_inventory(data)
        idx_inv = self._find_item_index(inv, key)
        if idx_inv < 0 or int(inv[idx_inv].get('qty', 0)) <= 0:
            await interaction.response.send_message(f"❌ You don't have a {key} in your inventory.", ephemeral=True)
            return
        entry = WEAPON_TABLE.get(key)
        is_custom = False
        tags = []
        if not isinstance(entry, dict):
            # Try custom weapon
            it = inv[idx_inv]
            if isinstance(it.get('weapon'), dict):
                is_custom = True
                tags = list(it['weapon'].get('tags') or [])
            else:
                await interaction.response.send_message(f"❌ Unknown weapon '{weapon}'.", ephemeral=True)
                return
        else:
            tags = list(entry.get('tags') or [])
        # If equipping a two-handed weapon, auto-disable shield if currently on
        shield_note = ""
        try:
            two_handed = False
            if isinstance(entry, dict) and entry.get('two_handed'):
                two_handed = True
            if not two_handed:
                two_handed = any(str(t).lower() == 'two-handed' for t in tags)
            if two_handed and bool(data.get('shield')):
                data['shield'] = False
                self._recompute_defense(data)
                shield_note = " (shield auto-disabled)"
        except Exception:
            pass
        data['weapon'] = key
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        kind = "custom " if is_custom else ""
        await interaction.response.send_message(f"⚔️ Equipped {kind}weapon: {key}{shield_note}", ephemeral=True)

    @equip_weapon.autocomplete("name")
    async def equip_weapon_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items = []
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

    @equip_weapon.autocomplete("weapon")
    async def equip_weapon_autocomplete(self, interaction: discord.Interaction, current: str):
        from modules.data_constants import WEAPON_TABLE
        cur = (current or '').lower()
        items: list[app_commands.Choice[str]] = []
        # Try to read the 'name' option from focused interaction to suggest inventory weapons
        target_name = None
        try:
            for opt in interaction.data.get('options', []):
                if opt.get('name') == 'name':
                    target_name = opt.get('value')
                    break
        except Exception:
            pass
        pool = []
        if target_name:
            data = await self._load_record(str(target_name))
            if data:
                inv = self._normalize_inventory(data)
                for it in inv:
                    nm = str(it.get('name') or '')
                    if not nm:
                        continue
                    # Include both table and custom weapons
                    if nm.lower() in WEAPON_TABLE or isinstance(it.get('weapon'), dict):
                        pool.append(nm)
        # Fallback to full weapon table if no inventory pool
        if not pool:
            pool = list(WEAPON_TABLE.keys())
        # Build choices, show damage when available from table
        seen = set()
        for k in pool:
            key = str(k)
            kl = key.lower()
            if kl in seen:
                continue
            if cur and cur not in kl:
                continue
            seen.add(kl)
            v = WEAPON_TABLE.get(kl)
            if isinstance(v, dict):
                dmg = v.get('damage','')
                label = f"{key} ({dmg})" if dmg else key
            else:
                label = key  # custom weapon; damage will show during attack
            items.append(app_commands.Choice(name=label, value=kl))
            if len(items) >= 25:
                break
        return items

    @equip.command(name="armor", description="Equip armor by name")
    @app_commands.describe(name="Character name", armor="Armor name (e.g., leather, chainmail)")
    async def equip_armor(self, interaction: discord.Interaction, name: str, armor: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        from modules.data_constants import ARMOR_TABLE
        key = armor.strip().lower()
        if key not in ARMOR_TABLE or key == 'shield':
            await interaction.response.send_message(f"❌ Unknown armor '{armor}'.", ephemeral=True)
            return
        data['armor'] = key
        self._recompute_defense(data)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        extra_note = ""
        try:
            cls = str(data.get('class') or '').strip().lower()
            if cls in {"wizard","mage"} and key != 'unarmored':
                extra_note = "\n⚠️ Wizards rarely wear armor, as it hinders spellcasting."
        except Exception:
            pass
        await interaction.response.send_message(f"🛡️ Equipped armor: {key} (AC {data.get('ac')}, Fumble {data.get('fumble_die')}){extra_note}", ephemeral=True)

    @equip_armor.autocomplete("name")
    async def equip_armor_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items = []
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

    @equip_armor.autocomplete("armor")
    async def equip_armor_autocomplete(self, interaction: discord.Interaction, current: str):
        from modules.data_constants import ARMOR_TABLE
        cur = (current or '').lower()
        items = []
        for k, v in ARMOR_TABLE.items():
            if k == 'shield':
                continue
            if cur and cur not in k:
                continue
            acb = v.get('ac_bonus') if isinstance(v, dict) else None
            label = f"{k} (+{acb} AC)" if isinstance(acb, int) else k
            items.append(app_commands.Choice(name=label, value=k))
            if len(items) >= 25:
                break
        if not items:
            for k in ("unarmored","padded","leather","studded leather","chainmail","full plate"):
                if k in ARMOR_TABLE and k != 'shield':
                    v = ARMOR_TABLE[k]
                    acb = v.get('ac_bonus') if isinstance(v, dict) else None
                    label = f"{k} (+{acb} AC)" if isinstance(acb, int) else k
                    items.append(app_commands.Choice(name=label, value=k))
        return items

    @equip.command(name="list", description="Show available weapons or armor")
    @app_commands.describe(kind="What to list")
    @app_commands.choices(kind=[
        app_commands.Choice(name="weapons", value="weapons"),
        app_commands.Choice(name="armor", value="armor"),
    ])
    async def equip_list(self, interaction: discord.Interaction, kind: app_commands.Choice[str]):
        if kind.value == 'weapons':
            from modules.data_constants import WEAPON_TABLE
            names = sorted(WEAPON_TABLE.keys())
            lines = []
            for k in names:
                v = WEAPON_TABLE.get(k) or {}
                dmg = v.get('damage','') if isinstance(v, dict) else ''
                lines.append(f"• {k}{' ('+dmg+')' if dmg else ''}")
            text = "\n".join(lines)
            await interaction.response.send_message(f"Available weapons (type to filter in the box):\n{text}", ephemeral=True)
            return
        else:
            from modules.data_constants import ARMOR_TABLE
            names = sorted([k for k in ARMOR_TABLE.keys() if k != 'shield'])
            lines = []
            for k in names:
                v = ARMOR_TABLE.get(k) or {}
                acb = v.get('ac_bonus') if isinstance(v, dict) else None
                lines.append(f"• {k}{' (+'+str(acb)+' AC)' if isinstance(acb,int) else ''}")
            text = "\n".join(lines)
            await interaction.response.send_message(f"Available armor (toggle shield separately):\n{text}", ephemeral=True)
            return

    @equip.command(name="shield", description="Toggle shield on/off")
    @app_commands.describe(name="Character name", on="Enable shield (true/false)")
    async def equip_shield(self, interaction: discord.Interaction, name: str, on: bool):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        # If turning shield on, it must be in inventory
        if bool(on):
            inv = self._normalize_inventory(data)
            idx_inv = self._find_item_index(inv, 'shield')
            if idx_inv < 0 or int(inv[idx_inv].get('qty', 0)) <= 0:
                await interaction.response.send_message(
                    "❌ You don't have a shield in your inventory.",
                    ephemeral=True,
                )
                return
            # Prevent enabling shield when a two-handed weapon is equipped
            try:
                from modules.data_constants import WEAPON_TABLE
                wkey = str(data.get('weapon', '') or '').lower()
                wentry = WEAPON_TABLE.get(wkey) if wkey else None
                if isinstance(wentry, dict) and bool(wentry.get('two_handed')):
                    await interaction.response.send_message(
                        "❌ You don't have enough hands for that.",
                        ephemeral=True,
                    )
                    return
            except Exception:
                pass
        data['shield'] = bool(on)
        self._recompute_defense(data)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        state = 'on' if on else 'off'
        await interaction.response.send_message(f"🛡️ Shield turned {state} (AC {data.get('ac')})", ephemeral=True)

    @equip_shield.autocomplete("name")
    async def equip_shield_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items = []
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


    @set.command(name="hp", description="Set HP current or max")
    @app_commands.describe(value="HP amount", max="If provided, sets max instead of current", name="Character name")
    async def set_hp(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        hp = data.get('hp', {}) if isinstance(data.get('hp'), dict) else {}
        if max:
            hp['max'] = int(value)
        else:
            hp['current'] = int(value)
        if 'current' in hp and 'max' in hp and hp['current'] > hp['max']:
            hp['current'] = hp['max']
        data['hp'] = hp
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"❤️ HP updated: {hp.get('current','?')}/{hp.get('max','?')}", ephemeral=True)

    def _set_ability_common(self, data: dict, code: str, value: Optional[int], max_value: Optional[int]):
        abil = data.setdefault('abilities', {})
        slot = abil.setdefault(code, {})
        if value is not None:
            slot['current'] = int(value)
        if max_value is not None:
            slot['max'] = int(max_value)
        return slot

    @set.command(name="str", description="Set Strength (str) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_str(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'STR', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('STR')} {ability_name('STR')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="dex", description="Set Dexterity (agi) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_dex(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'AGI', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('AGI')} {ability_name('AGI')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="agi", description="Set Agility (agi) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_agi(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        await self.set_dex(interaction, value, max, name)

    @set.command(name="sta", description="Set Stamina (sta) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_sta(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'STA', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('STA')} {ability_name('STA')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="int", description="Set Intelligence (int) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_int_cmd(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'INT', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('INT')} {ability_name('INT')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="per", description="Set Personality (per) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_per(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'PER', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('PER')} {ability_name('PER')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="lck", description="Set Luck (lck) current or max")
    @app_commands.describe(value="Score amount", max="If provided, sets max instead of current", name="Character name")
    async def set_lck(self, interaction: discord.Interaction, value: int, max: Optional[bool] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        slot = self._set_ability_common(data, 'LCK', value if not max else None, value if max else None)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        cur = slot.get('current', '?'); mx = slot.get('max', '?')
        await interaction.response.send_message(f"{ability_emoji('LCK')} {ability_name('LCK')} updated: {cur}/{mx}.", ephemeral=True)

    @set.command(name="save", description="Set a saving throw (ref/fort/wil)")
    @app_commands.describe(value="New bonus", which="ref/fort/wil", name="Character name")
    @app_commands.choices(which=[
        app_commands.Choice(name="ref", value="reflex"),
        app_commands.Choice(name="fort", value="fortitude"),
        app_commands.Choice(name="wil", value="will"),
    ])
    async def set_save(self, interaction: discord.Interaction, value: int, which: app_commands.Choice[str], name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        saves = data.get('saves', {}) if isinstance(data.get('saves'), dict) else {}
        saves[which.value] = int(value)
        data['saves'] = saves
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"🛡️ {which.name.upper()} set to {int(value):+}.", ephemeral=True)

    @set.command(name="ac", description="Set Armor Class")
    @app_commands.describe(value="Armor Class", name="Character name")
    async def set_ac(self, interaction: discord.Interaction, value: int, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        data['ac'] = int(value)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message("🛡️ AC updated.", ephemeral=True)

    @set.command(name="initiative", description="Set initiative")
    @app_commands.describe(value="Initiative", name="Character name")
    async def set_initiative(self, interaction: discord.Interaction, value: int, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        data['initiative'] = int(value)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message("⚡ Initiative updated.", ephemeral=True)
    @set.command(name="speed", description="Set speed")
    @app_commands.describe(value="Speed (ft)", name="Character name")
    async def set_speed(self, interaction: discord.Interaction, value: int, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        data['speed'] = int(value)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message("🏃 Speed updated.", ephemeral=True)

    @set.command(name="att", description="Set attack modifier")
    @app_commands.describe(value="Attack modifier", name="Character name")
    async def set_att(self, interaction: discord.Interaction, value: int, name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        data['attack_bonus'] = int(value)
        data['attack'] = int(value)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message("⚔️ Attack modifier updated.", ephemeral=True)

    @set.command(name="luck_weapon", description="Set fixed Luck weapon and modifier (Warrior/Dwarf)")
    @app_commands.describe(name="Character name", weapon="Exact weapon key (e.g., 'longsword', 'short sword')", mod="Luck modifier to fix (defaults to current LCK mod)")
    async def set_luck_weapon(self, interaction: discord.Interaction, name: str, weapon: str, mod: Optional[int] = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        cls = str(data.get('class','')).strip().lower()
        if cls not in {'warrior','dwarf'}:
            await interaction.response.send_message("Only Warriors and Dwarves have a fixed Luck weapon.", ephemeral=True)
            return
        wkey = (weapon or '').strip().lower()
        if not wkey:
            await interaction.response.send_message("Provide a weapon name.", ephemeral=True)
            return
        # Compute or accept provided modifier
        if mod is None:
            # Try to use stored modifier; fallback to computing from score
            try:
                lmod = int((data.get('abilities', {}).get('LCK', {}) or {}).get('mod') or 0)
            except Exception:
                lmod = 0
            if lmod == 0:
                try:
                    lck_score = int((data.get('abilities', {}).get('LCK', {}) or {}).get('current') or (data.get('abilities', {}).get('LCK', {}) or {}).get('max') or 10)
                except Exception:
                    lck_score = 10
                try:
                    lmod = int(get_modifier(int(lck_score)))
                except Exception:
                    lmod = 0
        else:
            try:
                lmod = int(mod)
            except Exception:
                await interaction.response.send_message("Modifier must be an integer.", ephemeral=True)
                return
        if cls == 'warrior':
            data['warrior_luck_weapon'] = wkey
            data['warrior_luck_weapon_mod'] = int(lmod)
        else:
            data['dwarf_luck_weapon'] = wkey
            data['dwarf_luck_weapon_mod'] = int(lmod)
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Luck weapon set: {wkey} ({int(lmod):+}). It will auto-apply on attacks with this weapon.", ephemeral=True)

    @set.command(name="coins", description="Set coins (cp/sp/gp)")
    @app_commands.describe(cp="Copper", sp="Silver", gp="Gold", name="Character name")
    async def set_coins(self, interaction: discord.Interaction, cp: Optional[int] = None, sp: Optional[int] = None, gp: Optional[int] = None, name: Optional[str] = None):
        if not name:
            await interaction.response.send_message("Please select a character name.", ephemeral=True)
            return
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        coins = data.get('coins', {}) if isinstance(data.get('coins'), dict) else {}
        if cp is not None:
            coins['cp'] = int(cp)
            data['cp'] = int(cp)  # maintain legacy field
        if sp is not None:
            coins['sp'] = int(sp)
        if gp is not None:
            coins['gp'] = int(gp)
        data['coins'] = coins
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        c = data.get('coins', {})
        await interaction.response.send_message(f"💰 Coins set: cp {int(c.get('cp',0))}, sp {int(c.get('sp',0))}, gp {int(c.get('gp',0))}.", ephemeral=True)

    @set.command(name="name", description="Rename a character")
    @app_commands.describe(current="Current name", new="New name")
    async def set_name(self, interaction: discord.Interaction, current: str, new: str):
        data = await self._load_record(current)
        if not data:
            await interaction.response.send_message(f"Character '{current}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        # Save under new filename
        new_data = dict(data)
        new_data['name'] = new
        saved = await self._save_record(new, new_data)
        if not saved:
            await interaction.response.send_message("Failed to save new record.", ephemeral=True)
            return
        # Remove old file
        old_path = self._char_path(current)
        try:
            old_path.unlink(missing_ok=True)
        except Exception:
            pass
        # If this is a familiar, update master's notes reference
        try:
            if str(data.get('class','')).strip().lower() == 'familiar':
                master_name = ((data.get('notes') or {}).get('familiar') or {}).get('master')
                if master_name:
                    master_rec = await self._load_record(master_name)
                    if master_rec and isinstance(master_rec.get('notes'), dict):
                        fam_name_in_master = master_rec['notes'].get('familiar_name')
                        if fam_name_in_master and fam_name_in_master == current:
                            master_rec['notes']['familiar_name'] = new
                            await self._save_record(master_name, master_rec)
        except Exception:
            pass
        await interaction.response.send_message(f"🧾 Renamed '{current}' → '{new}'.", ephemeral=True)

    # --- Spellbook ---
    @app_commands.command(name="spellbook", description="View a character's known spells in detail (with Mercurial effects)")
    @app_commands.describe(name="Character name", level="Specific spell level (1-5), optional", detail="Detail level: summary or full")
    @app_commands.choices(detail=[
        app_commands.Choice(name="summary", value="summary"),
        app_commands.Choice(name="full", value="full"),
    ])
    async def spellbook(self, interaction: discord.Interaction, name: str, level: Optional[int] = None, detail: Optional[str] = None):
        # Load character
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        spells = self._load_spells_data()
        if not spells:
            await interaction.response.send_message("Could not load Spells.json.", ephemeral=True)
            return
        # Determine available levels with spells
        levels = [lv for lv in (1,2,3,4,5) if (data.get('spells', {}) or {}).get(f'level_{lv}')]
        if not levels:
            await interaction.response.send_message("This character has no recorded spells.", ephemeral=True)
            return
        sel_level = int(level) if level in (1,2,3,4,5) else levels[0]
        mode = detail if detail in ("summary","full") else 'summary'
        emb = self._build_spellbook_embed(data, spells, sel_level, mode=mode)
        # Attach interactive view with level select + summary/full
        view = self.SpellbookView(self, data, spells, levels, level=sel_level, mode=mode)
        await interaction.response.send_message(embed=emb, view=view, ephemeral=True)

    @spellbook.autocomplete("name")
    async def spellbook_name_ac(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @set.command(name="alignment", description="Set alignment (L/N/C)")
    @app_commands.describe(value="L/N/C", name="Character name")
    @app_commands.choices(value=[
        app_commands.Choice(name="L", value="Lawful"),
        app_commands.Choice(name="N", value="Neutral"),
        app_commands.Choice(name="C", value="Chaotic"),
    ])
    async def set_alignment(self, interaction: discord.Interaction, value: app_commands.Choice[str], name: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        data['alignment'] = value.value
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message("🧭 Alignment updated.", ephemeral=True)

    # --- Training management ---
    @training.command(name="add", description="Add a trained weapon (validates against known weapons)")
    @app_commands.describe(name="Character name", weapon="Weapon key to add (e.g., longsword)")
    async def training_add(self, interaction: discord.Interaction, name: str, weapon: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        from modules.data_constants import WEAPON_TABLE
        key = (weapon or '').strip().lower()
        if not key or key not in WEAPON_TABLE:
            await interaction.response.send_message("❌ Unknown weapon. Use autocomplete to choose a valid weapon.", ephemeral=True)
            return
        wt = set()
        for w in (data.get('weapon_training') or []):
            try:
                wt.add(str(w).strip().lower())
            except Exception:
                continue
        for w in (data.get('weapon_proficiencies') or []):
            try:
                wt.add(str(w).strip().lower())
            except Exception:
                continue
        if key in wt:
            await interaction.response.send_message(f"Already trained in '{key}'.", ephemeral=True)
            return
        wt.add(key)
        data['weapon_training'] = sorted(wt)
        data['weapon_proficiencies'] = list(data['weapon_training'])
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"🗡️ Added weapon training: {weapon}", ephemeral=True)

    @training_add.autocomplete("name")
    async def _ac_training_add_name(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @training_add.autocomplete("weapon")
    async def _ac_training_add_weapon(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
        try:
            from modules.data_constants import WEAPON_TABLE
            for k in WEAPON_TABLE.keys():
                s = str(k)
                if cur and cur not in s.lower():
                    continue
                choices.append(app_commands.Choice(name=s, value=s))
                if len(choices) >= 25:
                    break
        except Exception:
            pass
        return choices

    @training.command(name="remove", description="Remove a trained weapon")
    @app_commands.describe(name="Character name", weapon="Weapon key to remove")
    async def training_remove(self, interaction: discord.Interaction, name: str, weapon: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        key = (weapon or '').strip().lower()
        wt = set()
        for w in (data.get('weapon_training') or []):
            try:
                wt.add(str(w).strip().lower())
            except Exception:
                continue
        for w in (data.get('weapon_proficiencies') or []):
            try:
                wt.add(str(w).strip().lower())
            except Exception:
                continue
        if key not in wt:
            await interaction.response.send_message(f"Not trained in '{weapon}'.", ephemeral=True)
            return
        wt.discard(key)
        data['weapon_training'] = sorted(wt)
        data['weapon_proficiencies'] = list(data['weapon_training'])
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"🗡️ Removed weapon training: {weapon}", ephemeral=True)

    @training_remove.autocomplete("name")
    async def _ac_training_remove_name(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

    @training_remove.autocomplete("weapon")
    async def _ac_training_remove_weapon(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
        try:
            # Suggest from current character's known trained weapons if name provided in interaction
            target = None
            try:
                for opt in interaction.data.get('options', []):
                    if isinstance(opt, dict) and opt.get('name') == 'name':
                        target = opt.get('value')
                        break
            except Exception:
                target = None
            trained_list = []
            if target:
                d = await self._load_record(str(target))
                if d:
                    try:
                        from modules.utils import character_trained_weapons
                        trained_list = sorted(character_trained_weapons(d))
                    except Exception:
                        pass
            pool = trained_list or []
            for s in pool:
                if cur and cur not in str(s).lower():
                    continue
                choices.append(app_commands.Choice(name=str(s), value=str(s)))
                if len(choices) >= 25:
                    break
        except Exception:
            pass
        return choices

    @set.command(name="occupation", description="Set occupation (Lv0 or background flavor)")
    @app_commands.describe(name="Character name", value="Occupation to set")
    async def set_occupation(self, interaction: discord.Interaction, name: str, value: str):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        msg = self._check_owner(interaction, data)
        if msg:
            await interaction.response.send_message(f"🚫 {msg}", ephemeral=True)
            return
        occ = (value or '').strip()
        if not occ:
            await interaction.response.send_message("Provide an occupation name.", ephemeral=True)
            return
        data['occupation'] = occ
        ok = await self._save_record(name, data)
        if not ok:
            await interaction.response.send_message("Failed to save.", ephemeral=True)
            return
        await interaction.response.send_message(f"🏷️ Occupation set to: {occ}", ephemeral=True)

    @set_occupation.autocomplete("value")
    async def _ac_set_occupation_value(self, interaction: discord.Interaction, current: str):
        # Suggest from occupations_full.json names
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
        try:
            path = Path(__file__).resolve().parents[1] / 'occupations_full.json'
            with path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            # File structure is likely a list of objects with 'name'
            pool = []
            if isinstance(data, list):
                pool = [str(x.get('name')) for x in data if isinstance(x, dict) and x.get('name')]
            elif isinstance(data, dict):
                # fallback: accept either dict of name->... or {'occupations':[...]} variants
                if 'occupations' in data and isinstance(data['occupations'], list):
                    pool = [str(x.get('name')) for x in data['occupations'] if isinstance(x, dict) and x.get('name')]
                else:
                    pool = [str(k) for k in data.keys()]
            seen = set()
            for nm in pool:
                s = str(nm)
                sl = s.lower()
                if sl in seen:
                    continue
                if cur and cur not in sl:
                    continue
                seen.add(sl)
                choices.append(app_commands.Choice(name=s, value=s))
                if len(choices) >= 25:
                    break
        except Exception:
            pass
        return choices

    @set_occupation.autocomplete("name")
    async def _ac_set_occupation_name(self, interaction: discord.Interaction, current: str):
        return self._ability_name_ac(current)

async def setup(bot: commands.Bot):  # For discord.py extension auto loader if used
    await bot.add_cog(CharacterCog(bot))
