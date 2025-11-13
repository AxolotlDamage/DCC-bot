import os, json
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from core.config import SAVE_FOLDER  # type: ignore
from utils.dice import roll_dice
from modules.utils import (
    get_modifier, get_luck_current, consume_luck_and_save, get_max_luck_mod,
    ability_name, ability_emoji, ABILITY_INFO, ABILITY_ORDER, get_global_roll_penalty
)  # type: ignore
from modules.initiative import INITIATIVE_ORDER, CURRENT_TURN_INDEX  # type: ignore

# Build choices from centralized ability mapping to avoid drift
ABILITY_CHOICES = [
    app_commands.Choice(
        name=f"{ABILITY_INFO[c]['name']} ({ABILITY_INFO[c]['short']})",
        value=c,
    )
    for c in ABILITY_ORDER
]


class ChecksCog(commands.Cog):
    """Ability and skill checks.

    Rules per request:
    - Untrained checks roll 1d10
    - Trained checks roll 1d20
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- helpers ---
    async def _load_record(self, name: str) -> Optional[dict]:
        safe = name.lower().strip().replace(' ', '_')
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
            v = data.get('abilities', {}).get(key, {})
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            return int(get_modifier(int(v)))
        except Exception:
            return 0

    def _will_save_total(self, data: dict) -> int:
        """Return the Will save total if stored, else PER mod + class_saves.will."""
        try:
            sv = data.get('saves') or {}
            w = int(sv.get('will', 0) or 0)
            if w:
                return w
        except Exception:
            pass
        try:
            base = self._ability_mod(data, 'PER')
            cls = int((data.get('class_saves') or {}).get('will', 0) or 0)
            return int(base) + int(cls)
        except Exception:
            return 0

    # --- internal: shared save roller ---
    async def _do_save(self, interaction: discord.Interaction, name: str, save_key: str, title: str,
                       dc: Optional[int] = None, bonus: Optional[int] = 0, burn: Optional[int] = 0,
                       vs_traps: Optional[bool] = False, vs_poison: Optional[bool] = False,
                       burn_from_halfling: Optional[str] = None):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return

        # Prefer stored total; fallback to ability + class_saves
        try:
            stored = int((data.get('saves') or {}).get(save_key, 0) or 0)
        except Exception:
            stored = 0
        if stored == 0:
            abil_map = {"reflex": "AGI", "fortitude": "STA", "will": "PER"}
            amod = self._ability_mod(data, abil_map.get(save_key, "STA"))
            try:
                class_bonus = int((data.get('class_saves') or {}).get(save_key, 0) or 0)
            except Exception:
                class_bonus = 0
            save_mod = amod + class_bonus
        else:
            save_mod = stored

        b = int(bonus or 0)
        roll, _ = roll_dice('1d20')
        # Conditional augury bonus (max Luck mod) applied only for vs_traps / vs_poison
        aug_bonus = 0
        try:
            eff = str((data.get('birth_augur') or {}).get('effect') or '').strip()
            mlm = int(get_max_luck_mod(data) or 0)
            if mlm:
                if bool(vs_traps) and eff == 'Saves vs traps':
                    aug_bonus += mlm
                if bool(vs_poison) and eff == 'Saves vs poison':
                    aug_bonus += mlm
        except Exception:
            aug_bonus = 0
        total = int(roll) + int(save_mod) + b + int(aug_bonus)
        # Global penalty (e.g., groggy)
        gpen, gnotes = get_global_roll_penalty(data)
        if gpen:
            total += int(gpen)

        burn_used = 0
        luck_bonus = 0
        donor_used = False
        if isinstance(burn, int) and burn and burn > 0:
            try:
                requested = int(burn)
            except Exception:
                requested = 0
            if requested > 0:
                # Donor halfling path
                if burn_from_halfling and burn_from_halfling.strip():
                    donor = await self._load_record(burn_from_halfling)
                    if not donor or str(donor.get('class','')).strip().lower() != 'halfling':
                        await interaction.response.send_message("Halfling donor not found or not a Halfling.", ephemeral=True)
                        return
                    donor_name = str(donor.get('name') or burn_from_halfling)
                    safe = donor_name.lower().strip().replace(' ', '_')
                    path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                    burn_used = int(consume_luck_and_save(donor, requested, filename=path) or 0)
                    total += burn_used  # 1:1 from halfling donor
                    donor_used = True
                else:
                    safe = name.lower().strip().replace(' ', '_')
                    path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                    burn_used = int(consume_luck_and_save(data, requested, filename=path) or 0)
                    cls = str(data.get('class') or '').strip().lower()
                    if burn_used > 0 and cls == 'thief':
                        luck_die = str(data.get('luck_die') or 'd3')
                        bonus, _ = roll_dice(f"{burn_used}{luck_die}")
                        luck_bonus = int(bonus)
                        total += luck_bonus
                    elif burn_used > 0 and cls == 'halfling':
                        total += (2 * burn_used)
                    else:
                        total += burn_used

        dc_text = f" vs DC {int(dc)}" if isinstance(dc, int) and dc > 0 else ""
        result_text = ""
        if isinstance(dc, int) and dc > 0:
            result_text = " — Success!" if total >= int(dc) else " — Fail"

        parts = [
            f"🛡️ {data.get('name','Unknown')} rolls a {title} save{dc_text}:\n",
            f"• Roll: {roll} (1d20) + save {save_mod:+} + bonus {b:+}"
        ]
        if aug_bonus:
            note = ' vs traps' if bool(vs_traps) else (' vs poison' if bool(vs_poison) else '')
            parts.append(f" + augur {int(aug_bonus):+}{note}")
        if gpen:
            parts.append(f" + {'; '.join(gnotes)}")
        if burn_used:
            if donor_used:
                parts.append(f" + LUCK (Halfling {burn_from_halfling}) {burn_used:+}")
            elif luck_bonus:
                parts.append(f" + LUCK ({burn_used}{str(data.get('luck_die') or 'd3')} = {luck_bonus})")
            else:
                cls_here = str(data.get('class') or '').strip().lower()
                if cls_here == 'halfling':
                    # Show halfling doubling as single annotation burn->+2*burn
                    parts.append(f" + LUCK {burn_used}→{2*burn_used:+}")
                else:
                    parts.append(f" + LUCK {burn_used:+}")
        parts.append(f" = **{total}**{result_text}")
        await interaction.response.send_message("".join(parts))

    @app_commands.command(name="check", description="Ability/skill check: trained d20, untrained d10; optional Luck burn (self or from a Halfling)")
    @app_commands.describe(
        name="Character name",
    ability="Ability (Strength/Agility/Stamina/Intelligence/Personality/Luck)",
        trained="Trained (True) or untrained (False)",
        bonus="Flat bonus to add (optional)",
        dc="Optional DC to compare against",
        burn="Luck to burn (adds to roll and subtracts from current Luck)",
        burn_from_halfling="Burn these Luck points from the named Halfling instead (1:1 bonus)",
    )
    @app_commands.choices(ability=ABILITY_CHOICES)
    async def ability_check(
        self,
        interaction: discord.Interaction,
        name: str,
        ability: app_commands.Choice[str],
        trained: bool = False,
        bonus: Optional[int] = 0,
        dc: Optional[int] = None,
        burn: Optional[int] = 0,
        burn_from_halfling: Optional[str] = None,
    ):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return

        ab = ability.value
        # Special-case Luck checks: flat 1d20, no modifiers; success if <= Luck score
        if ab == 'LCK':
            die = '1d20'
            roll, _ = roll_dice(die)
            try:
                threshold = int(get_luck_current(data))
            except Exception:
                # fallback to ability score if present
                lck = data.get('abilities', {}).get('LCK', {})
                threshold = int(lck.get('current', lck.get('max', lck.get('score', 0))) or 0)
            success = int(roll) <= int(threshold)
            msg_lines = [
                f"🍀 {data.get('name','Unknown')} makes a Luck check:\n"
                f"• Roll: {roll} ({die}) vs Luck {threshold} = **{'Success' if success else 'Fail'}**"
            ]
            # Ignore burn for Luck checks to honor flat-roll rule
            if isinstance(burn, int) and burn and burn > 0:
                msg_lines.append("\n• Note: Luck burn is not applied to Luck checks.")
            await interaction.response.send_message("".join(msg_lines))
            return

        # Other ability checks: trained d20, untrained d10, include ability mod and optional bonus
        die = '1d20' if trained else '1d10'
        roll, _ = roll_dice(die)
        mod = self._ability_mod(data, ab)
        b = int(bonus or 0)
        total = int(roll) + int(mod) + b
        # Global penalty (e.g., groggy)
        gpen2, gnotes2 = get_global_roll_penalty(data)
        if gpen2:
            total += int(gpen2)

        # Augury: Born under the loom — skill checks (including thief skills)
        try:
            aug_eff = str((data.get('birth_augur') or {}).get('effect') or '').strip()
            if aug_eff == 'Skill checks (including thief skills)':
                mlm = int(get_max_luck_mod(data) or 0)
                if mlm:
                    total += mlm
                    # reflect in output line
                    b = b + 0  # keep bonus label unchanged; we append a note below instead
                    aug_note = f" + augur {mlm:+}"
                else:
                    aug_note = ""
            else:
                aug_note = ""
        except Exception:
            aug_note = ""

        # Optional Luck burn: adds to total and reduces current Luck (self),
        # or from a named Halfling (1:1, no thief luck die, no halfling doubling)
        burn_used = 0
        luck_bonus = 0
        if isinstance(burn, int) and burn and burn > 0:
            # Persist the luck consumption
            requested = int(burn)
            donor_label = None
            if burn_from_halfling and str(burn_from_halfling).strip():
                donor = await self._load_record(str(burn_from_halfling))
                if not donor or str(donor.get('class','')).strip().lower() != 'halfling':
                    await interaction.response.send_message("Halfling donor not found or not a Halfling.", ephemeral=True)
                    return
                donor_label = str(donor.get('name') or burn_from_halfling)
                safe = donor_label.strip().lower().replace(' ', '_')
                path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                burn_used = int(consume_luck_and_save(donor, requested, filename=path) or 0)
                total += burn_used  # 1:1 bonus
            else:
                safe = name.lower().strip().replace(' ', '_')
                path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                burn_used = int(consume_luck_and_save(data, requested, filename=path) or 0)
                # Thief Luck & Wits: roll luck die per point burned
                cls = str(data.get('class') or '').strip().lower()
                if burn_used > 0 and cls == 'thief':
                    luck_die = str(data.get('luck_die') or 'd3')
                    bonus, _ = roll_dice(f"{burn_used}{luck_die}")
                    luck_bonus = int(bonus)
                    total += luck_bonus
                elif burn_used > 0 and cls == 'halfling':
                    total += (2 * burn_used)
                else:
                    total += burn_used

        dc_text = ""
        result_text = ""
        if isinstance(dc, int) and dc > 0:
            dc_text = f" vs DC {dc}"
            result_text = " — Success!" if total >= dc else " — Fail"

        label = ability_name(ab)
        emj = ability_emoji(ab)
        parts = [
            f"🎲 {data.get('name','Unknown')} makes a {label} check{dc_text}:\n",
            f"• {emj+' ' if emj else ''}Roll: {roll} ({die}) + {label} {mod:+} + bonus {b:+}{aug_note}"
        ]
        if gpen2:
            parts.append(f" + {'; '.join(gnotes2)}")
        if burn_used:
            if luck_bonus:
                parts.append(f" + LUCK ({burn_used}{str(data.get('luck_die') or 'd3')} = {luck_bonus})")
            else:
                if burn_from_halfling and str(burn_from_halfling).strip():
                    parts.append(f" + LUCK (Halfling {burn_from_halfling}) {burn_used:+}")
                elif str(data.get('class') or '').strip().lower() == 'halfling':
                    parts.append(f" + LUCK {burn_used}→{2*burn_used:+}")
                else:
                    parts.append(f" + LUCK {burn_used:+}")
            # If requested more than available, add a small cap note
            try:
                if isinstance(burn, int) and burn > burn_used:
                    parts.append(" You're not lucky enough for that.")
            except Exception:
                pass
        parts.append(f" = **{total}**{result_text}")
        # Post message; append warning if Luck ran out
        try:
            cur_luck_after = int(get_luck_current(data))
        except Exception:
            cur_luck_after = None
        msg = "".join(parts)
        if burn_used and cur_luck_after is not None and cur_luck_after <= 0:
            msg += "\nUh-oh, you're luck has run out!"
        await interaction.response.send_message(msg)

    # --- Morale ---
    @app_commands.command(name="morale", description="Morale: d20 + Will vs DC (11). Optional motivation and employer PER for retainers.")
    @app_commands.describe(
        name="Creature name (if a saved character) or leave blank to use direct will modifier",
        will_mod="Direct Will save modifier (used if name is not provided or not found)",
        dc="DC to meet or beat (default 11)",
        motivation="Judge modifier from -4 to +4",
        employer="Employer name (adds employer's Personality mod for retainers)",
    )
    async def morale_check(
        self,
        interaction: discord.Interaction,
        name: Optional[str] = None,
        will_mod: Optional[int] = None,
        dc: Optional[int] = 11,
        motivation: Optional[int] = 0,
        employer: Optional[str] = None,
    ):
        # Resolve will modifier: prefer character by name; if omitted, use current initiative combatant; else direct will_mod
        who = (name or '').strip()
        data = await self._load_record(who) if who else None
        # If no name provided, attempt to use current initiative combatant label
        if not data and not who and CURRENT_TURN_INDEX is not None:
            try:
                cur = INITIATIVE_ORDER[CURRENT_TURN_INDEX]
                who = cur.get('name') or cur.get('display') or 'Creature'
            except Exception:
                who = 'Creature'
        resolved_will = None
        label = who or 'Creature'
        if data:
            label = data.get('name', who) or who
            resolved_will = self._will_save_total(data)
        if resolved_will is None:
            try:
                resolved_will = int(will_mod if will_mod is not None else 0)
            except Exception:
                resolved_will = 0

        # Employer PER mod for retainers
        emp_mod = 0
        if employer:
            emp = await self._load_record(employer)
            if emp:
                emp_mod = int(self._ability_mod(emp, 'PER') or 0)

        # Motivation clamp
        try:
            mot = int(motivation or 0)
        except Exception:
            mot = 0
        mot = max(-4, min(4, mot))

        # Default DC 11 if not provided
        try:
            the_dc = int(dc if dc is not None else 11)
        except Exception:
            the_dc = 11

        # Roll d20 + will + employer PER + motivation
        roll, _ = roll_dice('1d20')
        total = int(roll) + int(resolved_will) + int(emp_mod) + int(mot)
        # Global penalty if this is a saved character
        gpen = 0; gnotes: list[str] = []
        if data:
            gpen, gnotes = get_global_roll_penalty(data)
            if gpen:
                total += int(gpen)
        success = total >= the_dc
        parts = [
            f"🧠 Morale check for {label}: d20 {roll} + Will {resolved_will:+} + Employer PER {emp_mod:+} + Motivation {mot:+}"
        ]
        if data and gpen:
            parts.append(f" + {'; '.join(gnotes)}")
        parts.append(f" = **{total}** vs DC {the_dc} — ")
        parts.append("Success!" if success else "Fail (attempts to flee)")
        await interaction.response.send_message("".join(parts))

    @morale_check.autocomplete('name')
    async def morale_name_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or '').lower()
        items: list[app_commands.Choice[str]] = []
        # First, suggest current initiative combatants
        try:
            for e in INITIATIVE_ORDER:
                disp = str(e.get('name') or e.get('display') or '').strip()
                if not disp:
                    continue
                if cur and cur not in disp.lower():
                    continue
                items.append(app_commands.Choice(name=f"[INIT] {disp}", value=disp))
                if len(items) >= 15:
                    break
        except Exception:
            pass
        # Fill remaining with saved character names
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                disp = fn[:-5].replace('_', ' ')
                if cur and cur not in disp.lower():
                    continue
                items.append(app_commands.Choice(name=disp, value=disp))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items

    @morale_check.autocomplete('employer')
    async def morale_employer_ac(self, interaction: discord.Interaction, current: str):
        return await self.morale_name_ac(interaction, current)

    # --- Saving throws (grouped commands) ---
    save = app_commands.Group(name="save", description="Roll saving throws")

    @save.command(name="will", description="Roll a Will save (optional Luck burn or Halfling donor)")
    @app_commands.describe(
        name="Character name",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., magic)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
        vs_traps="Saving against a trap? (applies 'Saves vs traps' augury)",
        vs_poison="Saving against poison? (applies 'Saves vs poison' augury)",
    )
    @app_commands.describe(burn_from_halfling="Burn these Luck points from the named Halfling instead (1:1 bonus)")
    async def save_will(self, interaction: discord.Interaction, name: str, dc: Optional[int] = None, bonus: Optional[int] = 0, burn: Optional[int] = 0, vs_traps: Optional[bool] = False, vs_poison: Optional[bool] = False, burn_from_halfling: Optional[str] = None):
        await self._do_save(interaction, name, 'will', 'Will', dc, bonus, burn, vs_traps=vs_traps, vs_poison=vs_poison, burn_from_halfling=burn_from_halfling)

    @save.command(name="fort", description="Roll a Fortitude save (optional Luck burn or Halfling donor)")
    @app_commands.describe(
        name="Character name",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., magic)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
        vs_traps="Saving against a trap? (applies 'Saves vs traps' augury)",
        vs_poison="Saving against poison? (applies 'Saves vs poison' augury)",
    )
    @app_commands.describe(burn_from_halfling="Burn these Luck points from the named Halfling instead (1:1 bonus)")
    async def save_fort(self, interaction: discord.Interaction, name: str, dc: Optional[int] = None, bonus: Optional[int] = 0, burn: Optional[int] = 0, vs_traps: Optional[bool] = False, vs_poison: Optional[bool] = False, burn_from_halfling: Optional[str] = None):
        await self._do_save(interaction, name, 'fortitude', 'Fortitude', dc, bonus, burn, vs_traps=vs_traps, vs_poison=vs_poison, burn_from_halfling=burn_from_halfling)

    @save.command(name="ref", description="Roll a Reflex save (optional Luck burn or Halfling donor)")
    @app_commands.describe(
        name="Character name",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., magic)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
        vs_traps="Saving against a trap? (applies 'Saves vs traps' augury)",
        vs_poison="Saving against poison? (applies 'Saves vs poison' augury)",
    )
    @app_commands.describe(burn_from_halfling="Burn these Luck points from the named Halfling instead (1:1 bonus)")
    async def save_ref(self, interaction: discord.Interaction, name: str, dc: Optional[int] = None, bonus: Optional[int] = 0, burn: Optional[int] = 0, vs_traps: Optional[bool] = False, vs_poison: Optional[bool] = False, burn_from_halfling: Optional[str] = None):
        await self._do_save(interaction, name, 'reflex', 'Reflex', dc, bonus, burn, vs_traps=vs_traps, vs_poison=vs_poison, burn_from_halfling=burn_from_halfling)

    # Autocomplete for character name (save subcommands)
    @save_will.autocomplete("name")
    async def _ac_name_save_will(self, interaction: discord.Interaction, current: str):
        try:
            items = []
            cur = (current or "").lower()
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.lower().endswith('.json'):
                    continue
                name = os.path.splitext(fn)[0]
                if cur in name.lower():
                    items.append(app_commands.Choice(name=name, value=name))
                if len(items) >= 25:
                    break
            return items
        except Exception:
            return []

    @save_fort.autocomplete("name")
    async def _ac_name_save_fort(self, interaction: discord.Interaction, current: str):
        return await self._ac_name_save_will(interaction, current)

    @save_ref.autocomplete("name")
    async def _ac_name_save_ref(self, interaction: discord.Interaction, current: str):
        return await self._ac_name_save_will(interaction, current)

    # --- Luck sharing ---
    luck = app_commands.Group(name="luck", description="Luck utilities")

    @luck.command(name="burn", description="Burn Luck from a named Halfling to aid an ally (1 point burned = +1 bonus to the ally's roll)")
    @app_commands.describe(
        halfling="Halfling character name to burn Luck from",
        ally="Ally name (free text or saved character name to annotate)",
        points="Luck points to burn (consumed from the halfling)",
        note="Optional context (e.g., 'attack', 'save', etc.)",
    )
    async def burn(self, interaction: discord.Interaction, halfling: str, ally: str, points: int, note: str | None = None):
        data = await self._load_record(halfling)
        if not data:
            await interaction.response.send_message(f"Character '{halfling}' not found.", ephemeral=True)
            return
        if str(data.get('class') or '').strip().lower() != 'halfling':
            await interaction.response.send_message("Only Halflings can share Luck.", ephemeral=True)
            return
        # Burn luck from the halfling and grant +points to ally
        safe = str(data.get('name') or halfling).strip().lower().replace(' ', '_')
        path = os.path.join(SAVE_FOLDER, f"{safe}.json")
        used = int(consume_luck_and_save(data, int(points), filename=path) or 0)
        if used <= 0:
            await interaction.response.send_message("No Luck burned (insufficient Luck).", ephemeral=True)
            return
        # By rule, halfling doubles Luck burned on their own rolls; shared Luck is 1:1 to allies
        bonus = used
        # Current Luck after burn
        try:
            cur_after = int(get_luck_current(data))
        except Exception:
            cur_after = None
        msg = [
            f"🍀 {data.get('name','Halfling')} burns Luck for {ally}: burn {used} → ally gains +{bonus}",
        ]
        if note:
            msg.append(f" ({note})")
        if cur_after is not None:
            msg.append(f". {data.get('name','Halfling')}'s Luck now {cur_after}.")
        await interaction.response.send_message("".join(msg))


    # --- Halfling/Thief: Sneak & Hide commands ---
    def _halfling_sneak_hide_base(self, data: dict) -> int:
        """Return Halfling Sneak & Hide base bonus from record or inferred by level."""
        try:
            if str(data.get('class','')).strip().lower() != 'halfling':
                return 0
            if 'sneak_hide' in data and data.get('sneak_hide') is not None:
                return int(data.get('sneak_hide') or 0)
            lvl = int(data.get('level', 0) or 0)
            table = {1:3,2:5,3:7,4:8,5:9,6:11,7:12,8:13,9:14,10:15}
            return int(table.get(lvl, 0))
        except Exception:
            return 0

    def _augur_skill_bonus(self, data: dict) -> int:
        """Max Luck mod if augur is 'Skill checks (including thief skills)'"""
        try:
            eff = str((data.get('birth_augur') or {}).get('effect') or '').strip()
            if eff == 'Skill checks (including thief skills)':
                mlm = int(get_max_luck_mod(data) or 0)
                return mlm
        except Exception:
            pass
        return 0

    async def _do_sneak_or_hide(self, interaction: discord.Interaction, name: str, which: str,
                                dc: int | None = None, bonus: int | None = 0, burn: int | None = 0):
        data = await self._load_record(name)
        if not data:
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return

        cls = str(data.get('class') or '').strip().lower()
        which_key = 'sneak_silently' if which == 'sneak' else 'hide_in_shadows'
        display = 'Sneak Silently' if which == 'sneak' else 'Hide in Shadows'

        # Determine base skill bonus and governing ability (AGI for both)
        base = 0
        if cls == 'thief':
            ts = data.get('thief_skills') if isinstance(data.get('thief_skills'), dict) else None
            skills = (ts or {}).get('skills') if isinstance(ts, dict) else {}
            try:
                base = int((skills or {}).get(which_key, 0) or 0)
            except Exception:
                base = 0
        elif cls == 'halfling':
            base = self._halfling_sneak_hide_base(data)
        else:
            await interaction.response.send_message("Only Halflings and Thieves use these commands.", ephemeral=True)
            return

        agi_mod = self._ability_mod(data, 'AGI')
        b = int(bonus or 0)

        # d20 roll + base + AGI + situational bonus + augur (if any)
        roll, _ = roll_dice('1d20')
        aug = self._augur_skill_bonus(data)
        total = int(roll) + int(base) + int(agi_mod) + int(b) + int(aug)
        # Global penalty (e.g., groggy)
        gpen, gnotes = get_global_roll_penalty(data)
        if gpen:
            total += int(gpen)

        # Optional Luck burn; Thieves roll luck die per point (Luck & Wits), Halflings add flat
        burn_used = 0
        luck_bonus = 0
        if isinstance(burn, int) and burn and burn > 0:
            safe = name.lower().strip().replace(' ', '_')
            path = os.path.join(SAVE_FOLDER, f"{safe}.json")
            burn_used = int(consume_luck_and_save(data, int(burn), filename=path) or 0)
            if burn_used > 0 and cls == 'thief':
                ld = str(data.get('luck_die') or 'd3')
                luck_bonus, _ = roll_dice(f"{burn_used}{ld}")
                total += int(luck_bonus)
            else:
                total += burn_used

        dc_text = f" vs DC {int(dc)}" if isinstance(dc, int) and dc > 0 else ""
        result_text = ""
        if isinstance(dc, int) and dc > 0:
            result_text = " — Success!" if total >= int(dc) else " — Fail"

        parts = [
            f"🥷 {data.get('name','Unknown')} {display}{dc_text}:\n",
            f"• Roll: {roll} (1d20) + base {base:+} + AGI {agi_mod:+} + bonus {b:+}"
        ]
        if aug:
            parts.append(f" + augur {aug:+}")
        if burn_used:
            if luck_bonus:
                ld = str(data.get('luck_die') or 'd3')
                parts.append(f" + LUCK ({burn_used}{ld} = {luck_bonus})")
            else:
                if cls == 'halfling':
                    parts.append(f" + LUCK {burn_used}→{2*burn_used:+}")
                    total += burn_used  # add the second +burn_used (first already applied above)
                else:
                    parts.append(f" + LUCK {burn_used:+}")
        if gpen:
            parts.append(f" + {'; '.join(gnotes)}")
        parts.append(f" = **{total}**{result_text}")
        await interaction.response.send_message("".join(parts))

    @app_commands.command(name="sneak", description="Roll Sneak Silently (Halfling base+AGI or Thief skill+AGI). Optional DC and Luck burn.")
    @app_commands.describe(
        name="Character name (Halfling or Thief)",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., terrain, aid)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
    )
    async def sneak(self, interaction: discord.Interaction, name: str, dc: int | None = None, bonus: int | None = 0, burn: int | None = 0):
        await self._do_sneak_or_hide(interaction, name, 'sneak', dc, bonus, burn)

    @app_commands.command(name="hide", description="Roll Hide in Shadows (Halfling base+AGI or Thief skill+AGI). Optional DC and Luck burn.")
    @app_commands.describe(
        name="Character name (Halfling or Thief)",
        dc="Optional DC to compare against",
        bonus="Flat situational bonus (e.g., lighting, cover)",
        burn="Luck to burn (adds to roll and reduces current Luck)",
    )
    async def hide(self, interaction: discord.Interaction, name: str, dc: int | None = None, bonus: int | None = 0, burn: int | None = 0):
        await self._do_sneak_or_hide(interaction, name, 'hide', dc, bonus, burn)

    @sneak.autocomplete('name')
    @hide.autocomplete('name')
    async def _ac_name_sneak_hide(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        items: list[app_commands.Choice[str]] = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                nm = fn[:-5].replace('_',' ')
                if q and q not in nm.lower():
                    continue
                # Filter to halfling or thief for relevance
                try:
                    with open(os.path.join(SAVE_FOLDER, fn), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    cls = str(data.get('class','')).strip().lower()
                    if cls not in {'halfling','thief'}:
                        continue
                except Exception:
                    continue
                items.append(app_commands.Choice(name=nm, value=nm))
                if len(items) >= 25:
                    break
        except Exception:
            pass
        return items


async def setup(bot: commands.Bot):
    await bot.add_cog(ChecksCog(bot))
