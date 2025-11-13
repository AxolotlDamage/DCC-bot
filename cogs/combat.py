import os, json, re
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List

from modules import initiative as init_mod  # type: ignore

from core.config import SAVE_FOLDER  # type: ignore
from utils.dice import roll_dice
from modules.utils import (
    get_modifier, dcc_dice_chain_step, is_weapon_trained,
    get_luck_current, consume_luck_and_save, get_max_luck_mod,
    ability_name, ability_emoji,
    double_damage_dice_expr,
    select_crit_table_for_character, load_crit_tables, lookup_crit_entry,
    load_fumble_tables, lookup_fumble_entry,
    resolve_crit_damage_bonus, roll_multiple_dice_expr,
    tags_to_conditions, apply_condition, load_conditions,
    apply_targeted_effects_from_entry, apply_targeted_effects_from_tags,
    get_global_roll_penalty,
)  # type: ignore
from modules.data_constants import WEAPON_TABLE  # type: ignore


class CombatCog(commands.Cog):
    """Combat-related slash commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Helpers
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

    def _save_record(self, name: str, data: dict) -> bool:
        safe = name.lower().strip().replace(' ', '_')
        path = os.path.join(SAVE_FOLDER, f"{safe}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False

    def _ability_mod(self, data: dict, key: str) -> int:
        try:
            v = data.get('abilities', {}).get(key, {})
            if isinstance(v, dict):
                return int(v.get('mod', 0))
            return int(get_modifier(int(v)))
        except Exception:
            return 0

    def _normalize_inventory(self, data: dict) -> list[dict]:
        raw = data.get('inventory') or []
        out: list[dict] = []
        for it in raw if isinstance(raw, list) else []:
            if isinstance(it, dict):
                nm = str(it.get('name') or it.get('item') or '').strip()
                if not nm:
                    continue
                try:
                    qty = int(it.get('qty', 1) or 1)
                except Exception:
                    qty = 1
                note = str(it.get('note') or '').strip() or None
                rec: dict = {'name': nm, 'qty': max(0, qty)}
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
        data['inventory'] = out
        return out

    def _has_in_inventory(self, data: dict, name: str) -> bool:
        inv = self._normalize_inventory(data)
        key = (name or '').strip().lower()
        for it in inv:
            try:
                if it.get('name','').strip().lower() == key and int(it.get('qty',0)) > 0:
                    return True
            except Exception:
                continue
        return False

    def _attack_bonus(self, data: dict) -> int:
        try:
            return int(data.get('attack_bonus', data.get('attack', 0)) or 0)
        except Exception:
            return 0

    def _parse_action_dice(self, data: dict) -> List[str]:
        """Return list of action dice from character data.
        Prefers 'action_dice' (e.g., '1d20+1d14'); falls back to 'action_die'.
        """
        try:
            raw = str(data.get('action_dice') or data.get('action_die') or '1d20')
        except Exception:
            raw = '1d20'
        parts = [p.strip() for p in raw.replace(',', '+').split('+') if p.strip()]
        return parts if parts else ['1d20']

    # Command: /attack
    @app_commands.command(name="attack", description="Make an attack with your equipped weapon or an initiative monster's saved attack (optional Halfling donor Luck)")
    @app_commands.describe(
        name="Attacker: character name or initiative monster name/abbr",
        target="Target name (character or initiative monster)",
        weapon="Override weapon (characters only)",
        offhand="Off-hand weapon (two-weapon fighting; melee only, no shield)",
        range="Range band (thrown: Strength to damage only at close)",
    burn="Luck to burn (characters only; self-burn doubles for Halflings; thief rolls luck die per point)",
    burn_from_halfling="Burn these Luck points from the named Halfling (1:1 bonus; no doubling)",
        backstab="Backstab attempt (thief only): adds backstab bonus; on hit, auto-crit",
        deed="Warrior/Dwarf Mighty Deed: add deed die to attack and damage (rolls per attack)",
        deed_text="Describe your deed (e.g., 'trip the ogre')",
        mounted="Are you mounted?",
        target_mounted="Is the target mounted?",
        charge="Charge attack (mounted lance/spear doubles damage dice)",
        target_ac="Target AC override",
        into_melee="Firing into melee (50% friendly fire on miss)",
        ally="Name of ally at risk from friendly fire",
        attack="For initiative monsters: which saved attack to use",
        force="Force the attack die result (testing)"
    )
    @app_commands.choices(range=[
        app_commands.Choice(name="close", value="close"),
        app_commands.Choice(name="medium", value="medium"),
        app_commands.Choice(name="long", value="long"),
    ])
    @app_commands.describe(die="Action die to use (e.g., 1d20, 1d16); default first")
    async def attack(self, interaction: discord.Interaction, name: str, target: Optional[str] = None, weapon: Optional[str] = None, offhand: Optional[str] = None, range: Optional[app_commands.Choice[str]] = None, burn: Optional[int] = 0, burn_from_halfling: Optional[str] = None, backstab: Optional[bool] = False, deed: Optional[bool] = False, deed_text: Optional[str] = None, mounted: Optional[bool] = False, target_mounted: Optional[bool] = False, charge: Optional[bool] = False, target_ac: Optional[int] = None, into_melee: Optional[bool] = False, ally: Optional[str] = None, attack: Optional[str] = None, force: Optional[int] = None, die: Optional[str] = None):
        data = await self._load_record(name)
        # If no character, attempt initiative monster path
        if not data:
            # Find attacker in initiative by exact name or abbr (case-insensitive)
            attacker = None
            q = (name or '').strip().lower()
            for e in init_mod.INITIATIVE_ORDER:
                nm = str(e.get('name','')).strip().lower()
                ab = str(e.get('abbr','')).strip().lower()
                if q and (q == nm or q == ab):
                    attacker = e
                    break
            if not attacker:
                await interaction.response.send_message(f"Attacker '{name}' not found (no character or initiative entry).", ephemeral=True)
                return
            # Parse attacks list from stored 'atk'
            atk_field = str(attacker.get('atk') or '').strip()
            if not atk_field:
                await interaction.response.send_message(f"No saved attacks found for '{attacker.get('name')}'. Use /init add with the attacks field.", ephemeral=True)
                return
            chunks = [c.strip() for c in re.split(r"[;,]", atk_field) if c.strip()]
            pat = re.compile(r"^(.+?)\s*([+-]\d+)?\s*\(([^)]+)\)\s*$")
            parsed = []
            for c in chunks:
                m = pat.match(c)
                if m:
                    nm = m.group(1).strip();
                    try:
                        md = int(m.group(2) or 0)
                    except Exception:
                        md = 0
                    dmg = m.group(3).strip()
                    parsed.append({'name': nm, 'mod': md, 'dmg': dmg, 'display': f"{nm} {md:+} ({dmg})"})
                else:
                    parsed.append({'name': c, 'mod': 0, 'dmg': None, 'display': c})
            if not parsed:
                await interaction.response.send_message(f"Could not parse any attacks for '{attacker.get('name')}'.", ephemeral=True)
                return
            # Choose attack by prefix/contains match if provided, else first
            choice = None
            if attack:
                aq = attack.strip().lower()
                for a in parsed:
                    disp = a['display'].lower()
                    nm = a['name'].lower()
                    if aq in disp or aq in nm:
                        choice = a; break
            if not choice:
                choice = parsed[0]

            # Determine action die (initiative monsters: optional simple parsing of multiple dice; default first)
            raw_act = str(attacker.get('act') or '1d20')
            act_parts = [p.strip() for p in raw_act.replace(',', '+').split('+') if p.strip()]
            idx = 0
            if die:
                s = str(die).strip().lower()
                # match by die expression
                try:
                    for i, d in enumerate(act_parts):
                        if str(d).strip().lower() == s:
                            idx = i
                            break
                except Exception:
                    pass
                # backward-compat textual indices
                if idx == 0 and s in {"second","2","#2","die2"} and len(act_parts) >= 2:
                    idx = 1
                elif idx == 0 and s in {"third","3","#3","die3"} and len(act_parts) >= 3:
                    idx = 2
            act_die = act_parts[idx] if act_parts else '1d20'
            atk_roll, atk_rolls = roll_dice(act_die, force=force)
            atk_total = int(atk_roll) + int(choice.get('mod') or 0)

            # Resolve target AC: prefer loaded character or initiative entry, else provided override
            defender_ac = None
            defender_label = None
            defender_data = None  # character dict if any
            if target:
                tname = target.strip()
                # Try character first
                defender_data = await self._load_record(tname)
                if defender_data:
                    try:
                        defender_ac = int(defender_data.get('ac', 10) or 10)
                        defender_label = defender_data.get('name') or tname
                    except Exception:
                        defender_ac = None
                else:
                    # Try initiative entry
                    tq = tname.lower()
                    tgt = None
                    for e in init_mod.INITIATIVE_ORDER:
                        nm = str(e.get('name','')).strip().lower()
                        ab = str(e.get('abbr','')).strip().lower()
                        if tq and (tq == nm or tq == ab):
                            tgt = e; break
                    if tgt is not None:
                        defender_label = tgt.get('name')
                        try:
                            defender_ac = int(tgt.get('ac')) if (tgt.get('ac') is not None) else None
                        except Exception:
                            defender_ac = None
            # Fall back to explicit target_ac param
            if defender_ac is None and target_ac is not None:
                try:
                    defender_ac = int(target_ac)
                except Exception:
                    defender_ac = None

            # Roll damage
            dmg_total, dmg_rolls = roll_dice(choice.get('dmg') or '1d2')
            dmg_final = max(1, int(dmg_total))

            # Determine hit/miss if AC available
            is_hit = None
            hit_text = ''
            tac_val = None
            if defender_ac is not None:
                tac_val = int(defender_ac)
                is_hit = bool(int(atk_total) >= int(defender_ac))
                hit_text = f" vs AC {defender_ac} " + ('HIT' if is_hit else 'MISS')

            # Apply damage to character target if hit
            apply_text = ""
            if is_hit is True:
                if defender_data is not None:
                    try:
                        hp = defender_data.get('hp', {}) if isinstance(defender_data.get('hp'), dict) else {}
                        cur = int(hp.get('current', 0) or 0)
                        mx = hp.get('max')
                        try:
                            mx = int(mx) if mx is not None else None
                        except Exception:
                            mx = None
                        new_cur = max(0, int(cur) - int(dmg_final))
                        hp['current'] = int(new_cur)
                        if mx is not None:
                            hp['max'] = int(mx)
                        defender_data['hp'] = hp
                        self._save_record(defender_label or target, defender_data)
                        apply_text = f"\n• {defender_label or target} HP: {cur} → {new_cur}"
                    except Exception:
                        apply_text = ""
                else:
                    # If defender is an initiative entry with hp, reduce it in memory
                    if target:
                        tq = target.strip().lower()
                        tgt = None
                        for e in init_mod.INITIATIVE_ORDER:
                            nm = str(e.get('name','')).strip().lower()
                            ab = str(e.get('abbr','')).strip().lower()
                            if tq and (tq == nm or tq == ab):
                                tgt = e; break
                        if tgt is not None and tgt.get('hp') is not None:
                            try:
                                cur = int(tgt.get('hp') or 0)
                            except Exception:
                                cur = 0
                            new_cur = max(0, cur - int(dmg_final))
                            tgt['hp'] = int(new_cur)
                            apply_text = f"\n• {tgt.get('name')} HP: {cur} → {new_cur}"

            # Basic nat 20/1 banner
            die_sides = 0
            try:
                if 'd' in act_die:
                    die_sides = int(act_die.split('d',1)[1])
            except Exception:
                die_sides = 0
            nat_text = ''
            if atk_rolls and die_sides:
                r = atk_rolls[0]
                if r == die_sides:
                    nat_text = ' — Critical!'
                elif r == 1:
                    nat_text = ' — Fumble!'

            title_target = f" → {defender_label or target}" if (defender_label or target) else ''
            emb = discord.Embed(
                title=f"{attacker.get('name','Monster')} attacks{title_target}",
                description=f"Attack: {choice.get('display')}",
                color=0x2ECC71 if is_hit is True else (0xE67E22 if is_hit is False else 0x95A5A6),
            )
            atk_field = (
                f"Roll {atk_roll} on {act_die}; Attack {int(choice.get('mod') or 0):+}; Total {atk_total}{hit_text}{nat_text}"
            )
            emb.add_field(name="Attack", value=atk_field, inline=False)
            dmg_field = f"{choice.get('dmg') or '1d2'} = {dmg_rolls} → {dmg_final} damage"
            emb.add_field(name="Damage", value=dmg_field, inline=False)
            if apply_text:
                emb.add_field(name="Applied", value=apply_text.strip(), inline=False)
            await interaction.response.send_message(embed=emb)
            return
        rng = (range.value if isinstance(range, app_commands.Choice) else None) or 'close'
        # Load defender if provided (for AC auto-hit calc and damage application)
        defender_data = None
        defender_ac = None
        if target:
            try:
                defender_data = await self._load_record(target)
                if defender_data:
                    defender_ac = int(defender_data.get('ac', 10) or 10)
            except Exception:
                defender_data = None
        # Determine weapon
        wkey = (weapon or data.get('weapon') or '').strip().lower()
        off_wkey = str(offhand or '').strip().lower()
        if not wkey:
            await interaction.response.send_message("No weapon equipped. Use /equip weapon or specify a weapon.", ephemeral=True)
            return
        is_shield_bash = False
        # Special case: Dwarf shield bash via /attack shield
        if wkey == 'shield':
            cls_low = str(data.get('class','') or '').strip().lower()
            if cls_low != 'dwarf':
                await interaction.response.send_message("❌ Shield bash is a Dwarf feature.", ephemeral=True)
                return
            # Must have shield equipped (not just in inventory)
            if not bool(data.get('shield')):
                await interaction.response.send_message("❌ You must have a shield equipped to use shield bash.", ephemeral=True)
                return
            # Enforce: only one shield bash per round (levels 5+ still only one bash per round)
            try:
                current_round = int(getattr(init_mod, 'COMBAT_ROUND', 0) or 0)
            except Exception:
                current_round = 0
            if current_round > 0:
                try:
                    rs = data.get('round_state') if isinstance(data.get('round_state'), dict) else {}
                except Exception:
                    rs = {}
                used_round = int(rs.get('shield_bash_round', -1) or -1)
                if used_round == current_round:
                    await interaction.response.send_message("❌ You have already used shield bash this round.", ephemeral=True)
                    return
                # Mark used for this round
                try:
                    rs['shield_bash_round'] = current_round
                    data['round_state'] = rs
                    await self._save_record(name, data)
                except Exception:
                    pass
            # Define a virtual weapon entry for shield bash
            wentry = {
                'damage': '1d3',
                'type': 'melee',
                'tags': ['melee','shield','bash']
            }
            weapon_meta = None
            is_shield_bash = True
        else:
            wentry = WEAPON_TABLE.get(wkey)
            weapon_meta = None
            if not isinstance(wentry, dict):
                # Try to resolve as a custom weapon from inventory metadata
                inv = self._normalize_inventory(data)
                for it in inv:
                    if str(it.get('name','')).strip().lower() == wkey and isinstance(it.get('weapon'), dict):
                        weapon_meta = dict(it['weapon'])
                        tags = list(weapon_meta.get('tags') or [])
                        wentry = {
                            'damage': weapon_meta.get('damage') or '1d2',
                            'type': 'missile' if any(str(t).lower() == 'missile' for t in tags) else 'melee',
                            'tags': tags,
                        }
                        break
                if not isinstance(wentry, dict):
                    await interaction.response.send_message(f"❌ Unknown weapon '{wkey}'.", ephemeral=True)
                    return
        # Inventory check
        if not self._has_in_inventory(data, wkey):
            await interaction.response.send_message(f"❌ You don't have a {wkey} in your inventory.", ephemeral=True)
            return
        # Two-weapon fighting off-hand resolution and validation (if provided)
        off_wentry = None
        off_weapon_meta = None
        use_twf = False
        if off_wkey:
            # No shield when dual-wielding
            if bool(data.get('shield')):
                await interaction.response.send_message("❌ Two-weapon fighting can't be used while a shield is equipped. Toggle your shield off first.", ephemeral=True)
                return
            # Primary must be melee and one-handed
            if str(wentry.get('type','melee')).lower() != 'melee':
                await interaction.response.send_message("❌ Two-weapon fighting is melee-only; your primary weapon isn't melee.", ephemeral=True)
                return
            if bool(wentry.get('two_handed')):
                await interaction.response.send_message("❌ Primary weapon must be one-handed for two-weapon fighting.", ephemeral=True)
                return
            # Resolve off-hand key
            off_wentry = WEAPON_TABLE.get(off_wkey)
            if not isinstance(off_wentry, dict):
                inv = self._normalize_inventory(data)
                for it in inv:
                    if str(it.get('name','')).strip().lower() == off_wkey and isinstance(it.get('weapon'), dict):
                        off_weapon_meta = dict(it['weapon'])
                        otags = list(off_weapon_meta.get('tags') or [])
                        off_wentry = {
                            'damage': off_weapon_meta.get('damage') or '1d2',
                            'type': 'missile' if any(str(t).lower() == 'missile' for t in otags) else 'melee',
                            'tags': otags,
                        }
                        break
                if not isinstance(off_wentry, dict):
                    await interaction.response.send_message(f"❌ Unknown off-hand weapon '{off_wkey}'.", ephemeral=True)
                    return
            # Off-hand inventory check
            if not self._has_in_inventory(data, off_wkey):
                await interaction.response.send_message(f"❌ You don't have an off-hand {off_wkey} in your inventory.", ephemeral=True)
                return
            # Off-hand must be melee and one-handed
            if str(off_wentry.get('type','melee')).lower() != 'melee':
                await interaction.response.send_message("❌ Two-weapon fighting is melee-only.", ephemeral=True)
                return
            if bool(off_wentry.get('two_handed')):
                await interaction.response.send_message("❌ Off-hand weapon must be one-handed.", ephemeral=True)
                return
            use_twf = True
        # Attack die (select from action dice; downgrade if untrained with this weapon per DCC dice chain)
        act_parts = self._parse_action_dice(data)
        # Interactive prompt if multiple dice and none specified (non-wizard classes only)
        used_prompt = False
        try:
            cls_low = str(data.get('class','') or '').strip().lower()
        except Exception:
            cls_low = ''
        if (not die) and len(act_parts) >= 2 and cls_low not in {'wizard','mage','elf'}:
            try:
                class DiePicker(discord.ui.View):
                    def __init__(self, dice: list[str]):
                        super().__init__(timeout=20)
                        self.choice: str | None = None
                        opts = [discord.SelectOption(label=d, value=d) for d in dice[:25]]
                        self.sel = discord.ui.Select(placeholder="Choose action die", options=opts, min_values=1, max_values=1)
                        async def on_change(itx: discord.Interaction):
                            self.choice = self.sel.values[0]
                            await itx.response.edit_message(content=f"Using {self.choice}", view=None)
                            self.stop()
                        self.sel.callback = on_change  # type: ignore
                        self.add_item(self.sel)
                view = DiePicker(act_parts)
                await interaction.response.send_message("Select which action die to use:", view=view, ephemeral=True)
                used_prompt = True
                await view.wait()
                if view.choice:
                    die = view.choice
            except Exception:
                pass
        idx = 0
        if die:
            s = str(die).strip().lower()
            try:
                for i, d in enumerate(act_parts):
                    if str(d).strip().lower() == s:
                        idx = i
                        break
            except Exception:
                pass
            if idx == 0 and s in {"second","2","#2","die2"} and len(act_parts) >= 2:
                idx = 1
            elif idx == 0 and s in {"third","3","#3","die3"} and len(act_parts) >= 3:
                idx = 2
        # Wizards' additional action dice are for spell checks only; force first die for attacks
        try:
            cls_low = str(data.get('class','') or '').strip().lower()
        except Exception:
            cls_low = ''
        if idx > 0 and cls_low in {'wizard','mage','elf'}:
            idx = 0
        base_action_die = act_parts[idx] if act_parts else '1d20'
        trained = is_weapon_trained(data, wkey)
        is_lv0 = str(data.get('class','')).strip().lower() in ('lv0','0','level 0','level-0')
        # Lv0 are untrained but do not suffer the untrained die penalty
        action_die = base_action_die if (trained or is_lv0) else dcc_dice_chain_step(base_action_die, -1)
        # Dwarf shield bash uses a d14 instead of a d20 for the attack roll
        if is_shield_bash:
            action_die = '1d14'
        # Two-weapon fighting per-hand dice (based on Agility mod) — adjust primary and compute off-hand die
        off_action_die = None
        if use_twf:
            agi_mod = self._ability_mod(data, 'AGI')
            # Halfling special: treat AGI as at least 16 (modifier floor to +2) when dual-wielding,
            # then use the normal two-weapon table. This allows high AGI to reach 0/0 penalties.
            try:
                if str((data.get('class') or '')).strip().lower() == 'halfling':
                    agi_mod = max(int(agi_mod), 2)
            except Exception:
                pass
            if agi_mod <= -3:
                prim_step, off_step = (-1, -4)
            elif agi_mod == -2:
                prim_step, off_step = (-1, -3)
            elif agi_mod == -1:
                prim_step, off_step = (-1, -2)
            elif agi_mod == 0:
                prim_step, off_step = (0, -2)
            elif agi_mod == 1:
                prim_step, off_step = (0, -1)
            else:
                prim_step, off_step = (0, 0)
            # Apply training penalties separately per hand
            off_trained = is_weapon_trained(data, off_wkey)
            prim_die_base = base_action_die if (trained or is_lv0) else dcc_dice_chain_step(base_action_die, -1)
            off_die_base  = base_action_die if (off_trained or is_lv0) else dcc_dice_chain_step(base_action_die, -1)
            action_die = dcc_dice_chain_step(prim_die_base, prim_step)
            off_action_die = dcc_dice_chain_step(off_die_base, off_step)
        atk_roll, atk_rolls = roll_dice(action_die, force=force)
        # Mods
        atk_bonus = self._attack_bonus(data)
        weapon_atk_bonus = 0
        ability_override = None
        no_ability_mod = False
        if weapon_meta:
            try:
                weapon_atk_bonus = int(weapon_meta.get('attack_bonus') or 0)
            except Exception:
                weapon_atk_bonus = 0
            try:
                ca_raw = str(weapon_meta.get('ability') or '').strip().upper()
                if ca_raw in ('STR','AGI','STA','INT','PER','LCK'):
                    ability_override = ca_raw
                elif ca_raw in ('', 'NONE'):
                    no_ability_mod = True
            except Exception:
                pass
        wtype = str(wentry.get('type','melee'))
        tags = wentry.get('tags', []) or []
        tags_l = [t.lower() for t in tags]
        is_thrown_capable = 'thrown' in tags_l
        # Treat as a thrown/missile attack only if the weapon is ranged, or if it's thrown-capable and the user explicitly provided a range option.
        is_missile = (wtype != 'melee')
        is_throwing = bool(is_missile or (is_thrown_capable and (range is not None)))
        # If a custom weapon defines an explicit ability, prefer it; allow 'none' to remove ability mod
        used_ability = ability_override or ('AGI' if is_throwing else 'STR')
        abil_mod = 0 if no_ability_mod else self._ability_mod(data, used_ability)
        atk_total = int(atk_roll) + int(atk_bonus) + int(weapon_atk_bonus) + int(abil_mod)
        # Notes accumulator (for flags like mounted, backstab, etc.)
        notes: list[str] = []
        # Global penalty (e.g., groggy)
        try:
            gpen, gnotes = get_global_roll_penalty(data)
            if gpen:
                atk_total += int(gpen)
                if gnotes:
                    notes.append("; ".join(gnotes))
        except Exception:
            pass

        # Mighty Deed option (Warrior melee only): compute deed die and add to attack now; add to damage later
        deed_active = False
        deed_value = 0
        deed_roll = 0
        deed_flat_plus = 0
        deed_die_display = ''
        deed_success = False
        cls_low = str(data.get('class','')).strip().lower()
        apply_deed = (cls_low in {'warrior','dwarf'}) and (str(wtype).lower() == 'melee')
        if apply_deed:
            # Warriors and Dwarves always roll a deed per melee attack; show the field for both
            deed_active = True
            raw = str(data.get('deed_die') or '').strip().lower() or 'd3'
            deed_die_display = raw
            m = re.match(r"^d(\d+)(?:\+(\d+))?$", raw)
            sides = 3
            if m:
                try:
                    sides = int(m.group(1))
                    deed_flat_plus = int(m.group(2) or 0)
                except Exception:
                    sides = 3; deed_flat_plus = 0
            # Roll a new deed for each attack (each action die)
            deed_roll, _ = roll_dice(f"1d{sides}")
            deed_value = int(deed_roll) + int(deed_flat_plus)
            atk_total += int(deed_value)
            # If deed not explicitly declared, note the auto deed for transparency
            if not bool(deed) and cls_low in {'warrior','dwarf'}:
                notes.append(f"auto deed {deed_die_display} +{int(deed_value)}")
        elif bool(deed):
            notes.append("deed ignored (requires Warrior/Dwarf melee)")

        # Warrior/Dwarf Luck weapon: fixed Luck modifier applies to one chosen weapon from level 1
        try:
            cls_now = str(data.get('class','')).strip().lower()
            if cls_now in {'warrior','dwarf'}:
                key_name = 'warrior_luck_weapon' if cls_now == 'warrior' else 'dwarf_luck_weapon'
                key_mod  = 'warrior_luck_weapon_mod' if cls_now == 'warrior' else 'dwarf_luck_weapon_mod'
                lw = str(data.get(key_name) or '').strip().lower()
                if lw and wkey == lw:
                    lmod = int(data.get(key_mod, 0) or 0)
                    if lmod:
                        atk_total += lmod
                        notes.append(f"luck weapon {lw} {lmod:+}")
        except Exception:
            pass

        # Thief backstab: add skill bonus to attack; note for display. Auto-crit applied after hit resolution.
        is_thief = str(data.get('class','')).strip().lower() == 'thief'
        can_backstab = 'backstab' in tags_l
        backstab_flagged = bool(backstab) and is_thief and can_backstab
        backstab_applied = False
        backstab_bonus_val = 0
        if bool(backstab) and not can_backstab:
            # Requested but weapon doesn't support backstab per tags
            notes.append("backstab not supported by this weapon")
        if backstab_flagged:
            try:
                ts = (data.get('thief_skills') or {}).get('skills') or {}
                backstab_bonus_val = int(ts.get('backstab', 0) or 0)
            except Exception:
                backstab_bonus_val = 0
            if backstab_bonus_val:
                atk_total += backstab_bonus_val
                backstab_applied = True
                notes.append(f"backstab +{backstab_bonus_val} to attack")
            else:
                notes.append("backstab attempt")

        # Augury-based attack adjustments (use static max Luck modifier)
        try:
            aug = (data.get('birth_augur') or {}).get('effect') or ''
            aug = str(aug).strip()
            mlm = int(get_max_luck_mod(data) or 0)
        except Exception:
            aug = ''
            mlm = 0
        # Determine melee/missile flags for augury checks
        is_melee_like = (wtype == 'melee' and not is_throwing)
        is_missile_like = bool(is_throwing or (wtype != 'melee'))
        if mlm:
            if aug == 'All attack rolls':
                atk_total += mlm
                notes.append(f"augur: all attacks {mlm:+}")
            if aug == 'Melee attack rolls' and is_melee_like:
                atk_total += mlm
                notes.append(f"augur: melee attack {mlm:+}")
            if aug == 'Missile fire attack rolls' and is_missile_like:
                atk_total += mlm
                notes.append(f"augur: missile attack {mlm:+}")
            if aug == 'Unarmed attack rolls':
                # heuristic detection of unarmed
                if wkey in ('unarmed','fist','fists','punch','kick') or 'unarmed' in tags_l:
                    atk_total += mlm
                    notes.append(f"augur: unarmed attack {mlm:+}")
            if aug == 'Mounted attack rolls' and bool(mounted):
                atk_total += mlm
                notes.append(f"augur: mounted attack {mlm:+}")
            if aug == 'Attack and damage rolls for 0-level starting weapon':
                try:
                    start_w = str(data.get('weapon') or '').strip().lower()
                except Exception:
                    start_w = ''
                if start_w and wkey == start_w:
                    atk_total += mlm
                    notes.append(f"augur: pack hunter attack {mlm:+}")

        # Damage
        dmg_expr = str(wentry.get('damage') or '1d2')
        # Mounted combat: higher ground (mounted vs unmounted) is typically +1 to attack; apply simply here.
        if mounted and not target_mounted:
            atk_total += 1
            notes.append('mounted:higher_ground +1')
        # Mounted charge: lance/spear double damage dice
        if mounted and charge:
            try:
                wtags = [t.lower() for t in (wentry.get('tags') or [])]
            except Exception:
                wtags = []
            if 'mounted' in wtags:
                dmg_expr = double_damage_dice_expr(dmg_expr)
                notes.append('mounted:charge double damage dice')
        base_dmg_expr = dmg_expr
        dmg_total, dmg_rolls = roll_dice(dmg_expr)
        dmg_mod = 0
        if not is_throwing and wtype == 'melee':
            dmg_mod = self._ability_mod(data, 'STR')
        elif is_throwing and rng == 'close':
            dmg_mod = self._ability_mod(data, 'STR')  # STR applies at close range for thrown
        # Augury-based damage adjustments
        add_dmg = 0
        if mlm:
            if aug == 'Damage rolls':
                add_dmg += mlm
            if aug == 'Melee damage rolls' and is_melee_like:
                add_dmg += mlm
            if aug == 'Missile fire damage rolls' and is_missile_like:
                add_dmg += mlm
            if aug == 'Attack and damage rolls for 0-level starting weapon':
                try:
                    start_w = str(data.get('weapon') or '').strip().lower()
                except Exception:
                    start_w = ''
                if start_w and wkey == start_w:
                    add_dmg += mlm
        # Clamp minimal damage to 1
        dmg_final = max(1, int(dmg_total) + int(dmg_mod) + (int(deed_value) if apply_deed else 0) + int(add_dmg))
        if add_dmg:
            notes.append(f"augur: damage {add_dmg:+}")

        # Determine target AC once; we'll compute hit after all modifiers
        hit_text = ''
        is_hit = None
        tac_val = None
        try:
            if target_ac is not None:
                tac_val = int(target_ac)
            elif defender_ac is not None:
                tac_val = int(defender_ac)
        except Exception:
            tac_val = None

        # Optional Luck burn on attack (adds to attack total and reduces current Luck)
        burn_used = 0
        cap_note = ""
        zero_note = ""
        donor_used = False
        donor_name = None
        try:
            requested = int(burn or 0)
        except Exception:
            requested = 0
        if requested and requested > 0:
            if burn_from_halfling and burn_from_halfling.strip():
                donor = await self._load_record(burn_from_halfling)
                if not donor or str(donor.get('class','')).strip().lower() != 'halfling':
                    await interaction.response.send_message("Halfling donor not found or not a Halfling.", ephemeral=True)
                    return
                donor_name = str(donor.get('name') or burn_from_halfling)
                safe = donor_name.lower().strip().replace(' ', '_')
                path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                burn_used = int(consume_luck_and_save(donor, requested, filename=path) or 0)
                atk_total += burn_used  # donor bonus 1:1
                donor_used = True
                if requested > burn_used:
                    cap_note = " Donor lacks that much Luck."  # rare
            else:
                safe = name.lower().strip().replace(' ', '_')
                path = os.path.join(SAVE_FOLDER, f"{safe}.json")
                burn_used = int(consume_luck_and_save(data, requested, filename=path) or 0)
                try:
                    cls = str(data.get('class') or '').strip().lower()
                except Exception:
                    cls = ''
                if burn_used > 0 and cls == 'halfling':
                    atk_total += (2 * burn_used)
                else:
                    atk_total += burn_used
                if requested > burn_used:
                    cap_note = " You're not lucky enough for that."
                try:
                    after = int(get_luck_current(data))
                    if after <= 0 and burn_used > 0:
                        zero_note = "\nUh-oh, you're luck has run out!"
                except Exception:
                    pass

        # Re-evaluate HIT/MISS after burn/backstab and other modifiers
        try:
            if tac_val is not None:
                is_hit = bool(int(atk_total) >= int(tac_val))
                hit_text = f" vs AC {tac_val} " + ('HIT' if is_hit else 'MISS')
        except Exception:
            pass

    # Fancy flags
        die_sides = 0
        try:
            # try to extract sides from action_die like 1d20
            if 'd' in action_die:
                die_sides = int(action_die.split('d',1)[1])
        except Exception:
            die_sides = 0
        nat_text = ''
        if atk_rolls and die_sides:
            r = atk_rolls[0]
            # Fumble on natural 1 always
            if r == 1:
                nat_text = ' — Fumble!'
            else:
                # Crit on natural die_sides or in-class threat range (warrior 19-20/18-20/17-20 on d20)
                # Only applies to a d20 action die; other dice follow simple max-only crits
                threat_min = None
                try:
                    if die_sides == 20:
                        thr = str(data.get('crit_threat') or '').strip()
                        if '-' in thr:
                            threat_min = int(thr.split('-', 1)[0])
                        else:
                            # Support single value like '20' if present
                            tv = int(thr) if thr.isdigit() else None
                            threat_min = tv
                except Exception:
                    threat_min = None
                if r == die_sides:
                    nat_text = ' — Critical!'
                elif threat_min is not None and r >= int(threat_min):
                    nat_text = ' — Critical!'

        # Backstab auto-crit on successful hit (unless a fumble)
        if backstab_flagged and is_hit is True and 'Fumble' not in nat_text:
            nat_text = ' — Critical!'

        # Determine deed success (threshold 3+) after we know hit status
        if deed_active:
            deed_success = (int(deed_value) >= 3) and (is_hit is True or tac_val is None)

        target_text = f" vs {target}" if target else ''
        rng_text = f" [{rng}]" if is_throwing else ''
        train_note = "" if (trained or is_lv0) else " (untrained: action die stepped down)"
        if no_ability_mod:
            abil_label = 'No ability'
            abil_emoji = ''
        else:
            abil_label = ability_name(used_ability)
            abil_emoji = ability_emoji(used_ability)
        dmg_abil_label = ability_name('STR')
        dmg_abil_emoji = ability_emoji('STR')
        if burn_used:
            if donor_used and donor_name:
                burn_text = f"; LUCK (Halfling {donor_name}) {burn_used:+}"
            else:
                try:
                    cls_disp = str(data.get('class') or '').strip().lower()
                except Exception:
                    cls_disp = ''
                if cls_disp == 'halfling':
                    burn_text = f"; LUCK {burn_used}→{2*burn_used:+}"
                else:
                    burn_text = f"; LUCK {burn_used:+}"
        else:
            burn_text = ""
    # Crit/Fumble resolution (basic): roll on tables, compute extra damage if applicable
        extra_text = ""
        if nat_text and 'Critical' in nat_text:
            table_key = select_crit_table_for_character(data)
            tables = load_crit_tables().get('tables', {})
            # Roll the character's crit die (e.g., Lv0: 1d4) instead of a fixed d20
            crit_die = str(data.get('crit_die') or '1d4').strip()
            # Fallback safety: ensure it looks like NdX
            if not crit_die or 'd' not in crit_die:
                crit_die = '1d4'
            crit_roll, _ = roll_dice(crit_die)
            # Luck applies to critical hit table rolls (all classes)
            try:
                lck = int(self._ability_mod(data, 'LCK'))
                if lck:
                    crit_roll = int(crit_roll) + lck
                    notes.append(f"luck: crit roll {lck:+}")
            except Exception:
                pass
            # Augury: Warrior’s arm improves critical results
            try:
                if mlm and aug == 'Critical hit tables':
                    crit_roll = int(crit_roll) + int(mlm)
                    notes.append(f"augur: crit roll {mlm:+}")
            except Exception:
                pass
            centry = lookup_crit_entry(table_key, int(crit_roll))
            # Load defender record if provided for conditional logic (no weapon / no shield)
            defender_data = None
            if target:
                try:
                    defender_data = await self._load_record(target)
                except Exception:
                    defender_data = None
            extra = resolve_crit_damage_bonus(centry, attacker=data, defender=defender_data, context={})
            extra_dice = extra.get('dice')
            extra_total = 0
            if extra_dice:
                extra_total, _ = roll_multiple_dice_expr(extra_dice)
            # Resolve any save attached to the crit entry (e.g., save_if_no_shield) or generic centry.save
            save_info = extra.get('save') or centry.get('save')
            if isinstance(save_info, dict) and defender_data is not None:
                try:
                    stype = str(save_info.get('type','Fort')).strip().lower()
                    dc_spec = str(save_info.get('dc','')).strip()
                    # parse DC, supporting patterns like "20 + PC level"
                    dc_val = None
                    if isinstance(dc_spec, (int, float)):
                        dc_val = int(dc_spec)
                    else:
                        base = 0
                        add_pc_lvl = 0
                        for tok in dc_spec.replace('+', ' + ').split():
                            t = tok.strip().lower()
                            if t.isdigit():
                                base += int(t)
                            elif 'pc' in t and 'level' in t:
                                try:
                                    add_pc_lvl = int(data.get('level', 0) or 0)
                                except Exception:
                                    add_pc_lvl = 0
                        dc_val = int(base + add_pc_lvl)
                    abil_map = {'fort':'STA','ref':'AGI','will':'PER'}
                    abil_key = abil_map.get(stype, 'STA')
                    sav_mod = self._ability_mod(defender_data, abil_key)
                    sroll, _ = roll_dice('1d20')
                    stotal = int(sroll) + int(sav_mod)
                    sresult = 'SUCCESS' if (dc_val is not None and stotal >= dc_val) else 'FAIL'
                    effect = save_info.get('fail') if sresult == 'FAIL' else None
                    extra_text += f"\n  ↳ Save ({save_info.get('type')} DC {dc_val}): {sroll} + {abil_key} {sav_mod:+} = {stotal} → {sresult}"
                    if effect:
                        extra_text += f"; effect: {effect}"
                except Exception:
                    pass
            # Build display text: include table name and roll, prefer effect text
            ctitle = str(centry.get('title') or '').strip()
            ceffect = str(centry.get('effect') or '').strip()
            cmain = ceffect or ctitle
            hdr = f"Crit [{table_key}] roll {int(crit_roll)}"
            if extra_total:
                dmg_final += int(extra_total)
                # Normalize dice text for display (strip leading '+')
                shown_expr = str(extra_dice or '').lstrip('+')
                extra_text = f"\n• {hdr}: {cmain} (+{extra_total} from {shown_expr})"
            else:
                extra_text = f"\n• {hdr}: {cmain}"
            # Apply crit tags as conditions to defender, if any
            try:
                tags = centry.get('tags', []) or []
                conds = tags_to_conditions(tags)
                if conds and defender_data is not None and target:
                    for c in conds:
                        apply_condition(defender_data, c.get('key'), c.get('payload'))
                    # Targeted effects (HP/ability adjustments)
                    changes = apply_targeted_effects_from_entry(defender_data, centry, {})
                    if changes:
                        extra_text += "\n  ↳ Effects: " + "; ".join(changes)
                    self._save_record(target, defender_data)
                    # Pretty labels
                    reg = (load_conditions() or {}).get('conditions', {})
                    labels = []
                    for c in conds:
                        k = c.get('key')
                        lab = reg.get(k, {}).get('label') if isinstance(reg.get(k), dict) else None
                        labels.append(lab or k)
                    if labels:
                        extra_text += f"\n  ↳ Conditions on {target}: " + ", ".join(labels)
            except Exception:
                pass
        elif nat_text and 'Fumble' in nat_text:
            fdata = load_fumble_tables()
            tables = fdata.get('tables', {})
            # Use armor-based fumble die from character data (defaults to d4 if missing)
            fdie = str(data.get('fumble_die') or 'd4')
            froll, _ = roll_dice(fdie)
            # Luck applies inversely on fumbles: subtract Luck mod (so +2 Luck → -2 to roll; -2 Luck → +2 to roll)
            try:
                lck = int(self._ability_mod(data, 'LCK'))
                if lck:
                    froll = int(froll) - int(lck)
                    notes.append(f"luck: fumble roll {-int(lck):+}")
            except Exception:
                pass
            # Augury: The Broken Star improves fumble results
            try:
                if mlm and aug == 'Fumbles':
                    froll = int(froll) + int(mlm)
                    notes.append(f"augur: fumble roll {mlm:+}")
            except Exception:
                pass
            fentry = lookup_fumble_entry('FUMBLES', int(froll))
            eff = ''
            title = ''
            if isinstance(fentry, dict):
                eff = str(fentry.get('effect') or '').strip()
                title = str(fentry.get('title') or '').strip()
            if eff or title:
                main = eff or title
                extra_text = f"\n• Fumble: {main} (roll {int(froll)} on {fdie})"
            else:
                # Helpful fallback when fumble tables are not installed or entry missing
                hint = " — install data/fumble_tables.json to enable fumble lookups" if not tables else ""
                extra_text = f"\n• Fumble: roll {int(froll)} on {fdie} (no table entry found{hint})"
            # Apply fumble tags to attacker (self)
            try:
                tags = fentry.get('tags', []) or []
                conds = tags_to_conditions(tags)
                if conds:
                    for c in conds:
                        apply_condition(data, c.get('key'), c.get('payload'))
                    # Targeted effects (if any) from tags on self
                    changes = apply_targeted_effects_from_tags(data, tags)
                    if changes:
                        extra_text += "\n  ↳ Effects on you: " + "; ".join(changes)
                    self._save_record(name, data)
                    reg = (load_conditions() or {}).get('conditions', {})
                    labels = []
                    for c in conds:
                        k = c.get('key')
                        lab = reg.get(k, {}).get('label') if isinstance(reg.get(k), dict) else None
                        labels.append(lab or k)
                    if labels:
                        extra_text += f"\n  ↳ Conditions on {data.get('name','you')}: " + ", ".join(labels)
            except Exception:
                pass

        # Apply damage to defender on hit (after crit/fumble effects have possibly modified damage)
        apply_text = ""
        if is_hit is True and defender_data is not None:
            try:
                hp = defender_data.get('hp', {}) if isinstance(defender_data.get('hp'), dict) else {}
                cur = int(hp.get('current', 0) or 0)
                mx = hp.get('max')
                try:
                    mx = int(mx) if mx is not None else None
                except Exception:
                    mx = None
                new_cur = max(0, int(cur) - int(dmg_final))
                hp['current'] = int(new_cur)
                if mx is not None:
                    hp['max'] = int(mx)
                defender_data['hp'] = hp
                # Dying system: if HP drops to 0
                try:
                    if cur > 0 and new_cur == 0:
                        lvl = int(defender_data.get('level', 0) or 0)
                        if lvl <= 0:
                            defender_data['dead'] = True
                            try:
                                import time as _t
                                defender_data['time_of_death'] = int(_t.time())
                            except Exception:
                                pass
                        else:
                            defender_data.setdefault('dying', {})['remaining_turns'] = int(lvl)
                    # If HP above 0 after previous dying state, clear it
                    if new_cur > 0 and defender_data.get('dying'):
                        defender_data.pop('dying', None)
                except Exception:
                    pass
                self._save_record(target, defender_data)
                apply_text = f"\n• {target} HP: {cur} → {new_cur}"
            except Exception:
                apply_text = ""

        if force is not None:
            try:
                notes.append(f"forced attack die = {int(force)}")
            except Exception:
                notes.append("forced attack die")
        note_text = ("\n• Notes: " + ", ".join(notes)) if notes else ""
        # Friendly fire on miss when firing into melee (missile/thrown only)
        ff_text = ""
        try:
            if (is_hit is False) and bool(into_melee) and (wtype != 'melee'):
                # 50% chance to hit an ally
                ff_roll, _ = roll_dice('1d2')
                who = ally or 'a nearby ally'
                if int(ff_roll) == 1:
                    # Do NOT auto-apply damage; instruct to reroll against ally AC per procedure
                    ff_text = (
                        f"\n• Friendly fire check: rolled {int(ff_roll)} on 1d2 → potential ally hit. "
                        f"Reroll the attack against {who}'s AC. If that roll hits, apply damage to the ally."
                    )
                else:
                    ff_text = f"\n• Friendly fire check: rolled {int(ff_roll)} on 1d2 → no ally hit."
        except Exception:
            pass

        # Build a clean embed for output
        did_crit = bool(nat_text and 'Critical' in nat_text)
        did_fumble = bool(nat_text and 'Fumble' in nat_text)
        # Color by outcome
        color = 0x95A5A6  # default gray
        if did_crit:
            color = 0xF1C40F  # gold
        elif did_fumble:
            color = 0xE74C3C  # red
        elif is_hit is True:
            color = 0x2ECC71  # green
        elif is_hit is False:
            color = 0xE67E22  # orange

        title_target = f" → {target}" if target else ""
        emb = discord.Embed(
            title=f"{data.get('name','Unknown')} attacks{title_target}",
            description=f"Weapon: {wkey}{rng_text}",
            color=color,
        )
        # Attack field
        hit_part = f" vs AC {tac_val} → {'HIT' if is_hit else 'MISS'}" if tac_val is not None and is_hit is not None else ""
        nat_part = " — Critical!" if did_crit else (" — Fumble!" if did_fumble else "")
        train_part = "; untrained (action die stepped down)" if (not trained and not is_lv0) else ""
        wpn_part = f"; WPN {weapon_atk_bonus:+}" if weapon_atk_bonus else ""
        abi_part = f"; {abil_emoji} {abil_label} {abil_mod:+}" if abil_label else ""
        deed_part = (f"; Deed +{deed_value}") if deed_active else ""
        atk_field = (
            f"Roll {atk_roll} on {action_die}{train_part}; "
            f"AB {atk_bonus:+}{wpn_part}{abi_part}{burn_text}{deed_part}; "
            f"Total {atk_total}{hit_part}{nat_part}{cap_note}"
        )
        emb.add_field(name="Attack", value=atk_field, inline=False)

        # Damage field
        dmg_chain_note = f" (from {base_dmg_expr} → {dmg_expr})" if (base_dmg_expr != dmg_expr) else ""
        abil_dmg_part = f"; {dmg_abil_emoji} {dmg_abil_label} {dmg_mod:+}" if dmg_mod else ""
        deed_dmg_part = (f"; Deed +{deed_value}") if deed_active else ""
        dmg_field = (
            f"Roll {dmg_total} on {dmg_expr}{dmg_chain_note}{abil_dmg_part}{deed_dmg_part}; "
            f"Total {dmg_final}{zero_note}"
        )
        emb.add_field(name="Damage", value=dmg_field, inline=False)

        # Deed field
        if deed_active:
            detail = f"Deed: “{(deed_text or '').strip() or '—'}” — rolled {int(deed_roll)} on {deed_die_display}"
            if deed_flat_plus:
                detail += f" (+{int(deed_flat_plus)}) = {int(deed_value)}"
            detail += f" → {'SUCCESS' if deed_success else 'FAILED'}"
            emb.add_field(name="Mighty Deed", value=detail, inline=False)

        # Crit/Fumble details (if any)
        if extra_text:
            # Strip leading bullet/newline for a cleaner field
            et = extra_text.strip()
            if et.startswith('•'):
                et = et[1:].strip()
            emb.add_field(name=("Critical" if did_crit else ("Fumble" if did_fumble else "Effects")), value=et, inline=False)

        # Friendly fire
        if ff_text:
            ff = ff_text.strip()
            if ff.startswith('•'):
                ff = ff[1:].strip()
            emb.add_field(name="Friendly fire", value=ff, inline=False)

        # Applied effects (HP changes on defender)
        if apply_text:
            ap = apply_text.strip()
            if ap.startswith('•'):
                ap = ap[1:].strip()
            emb.add_field(name="Applied", value=ap, inline=False)

        # Notes
        if notes:
            emb.add_field(name="Notes", value=", ".join(notes), inline=False)

        if used_prompt:
            await interaction.followup.send(embed=emb)
        else:
            await interaction.response.send_message(embed=emb)

        # Off-hand follow-up attack (two-weapon fighting)
        try:
            if 'use_twf' in locals() and use_twf and (off_action_die is not None) and (off_wkey):
                # Off-hand attack roll
                off_weapon_atk_bonus = 0
                off_ability_override = None
                off_no_ability_mod = False
                if off_weapon_meta:
                    try:
                        off_weapon_atk_bonus = int(off_weapon_meta.get('attack_bonus') or 0)
                    except Exception:
                        off_weapon_atk_bonus = 0
                    try:
                        ca_raw = str(off_weapon_meta.get('ability') or '').strip().upper()
                        if ca_raw in ('STR','AGI','STA','INT','PER','LCK'):
                            off_ability_override = ca_raw
                        elif ca_raw in ('', 'NONE'):
                            off_no_ability_mod = True
                    except Exception:
                        pass
                off_used_ability = off_ability_override or 'STR'
                off_abil_mod = 0 if off_no_ability_mod else self._ability_mod(data, off_used_ability)
                off_atk_roll, off_atk_rolls = roll_dice(off_action_die, force=force)
                off_atk_total = int(off_atk_roll) + int(self._attack_bonus(data)) + int(off_weapon_atk_bonus) + int(off_abil_mod)
                # Global penalty (e.g., groggy)
                try:
                    gpen_off, gnotes_off = get_global_roll_penalty(data)
                    if gpen_off:
                        off_atk_total += int(gpen_off)
                        if gnotes_off:
                            # Append to main notes; off-hand embed is separate
                            notes.append("; ".join(gnotes_off))
                except Exception:
                    pass
                # Apply deed value to attack if active
                if 'apply_deed' in locals() and apply_deed:
                    off_atk_total += int(deed_value)
                # Resolve hit
                off_is_hit = None
                off_hit_text = ''
                try:
                    if tac_val is not None:
                        off_is_hit = bool(int(off_atk_total) >= int(tac_val))
                        off_hit_text = f" vs AC {tac_val} " + ('HIT' if off_is_hit else 'MISS')
                except Exception:
                    pass
                # Banners
                off_die_sides = 0
                try:
                    if 'd' in off_action_die:
                        off_die_sides = int(off_action_die.split('d',1)[1])
                except Exception:
                    off_die_sides = 0
                off_nat_text = ''
                if off_atk_rolls and off_die_sides:
                    r2 = off_atk_rolls[0]
                    if r2 == 1:
                        off_nat_text = ' — Fumble!'
                    elif r2 == off_die_sides:
                        off_nat_text = ' — Critical!'
                # Damage
                off_dmg_expr = str((off_wentry or {}).get('damage') or '1d2')
                off_dmg_total, off_dmg_rolls = roll_dice(off_dmg_expr)
                off_dmg_mod = self._ability_mod(data, 'STR')
                off_add_dmg = 0
                try:
                    if mlm:
                        if aug == 'Damage rolls':
                            off_add_dmg += mlm
                        if aug == 'Melee damage rolls':
                            off_add_dmg += mlm
                except Exception:
                    pass
                off_dmg_final = max(1, int(off_dmg_total) + int(off_dmg_mod) + (int(deed_value) if ('apply_deed' in locals() and apply_deed) else 0) + int(off_add_dmg))
                # Apply damage
                off_apply_text = ''
                if off_is_hit is True:
                    if defender_data is not None:
                        try:
                            hp = defender_data.get('hp', {}) if isinstance(defender_data.get('hp'), dict) else {}
                            cur = int(hp.get('current', 0) or 0)
                            mx = hp.get('max')
                            try:
                                mx = int(mx) if mx is not None else None
                            except Exception:
                                mx = None
                            new_cur = max(0, int(cur) - int(off_dmg_final))
                            hp['current'] = int(new_cur)
                            if mx is not None:
                                hp['max'] = int(mx)
                            defender_data['hp'] = hp
                            # Dying system for off-hand damage
                            try:
                                if cur > 0 and new_cur == 0:
                                    lvl = int(defender_data.get('level', 0) or 0)
                                    if lvl <= 0:
                                        defender_data['dead'] = True
                                        try:
                                            import time as _t
                                            defender_data['time_of_death'] = int(_t.time())
                                        except Exception:
                                            pass
                                    else:
                                        defender_data.setdefault('dying', {})['remaining_turns'] = int(lvl)
                                if new_cur > 0 and defender_data.get('dying'):
                                    defender_data.pop('dying', None)
                            except Exception:
                                pass
                            self._save_record(target, defender_data)
                            off_apply_text = f"\n• {target} HP: {cur} → {new_cur}"
                        except Exception:
                            off_apply_text = ''
                    else:
                        if target:
                            tq = target.strip().lower()
                            tgt = None
                            for e in init_mod.INITIATIVE_ORDER:
                                nm = str(e.get('name','')).strip().lower()
                                ab = str(e.get('abbr','')).strip().lower()
                                if tq and (tq == nm or tq == ab):
                                    tgt = e; break
                            if tgt is not None and tgt.get('hp') is not None:
                                try:
                                    cur = int(tgt.get('hp') or 0)
                                except Exception:
                                    cur = 0
                                new_cur = max(0, cur - int(off_dmg_final))
                                tgt['hp'] = int(new_cur)
                                off_apply_text = f"\n• {tgt.get('name')} HP: {cur} → {new_cur}"
                # Build embed
                off_title_target = f" → {target}" if target else ''
                off_color = 0x2ECC71 if off_is_hit is True else (0xE67E22 if off_is_hit is False else 0x95A5A6)
                off_emb = discord.Embed(
                    title=f"{data.get('name','Unknown')} attacks (off-hand){off_title_target}",
                    description=f"Weapon: {off_wkey}",
                    color=off_color,
                )
                deed_part = (f"; Deed +{deed_value}") if ('apply_deed' in locals() and apply_deed) else ""
                off_atk_field = (
                    f"Roll {off_atk_roll} on {off_action_die}; AB {self._attack_bonus(data):+}; {ability_emoji(off_used_ability)} {ability_name(off_used_ability)} {off_abil_mod:+}; Total {off_atk_total}{off_hit_text}{off_nat_text}{deed_part}"
                )
                off_emb.add_field(name="Attack", value=off_atk_field, inline=False)
                deed_dmg_part = (f"; Deed +{deed_value}") if ('apply_deed' in locals() and apply_deed) else ""
                off_dmg_field = (
                    f"Roll {off_dmg_total} on {off_dmg_expr}{deed_dmg_part}; Total {off_dmg_final}"
                )
                off_emb.add_field(name="Damage", value=off_dmg_field, inline=False)
                if off_apply_text:
                    ap = off_apply_text.strip()
                    if ap.startswith('•'):
                        ap = ap[1:].strip()
                    off_emb.add_field(name="Applied", value=ap, inline=False)
                await interaction.followup.send(embed=off_emb)
        except Exception:
            # Fail-safe: never block the primary attack output due to off-hand errors
            pass

    # ---- Autocompletes ----
    @attack.autocomplete('name')
    async def ac_attack_name(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        choices: list[app_commands.Choice[str]] = []
        # Initiative entries (name and abbr)
        seen = set()
        for e in init_mod.INITIATIVE_ORDER:
            disp = e.get('name') or e.get('display') or ''
            ab = e.get('abbr') or ''
            show = f"{disp} [{ab}]" if ab else str(disp)
            val = str(disp)
            for cand in [disp, ab]:
                s = str(cand or '').lower()
                if q and q not in s:
                    continue
                key = (show, val)
                if key in seen:
                    continue
                choices.append(app_commands.Choice(name=show, value=val))
                seen.add(key)
                break
            if len(choices) >= 20:
                break
        # Characters from SAVE_FOLDER
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.endswith('.json'):
                    continue
                nm = fn[:-5]
                if q and q not in nm.lower():
                    continue
                choices.append(app_commands.Choice(name=nm, value=nm))
                if len(choices) >= 25:
                    break
        except Exception:
            pass
        return choices[:25]

    @attack.autocomplete('die')
    async def attack_die_ac(self, interaction: discord.Interaction, current: str):
        # Suggest action dice for the selected attacker (characters only); for wizards, only first die is offered
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
                cls_low = str(data.get('class','') or '').strip().lower()
                parts = self._parse_action_dice(data)
                if cls_low in {'wizard','mage','elf'} and parts:
                    dice = [parts[0]]
                else:
                    dice = parts
        if not dice:
            dice = ['1d20']
        cur = (current or '').strip().lower()
        out: List[app_commands.Choice[str]] = []
        for d in dice:
            if cur and cur not in d.lower():
                continue
            out.append(app_commands.Choice(name=d, value=d))
        return out[:25]

    @attack.autocomplete('target')
    async def ac_attack_target(self, interaction: discord.Interaction, current: str):
        # Same source pool as attacker
        return await self.ac_attack_name(interaction, current)

    @attack.autocomplete('attack')
    async def ac_attack_choice(self, interaction: discord.Interaction, current: str):
        # If 'name' refers to an initiative monster, suggest its saved attacks
        qname = ''
        try:
            # Pull the resolved 'name' option from the interaction data
            for opt in interaction.data.get('options', []):
                if opt.get('name') == 'name':
                    qname = str(opt.get('value') or '')
                    break
        except Exception:
            qname = ''
        q = (current or '').strip().lower()
        choices: list[app_commands.Choice[str]] = []
        attacker = None
        qq = qname.strip().lower()
        for e in init_mod.INITIATIVE_ORDER:
            nm = str(e.get('name','')).strip().lower()
            ab = str(e.get('abbr','')).strip().lower()
            if qq and (qq == nm or qq == ab):
                attacker = e; break
        if attacker and attacker.get('atk'):
            chunks = [c.strip() for c in re.split(r"[;,]", str(attacker.get('atk'))) if c.strip()]
            for c in chunks:
                if q and q not in c.lower():
                    continue
                choices.append(app_commands.Choice(name=c, value=c))
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(name="charge", description="Mounted charge attack (wraps /attack with mounted=true, charge=true)")
    @app_commands.describe(name="Character name", target="Optional target name", weapon="Override weapon to use", range="Range band", burn="Luck to burn (adds to attack total)", burn_from_halfling="Burn these Luck points from the named Halfling (1:1 bonus)", backstab="Backstab attempt (thief only)", target_mounted="Is the target mounted?", force="Force the attack die result (testing)")
    async def charge_slash(self, interaction: discord.Interaction, name: str, target: Optional[str] = None, weapon: Optional[str] = None, range: Optional[app_commands.Choice[str]] = None, burn: Optional[int] = 0, burn_from_halfling: Optional[str] = None, backstab: Optional[bool] = False, target_mounted: Optional[bool] = False, force: Optional[int] = None):
        # Delegate to attack roll with mounted + charge flags
        await self.attack(interaction, name=name, target=target, weapon=weapon, range=range, burn=burn, burn_from_halfling=burn_from_halfling, backstab=backstab, mounted=True, target_mounted=bool(target_mounted), charge=True, force=force)

    # Autocompletes
    @attack.autocomplete('target')
    async def attack_target_ac(self, interaction: discord.Interaction, current: str):
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
    @attack.autocomplete('name')
    async def attack_name_ac(self, interaction: discord.Interaction, current: str):
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

    @attack.autocomplete('weapon')
    async def attack_weapon_ac(self, interaction: discord.Interaction, current: str):
        # Prefer suggesting weapons present in character inventory when name is provided
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
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
                    if nm.lower() in WEAPON_TABLE:
                        pool.append(nm)
                    elif isinstance(it.get('weapon'), dict):
                        # custom weapon record
                        pool.append(nm)
                # If shield is equipped, offer special 'shield' bash entry
                try:
                    if bool(data.get('shield')) and 'shield' not in [p.lower() for p in pool]:
                        pool.append('shield')
                except Exception:
                    pass
        if not pool:
            pool = list(WEAPON_TABLE.keys())
        seen = set()
        for k in pool:
            s = str(k)
            kl = s.lower()
            if kl in seen:
                continue
            if cur and cur not in kl:
                continue
            seen.add(kl)
            choices.append(app_commands.Choice(name=s, value=kl))
            if len(choices) >= 25:
                break
        return choices

    @attack.autocomplete('offhand')
    async def attack_offhand_ac(self, interaction: discord.Interaction, current: str):
        # Mirror weapon autocomplete for off-hand selection
        cur = (current or '').lower()
        choices: list[app_commands.Choice[str]] = []
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
                    if nm.lower() in WEAPON_TABLE:
                        pool.append(nm)
                    elif isinstance(it.get('weapon'), dict):
                        pool.append(nm)
        if not pool:
            pool = list(WEAPON_TABLE.keys())
        seen = set()
        for k in pool:
            s = str(k)
            kl = s.lower()
            if kl in seen:
                continue
            if cur and cur not in kl:
                continue
            seen.add(kl)
            choices.append(app_commands.Choice(name=s, value=kl))
            if len(choices) >= 25:
                break
        return choices

async def setup(bot: commands.Bot):
    await bot.add_cog(CombatCog(bot))
