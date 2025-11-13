import os, json, random, re
import discord
from discord.ext import commands
from modules.utils import get_modifier, roll_dice, effective_initiative_die

# Public state (kept simple for now; future: per-guild dict)
INITIATIVE_OPEN = False
INITIATIVE_ORDER = []  # list of dicts {name, display, roll, owner, hp?, ac?}
CURRENT_TURN_INDEX = None
COMBAT_ROUND = 0
SAVE_FOLDER = 'characters'

__all__ = [
    'INITIATIVE_OPEN','INITIATIVE_ORDER','CURRENT_TURN_INDEX','COMBAT_ROUND','register'
]

def _ability_mod_from_char(char, key):
    try:
        abil = char.get('abilities', {})
        v = abil.get(key, {})
        if isinstance(v, dict):
            return int(v.get('mod', 0))
        return int(v)
    except Exception:
        return 0

async def _load_character(name):
    path = os.path.join(SAVE_FOLDER, f"{name}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

# Command registration

def register(bot: commands.Bot):
    @bot.command(name='init')
    async def init_open(ctx):
        global INITIATIVE_OPEN, INITIATIVE_ORDER, CURRENT_TURN_INDEX, COMBAT_ROUND
        INITIATIVE_OPEN = True
        INITIATIVE_ORDER = []
        CURRENT_TURN_INDEX = None
        COMBAT_ROUND = 1
        await ctx.send("🧭 Initiative is open (Round 1). Players may join with `!ijoin <CharacterName>` (roll: 1d20+AGI, or 1d16+AGI if holding a two-handed weapon).")

    @bot.command(name='ijoin')
    async def ijoin(ctx, *, char_name: str = None):
        global INITIATIVE_OPEN, INITIATIVE_ORDER
        if not INITIATIVE_OPEN:
            await ctx.send("⚠️ Initiative is not open. Start it with `!init`.")
            return
        if not char_name or not char_name.strip():
            await ctx.send("Who should join initiative? Reply with the character name.")
            def check(m): return m.author == ctx.author and m.channel == ctx.channel
            try:
                msg = await bot.wait_for('message', check=check, timeout=60)
                char_name = msg.content.strip()
            except Exception:
                await ctx.send("⏳ Timeout. Join cancelled.")
                return
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return
        for e in INITIATIVE_ORDER:
            if str(e.get('name','')).lower() == char_name.lower():
                await ctx.send(f"⚠️ `{char_name}` is already in initiative.")
                return
        try:
            with open(filename,'r', encoding='utf-8') as f:
                character = json.load(f)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to load character: {e}")
            return
        agi_mod = 0
        try:
            agi_field = character.get('abilities', {}).get('AGI', {})
            if isinstance(agi_field, dict):
                agi_mod = int(agi_field.get('mod', 0))
            else:
                agi_mod = get_modifier(int(agi_field))
        except Exception:
            pass
        die = effective_initiative_die(character)
        roll = random.randint(1, die)
        total = roll + agi_mod
        entry = {"name": char_name, "display": f"{char_name} ({total})", "roll": int(total), "owner": character.get('owner')}
        INITIATIVE_ORDER.append(entry)
        INITIATIVE_ORDER.sort(key=lambda x: x.get('roll',0), reverse=True)
        await ctx.send(f"✅ `{char_name}` joined initiative: rolled {roll} + {agi_mod} = **{total}**. Use `!inext` to begin/advance turns.")

    @bot.command(name='ijoin_mounted')
    async def ijoin_mounted(ctx, rider_name: str = None, mount_name: str = None):
        """Join initiative as a mounted pair using the worse AGI modifier; die based on rider's equipment."""
        global INITIATIVE_OPEN, INITIATIVE_ORDER
        if not INITIATIVE_OPEN:
            await ctx.send("⚠️ Initiative is not open. Start it with `!init`.")
            return
        if not rider_name or not mount_name:
            await ctx.send("Usage: `!ijoin_mounted <RiderName> <MountName>`")
            return
        rider_file = os.path.join(SAVE_FOLDER, f"{rider_name}.json")
        mount_file = os.path.join(SAVE_FOLDER, f"{mount_name}.json")
        if not os.path.exists(rider_file):
            await ctx.send(f"❌ Rider `{rider_name}` not found.")
            return
        if not os.path.exists(mount_file):
            await ctx.send(f"❌ Mount `{mount_name}` not found.")
            return
        try:
            with open(rider_file,'r', encoding='utf-8') as f:
                rider = json.load(f)
            with open(mount_file,'r', encoding='utf-8') as f:
                mount = json.load(f)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to load records: {e}")
            return
        # Compute worse AGI modifier
        r_agi = _ability_mod_from_char(rider, 'AGI')
        m_agi = _ability_mod_from_char(mount, 'AGI')
        worse_mod = min(int(r_agi or 0), int(m_agi or 0))
        die = effective_initiative_die(rider)
        roll = random.randint(1, die)
        total = roll + worse_mod
        display = f"{rider_name} mounted on {mount_name} ({total})"
        entry = {"name": f"{rider_name} (mounted)", "display": display, "roll": int(total), "owner": rider.get('owner')}
        INITIATIVE_ORDER.append(entry)
        INITIATIVE_ORDER.sort(key=lambda x: x.get('roll',0), reverse=True)
        await ctx.send(f"✅ `{rider_name}` (mounted on {mount_name}) joined initiative: rolled {roll} + {worse_mod} = **{total}**. Use `!inext` to begin/advance turns.")

    @bot.command(name='ispook')
    async def ispook(ctx, rider_name: str = None, training_bonus: int = 0, dc: int = 10):
        """Make a rider Agility check to control a spooked mount. Usage: !ispook <RiderName> [training_bonus] [dc]"""
        if not rider_name:
            await ctx.send("Usage: `!ispook <RiderName> [training_bonus] [dc]`")
            return
        rider_file = os.path.join(SAVE_FOLDER, f"{rider_name}.json")
        if not os.path.exists(rider_file):
            await ctx.send(f"❌ Rider `{rider_name}` not found.")
            return
        try:
            with open(rider_file,'r', encoding='utf-8') as f:
                rider = json.load(f)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to load rider: {e}")
            return
        try:
            training_bonus = int(training_bonus)
        except Exception:
            training_bonus = 0
        try:
            dc = int(dc)
        except Exception:
            dc = 10
        agi_mod = _ability_mod_from_char(rider, 'AGI')
        roll = random.randint(1,20)
        total = roll + int(agi_mod) + int(training_bonus)
        outcome = "SUCCESS" if total >= dc else "FAIL"
        await ctx.send(f"🐎 Spook check for `{rider_name}`: rolled {roll} + AGI {agi_mod:+} + training {training_bonus:+} = **{total}** vs DC {dc} → {outcome}.")

    @bot.command(name='inext')
    async def inext(ctx):
        global INITIATIVE_ORDER, CURRENT_TURN_INDEX, COMBAT_ROUND
        if not INITIATIVE_ORDER:
            await ctx.send("⚠️ No participants in initiative.")
            return
        if CURRENT_TURN_INDEX is None:
            CURRENT_TURN_INDEX = 0
        else:
            CURRENT_TURN_INDEX += 1
            if CURRENT_TURN_INDEX >= len(INITIATIVE_ORDER):
                CURRENT_TURN_INDEX = 0
                COMBAT_ROUND += 1
        # Dying system turn tick: decrement remaining_turns for current combatant if dying
        try:
            cur_entry = INITIATIVE_ORDER[CURRENT_TURN_INDEX]
            cname = str(cur_entry.get('name') or '').strip().lower()
            path = os.path.join(SAVE_FOLDER, f"{cname}.json")
            if os.path.exists(path):
                with open(path,'r',encoding='utf-8') as f:
                    rec = json.load(f)
                hp_block = rec.get('hp') if isinstance(rec.get('hp'), dict) else {}
                cur_hp = int(hp_block.get('current', 0) or 0)
                dying = rec.get('dying') if isinstance(rec.get('dying'), dict) else None
                if cur_hp == 0 and not rec.get('dead') and dying and 'remaining_turns' in dying:
                    dying['remaining_turns'] = max(0, int(dying.get('remaining_turns',0)) - 1)
                    if dying['remaining_turns'] <= 0:
                        # Death occurs now
                        rec['dead'] = True
                        try:
                            import time as _t
                            rec['time_of_death'] = int(_t.time())
                        except Exception:
                            pass
                        rec.pop('dying', None)
                        await ctx.send(f"☠️ {cur_entry.get('name')} has succumbed (no healing received in time).")
                    else:
                        await ctx.send(f"💀 {cur_entry.get('name')} is DYING — {dying['remaining_turns']} turn(s) remain to receive healing.")
                elif cur_hp > 0 and dying:
                    # Stabilized implicitly by HP restore — apply permanent -1 STA, then clear dying
                    try:
                        abl = rec.setdefault('abilities', {})
                        sta = abl.setdefault('STA', {})
                        try:
                            mx = int(sta.get('max', sta.get('current', sta.get('score', 1)) or 1))
                        except Exception:
                            mx = 1
                        try:
                            cur_sta = int(sta.get('current', mx) or mx)
                        except Exception:
                            cur_sta = mx
                        new_max = max(1, mx - 1)
                        new_cur = max(1, min(new_max, cur_sta - 1))
                        sta['max'] = int(new_max)
                        sta['current'] = int(new_cur)
                        try:
                            from modules.utils import get_modifier
                            sta['mod'] = int(get_modifier(int(new_cur)))
                        except Exception:
                            pass
                    except Exception:
                        pass
                    rec.pop('dying', None)
                    await ctx.send(f"🩹 {cur_entry.get('name')} is no longer dying (HP restored). ⚠️ Lasting injury: STA -1 (permanent)")
                # Persist any changes
                with open(path,'w',encoding='utf-8') as f:
                    json.dump(rec, f, indent=2)
        except Exception:
            pass
        lines = [f"__Initiative Order — Round {COMBAT_ROUND}:__"]
        for i,e in enumerate(INITIATIVE_ORDER):
            marker = "➡️" if i == CURRENT_TURN_INDEX else "   "
            ab = e.get('abbr')
            ab_text = f" [{ab}]" if ab else ""
            lines.append(f"{marker} {i+1}. {e.get('display', e.get('name','Unknown'))}{ab_text}")
        await ctx.send("\n".join(lines))
        current = INITIATIVE_ORDER[CURRENT_TURN_INDEX]
        turn_msg = f"**It's now {current.get('display', current.get('name','Unknown'))}'s turn. (Round {COMBAT_ROUND})**"
        await ctx.send(turn_msg)
        if current.get('owner'):
            try:
                user = await bot.fetch_user(current.get('owner'))
                await ctx.send(f"📣 {user.mention}, it's your turn!")
            except Exception:
                pass

    @bot.command(name='ilist')
    async def ilist(ctx):
        if not INITIATIVE_ORDER:
            await ctx.send("⚠️ No participants in initiative.")
            return
        lines = [f"__Initiative Order — Round {COMBAT_ROUND}:__"]
        for i,e in enumerate(INITIATIVE_ORDER):
            marker = "➡️" if i == CURRENT_TURN_INDEX else "   "
            ab = e.get('abbr')
            ab_text = f" [{ab}]" if ab else ""
            lines.append(f"{marker} {i+1}. {e.get('display', e.get('name','Unknown'))}{ab_text}")
        await ctx.send("\n".join(lines))

    @bot.command(name='iend')
    async def iend(ctx, option: str = None):
        global INITIATIVE_OPEN, INITIATIVE_ORDER, CURRENT_TURN_INDEX, COMBAT_ROUND
        perms = getattr(ctx.author, 'guild_permissions', None)
        if not perms or not (perms.manage_guild or perms.administrator):
            await ctx.send("⛔ You don't have permission to end initiative (GM-only).")
            return
        INITIATIVE_OPEN = False
        cleared = False
        if option and option.lower() in ('clear','reset','yes'):
            INITIATIVE_ORDER = []
            CURRENT_TURN_INDEX = None
            cleared = True
        if cleared:
            await ctx.send("🛑 Initiative closed and cleared.")
            COMBAT_ROUND = 0
        else:
            await ctx.send("🛑 Initiative closed (order preserved). Use `!ilist` to view the order or `!iend clear` to clear it.")

    @bot.command(name='iadd')
    async def iadd(ctx, *, text: str = None):
        global INITIATIVE_ORDER
        if not text:
            await ctx.send("Paste the monster line(s) to add. Reply now.")
            def check(m): return m.author == ctx.author and m.channel == ctx.channel
            try:
                msg = await bot.wait_for('message', check=check, timeout=120)
                text = msg.content.strip()
            except Exception:
                await ctx.send("⏳ Timeout. iadd cancelled.")
                return
        added = []
        lines_in = [l.strip() for l in text.splitlines() if l.strip()]
        for line in lines_in:
            m = re.match(r"^([^:]+?)\s*(?:\((\d+)\))?\s*:\s*(.+)$", line)
            if m:
                base_name = m.group(1).strip()
                count = int(m.group(2)) if m.group(2) else 1
                body = m.group(3).strip()
            else:
                parts = [p.strip() for p in line.split(';') if p.strip()]
                namepart = parts[0]
                mc = re.match(r"^(.+?)\s*\((\d+)\)\s*$", namepart)
                if mc:
                    base_name = mc.group(1).strip()
                    count = int(mc.group(2))
                    body = ';'.join(parts[1:])
                else:
                    base_name = line
                    count = 1
                    body = ''
            parsed = { 'init':0,'ac':None,'hd':None,'hp':None,'mv':None,'act':None,'atk':None,'sp':None,'sv':None,'al':None }
            for tok in re.split(r";", body):
                tok = tok.strip()
                if not tok: continue
                mm = re.match(r"^Init\s*([+-]?\d+)$", tok, re.I)
                if mm: parsed['init']=int(mm.group(1)); continue
                mm = re.match(r"^AC\s*(\d+)$", tok, re.I)
                if mm: parsed['ac']=int(mm.group(1)); continue
                mm = re.match(r"^HD\s*(.+)$", tok, re.I)
                if mm: parsed['hd']=mm.group(1).strip(); continue
                mm = re.match(r"^hp\s*(\d+)$", tok, re.I)
                if mm: parsed['hp']=int(mm.group(1)); continue
                mm = re.match(r"^MV\s*(.+)$", tok, re.I)
                if mm: parsed['mv']=mm.group(1).strip(); continue
                mm = re.match(r"^Act\s*(.+)$", tok, re.I)
                if mm: parsed['act']=mm.group(1).strip(); continue
                mm = re.match(r"^Atk\s*(.+)$", tok, re.I)
                if mm: parsed['atk']=mm.group(1).strip(); continue
                mm = re.match(r"^SP\s*(.+)$", tok, re.I)
                if mm: parsed['sp']=mm.group(1).strip(); continue
                mm = re.match(r"^SV\s*(.+)$", tok, re.I)
                if mm: parsed['sv']=mm.group(1).strip(); continue
                mm = re.match(r"^AL\s*(.+)$", tok, re.I)
                if mm: parsed['al']=mm.group(1).strip(); continue
            for i in range(1, count+1):
                inst_name = base_name if count==1 else f"{base_name} #{i}"
                init_mod = parsed.get('init',0) or 0
                roll = random.randint(1,20) + int(init_mod)
                display = f"{inst_name} ({roll})"
                words = re.findall(r"[A-Za-z0-9]+", base_name)
                initials = ''.join([w[0].upper() for w in words[:3]]) if words else base_name[:3].upper()
                if not initials: initials = (base_name[:1] or 'X').upper()
                abbr = f"{initials}{i}"
                hp_value = parsed.get('hp')
                if hp_value is None and parsed.get('hd'):
                    try:
                        mhd = re.search(r"(\d+)d(\d+)", parsed.get('hd'))
                        if mhd:
                            hd_n = int(mhd.group(1)); hd_s = int(mhd.group(2))
                            hp_value = sum(random.randint(1, hd_s) for _ in range(hd_n))
                    except Exception:
                        hp_value = None
                entry = { 'name':inst_name,'abbr':abbr,'display':display,'roll':int(roll),'owner':getattr(ctx.author,'id',None),'ac':parsed.get('ac'),'hd':parsed.get('hd'),'hp':hp_value,'mv':parsed.get('mv'),'act':parsed.get('act'),'atk':parsed.get('atk'),'sp':parsed.get('sp'),'sv':parsed.get('sv'),'al':parsed.get('al') }
                INITIATIVE_ORDER.append(entry)
                added.append(entry)
        INITIATIVE_ORDER.sort(key=lambda x: x.get('roll',0), reverse=True)
        if added:
            names = ', '.join([f"{a.get('display')} [{a.get('abbr','')}]" for a in added])
            await ctx.send(f"✅ Added to initiative: {names}")
        else:
            await ctx.send("⚠️ Couldn't parse any monsters from the provided text.")
