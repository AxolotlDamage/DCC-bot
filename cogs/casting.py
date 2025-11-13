import os
import json
from typing import Optional, Tuple, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from utils.dice import roll_dice
from modules.utils import dcc_dice_chain_step  # type: ignore


def _parse_result_key(key: str) -> Tuple[Optional[int], Optional[int], str]:
    """Parse a results key from Spells.json into a numeric range (inclusive).

    Returns (low, high, normalized_label). Use None for open-ended.
    Supports forms: '1', '2-11', '32+', '1 or lower'.
    """
    s = str(key).strip()
    lab = s
    # 'N or lower'
    import re
    m = re.match(r"^(-?\d+)\s*or\s*lower$", s, re.I)
    if m:
        hi = int(m.group(1))
        return None, hi, lab
    # 'N+'
    m = re.match(r"^(-?\d+)\+$", s)
    if m:
        lo = int(m.group(1))
        return lo, None, lab
    # 'A-B'
    m = re.match(r"^(-?\d+)\s*-\s*(-?\d+)$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b:
            return a, b, lab
        else:
            return b, a, lab
    # exact number
    try:
        n = int(s)
        return n, n, lab
    except Exception:
        return None, None, lab


class CastingCog(commands.Cog):
    """Spell casting: roll 1d20 + caster level and read result from Spells.json."""

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

    def _load_spells_data(self) -> dict:
        try:
            path = os.path.join(self._root_dir(), 'Spells.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _caster_info(self, data: dict) -> Tuple[str, int]:
        """Return (caster_type, caster_level).

        caster_type: 'arcane', 'divine', or 'none'
        caster_level: integer level for the class.
        """
        cls = str(data.get('class') or '').strip().lower()
        try:
            lvl = int(data.get('level', 0) or 0)
        except Exception:
            lvl = 0
        if cls in {'wizard', 'mage', 'elf'}:
            return 'arcane', lvl
        if cls == 'cleric':
            return 'divine', lvl
        return 'none', 0

    def _spell_bucket_for(self, caster_type: str) -> Optional[str]:
        if caster_type == 'arcane':
            return 'Wizard Spells'
        if caster_type == 'divine':
            return 'Cleric Spells'
        return None

    def _ability_mod(self, data: dict, key: str) -> int:
        """Return the ability modifier for key (e.g., 'INT')."""
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

    def _flatten_spells(self, spells_data: dict, bucket: str) -> Dict[str, Tuple[str, str, dict]]:
        """Return mapping lowercased spell name -> (display_name, level_label, spell_data)."""
        out: Dict[str, Tuple[str, str, dict]] = {}
        container = spells_data.get('spells', {}).get(bucket, {})
        if not isinstance(container, dict):
            return out
        for lvl_key, level_spells in container.items():
            if not isinstance(level_spells, dict):
                continue
            for nm, rec in level_spells.items():
                if not isinstance(rec, dict):
                    continue
                out[nm.lower()] = (nm, lvl_key, rec)
        return out

    def _find_spell(self, spells_data: dict, caster_type: str, query: str) -> Optional[Tuple[str, str, dict]]:
        bucket = self._spell_bucket_for(caster_type)
        if not bucket:
            return None
        flat = self._flatten_spells(spells_data, bucket)
        if not flat:
            return None
        q = (query or '').strip().lower()
        # exact
        if q in flat:
            return flat[q]
        # startswith
        for k, v in flat.items():
            if k.startswith(q):
                return v
        # contains
        for k, v in flat.items():
            if q in k:
                return v
        return None

    def _roll_subtable(self, section: dict, bonus: int = 0) -> Tuple[int, str, str]:
        """Generic roller for a section shaped like { die: '1dX', table: {...} }.
        Returns (raw_roll, key_label, result_text). If section invalid, returns (0,'N/A','No table').
        """
        try:
            die = str(section.get('die') or '1d20')
            tbl = section.get('table')
            if not isinstance(tbl, dict) or not tbl:
                return (0, 'N/A', 'No table')
            r, _ = roll_dice(die)
            total = int(r) + int(bonus or 0)
            # choose entry by matching parsed key ranges
            parsed: List[Tuple[Optional[int], Optional[int], str]] = []
            for k in tbl.keys():
                lo, hi, lab = _parse_result_key(k)
                parsed.append((lo, hi, lab))
            def _key(x: Tuple[Optional[int], Optional[int], str]):
                lo, hi, _ = x
                return (lo if lo is not None else -10**9, hi if hi is not None else 10**9)
            parsed.sort(key=_key)
            chosen_key = None
            for lo, hi, lab in parsed:
                ok = False
                if lo is None and hi is not None:
                    ok = total <= hi
                elif lo is not None and hi is None:
                    ok = total >= lo
                elif lo is not None and hi is not None:
                    ok = lo <= total <= hi
                if ok:
                    chosen_key = lab
                    break
            if chosen_key is None:
                chosen_key = str(total)
            val = tbl.get(chosen_key)
            text = val if isinstance(val, str) else (val.get('text') if isinstance(val, dict) else str(val))
            return (int(r), str(chosen_key), str(text or ''))
        except Exception:
            return (0, 'N/A', 'No table')

    def _match_result(self, results: dict, total: int) -> Optional[Tuple[str, Any]]:
        """Pick the first matching result entry for a total score."""
        ranges: List[Tuple[Optional[int], Optional[int], str]] = []
        for key in results.keys():
            lo, hi, lab = _parse_result_key(key)
            ranges.append((lo, hi, lab))
        # Sort by lower bound (None first), then by upper
        def _key(x: Tuple[Optional[int], Optional[int], str]):
            lo, hi, _ = x
            return (lo if lo is not None else -10**9, hi if hi is not None else 10**9)
        ranges.sort(key=_key)
        chosen_label: Optional[str] = None
        for lo, hi, lab in ranges:
            ok = False
            if lo is None and hi is None:
                continue
            if lo is None and hi is not None:
                ok = total <= hi
            elif lo is not None and hi is None:
                ok = total >= lo
            else:
                ok = (lo is not None and hi is not None and lo <= total <= hi)
            if ok:
                chosen_label = lab
                break
        if chosen_label is None:
            return None
        return chosen_label, results.get(chosen_label)

    def _is_failure_result(self, key_label: str, payload: Any) -> bool:
        """Heuristic to detect a failed spell result.

        - If payload contains text mentioning failure, treat as failed.
        - If the matched key range is entirely <= 11, treat as failed (common DCC cutoff).
        - Otherwise assume success.
        """
        try:
            text = None
            if isinstance(payload, dict):
                text = str(payload.get('text') or payload.get('result') or '')
                # Some datasets include 'lost' to indicate failed casting (mostly arcane)
                if bool(payload.get('lost')):
                    return True
            elif isinstance(payload, str):
                text = payload
            if text and any(tok in text.lower() for tok in ('failure', 'fails', 'failed')):
                return True
            lo, hi, _ = _parse_result_key(key_label)
            if hi is not None and hi <= 11:
                return True
        except Exception:
            pass
        return False

    def _roll_disapproval(self, spells_data: dict) -> Tuple[int, str, str]:
        """Roll the Disapproval Table and return (roll, key_label, result_text)."""
        table_def = spells_data.get('Disapproval Table', {})
        tbl = {}
        if isinstance(table_def, dict):
            tbl = table_def.get('table', {}) or {}
        if not isinstance(tbl, dict) or not tbl:
            return (0, 'N/A', 'Disapproval table not found.')
        r, _ = roll_dice('1d20')
        # Find the matching entry by key ranges
        chosen_key = None
        chosen_val: Any = None
        # Build parsed list
        parsed: List[Tuple[Optional[int], Optional[int], str]] = []
        for k in tbl.keys():
            lo, hi, lab = _parse_result_key(k)
            parsed.append((lo, hi, lab))
        def _key(x: Tuple[Optional[int], Optional[int], str]):
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
            # fallback: exact string match
            chosen_key = str(r)
            chosen_val = tbl.get(chosen_key, 'No entry found.')
        text = chosen_val if isinstance(chosen_val, str) else (chosen_val.get('text') if isinstance(chosen_val, dict) else str(chosen_val))
        return (int(r), str(chosen_key), str(text or 'No entry'))

    def _roll_corruption(self, spells_data: dict, severity: str, bonus: int = 0) -> Tuple[int, int, str, str]:
        """Roll a corruption table by severity ('Minor' or 'Major').
        Returns (roll, total_with_bonus, key_label, result_text).
        """
        tables = spells_data.get('Corruption tables', {})
        sect = tables.get(severity.title(), {}) if isinstance(tables, dict) else {}
        die = sect.get('die', '1d10')
        tbl = sect.get('table', {}) if isinstance(sect, dict) else {}
        if not isinstance(tbl, dict) or not tbl:
            return (0, 0, 'N/A', 'Corruption table not found.')
        r, _ = roll_dice(die)
        total = int(r) + int(bonus or 0)
        # choose entry via parsed ranges
        chosen_key = None
        chosen_val: Any = None
        parsed: List[Tuple[Optional[int], Optional[int], str]] = []
        for k in tbl.keys():
            lo, hi, lab = _parse_result_key(k)
            parsed.append((lo, hi, lab))
        def _key(x: Tuple[Optional[int], Optional[int], str]):
            lo, hi, _ = x
            return (lo if lo is not None else -10**9, hi if hi is not None else 10**9)
        parsed.sort(key=_key)
        for lo, hi, lab in parsed:
            ok = False
            if lo is None and hi is not None:
                ok = total <= hi
            elif lo is not None and hi is None:
                ok = total >= lo
            elif lo is not None and hi is not None:
                ok = lo <= total <= hi
            if ok:
                chosen_key = lab
                chosen_val = tbl.get(lab)
                break
        if chosen_key is None:
            chosen_key = str(total)
            chosen_val = tbl.get(chosen_key, 'No entry found.')
        text = chosen_val if isinstance(chosen_val, str) else (chosen_val.get('text') if isinstance(chosen_val, dict) else str(chosen_val))
        return (int(r), int(total), str(chosen_key), str(text or 'No entry'))

    def _parse_action_dice(self, data: dict) -> list[str]:
        """Return a list of action dice strings from character data.
        Prefers 'action_dice' (e.g., '1d20+1d14'); falls back to 'action_die'.
        """
        raw = str(data.get('action_dice') or data.get('action_die') or '1d20')
        if not raw:
            return ['1d20']
        parts = [p.strip() for p in raw.replace(',', '+').split('+') if p.strip()]
        return parts if parts else ['1d20']

    def _find_known_spell_entry(self, data: dict, disp_name: str) -> dict | None:
        spells = data.get('spells') or {}
        if not isinstance(spells, dict):
            return None
        target = (disp_name or '').strip().lower()
        for lvl in (1,2,3,4,5):
            arr = spells.get(f'level_{lvl}')
            if not isinstance(arr, list):
                continue
            for entry in arr:
                if isinstance(entry, dict) and str(entry.get('name','')).strip().lower() == target:
                    return entry
        return None

    @app_commands.command(name="cast", description="Cast a spell: action die + caster level (with class/gear mods), lookup result from Spells.json")
    @app_commands.describe(name="Character name", spell="Spell name (case-insensitive)", die="Action die to use (e.g., 1d20, 1d16); default first")
    async def cast_slash(self, interaction: discord.Interaction, name: str, spell: str, die: Optional[str] = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        caster_type, cl = self._caster_info(data)
        if caster_type == 'none' or cl <= 0:
            await interaction.response.send_message("This character is not a spellcaster.", ephemeral=True)
            return
        spells_data = self._load_spells_data()
        found = self._find_spell(spells_data, caster_type, spell)
        if not found:
            await interaction.response.send_message(f"Spell '{spell}' not found for this caster.", ephemeral=True)
            return
        disp_name, lvl_label, rec = found
        # Note: We do not gate casting by ability-based max spell level; adjustments are applied to known spells only.
        # For arcane casters, require the spell to be known; also prevent casting if it's lost for the day
        known_entry = None
        if caster_type == 'arcane':
            # Only check if the character actually knows the spell; unknown spells still roll the reference table
            known_entry = self._find_known_spell_entry(data, disp_name)
            if not isinstance(known_entry, dict):
                await interaction.response.send_message(f"❌ {disp_name} isn't in your spellbook.", ephemeral=True)
                return
            if isinstance(known_entry, dict) and bool(known_entry.get('lost')):
                await interaction.response.send_message(f"❌ {disp_name} is lost for the day.", ephemeral=True)
                return
        elif caster_type == 'divine':
            # Clerics must also know the spell (from their prayers list)
            known_entry = self._find_known_spell_entry(data, disp_name)
            if not isinstance(known_entry, dict):
                await interaction.response.send_message(f"❌ {disp_name} isn't among your prayers.", ephemeral=True)
                return
        results = rec.get('results', {}) if isinstance(rec, dict) else {}
        if not isinstance(results, dict) or not results:
            await interaction.response.send_message(f"Spell '{disp_name}' has no results table.", ephemeral=True)
            return
        # Decide action die for this spell check
        action_dice = self._parse_action_dice(data)
        use_idx = 0
        if die:
            s = str(die).strip().lower()
            # Try to match by exact die expression first
            try:
                for i, d in enumerate(action_dice):
                    if str(d).strip().lower() == s:
                        use_idx = i
                        break
            except Exception:
                pass
            # Backward-compat: support 'second' textual selection
            if use_idx == 0 and s in {"second","2","#2","die2"} and len(action_dice) >= 2:
                use_idx = 1
        die_expr = action_dice[use_idx] if action_dice else '1d20'
        # Optional: adjust by mercurial die step if provided on this spell — Wizards/Mages only (Elves do not use mercurial)
        if caster_type == 'arcane':
            known_entry = self._find_known_spell_entry(data, disp_name)
            cls_now = str(data.get('class') or '').strip().lower()
            apply_mercurial = cls_now in {'wizard', 'mage'}
            if apply_mercurial:
                try:
                    step = int(((known_entry or {}).get('mercurial') or {}).get('die_step', 0) or 0)
                except Exception:
                    step = 0
                # Mercurial 96: Powerful caster — improve the die by one step on the dice chain
                extra_step = 0
                try:
                    mm = (known_entry or {}).get('mercurial') if isinstance((known_entry or {}).get('mercurial'), dict) else None
                    if mm:
                        mroll = mm.get('roll')
                        meff = str(mm.get('effect') or '').lower()
                        if mroll == 96 or 'powerful caster' in meff:
                            extra_step = 1
                except Exception:
                    extra_step = 0
                total_step = int(step) + int(extra_step)
                if total_step:
                    die_expr = dcc_dice_chain_step(die_expr, total_step)
        # Roll action die + caster level (+ INT if arcane; + augury; + armor penalties if arcane)
        dres, rolls = roll_dice(die_expr)
        raw = int(rolls[0] if isinstance(rolls, list) and rolls else dres)
        total = int(raw) + int(cl)
        parts = [f"{die_expr} {raw}", f"CL {cl}"]
        # If Powerful caster applied, annotate the roll components
        try:
            if caster_type == 'arcane' and 'total_step' in locals() and total_step:
                note = f"mercurial {'+' if total_step>0 else ''}{total_step} step"
                # If we only applied the Powerful caster (and not die_step), still helpful to note
                parts.append(note)
        except Exception:
            pass
        # Arcane casters add INT modifier to spell checks
        if caster_type == 'arcane':
            int_mod = self._ability_mod(data, 'INT')
            if int_mod:
                total += int(int_mod)
                parts.append(f"INT {int_mod:+}")
            # Augury: Seventh son → Spell checks (use static max Luck mod)
            try:
                aug = (data.get('birth_augur') or {}).get('effect')
                if str(aug).strip() == 'Spell checks':
                    from modules.utils import get_max_luck_mod  # type: ignore
                    lmod = int(get_max_luck_mod(data) or 0)
                    if lmod:
                        total += lmod
                        parts.append(f"augur {lmod:+}")
            except Exception:
                pass
            # Elf Favorite Spell: apply fixed Luck mod to checks for the chosen spell (set at 1st level)
            try:
                if str(data.get('class') or '').strip().lower() == 'elf':
                    fav = str(data.get('elf_favorite_spell') or '').strip().lower()
                    if fav and fav == str(disp_name).strip().lower():
                        fmod = int(data.get('elf_favorite_spell_luck_mod', 0) or 0)
                        if fmod:
                            total += fmod
                            parts.append(f"favorite {fmod:+}")
            except Exception:
                pass
            # Armor penalties: apply armor (and shield) check_penalty to arcane spell checks
            try:
                from modules.data_constants import ARMOR_TABLE  # type: ignore
                armor_key = str(data.get('armor', 'unarmored') or 'unarmored').lower()
                armor_entry = ARMOR_TABLE.get(armor_key) if armor_key else None
                pen = 0
                if isinstance(armor_entry, dict):
                    pen += int(armor_entry.get('check_penalty', 0) or 0)
                # Shields impose additional check penalty if toggled on
                if bool(data.get('shield')):
                    shield_pen = int(ARMOR_TABLE.get('shield', {}).get('check_penalty', 0) or 0)
                    pen += shield_pen
                if pen:
                    total += int(pen)  # penalties are negative numbers
                    parts.append(f"armor {int(pen):+}")
            except Exception:
                pass
        matched = self._match_result(results, total)
        if not matched:
            await interaction.response.send_message(f"No matching result found for total {total} on '{disp_name}'.", ephemeral=True)
            return
        key, payload = matched
        # Extract text and arcane flags
        text = None
        flags: List[str] = []
        if isinstance(payload, dict):
            text = payload.get('text') or payload.get('result') or None
            # Arcane flags
            if caster_type == 'arcane':
                for fld in ('misfire', 'corruption', 'lost'):
                    try:
                        if bool(payload.get(fld)):
                            flags.append(fld)
                    except Exception:
                        pass
        elif isinstance(payload, str):
            text = payload
        if not text:
            text = str(payload)
        # Handle arcane misfire/corruption/lost effects (prior to message formatting)
        extra_blocks: List[str] = []
        if caster_type == 'arcane':
            # If the matched result is a failure by DCC standards (e.g., 2-11), the spell is lost for the day
            try:
                is_fail = self._is_failure_result(key, payload)
            except Exception:
                is_fail = False
            # Lost: mark known spell as lost if present in known list
            if (('lost' in flags) or is_fail) and known_entry is not None:
                try:
                    if not bool(known_entry.get('lost')):
                        known_entry['lost'] = True
                        await self._save_record(data.get('name', name), data)
                    # Add a single note for visibility
                    extra_blocks.append(f"📘 Spell lost for the day: {disp_name}")
                except Exception:
                    pass
            # Misfire: roll the spell's own misfire table if available
            if 'misfire' in flags:
                try:
                    mis = rec.get('misfire') if isinstance(rec, dict) else None
                    if isinstance(mis, dict) and mis.get('table'):
                        mr, mk, mt = self._roll_subtable(mis, bonus=0)
                        extra_blocks.append(f"💥 Misfire {mr} → {mk}: {mt}")
                except Exception:
                    pass
            # Corruption: roll the spell's corruption table; if it indicates Minor/Major, also roll global
            if 'corruption' in flags:
                try:
                    corr = rec.get('corruption') if isinstance(rec, dict) else None
                    if isinstance(corr, dict) and corr.get('table'):
                        # Build bonus = wizard luck mod (if class is wizard/mage) + augury bonus if applicable
                        cls_name = str(data.get('class') or '').strip().lower()
                        lck_mod = self._ability_mod(data, 'LCK') if cls_name in {'wizard','mage'} else 0
                        aug_bonus2 = 0
                        try:
                            from modules.utils import get_max_luck_mod  # type: ignore
                            if str((data.get('birth_augur') or {}).get('effect') or '').strip() == 'Corruption rolls':
                                aug_bonus2 = int(get_max_luck_mod(data) or 0)
                        except Exception:
                            aug_bonus2 = 0
                        bonus_total = int(lck_mod) + int(aug_bonus2)
                        cr, ck, ct = self._roll_subtable(corr, bonus=bonus_total)
                        note = []
                        if lck_mod:
                            note.append(f"Luck {lck_mod:+}")
                        if aug_bonus2:
                            note.append(f"augur {aug_bonus2:+}")
                        note_str = f" ({' + '.join(note)})" if note else ""
                        extra_blocks.append(f"☠️ Corruption {cr}{note_str} → {ck}: {ct}")
                        sev = None
                        low = ct.lower()
                        if 'minor corruption' in low or low.strip() == 'minor corruption.':
                            sev = 'Minor'
                        elif 'major corruption' in low or low.strip() == 'major corruption.':
                            sev = 'Major'
                        if sev:
                            # Apply wizard Luck mod and augury bonus to global corruption roll as well
                            aug_bonus = 0
                            try:
                                from modules.utils import get_max_luck_mod  # type: ignore
                                if str((data.get('birth_augur') or {}).get('effect') or '').strip() == 'Corruption rolls':
                                    aug_bonus = int(get_max_luck_mod(data) or 0)
                            except Exception:
                                aug_bonus = 0
                            total_bonus = int(aug_bonus) + int(lck_mod)
                            r2, total2, k2, t2 = self._roll_corruption(spells_data, sev, total_bonus)
                            note2 = []
                            if lck_mod:
                                note2.append(f"Luck {lck_mod:+}")
                            if aug_bonus:
                                note2.append(f"augur {aug_bonus:+}")
                            note2_str = f" ({' + '.join(note2)})" if note2 else ""
                            extra_blocks.append(f"☠️ {sev} Corruption roll {r2}{note2_str} → total {total2} ⇒ {k2}: {t2}")
                except Exception:
                    pass

        # Cleric disapproval handling
        disapproval_text_block = None
        updated_range = None
        if caster_type == 'divine':
            try:
                dis_range = int(data.get('disapproval_range', 1) or 1)
            except Exception:
                dis_range = 1
            # If raw die <= disapproval range: Disapproval triggers; spell fails and roll on table
            if raw <= dis_range:
                # Spell fails; increment range by 1
                new_range = max(1, dis_range + 1)
                data['disapproval_range'] = int(new_range)
                updated_range = new_range
                r, dkey, dtext = self._roll_disapproval(spells_data)
                disapproval_text_block = f"❗ Disapproval triggered (raw {raw} ≤ range {dis_range}). Spell fails.\nDisapproval roll {r} → {dkey}: {dtext}\nNew disapproval range: {new_range}"
                # Save update
                await self._save_record(data.get('name', name), data)
            else:
                # If the result is a failure, increment range by 1
                if self._is_failure_result(key, payload):
                    new_range = max(1, dis_range + 1)
                    data['disapproval_range'] = int(new_range)
                    updated_range = new_range
                    await self._save_record(data.get('name', name), data)
        # Augury side notes for arcane casters
        aug_damage_note = None
        aug_corr_note = None
        if caster_type == 'arcane':
            try:
                aug_eff = str((data.get('birth_augur') or {}).get('effect') or '').strip()
                from modules.utils import get_max_luck_mod  # type: ignore
                lmod = int(get_max_luck_mod(data) or 0)
                if lmod:
                    if aug_eff == 'Spell damage':
                        aug_damage_note = f"Augury bonus to spell damage: {lmod:+}"
                    if aug_eff == 'Corruption rolls':
                        aug_corr_note = f"Augury bonus to corruption rolls: {lmod:+}"
            except Exception:
                pass
        # Format message (include Mercurial effect if arcane and known)
        cls_name = str(data.get('class') or '').title() or 'Unknown'
        name_disp = data.get('name') or name
        roll_part = " + ".join(parts) + f" = {total}"
        header = f"{name_disp} ({cls_name}) casts {disp_name} [{lvl_label}]"
        # Mercurial Magic effect tied to this spell for this character (Wizards/Mages only)
        mercurial_line = None
        if caster_type == 'arcane' and isinstance(known_entry, dict) and str(data.get('class') or '').strip().lower() in {'wizard','mage'}:
            mm = known_entry.get('mercurial') if isinstance(known_entry.get('mercurial'), dict) else None
            if mm:
                try:
                    mroll = mm.get('roll')
                    meff = mm.get('effect') or ''
                    # Compact summarize effect if very long
                    import re as _re
                    s = _re.sub(r"\s+", " ", str(meff).strip())
                    if len(s) > 240:
                        s = s[:240] + "…"
                    if mroll is not None:
                        mercurial_line = f"Mercurial: M{mroll} — {s}"
                    else:
                        mercurial_line = f"Mercurial: {s}"
                except Exception:
                    mercurial_line = None
        lines = [f"{header}"]
        if mercurial_line:
            lines.append(mercurial_line)
        lines.append(f"Roll: {roll_part}")
        if aug_damage_note:
            lines.append(aug_damage_note)
        if disapproval_text_block:
            lines.append(disapproval_text_block)
        else:
            lines.extend([f"Matched: {key}", f"Result: {text}"])
            if flags:
                lines.append(f"Flags: {', '.join(flags)}")
            if caster_type == 'divine' and updated_range is not None:
                lines.append(f"Cleric spell failed; disapproval range increases to {updated_range}.")
        # If arcane corruption flagged, offer interactive corruption roll
        # Append any arcane extra rolls/notes
        lines.extend(extra_blocks)
        # Familiar summoning hook for Find Familiar
        try:
            if str(disp_name).strip().lower() == 'find familiar':
                # Determine if casting succeeded (avoid failure bands and lost cases)
                succeeded = not self._is_failure_result(key, payload)
                # Only create if success and character has no existing familiar
                has_fam = bool(((data.get('notes') or {}).get('familiar_name')) or ((data.get('notes') or {}).get('familiar')))
                if succeeded and not has_fam:
                    from modules.familiars import generate_familiar_record  # type: ignore
                    fam_rec = generate_familiar_record(data, total)
                    # Ensure unique filename by suffixing if needed
                    base_name = fam_rec.get('name') or 'Familiar'
                    safe_base = base_name
                    # If a file with same sanitized name exists, append numeric suffix
                    idx = 2
                    while True:
                        path_try = os.path.join(SAVE_FOLDER, f"{safe_base.strip().lower().replace(' ', '_')}.json")
                        if not os.path.exists(path_try):
                            break
                        safe_base = f"{base_name} {idx}"
                        idx += 1
                    fam_rec['name'] = safe_base
                    # Save familiar
                    await self._save_record(fam_rec['name'], fam_rec)
                    # Apply wizard HP bonus (equal to familiar HP) if specified
                    try:
                        fam_hp = int(fam_rec.get('hp', {}).get('max', fam_rec.get('hp', {}).get('current', 0)) or 0)
                        hp_field = data.get('hp')
                        if isinstance(hp_field, dict):
                            data['hp']['current'] = int(data['hp'].get('current', 0)) + fam_hp
                            data['hp']['max'] = int(data['hp'].get('max', 0)) + fam_hp
                        elif isinstance(hp_field, int):
                            data['hp'] = {'current': hp_field + fam_hp, 'max': hp_field + fam_hp}
                        else:
                            data['hp'] = {'current': fam_hp, 'max': fam_hp}
                    except Exception:
                        pass
                    # Mark familiar reference on wizard
                    data.setdefault('notes', {})['familiar_name'] = fam_rec['name']
                    data['notes']['familiar_link'] = fam_rec.get('notes', {}).get('familiar', {})
                    await self._save_record(data.get('name', name), data)
                    lines.append(f"🐾 Familiar summoned: {fam_rec['name']} (type: {fam_rec['notes']['familiar']['type']}) — HP {fam_rec['hp']['max']}, AC {fam_rec['ac']}, AB {fam_rec.get('attack_bonus','+0')} | Form: {fam_rec['notes']['familiar']['creature_form']} | Personality: {fam_rec['notes']['familiar']['personality']}")
                elif not succeeded:
                    lines.append("🐾 No familiar (spell failed).")
                else:
                    lines.append("🐾 Familiar already bound; no new familiar created.")
        except Exception as _fam_err:
            lines.append(f"⚠️ Familiar generation error: {_fam_err}")
        await interaction.response.send_message("\n".join(lines))

    # ---- Autocompletes ----
    @cast_slash.autocomplete('name')
    async def cast_name_ac(self, interaction: discord.Interaction, current: str):
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

    @cast_slash.autocomplete('spell')
    async def cast_spell_ac(self, interaction: discord.Interaction, current: str):
        # Try to read chosen character to filter to their spell list
        target_name = None
        try:
            for opt in interaction.data.get('options', []):
                if opt.get('name') == 'name':
                    target_name = opt.get('value')
                    break
        except Exception:
            pass
        caster_type = None
        data = None
        if target_name:
            data = await self._load_record(str(target_name))
            if data:
                caster_type, _ = self._caster_info(data)
        cur = (current or '').lower()
        items: List[app_commands.Choice[str]] = []
        # If we have a character and caster type, prefer their known/prayers list
        def _known_spells_list(char: dict) -> List[tuple[str,int,bool]]:
            out: List[tuple[str,int,bool]] = []
            try:
                spells = (char or {}).get('spells') or {}
                for lvl in (1,2,3,4,5):
                    arr = spells.get(f'level_{lvl}')
                    if not isinstance(arr, list):
                        continue
                    for entry in arr:
                        if not isinstance(entry, dict):
                            continue
                        nm = str(entry.get('name') or '').strip()
                        if not nm:
                            continue
                        lost = bool(entry.get('lost'))
                        out.append((nm, lvl, lost))
            except Exception:
                pass
            return out
        used_known = False
        if data and caster_type in {'arcane', 'divine'}:
            known = _known_spells_list(data)
            if known:
                used_known = True
                for nm, lvl, lost in known:
                    if cur and cur not in nm.lower():
                        continue
                    label = f"{nm} (level {lvl}{' — lost' if lost else ''})"
                    items.append(app_commands.Choice(name=label, value=nm))
                    if len(items) >= 25:
                        break
        if not used_known:
            # Fallback to full spell list for the caster's bucket (or both if unknown), still limited to 25
            spells_data = self._load_spells_data()
            buckets: List[str] = []
            if caster_type in {'arcane', 'divine'}:
                b = self._spell_bucket_for(caster_type)
                if b:
                    buckets = [b]
            else:
                buckets = ['Wizard Spells', 'Cleric Spells']
            seen: set[str] = set()
            for bucket in buckets:
                flat = self._flatten_spells(spells_data, bucket) if bucket else {}
                for nm, (disp, lvl, _) in flat.items():
                    if nm in seen:
                        continue
                    if cur and cur not in disp.lower():
                        continue
                    seen.add(nm)
                    label = f"{disp} ({lvl})"
                    items.append(app_commands.Choice(name=label, value=disp))
                    if len(items) >= 25:
                        break
                if len(items) >= 25:
                    break
        # Sort choices for stability and readability
        try:
            items.sort(key=lambda c: c.name.lower())
        except Exception:
            pass
        return items

    @cast_slash.autocomplete('die')
    async def cast_die_ac(self, interaction: discord.Interaction, current: str):
        # Suggest dice from the selected character's action dice
        target_name = None
        try:
            for opt in interaction.data.get('options', []):
                if opt.get('name') == 'name':
                    target_name = opt.get('value')
                    break
        except Exception:
            pass
        dice: List[str] = []
        if target_name:
            data = await self._load_record(str(target_name))
            if data:
                dice = self._parse_action_dice(data)
        if not dice:
            dice = ['1d20']
        cur = (current or '').strip().lower()
        out: List[app_commands.Choice[str]] = []
        for d in dice:
            if cur and cur not in d.lower():
                continue
            out.append(app_commands.Choice(name=d, value=d))
        return out[:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(CastingCog(bot))
