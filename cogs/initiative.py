import os, json, re, random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from modules import initiative as init_mod  # type: ignore
from modules.utils import effective_initiative_die  # type: ignore
from utils.dice import roll_dice  # type: ignore
from modules.data_constants import WEAPON_TABLE  # type: ignore


class InitiativeCog(commands.Cog):
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

    async def _save_record(self, name: str, data: dict) -> bool:
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
            # fallback: compute from score
            from modules.utils import get_modifier
            return int(get_modifier(int(v)))
        except Exception:
            return 0

    init = app_commands.Group(name="init", description="Initiative controls")

    @init.command(name="start", description="Start initiative and allow players to join")
    async def init_start(self, interaction: discord.Interaction):
        init_mod.INITIATIVE_OPEN = True
        init_mod.INITIATIVE_ORDER = []
        init_mod.CURRENT_TURN_INDEX = None
        init_mod.COMBAT_ROUND = 1
        await interaction.response.send_message("🧭 Initiative is open (Round 1). Players may join with /init join name.")

    @init.command(name="join", description="Join initiative with a character name")
    @app_commands.describe(name="Character name to join initiative")
    async def init_join(self, interaction: discord.Interaction, name: str):
        if not init_mod.INITIATIVE_OPEN:
            await interaction.response.send_message("⚠️ Initiative is not open. Start it with /init start.", ephemeral=True)
            return
        rec = await self._load_record(name)
        if not rec:
            await interaction.response.send_message(f"❌ Character '{name}' not found.", ephemeral=True)
            return
        # Prevent duplicates
        for e in init_mod.INITIATIVE_ORDER:
            if str(e.get('name','')).lower() == name.lower():
                await interaction.response.send_message(f"⚠️ '{name}' is already in initiative.", ephemeral=True)
                return
        # Determine initiative modifier: prefer character 'initiative' field; fallback to AGI mod
        try:
            init_mod_val = int(rec.get('initiative'))
        except Exception:
            init_mod_val = self._ability_mod(rec, 'AGI')
        die = effective_initiative_die(rec)
        import random
        roll = random.randint(1, int(die))
        total = int(roll) + int(init_mod_val)
        entry = {
            'name': name,
            'display': f"{name} ({total})",
            'roll': int(total),
            'owner': rec.get('owner'),
        }
        init_mod.INITIATIVE_ORDER.append(entry)
        init_mod.INITIATIVE_ORDER.sort(key=lambda x: x.get('roll',0), reverse=True)
        await interaction.response.send_message(f"✅ '{name}' joined: rolled {roll} + {init_mod_val:+} = **{total}**. Use /init next to advance.")

    @init.command(name="next", description="Advance to next turn and ping the actor")
    async def init_next(self, interaction: discord.Interaction):
        if not init_mod.INITIATIVE_ORDER:
            await interaction.response.send_message("⚠️ No participants in initiative.", ephemeral=True)
            return
        if init_mod.CURRENT_TURN_INDEX is None:
            init_mod.CURRENT_TURN_INDEX = 0
        else:
            init_mod.CURRENT_TURN_INDEX += 1
            if init_mod.CURRENT_TURN_INDEX >= len(init_mod.INITIATIVE_ORDER):
                init_mod.CURRENT_TURN_INDEX = 0
                init_mod.COMBAT_ROUND = int(init_mod.COMBAT_ROUND or 1) + 1
        # Build view
        lines = [f"__Initiative Order — Round {init_mod.COMBAT_ROUND}:__"]
        for i, e in enumerate(init_mod.INITIATIVE_ORDER):
            marker = "➡️" if i == init_mod.CURRENT_TURN_INDEX else "  "
            lines.append(f"{marker} {e.get('display') or e.get('name')}")
        current = init_mod.INITIATIVE_ORDER[init_mod.CURRENT_TURN_INDEX]
        mention = None
        owner_id = current.get('owner')
        if owner_id:
            mention = f"<@{owner_id}>"
        ping = f" — your turn {mention}!" if mention else " — their turn!"
        await interaction.response.send_message("\n".join(lines) + ping)

    @init.command(name="end", description="End initiative and clear state")
    async def init_end(self, interaction: discord.Interaction):
        init_mod.INITIATIVE_OPEN = False
        init_mod.INITIATIVE_ORDER = []
        init_mod.CURRENT_TURN_INDEX = None
        init_mod.COMBAT_ROUND = 0
        await interaction.response.send_message("🛑 Initiative closed and cleared.")

    @init.command(name="attack", description="Make an attack roll for the current actor in initiative")
    @app_commands.describe(
        target_ac="Optional target AC to judge hit/miss",
        attack="For monsters: which saved attack to use (name contains)",
        force="Force the attack die result (testing)",
        mode="For characters: include STR (melee) or AGI (missile)",
        target="Optional target name (character or initiative entry)",
        weapon="For characters: override equipped weapon"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="melee (STR)", value="melee"),
        app_commands.Choice(name="missile (AGI)", value="missile"),
    ])
    async def init_attack(self, interaction: discord.Interaction, target: str | None = None, target_ac: int | None = None, attack: str | None = None, force: int | None = None, mode: app_commands.Choice[str] | None = None, weapon: str | None = None):
        if not init_mod.INITIATIVE_ORDER or init_mod.CURRENT_TURN_INDEX is None:
            await interaction.response.send_message("⚠️ No active turn. Use /init next to start.", ephemeral=True)
            return
        actor = init_mod.INITIATIVE_ORDER[init_mod.CURRENT_TURN_INDEX]
        name = actor.get('name') or 'Unknown'
        # Try as character
        char = await self._load_record(name)
        if char:
            action_die = str(char.get('action_die') or '1d20')
            atk_roll, atk_rolls = roll_dice(action_die, force=force)
            try:
                ab = int(char.get('attack_bonus', char.get('attack', 0)) or 0)
            except Exception:
                ab = 0
            abil_add = 0
            mval = mode.value if isinstance(mode, app_commands.Choice) else None
            if mval == 'melee':
                abil_add = int(self._ability_mod(char, 'STR'))
            elif mval == 'missile':
                abil_add = int(self._ability_mod(char, 'AGI'))
            total = int(atk_roll) + int(ab) + int(abil_add)
            # Resolve target (optional) and AC
            defender_data = None
            defender_entry = None
            tac = None
            if target:
                # Try character target
                defender_data = await self._load_record(target)
                if defender_data:
                    try:
                        tac = int(defender_data.get('ac', 10) or 10)
                    except Exception:
                        tac = None
                else:
                    # Try initiative entry by name or abbr
                    tq = target.strip().lower()
                    for e in init_mod.INITIATIVE_ORDER:
                        nm = str(e.get('name','')).strip().lower()
                        abbr = str(e.get('abbr','')).strip().lower()
                        if tq == nm or tq == abbr:
                            defender_entry = e
                            try:
                                tac = int(e.get('ac')) if e.get('ac') is not None else None
                            except Exception:
                                tac = None
                            break
            try:
                tac = int(target_ac) if target_ac is not None else None
            except Exception:
                tac = None
            outcome = ''
            if tac is not None:
                outcome = f" vs AC {tac} " + ('HIT' if total >= tac else 'MISS')
            # Simple nat text
            nat = ''
            try:
                if atk_rolls:
                    r = atk_rolls[0]
                    sides = int(action_die.split('d',1)[1]) if 'd' in action_die else 20
                    if r == sides: nat = ' — Critical!'
                    elif r == 1: nat = ' — Fumble!'
            except Exception:
                pass
            # Resolve weapon (override or equipped) and roll damage
            dmg_expr = '1d2'
            chosen_label = None
            try:
                wkey_in = (weapon or char.get('weapon') or '').strip()
                chosen_label = wkey_in or None
                wkey = wkey_in.lower()
                wentry = WEAPON_TABLE.get(wkey) if wkey else None
                if isinstance(wentry, dict) and wentry.get('damage'):
                    dmg_expr = str(wentry.get('damage'))
                else:
                    # try inventory custom weapon metadata by name match
                    inv = char.get('inventory') or []
                    if isinstance(inv, list):
                        for it in inv:
                            if not isinstance(it, dict):
                                continue
                            nm = str(it.get('name') or it.get('item') or '').strip()
                            if not nm:
                                continue
                            if wkey and nm.lower() != wkey:
                                continue
                            if isinstance(it.get('weapon'), dict):
                                dmg_expr = str(it['weapon'].get('damage') or dmg_expr)
                                chosen_label = nm
                                break
            except Exception:
                pass
            dmg_roll, _ = roll_dice(dmg_expr)
            dmg_mod = int(self._ability_mod(char, 'STR')) if mval == 'melee' else 0
            dmg_final = max(1, int(dmg_roll) + int(dmg_mod))

            # Apply damage on hit
            apply_text = ''
            did_hit = (tac is not None and total >= tac)
            if did_hit and (defender_data is not None or defender_entry is not None):
                if defender_data is not None:
                    try:
                        hp = defender_data.get('hp', {}) if isinstance(defender_data.get('hp'), dict) else {}
                        cur = int(hp.get('current', 0) or 0)
                        new_cur = max(0, cur - int(dmg_final))
                        hp['current'] = int(new_cur)
                        defender_data['hp'] = hp
                        await self._save_record(defender_data.get('name') or target, defender_data)
                        apply_text = f"\n• {defender_data.get('name') or target} HP: {cur} → {new_cur}"
                    except Exception:
                        apply_text = ''
                elif defender_entry is not None and defender_entry.get('hp') is not None:
                    try:
                        cur = int(defender_entry.get('hp') or 0)
                    except Exception:
                        cur = 0
                    new_cur = max(0, cur - int(dmg_final))
                    defender_entry['hp'] = int(new_cur)
                    apply_text = f"\n• {defender_entry.get('name')} HP: {cur} → {new_cur}"

            title_weapon = f" with {chosen_label}" if chosen_label else ""
            emb = discord.Embed(title=f"{name} attacks{title_weapon}", color=0x2ECC71 if (tac is not None and total >= tac) else 0x95A5A6)
            abil_text = f"; {('STR' if mval=='melee' else 'AGI') } {abil_add:+}" if mval else ''
            emb.add_field(name="Attack", value=f"Roll {atk_roll} on {action_die}; AB {ab:+}{abil_text}; Total {total}{outcome}{nat}")
            emb.add_field(name="Damage", value=f"{dmg_expr} → {dmg_final}")
            if apply_text:
                emb.add_field(name="Applied", value=apply_text, inline=False)
            await interaction.response.send_message(embed=emb)
            return
        # Monster path
        atk_field = str(actor.get('atk') or '').strip()
        if not atk_field:
            await interaction.response.send_message(f"'{name}' has no saved attacks. Use /init add with attacks.", ephemeral=True)
            return
        chunks = [c.strip() for c in re.split(r"[;,]", atk_field) if c.strip()]
        pat = re.compile(r"^(.+?)\s*([+-]\d+)?\s*\(([^)]+)\)\s*$")
        parsed = []
        for c in chunks:
            m = pat.match(c)
            if m:
                nm = m.group(1).strip()
                try: md = int(m.group(2) or 0)
                except Exception: md = 0
                dmg = m.group(3).strip()
                parsed.append({'name': nm, 'mod': md, 'dmg': dmg, 'display': f"{nm} {md:+} ({dmg})"})
            else:
                parsed.append({'name': c, 'mod': 0, 'dmg': None, 'display': c})
        if not parsed:
            await interaction.response.send_message(f"No parsable attacks found for '{name}'.", ephemeral=True)
            return
        choice = None
        if attack:
            aq = attack.strip().lower()
            for a in parsed:
                if aq in a['display'].lower() or aq in a['name'].lower():
                    choice = a; break
        if not choice:
            choice = parsed[0]
        act_die = str(actor.get('act') or '1d20')
        atk_roll, atk_rolls = roll_dice(act_die, force=force)
        total = int(atk_roll) + int(choice.get('mod') or 0)
        tac = None
        try:
            tac = int(target_ac) if target_ac is not None else None
        except Exception:
            tac = None
        outcome = ''
        if tac is not None:
            outcome = f" vs AC {tac} " + ('HIT' if total >= tac else 'MISS')
        # Simple nat text
        nat = ''
        try:
            if atk_rolls:
                r = atk_rolls[0]
                sides = int(act_die.split('d',1)[1]) if 'd' in act_die else 20
                if r == sides: nat = ' — Critical!'
                elif r == 1: nat = ' — Fumble!'
        except Exception:
            pass
        # Roll damage and apply on hit if a target is provided
        dmg_expr = choice.get('dmg') or '1d2'
        dmg_roll, _ = roll_dice(dmg_expr)
        dmg_final = max(1, int(dmg_roll))
        apply_text = ''
        # Resolve optional named target for auto AC and HP application
        if target and tac is None:
            # try to get AC if not given
            tname = target.strip()
            tchar = await self._load_record(tname)
            if tchar:
                try:
                    tac = int(tchar.get('ac', 10) or 10)
                except Exception:
                    tac = None
            else:
                tq = tname.lower()
                for e in init_mod.INITIATIVE_ORDER:
                    if tq == str(e.get('name','')).strip().lower() or tq == str(e.get('abbr','')).strip().lower():
                        try:
                            tac = int(e.get('ac')) if e.get('ac') is not None else None
                        except Exception:
                            tac = None
                        break
            if tac is not None:
                outcome = f" vs AC {tac} " + ('HIT' if total >= tac else 'MISS')
        if target and tac is not None and total >= tac:
            # Apply damage
            tname = target.strip()
            tchar = await self._load_record(tname)
            if tchar:
                try:
                    hp = tchar.get('hp', {}) if isinstance(tchar.get('hp'), dict) else {}
                    cur = int(hp.get('current', 0) or 0)
                    new_cur = max(0, cur - int(dmg_final))
                    hp['current'] = int(new_cur)
                    tchar['hp'] = hp
                    await self._save_record(tchar.get('name') or tname, tchar)
                    apply_text = f"\n• {tchar.get('name') or tname} HP: {cur} → {new_cur}"
                except Exception:
                    apply_text = ''
            else:
                tq = tname.lower()
                tgt = None
                for e in init_mod.INITIATIVE_ORDER:
                    if tq == str(e.get('name','')).strip().lower() or tq == str(e.get('abbr','')).strip().lower():
                        tgt = e; break
                if tgt is not None and tgt.get('hp') is not None:
                    try:
                        cur = int(tgt.get('hp') or 0)
                    except Exception:
                        cur = 0
                    new_cur = max(0, cur - int(dmg_final))
                    tgt['hp'] = int(new_cur)
                    apply_text = f"\n• {tgt.get('name')} HP: {cur} → {new_cur}"

        emb = discord.Embed(title=f"{name} attacks", description=f"{choice.get('display')}")
        emb.add_field(name="Attack", value=f"Roll {atk_roll} on {act_die}; Attack {int(choice.get('mod') or 0):+}; Total {total}{outcome}{nat}")
        emb.add_field(name="Damage", value=f"{dmg_expr} → {dmg_final}")
        if apply_text:
            emb.add_field(name="Applied", value=apply_text, inline=False)
        await interaction.response.send_message(embed=emb)

    # --- Autocomplete for character weapon override ---
    @init_attack.autocomplete('weapon')
    async def init_attack_weapon_ac(self, interaction: discord.Interaction, current: str):
        # Only offer when current actor is a character
        choices: list[app_commands.Choice[str]] = []
        if not (init_mod.INITIATIVE_ORDER and init_mod.CURRENT_TURN_INDEX is not None):
            return choices
        actor = init_mod.INITIATIVE_ORDER[init_mod.CURRENT_TURN_INDEX]
        nm = actor.get('name') or ''
        char = await self._load_record(nm)
        if not char:
            return choices
        q = (current or '').strip().lower()
        seen = set()
        # Equipped first
        try:
            eq = str(char.get('weapon') or '').strip()
            if eq:
                show = eq
                if not q or q in show.lower():
                    if show.lower() not in seen:
                        choices.append(app_commands.Choice(name=show, value=show))
                        seen.add(show.lower())
        except Exception:
            pass
        # Inventory weapons from table and custom
        inv = char.get('inventory') or []
        if isinstance(inv, list):
            for it in inv:
                try:
                    if isinstance(it, dict):
                        nm = str(it.get('name') or it.get('item') or '').strip()
                        if not nm:
                            continue
                        if q and q not in nm.lower():
                            continue
                        if nm.lower() in seen:
                            continue
                        choices.append(app_commands.Choice(name=nm, value=nm))
                        seen.add(nm.lower())
                    else:
                        nm = str(it).strip()
                        if not nm:
                            continue
                        if q and q not in nm.lower():
                            continue
                        if nm.lower() in seen:
                            continue
                        choices.append(app_commands.Choice(name=nm, value=nm))
                        seen.add(nm.lower())
                    if len(choices) >= 25:
                        break
                except Exception:
                    continue
        return choices
    # ---- Autocompletes for /init attack ----
    @init_attack.autocomplete('target')
    async def init_attack_target_ac(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        choices: list[app_commands.Choice[str]] = []
        # Initiative entries first
        for e in init_mod.INITIATIVE_ORDER:
            disp = e.get('name') or e.get('display') or ''
            ab = e.get('abbr') or ''
            show = f"{disp} [{ab}]" if ab else str(disp)
            if q and q not in show.lower():
                continue
            choices.append(app_commands.Choice(name=show, value=str(disp)))
            if len(choices) >= 20:
                break
        # Character files
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
        return choices

    @init_attack.autocomplete('attack')
    async def init_attack_attack_ac(self, interaction: discord.Interaction, current: str):
        # Suggest attacks from the current actor's saved 'atk' field
        q = (current or '').strip().lower()
        choices: list[app_commands.Choice[str]] = []
        actor = None
        if init_mod.INITIATIVE_ORDER and init_mod.CURRENT_TURN_INDEX is not None:
            try:
                actor = init_mod.INITIATIVE_ORDER[init_mod.CURRENT_TURN_INDEX]
            except Exception:
                actor = None
        if actor and actor.get('atk'):
            chunks = [c.strip() for c in re.split(r"[;,]", str(actor.get('atk'))) if c.strip()]
            for c in chunks:
                if q and q not in c.lower():
                    continue
                choices.append(app_commands.Choice(name=c, value=c))
                if len(choices) >= 25:
                    break
        return choices

    @init.command(name="hp", description="Set or adjust HP for an initiative entry")
    @app_commands.describe(
        name="Initiative name or abbreviation",
        value="New HP (absolute) or delta when 'add' is true",
        add="If true, adds value as a delta instead of setting absolute"
    )
    async def init_hp(self, interaction: discord.Interaction, name: str, value: int, add: bool = False):
        # Find entry
        q = (name or '').strip().lower()
        target = None
        for e in init_mod.INITIATIVE_ORDER:
            nm = str(e.get('name','')).strip().lower()
            ab = str(e.get('abbr','')).strip().lower()
            if q == nm or q == ab:
                target = e; break
        if not target:
            await interaction.response.send_message(f"'{name}' not found in initiative.", ephemeral=True)
            return
        try:
            cur = int(target.get('hp') or 0)
        except Exception:
            cur = 0
        if add:
            new_val = max(0, int(cur) + int(value))
        else:
            new_val = max(0, int(value))
        target['hp'] = int(new_val)
        await interaction.response.send_message(f"🩸 {target.get('name')} HP: {cur} → {new_val}")

    @init_hp.autocomplete('name')
    async def init_hp_name_ac(self, interaction: discord.Interaction, current: str):
        q = (current or '').strip().lower()
        items = []
        for e in init_mod.INITIATIVE_ORDER:
            disp = e.get('name') or e.get('display')
            ab = e.get('abbr')
            show = f"{disp} [{ab}]" if ab else str(disp)
            val = str(e.get('name') or '')
            if q and q not in show.lower():
                continue
            items.append(app_commands.Choice(name=show, value=val))
            if len(items) >= 25:
                break
        return items

    @init.command(name="ac", description="Set or adjust AC for an initiative entry")
    @app_commands.describe(
        name="Initiative name or abbreviation",
        value="New AC (absolute) or delta when 'add' is true",
        add="If true, adds value as a delta instead of setting absolute"
    )
    async def init_ac(self, interaction: discord.Interaction, name: str, value: int, add: bool = False):
        q = (name or '').strip().lower()
        target = None
        for e in init_mod.INITIATIVE_ORDER:
            nm = str(e.get('name','')).strip().lower()
            ab = str(e.get('abbr','')).strip().lower()
            if q == nm or q == ab:
                target = e; break
        if not target:
            await interaction.response.send_message(f"'{name}' not found in initiative.", ephemeral=True)
            return
        try:
            cur = int(target.get('ac')) if target.get('ac') is not None else None
        except Exception:
            cur = None
        if add:
            base = int(cur or 0)
            new_val = int(base + int(value))
        else:
            new_val = int(value)
        target['ac'] = int(new_val)
        if cur is None:
            await interaction.response.send_message(f"🛡️ {target.get('name')} AC set to {new_val}")
        else:
            await interaction.response.send_message(f"🛡️ {target.get('name')} AC: {cur} → {new_val}")

    @init_ac.autocomplete('name')
    async def init_ac_name_ac(self, interaction: discord.Interaction, current: str):
        return await self.init_hp_name_ac(interaction, current)

    @init.command(name="list", description="Show current initiative order")
    async def init_list(self, interaction: discord.Interaction):
        order = init_mod.INITIATIVE_ORDER
        if not order:
            await interaction.response.send_message("⚠️ No participants in initiative.", ephemeral=True)
            return
        rnd = int(init_mod.COMBAT_ROUND or 0) or 1
        idx = init_mod.CURRENT_TURN_INDEX
        lines: list[str] = [f"__Initiative Order — Round {rnd}:__"]
        for i, e in enumerate(order):
            marker = "➡️" if (idx is not None and i == idx) else "  "
            name = e.get('name') or e.get('display') or 'Unknown'
            ab = e.get('abbr')
            ac = e.get('ac')
            hp = e.get('hp')
            bits = []
            if ab: bits.append(f"[{ab}]")
            if ac is not None: bits.append(f"AC {ac}")
            if hp is not None: bits.append(f"HP {hp}")
            tail = f" {' '.join(bits)}" if bits else ""
            lines.append(f"{marker} {i+1}. {name}{tail}")
        text = "\n".join(lines)
        # If too long, split into chunks under Discord limit
        if len(text) <= 1900:
            await interaction.response.send_message(text)
        else:
            sent = False
            chunk: list[str] = []
            size = 0
            for line in lines:
                if size + len(line) + 1 > 1900:
                    try:
                        if not sent:
                            await interaction.response.send_message("\n".join(chunk))
                            sent = True
                        else:
                            await interaction.followup.send("\n".join(chunk))
                    except Exception:
                        pass
                    chunk = []
                    size = 0
                chunk.append(line)
                size += len(line) + 1
            if chunk:
                if not sent:
                    await interaction.response.send_message("\n".join(chunk))
                else:
                    await interaction.followup.send("\n".join(chunk))

    @init.command(name="xp", description="Distribute XP to all characters in initiative")
    @app_commands.describe(amount="Total XP to split evenly among characters", note="Optional note recorded to each character")
    async def init_xp(self, interaction: discord.Interaction, amount: int, note: str | None = None):
        # Collect characters currently in initiative
        from modules import initiative as init_mod
        chars: list[dict] = []
        names: list[str] = []
        for e in init_mod.INITIATIVE_ORDER:
            rec = await self._load_record(e.get('name') or '')
            if rec:
                chars.append(rec)
                names.append(rec.get('name') or str(e.get('name')))
        if not chars:
            await interaction.response.send_message("No characters in initiative to award XP.", ephemeral=True)
            return
        # Compute split (integer division), ignore remainder
        try:
            total = int(amount)
        except Exception:
            await interaction.response.send_message("Amount must be an integer.", ephemeral=True)
            return
        each = max(0, total // len(chars))
        if each <= 0:
            await interaction.response.send_message(f"Amount {amount} too small to split among {len(chars)} characters.", ephemeral=True)
            return
        # Permission: allow admins to award to all; otherwise restrict to awarding only to characters owned by issuer
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        is_admin = bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))
        awarded: list[str] = []
        skipped: list[str] = []
        for data in chars:
            if not is_admin and str(data.get('owner')) != str(interaction.user.id):
                skipped.append(data.get('name') or 'Unknown')
                continue
            try:
                cur = int(data.get('xp', 0) or 0)
            except Exception:
                cur = 0
            new_val = max(0, cur + each)
            data['xp'] = int(new_val)
            if note:
                notes = data.setdefault('notes', {})
                log = notes.setdefault('xp_log', [])
                if isinstance(log, list):
                    log.append({'delta': int(each), 'share_of': int(total), 'by': int(interaction.user.id), 'note': note})
            await self._save_record(data.get('name') or 'unknown', data)
            awarded.append(f"{data.get('name')} +{each} (now {new_val})")
        # Build summary
        lines = [f"XP distribution: total {total}, {len(chars)} characters, each +{each}."]
        if awarded:
            lines.append("Awarded:")
            lines.extend([f"• {t}" for t in awarded])
        if skipped:
            lines.append("Skipped (not owner; admin required):")
            lines.extend([f"• {n}" for n in skipped])
        await interaction.response.send_message("\n".join(lines))

    @init.command(name="add", description="Add custom monsters to initiative")
    @app_commands.describe(
        name="Monster name",
        count="How many to add (default 1)",
        init_bonus="Initiative bonus to add to d20",
        hd="Hit Dice expression (e.g. 2d8+2)",
        ac="Armor Class",
        attacks="One or more attacks, e.g. 'Bite +6 (1d6); Claw +4 (1d4)'",
        save_reflex="Reflex save modifier (e.g., +1)",
        save_fortitude="Fortitude save modifier (e.g., +2)",
        save_will="Will save modifier (e.g., +0)",
        action="Action dice (e.g., 1d20)",
        tags="Comma-separated tags (e.g., undead, dragon)"
    )
    async def init_add(
        self,
        interaction: discord.Interaction,
        name: str,
        count: int | None = 1,
        init_bonus: int | None = 0,
        hd: str | None = None,
        ac: int | None = None,
        attacks: str | None = None,
        save_reflex: int | None = None,
        save_fortitude: int | None = None,
        save_will: int | None = None,
        action: str | None = None,
        tags: str | None = None,
    ):
        # Safety defaults
        try:
            count = max(1, int(count or 1))
        except Exception:
            count = 1
        try:
            init_bonus = int(init_bonus or 0)
        except Exception:
            init_bonus = 0

        # Parse tags
        tags_list: list[str] = []
        if tags:
            parts = [p.strip() for p in tags.split(',') if p.strip()]
            tags_list = [p.lower() for p in parts]

        # HD roller
        def roll_hd(hd_expr: str) -> int | None:
            if not hd_expr:
                return None
            s = hd_expr.strip().lower()
            m = re.search(r"(\d+)d(\d+)([+-]\d+)?", s)
            if not m:
                return None
            n = int(m.group(1)); sides = int(m.group(2)); mod = int(m.group(3) or 0)
            total = sum(random.randint(1, sides) for _ in range(max(1, n))) + mod
            return max(1, int(total))

        # Parse attacks: split by ';' or ',', trim, and validate each chunk as 'Name [+/-N] (NdX[+/-N])'
        atk_list: list[str] = []
        if attacks:
            chunks = [c.strip() for c in re.split(r"[;,]", attacks) if c.strip()]
            pat = re.compile(r"^(.+?)\s*([+-]\d+)?\s*\(([^)]+)\)\s*$")
            norm: list[str] = []
            for c in chunks:
                m = pat.match(c)
                if m:
                    nm = m.group(1).strip()
                    md = m.group(2) or ''
                    dmg = m.group(3).strip()
                    md = f" {md}" if md else ''
                    norm.append(f"{nm}{md} ({dmg})")
                else:
                    # If not matched, keep raw but trimmed
                    norm.append(c)
            atk_list = norm

        # Build entries
        added = []
        base_name = name.strip()
        for i in range(1, count + 1):
            inst_name = base_name if count == 1 else f"{base_name} #{i}"
            roll_total = random.randint(1, 20) + int(init_bonus)
            display = f"{inst_name} ({roll_total})"
            # Abbreviation
            words = re.findall(r"[A-Za-z0-9]+", base_name)
            initials = ''.join([w[0].upper() for w in words[:3]]) if words else base_name[:3].upper()
            if not initials:
                initials = (base_name[:1] or 'X').upper()
            abbr = f"{initials}{i if count>1 else ''}"
            # HP
            hp_value = roll_hd(hd) if hd else None
            entry = {
                'name': inst_name,
                'abbr': abbr,
                'display': display,
                'roll': int(roll_total),
                'owner': interaction.user.id,
                'ac': ac if ac is not None else None,
                'hd': hd or None,
                'hp': hp_value,
                'act': action or None,
                'sv': None,
                'tags': tags_list or None,
            }
            # Attack lines
            if atk_list:
                entry['atk'] = "; ".join(atk_list)
            # Compose saves string if separate fields provided
            if (save_reflex is not None) or (save_fortitude is not None) or (save_will is not None):
                try:
                    r = f"{int(save_reflex):+}" if (save_reflex is not None) else "—"
                except Exception:
                    r = "—"
                try:
                    f = f"{int(save_fortitude):+}" if (save_fortitude is not None) else "—"
                except Exception:
                    f = "—"
                try:
                    w = f"{int(save_will):+}" if (save_will is not None) else "—"
                except Exception:
                    w = "—"
                entry['sv'] = f"{r}/{f}/{w}"
            init_mod.INITIATIVE_ORDER.append(entry)
            added.append(entry)
        init_mod.INITIATIVE_ORDER.sort(key=lambda x: x.get('roll', 0), reverse=True)
        if added:
            names = ', '.join([f"{a.get('display')} [{a.get('abbr','')}]" for a in added])
            await interaction.response.send_message(f"✅ Added to initiative: {names}")
        else:
            await interaction.response.send_message("⚠️ Nothing was added.", ephemeral=True)

    # Autocomplete for names
    @init_join.autocomplete('name')
    async def init_join_name_ac(self, interaction: discord.Interaction, current: str):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(InitiativeCog(bot))
