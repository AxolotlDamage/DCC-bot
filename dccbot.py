import discord
from discord.ext import commands
import os
import json
import random
import re
import logging
from modules.data_constants import (
    # Legacy !create removed; use /create instead (cogs.character)
        thief_skills = [
            "backstab",
            "sneak silently",
            "hide in shadows",
            "pick pocket",
            "climb sheer surfaces",
            "pick lock",
            "find trap",
            "forge document",
            "disguise self",
            "read languages",
            "handle poison",
            "cast spell from scroll"
        ]
        await ctx.send(
            "🧭 Supported thief skills:\n" +
            ", ".join(thief_skills) +
            "\n\nUsage: `!c <STAT|skill> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn <N>]`\n" +
            "Example: `!c backstab Char1 -b 2 -burn 1 -dc 12`"
        )
        return

    if len(args) < 2:
        await ctx.send("❗ Usage: `!c <STAT> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn]`")
        return

    bonus = 0
    die_expr = "1d20"
    dc = None
    burn = 0

    # Determine stat and character name (support multi-word stats like 'sneak silently')
    stat = None
    char_name = None
    char_idx = None
    # look for an arg that matches an existing character file (common case)
    for idx in range(1, len(args)):
        candidate = args[idx]
        if os.path.exists(os.path.join(SAVE_FOLDER, f"{candidate}.json")):
            char_idx = idx
            break

    if char_idx is not None:
        stat = " ".join(args[:char_idx]).upper()
        char_name = args[char_idx]
        i = char_idx + 1
    else:
        # fallback to original behaviour: first token is stat, second is character
        if len(args) < 2:
            await ctx.send("❗ Usage: `!c <STAT> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn]`")
            return
        stat = args[0].upper()
        char_name = args[1]
        i = 2

    # parse flags
    while i < len(args):
        a = args[i]
        if a == "-b" and i + 1 < len(args):
            try:
                bonus = int(args[i+1])
            except Exception:
                await ctx.send(f"❗ Invalid bonus: `{args[i+1]}`")
                return
            i += 2
        elif a == "-die" and i + 1 < len(args):
            die_expr = args[i+1]
            i += 2
        elif a == "-dc" and i + 1 < len(args):
            try:
                dc = int(args[i+1])
            except Exception:
                await ctx.send(f"❗ Invalid DC: `{args[i+1]}`")
                return
            i += 2
        elif a == "-burn":
            # accept -burn or -burn N
            if i + 1 < len(args) and re.match(r"^\d+$", args[i+1]):
                burn = int(args[i+1])
                i += 2
            else:
                burn = 1
                i += 1
        else:
            i += 1

    # load character
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    with open(filename, "r") as f:
        char = json.load(f)

    # Support a set of thief skills as special checks (normalize input)
    stat_lower = stat.lower()
    norm = re.sub(r"[^a-z0-9]", "", stat_lower)
    thief_skill_map = {
        'backstab': 'Backstab',
        'sneaksilently': 'Sneak Silently',
        'hideinshadows': 'Hide in Shadows',
        'pickpocket': 'Pick Pocket',
        'climbsheersurfaces': 'Climb Sheer Surfaces',
        'picklock': 'Pick Lock',
        'findtrap': 'Find Trap',
        'forgedocument': 'Forge Document',
        'disguiseself': 'Disguise Self',
        'disguiseserlf': 'Disguise Self',  # tolerate common typo
        'readlanguages': 'Read Languages',
        'handlepoison': 'Handle Poison',
        'castspellfromscroll': 'Cast Spell from Scroll',
        'castscroll': 'Cast Spell from Scroll'
    }

    if norm in thief_skill_map:
        # Only thieves may use these skills
        if char.get('class', '').lower() != 'thief':
            await ctx.send(f"❗ The `{thief_skill_map[norm]}` skill is exclusive to thieves.")
            return

        # Robust skill bonus lookup: find matching key in char['skills'] by normalizing keys
        skills = char.get('skills', {}) if isinstance(char.get('skills', {}), dict) else {}
        skill_bonus = 0
        found = False
        for k, v in skills.items():
            k_norm = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if k_norm == norm:
                try:
                    skill_bonus = int(v)
                except Exception:
                    skill_bonus = 0
                found = True
                break

        if not found:
            # fallback to top-level fields like char['picklock'] or char[stat]
            try:
                skill_bonus = int(char.get(stat_lower, char.get(norm, 0)))
            except Exception:
                skill_bonus = 0

        # Roll and compute total (die_expr default), include -b bonus and optional burn
        dice_total, rolls = roll_dice(die_expr)
        total = dice_total + int(skill_bonus) + int(bonus)

        # Add ability modifier when the skill calls for it
        ability_map = {
            'sneaksilently': 'AGI',
            'hideinshadows': 'AGI',
            'pickpocket': 'AGI',
            'climbsheersurfaces': 'AGI',
            'picklock': 'AGI',
            'disabletrap': 'AGI',
            'forgedocument': 'AGI',
            'findtrap': 'INT',
            'readlanguages': 'INT',
            'castspellfromscroll': 'INT',
            'disguiseself': 'PER'
        }
        extra_mod = 0
        ab_key = ability_map.get(norm)
        if ab_key:
            try:
                extra_mod = int(char.get('abilities', {}).get(ab_key, {}).get('mod', 0))
            except Exception:
                extra_mod = 0
            total += extra_mod

        if burn and burn > 0:
            cur = get_luck_current(char)
            if burn > cur:
                await ctx.send(f"❌ `{char_name}` does not have {burn} luck to burn (current: {cur}).")
                return
            total += burn
            used = consume_luck_and_save(char, burn, filename)
            if char.get('luck', {}).get('current', 0) == 0:
                await ctx.send(f"Uh-oh! `{char_name}` is out of luck. Their fate is in the Judge's hand.")

        rolls_text = "+".join(str(r) for r in rolls)
        display_name = thief_skill_map.get(norm, stat.title())
        msg = f"🎲 {char_name} {display_name} check: `{die_expr}` = {rolls_text} ({dice_total}) + {skill_bonus:+} + {bonus:+} = **{total}**"
        if dc is not None:
            success = total >= dc
            msg += f" vs DC {dc} → {'Success' if success else 'Failure'}"

        await ctx.send(msg)
        # (Removed redundant sheet invocation and levelup debug remnants.)
        return

    abilities = char.get("abilities", {})
    if stat not in abilities:
        await ctx.send(f"❗ Ability `{stat}` not found on `{char_name}`. Available: {', '.join(abilities.keys())}")
        return

    stat_mod = int(abilities[stat].get("mod", 0))
    # Special-case: Luck checks are a flat d20 roll and succeed if the roll is
    # equal to or below the character's current luck value.
    if stat == "LCK":
        current_luck = get_luck_current(char)
        if burn and int(burn) > 0:
            await ctx.send("⚠️ Burning luck is not applicable to a Luck check.")
            return
        # Always a 1d20
        luck_roll = random.randint(1, 20)
        success = luck_roll <= current_luck
        await ctx.send(
            f"🎲 {char_name} Luck check: `1d20` = {luck_roll} vs current luck {current_luck} → {'✅ Success' if success else '❌ Failure'}"
        )
        return

    # roll (normal ability checks)
    dice_total, rolls = roll_dice(die_expr)
    total = dice_total + stat_mod + int(bonus)

    # (debug lines removed) burning logic will now proceed silently

    # Handle burning luck: subtract N from current luck and add N to the roll
    burn_note = ""
    if burn and burn > 0:
        cur = get_luck_current(char)
        if burn > cur:
            await ctx.send(f"❌ `{char_name}` does not have {burn} luck to burn (current: {cur}).")
            return
        # apply flat +N and consume
        total += burn
        used = consume_luck_and_save(char, burn, filename)
        burn_note = f" (burned {used} luck → +{used}; remaining luck: {char.get('luck', {}).get('current', 0)})"
        if char.get('luck', {}).get('current', 0) == 0:
            await ctx.send(f"Uh-oh! `{char_name}` is out of luck. Their fate is in the Judge's hand.")
    # persistence attempted; no debug output

    # result message
    rolls_text = "+".join(str(r) for r in rolls)
    msg = f"🎲 {char_name} {stat} check: `{die_expr}` = {rolls_text} ({dice_total}) + {stat_mod:+} + {bonus:+} {burn_note} = **{total}**"
    if dc is not None:
        success = total >= dc
        msg += f" vs DC {dc} → {'Success' if success else 'Failure'}"

    await ctx.send(msg)


@bot.command(name="language")
async def learn_languages(ctx):
    import random

    def load_character(user_id, name):
        filename = os.path.join(SAVE_FOLDER, f"{name}.json")
        if not os.path.exists(filename):
            return None, f"❌ Character `{name}` not found."
        with open(filename, "r") as f:
            data = json.load(f)
        if str(data.get("owner")) != str(user_id):
            return None, "🚫 You do not own this character."
        return data, filename

    def get_languages_from_table(table):
        roll = random.randint(1, 100)
        for rng, lang in table.items():
            if isinstance(rng, int):
                if roll == rng:
                    return lang
            elif roll in rng:
                return lang
        return None

    await ctx.send("📝 What is the name of the character?")
    def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=60)
        char_name = msg.content.strip()
        character, path = load_character(ctx.author.id, char_name)
        if not character:
            await ctx.send(path)
            return
    except:
        await ctx.send("⏳ Timeout. Try again.")
        return

    cls = character.get("class", "Lv0")
    alignment = character.get("alignment", "").lower()
    int_mod = character.get("abilities", {}).get("INT", {}).get("mod", 0)
    lck_mod = abs(character.get("abilities", {}).get("LCK", {}).get("mod", 0))
    augur = character.get("birth_augur", {}).get("sign", "").lower()
    occupation = character.get("occupation", "").lower()
    # Normalize known languages to lowercase for comparison
    known_langs_raw = character.get("languages", [])
    known_langs = set(l.lower() for l in known_langs_raw)
    new_langs = []

    # Default to 0 rolls
    rolls = 0
    bonus_langs = set()

    # Choose table and bonus language based on class or occupation
    if cls == "Lv0":
        rolls = int_mod
        if "birdsong" in augur:
            rolls += lck_mod

        # Always at least 1 roll for native tongue
        rolls = max(1, rolls)

        dwarf = "dwarven" in occupation or "dwarf" in cls.lower()
        elf = "elven" in occupation or "elf" in cls.lower()
        halfling = "halfling" in occupation or "halfling" in cls.lower()

        if dwarf:
            table = DWARF_LANGUAGE_TABLE
            bonus_langs.add("Dwarf")
        elif elf:
            table = ELF_LANGUAGE_TABLE
            bonus_langs.add("Elven")
        elif halfling:
            table = HALFLING_LANGUAGE_TABLE
            bonus_langs.add("Halfling")
        else:
            table = LV0_LANGUAGE_TABLE

    elif cls.lower() == "wizard":
        rolls = int_mod * 2
        table = WIZARD_LANGUAGE_TABLE
    else:
        await ctx.send("🧠 Only level 0 and wizards learn languages this way.")
        return

    # Check if any languages from this table are available (case-insensitive)
    available_langs = set(lang for rng, lang in table.items() if lang.lower() not in known_langs)
    # Also normalize bonus_langs for comparison
    bonus_langs_lower = set(l.lower() for l in bonus_langs)
    if not available_langs and not (bonus_langs_lower - known_langs):
        await ctx.send(f"📘 {char_name} already knows all possible languages from this table.")
        return

    while rolls > 0:
        lang = get_languages_from_table(table)

        if lang == "by_alignment":
            if alignment == "lawful":
                lang = "Law"
            elif alignment == "chaotic":
                lang = "Chaos"
            elif alignment == "neutral":
                lang = "Neutrality"
            else:
                continue  # Skip if alignment is unknown

        # Compare lowercased
        if lang and lang.lower() not in known_langs:
            known_langs.add(lang.lower())
            new_langs.append(lang)
            rolls -= 1


    # Now apply bonus race language (Dwarf, Elven, Halfling)
    for lang in bonus_langs:
        if lang.lower() not in known_langs:
            known_langs.add(lang.lower())
            new_langs.append(lang)

    # Save languages in original case (as much as possible)
    # Try to preserve original casing for already-known, and add new as-is
    all_langs = set(l.lower() for l in known_langs_raw)
    result_langs = list(known_langs_raw)
    for l in new_langs:
        if l not in result_langs and l.lower() not in all_langs:
            result_langs.append(l)
    character["languages"] = sorted(result_langs)
    with open(path, "w") as f:
        json.dump(character, f, indent=4)

    if new_langs:
        await ctx.send(f"🗣️ {char_name} learned: {', '.join(new_langs)}")
    else:
        await ctx.send(f"📘 {char_name} already knows all possible languages from this table.")


@bot.command(name="a")
async def attack(ctx, *args):
    """Deprecated direct attack function. Forwarding to CombatCog if loaded."""
    cog = bot.get_cog("CombatCog")
    if cog and hasattr(cog, "attack"):
        return await cog.attack(ctx, *args)
    await ctx.send("⚠️ CombatCog not loaded yet; attack command temporarily unavailable.")
    return

@bot.command(name="create")
async def create_character(ctx, *args):
    try:
        # Optional first arg: dice spec in the form '-NdM', '-NdMdlK' (drop lowest K), or '-NdMkhK' (keep highest K)
        dice_spec = "3d6"
        keep_mode = None  # 'dl' to drop lowest K, 'kh' to keep highest K
        keep_count = 0
        if len(args) >= 1 and isinstance(args[0], str):
            raw = args[0]
            # allow either '4d6dl1' or '-4d6dl1'
            if raw.startswith('-'):
                raw = raw[1:]
            m = re.match(r"^(\d+)d(\d+)((dl|kh)(\d+))?$", raw, re.I)
            if m:
                dice_spec = f"{m.group(1)}d{m.group(2)}"
                if m.group(4):
                    keep_mode = m.group(4).lower()
                    keep_count = int(m.group(5))
            else:
                await ctx.send(f"⚠️ Invalid dice spec `{args[0]}`; using default 3d6.")

        # Step 1: Roll stats (capture individual rolls and optionally drop lowest)
        stats = {}
        rolls_by_stat = {}
        kept_by_stat = {}
        m = re.match(r"^(\d+)d(\d+)$", dice_spec, re.I)
        if m:
            n = int(m.group(1))
            s = int(m.group(2))
        else:
            n, s = 3, 6
        for ability in ["STR", "AGI", "STA", "INT", "PER", "LCK"]:
            rolls = [random.randint(1, s) for _ in range(n)]
            # determine kept rolls based on keep_mode
            if keep_mode == 'dl' and keep_count and 0 < keep_count < len(rolls):
                # drop lowest keep_count dice
                kept = sorted(rolls)[keep_count:]
            elif keep_mode == 'kh' and keep_count and 0 < keep_count < len(rolls):
                # keep highest keep_count dice
                kept = sorted(rolls, reverse=True)[:keep_count]
            else:
                kept = rolls[:]
            total = sum(kept)
            stats[ability] = int(total)
            rolls_by_stat[ability] = rolls
            kept_by_stat[ability] = kept
        mods = {k: get_modifier(v) for k, v in stats.items()}
        max_luck_mod = mods["LCK"]

        # Step 2: Occupation from 1-100 table (occupations_full.json)
        try:
            occ_path = os.path.join(os.path.dirname(__file__), 'occupations_full.json')
            with open(occ_path, 'r', encoding='utf-8') as f:
                occ_data = json.load(f)
        except Exception:
            occ_data = {}
        if occ_data:
            k = str(random.randint(1, 100))
            occ = occ_data.get(k, {})
            occ_name = occ.get('name', 'Gongfarmer')
            weapon = occ.get('weapon', 'trowel')
            goods = occ.get('goods', 'sack of night soil')
        else:
            # Fallback to legacy pool
            _occ = random.choice(OCCUPATIONS)
            occ_name = _occ["name"]
            weapon = _occ["weapon"]
            goods = _occ["goods"]
            if goods == "animal":
                goods = random.choice(ANIMALS)
        weapon_name = weapon.split(" (" )[0] if isinstance(weapon, str) else str(weapon)
        inventory = [weapon_name, goods]

        # Step 3: Augur
        augur = random.choice(AUGURIES)
        sign, effect = augur

        # Step 4: Initial values
        hp = max(1, random.randint(1, 4) + mods["STA"])
        ac = 10 + mods["AGI"]
        reflex = mods["AGI"]
        fort = mods["STA"]
        will = mods["PER"]
        initiative = mods["AGI"]
        speed = 30
        attack = 0

        # Apply augur effects
        if effect == "Harsh winter":
            attack += max_luck_mod
        if effect == "Lucky sign":
            reflex += max_luck_mod
            fort += max_luck_mod
            will += max_luck_mod
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
        if effect == "Birdsong":
            extra_langs = max_luck_mod
        else:
            extra_langs = 0

        # Step 5: Languages
        languages = []
        if extra_langs > 0:
            known_languages = ["Elvish", "Dwarvish", "Halfling", "Draconic", "Infernal", "Celestial", "Goblin", "Orc"]
            languages = random.sample(known_languages, min(extra_langs, len(known_languages)))

        # Step 6: Coin
        cp = sum(random.randint(1, 12) for _ in range(5))

        # Step 7: Unique name like Char1, Char2, etc.
        existing_files = [f for f in os.listdir(SAVE_FOLDER) if f.startswith("Char") and f.endswith(".json")]
        used_numbers = [int(f[4:-5]) for f in existing_files if f[4:-5].isdigit()]
        next_number = max(used_numbers, default=0) + 1
        character_name = f"Char{next_number}"

        # Step 8: Alignment
        alignment = random.choice(ALIGNMENTS)

        # Step 9: Create character
        # Initial luck comes from the LCK stat roll we just made.
        character = {
            "name": character_name,
            "alignment": alignment,
            "class": "Lv0",
            "owner": ctx.author.id,
            "occupation": occ_name,
            "weapon": weapon_name,
            "inventory": inventory,
            "birth_augur": {
                "sign": sign,
                "effect": effect
            },
            "max_luck_mod": max_luck_mod,
            "hp": {
                "current": hp,
                "max": hp
            },
            "luck": {
                "current": int(stats.get("LCK", 0)),
                "max": int(stats.get("LCK", 0))
            },
            "ac": ac,
            "saves": {
                "reflex": reflex,
                "fortitude": fort,
                "will": will
            },
            "initiative": initiative,
            "speed": speed,
            "attack": attack,
            "cp": cp,
            "abilities": {k: {"max": int(stats[k]), "current": int(stats[k]), "mod": int(mods[k])} for k in stats},
            "armor": "unarmored",
            "shield": False,
            "fumble_die": ARMOR_TABLE["unarmored"]["fumble_die"],
            "action_die": "1d20",
            "crit_die": "1d4",
            "crit_table": "I",
            "languages": languages
        }

        # Save character
        filename = os.path.join(SAVE_FOLDER, f"{character_name}.json")
        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        await ctx.send(f"✅ New level 0 character created: **{character_name}**")
        await view_sheet(ctx, char_name=character_name)
        # send an explicit blank line to ensure separation after the sheet
        try:
            await ctx.send('\u200b')
        except Exception:
            pass
        # Show the rolled dice for each stat
        try:
            rolls_lines = []
            for st in ["STR", "AGI", "STA", "INT", "PER", "LCK"]:
                all_rolls = rolls_by_stat.get(st, [])
                kept = kept_by_stat.get(st, all_rolls)
                # If a keep_mode was used (dl/kh), always show which dice were kept and which dropped
                if keep_mode:
                    temp_kept = list(kept)
                    dropped = []
                    for r in all_rolls:
                        if r in temp_kept:
                            temp_kept.remove(r)
                        else:
                            dropped.append(r)
                    dropped_str = '+'.join(map(str, dropped)) if dropped else 'none'
                    rolls_lines.append(f"{st}: {stats[st]} (kept: {'+'.join(map(str, kept))}; dropped: {dropped_str})")
                else:
                    rolls_lines.append(f"{st}: {stats[st]} ({'+'.join(map(str, all_rolls))})")
            header = f"🎲 Rolls ({dice_spec}{keep_mode+str(keep_count) if keep_mode else ''}): "
            # add blank line before and after for readability and send an explicit blank message after
            await ctx.send("\n" + header + " | ".join(rolls_lines) + "\n")
            try:
                await ctx.send('\u200b')
            except Exception:
                pass
        except Exception:
            pass
        # Reminder message (sent as its own message to ensure visibility)
        try:
            await ctx.send("\n" + f":loudspeaker: Remember to use `!edit \"{character_name}\" set <name>` to change the character's name and `!edit \"{character_name}\" set alignment Lawful/Neutral/Chaotic` to choose an alignment. After that you can use `!language` to roll on the language table.")
        except Exception:
            # non-fatal if send fails
            pass

    except Exception as e:
        await ctx.send(f"⚠️ Error creating character: `{e}`")
        print(f"Create Error: {e}")


#-------Command List-------
@bot.command(name="helpme")
async def helpme(ctx):
    help_text = (
        "**🧙‍♂️ DCC Bot Command List**\n\n"
        "🆕 **Create Character**\n"
        "`!create` — Generate a random level 0 DCC character.\n\n"

        "🧾 **View Character Sheet**\n"
        "`!sheet <name>` — View a character sheet by name.\n\n"

        "🛠️ **Edit Character Sheet**\n"
        "`!edit \"<name>\" set <field> <value>` — Edit any part of a character.\n"
        "Example: `!edit \"Lv0 Character 1\" set name Bartholomew`\n"
        "Editable fields: `name`, `alignment`, `occupation`, `weapon`, `hp`, `ac`, `cp`, ability scores (e.g. `STR`), `birth_augur`, saves (`reflex`, `will`, `fortitude`), `inventory`, `armor`, `shield`\n\n"

        "🎒 **Modify Inventory**\n"
        "`!in \"<name>\" + item1, item2` — Add items to inventory\n"
        "`!in \"<name>\" - item1, item2` — Remove items from inventory\n"
        "Example: `!in \"Lv0 Character 1\" + torch, crowbar`\n\n"

        "🛡️ **Equip Armor or Shield**\n"
        "`!armor <name>` — Choose armor from a list\n"
        "`!shield <name>` — Toggle shield on/off and update AC\n\n"

        
        "❤️ **HP Management**\n"
        "`!hp <name> +/-<number>` — Heal or damage a character.\n"
        "Example: `!hp \"Lv0 Character 1\" -2`\n\n"

        "🗑️ **Delete Character**\n"
        "`!delete <name>` — Permanently delete a character you own.\n\n"

        "ℹ️ **Note**: Enclose character names in quotes if they contain spaces.\n"
    )
    await ctx.send(help_text)


@bot.command(name="fixmods")
async def fixmods(ctx, char_name: str):
    """Admin helper: recompute ability modifiers from ability scores and adjust saves.
    Usage: !fixmods <CharacterName>
    """
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            character = json.load(f)
    except Exception as e:
        await ctx.send(f"⚠️ Could not read file: {e}")
        return

    # permission: only owner can run
    if str(character.get('owner')) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return

    abilities = character.get('abilities', {}) or {}
    changes = []
    for stat, data in list(abilities.items()):
        try:
            if isinstance(data, dict):
                # prefer 'current', then 'max', then 'score'
                score = data.get('current', data.get('max', data.get('score', None)))
                if score is None:
                    # fallback: if mod exists, leave it
                    continue
                mod = int(get_modifier(int(score)))
                old = data.get('mod')
                data['mod'] = mod
                changes.append(f"{stat}: {old}->{mod}")
            else:
                # plain numeric value interpreted as score
                try:
                    mod = int(get_modifier(int(data)))
                    abilities[stat] = {'current': int(data), 'max': int(data), 'mod': mod}
                    changes.append(f"{stat}: ->{mod}")
                except Exception:
                    continue
        except Exception:
            continue

    # Recompute saves from AGI/STA/PER
    agi_mod = int(abilities.get('AGI', abilities.get('Agi', {})).get('mod', 0) if isinstance(abilities.get('AGI', {}), dict) else 0)
    sta_mod = int(abilities.get('STA', abilities.get('Sta', {})).get('mod', 0) if isinstance(abilities.get('STA', {}), dict) else 0)
    per_mod = int(abilities.get('PER', abilities.get('Per', {})).get('mod', 0) if isinstance(abilities.get('PER', {}), dict) else 0)
    character.setdefault('saves', {})
    old_saves = dict(character.get('saves', {}))
    character['saves']['reflex'] = character['saves'].get('reflex', 0) + agi_mod
    character['saves']['fortitude'] = character['saves'].get('fortitude', 0) + sta_mod
    character['saves']['will'] = character['saves'].get('will', 0) + per_mod

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(character, f, indent=2)
    except Exception as e:
        await ctx.send(f"⚠️ Could not save file: {e}")
        return

    msg_lines = [f"✅ Recomputed mods for `{char_name}`."]
    if changes:
        msg_lines.append("Changes: " + ", ".join(changes))
    msg_lines.append(f"Saves: {old_saves} -> {character.get('saves', {})}")
    await ctx.send("\n".join(msg_lines))


@bot.command(name="dumpchar")
async def dumpchar(ctx, char_name: str):
    """Owner-only debug: dump abilities and saves and computed AGI/STA/PER mods."""
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            character = json.load(f)
    except Exception as e:
        await ctx.send(f"⚠️ Could not read file: {e}")
        return
    if str(character.get('owner')) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return

    abil = character.get('abilities', {}) or {}
    saves = character.get('saves', {}) or {}

    def comp_mod(key):
        v = abil.get(key)
        if v is None:
            v = abil.get(key.lower()) or abil.get(key.upper())
        if isinstance(v, dict):
            mod = v.get('mod')
            cur = v.get('current', v.get('max', v.get('score')))
            return {'raw': v, 'mod': mod, 'current': cur, 'computed_mod': int(get_modifier(int(cur or 0))) if cur is not None else None}
        try:
            if isinstance(v, (int, float)):
                return {'raw': v, 'mod': None, 'current': v, 'computed_mod': int(get_modifier(int(v)))}
            if isinstance(v, str) and v.isdigit():
                return {'raw': v, 'mod': None, 'current': int(v), 'computed_mod': int(get_modifier(int(v)))}
        except Exception:
            pass
        return {'raw': v, 'mod': None, 'current': None, 'computed_mod': None}

    agi = comp_mod('AGI')
    sta = comp_mod('STA')
    per = comp_mod('PER')

    lines = [
        f"Abilities (raw): {json.dumps(abil, default=str)}",
        f"Saves (raw): {json.dumps(saves, default=str)}",
        f"AGI -> {agi}",
        f"STA -> {sta}",
        f"PER -> {per}"
    ]
    # send in chunks if too long
    msg = "\n".join(lines)
    for chunk in [msg[i:i+1900] for i in range(0, len(msg), 1900)]:
        await ctx.send("```" + chunk + "```")
#------------Lay on hands-------------------------------
async def _handle_disapproval_roll(char, filename, ctx, cause="disapproval"):
    """Load Disapproval Table from Spells.json, roll, post result, and apply simple effects."""
    import os, json, random
    spells_json_path = os.path.join(os.path.dirname(__file__), "Spells.json")
    try:
        with open(spells_json_path, "r", encoding="utf-8") as sf:
            spells = json.load(sf)
    except Exception as e:
        await ctx.send(f"⚠️ Could not load spells file for disapproval table: {e}")
        return

    dis_table = spells.get("Disapproval Table", {})
    die = dis_table.get("die", "1d20")
    # simple dice parser like '1d20'
    try:
        cnt, sides = die.lower().split('d')
        cnt = int(cnt)
        sides = int(sides)
    except Exception:
        cnt, sides = 1, 20
    roll_total = sum(random.randint(1, sides) for _ in range(cnt))

    table = dis_table.get("table", {})
    result_text = None
    for key, val in table.items():
        if isinstance(key, str) and key.endswith('+'):
            base = int(key[:-1])
            if roll_total >= base:
                result_text = val
                break
        elif isinstance(key, str) and '-' in key:
            lo, hi = key.split('-')
            try:
                lo_i = int(lo)
                hi_i = int(hi)
                if lo_i <= roll_total <= hi_i:
                    result_text = val
                    break
            except Exception:
                continue
        else:
            try:
                if int(key) == roll_total:
                    result_text = val
                    break
            except Exception:
                continue

    if result_text is None:
        display = "(No disapproval table entry found)"
    elif isinstance(result_text, dict):
        display = result_text.get("text", str(result_text))
    else:
        display = str(result_text)

    # Announce deity disapproval if we know the cleric's god
    god_name = char.get("god") if isinstance(char, dict) else None
    if god_name:
        await ctx.send(f"⚠️ {god_name} disapproves of your actions.")
    await ctx.send(f"⚠️ Disapproval triggered by {cause}! Rolled {die} = **{roll_total}**\n> {display}")

    # Apply a couple obvious mechanical effects if present in text
    text_low = display.lower()
    if "disapproval range immediately increases by another point" in text_low or "disapproval range immediately increases" in text_low:
        char["disapproval_range"] = char.get("disapproval_range", 1) + 1
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
            await ctx.send(f"🔺 Disapproval range increased to {char['disapproval_range']}.")
        except Exception:
            pass


async def check_and_handle_disapproval(char, filename, ctx, roll, cause="disapproval"):
    """Return (triggered:bool, orig_disapproval:int). If triggered, call the disapproval table handler."""
    import json
    triggered = False
    orig = char.get("disapproval_range", 1) if isinstance(char, dict) else 1
    if roll <= orig:
        triggered = True
        await _handle_disapproval_roll(char, filename, ctx, cause=cause)
    return triggered, orig


def apply_disapproval_penalty(char, filename, amount):
    """Apply an amount to char['disapproval_range'], save the file, and return the new value."""
    import json
    try:
        char["disapproval_range"] = char.get("disapproval_range", 1) + int(amount)
        with open(filename, "w") as f:
            json.dump(char, f, indent=2)
        return char["disapproval_range"]
    except Exception:
        return char.get("disapproval_range", 1)

@bot.command(name="loh", aliases=["layonhands"])
async def layonhands(ctx, char_name: str = None, target_name: str = None):
    """Cleric heals self or another character using Lay on Hands (command: !loh).
    If no cleric name is provided, the bot will prompt for it."""
    import os, json, random
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Prompt for cleric name if not provided
    if not char_name:
        await ctx.send("Who is using Lay on Hands? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            char_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Lay on Hands cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    with open(filename, "r") as f:
        char = json.load(f)
    if char.get("class", "").lower() != "cleric":
        await ctx.send(f"❌ `{char_name}` is not a cleric.")
        return

    # Prompt for target name if not provided
    if not target_name:
        await ctx.send("Who is the target of Lay on Hands? Type the character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            target_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Lay on Hands cancelled.")
            return

    target_file = os.path.join(SAVE_FOLDER, f"{target_name}.json")
    if not os.path.exists(target_file):
        await ctx.send(f"❌ Target `{target_name}` not found.")
        return
    with open(target_file, "r") as f:
        target = json.load(f)
    cleric_alignment = char.get("alignment", "neutral").lower()
    target_alignment = target.get("alignment", "neutral").lower()
    roll = random.randint(1, 20)
    # If the raw d20 roll is within or below the cleric's disapproval range, trigger Disapproval Table
    cleric_file = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    disapproval_triggered = False
    if roll <= char.get("disapproval_range", 1):
        disapproval_triggered = True
        await _handle_disapproval_roll(char, cleric_file, ctx, cause="Lay on Hands")
    level = char.get("level", 1)
    personality = char.get("personality", 0)
    mod = get_modifier(personality)
    # Determine relationship
    if cleric_alignment == target_alignment:
        relation = "same"
    elif (cleric_alignment, target_alignment) in [("lawful", "neutral"), ("neutral", "lawful"), ("neutral", "chaotic"), ("chaotic", "neutral")]:
        relation = "adjacent"
    else:
        relation = "opposed"

    # DCC Lay on Hands table
    # Format: (min, max, same, adjacent, opposed)
    lay_on_hands_table = [
        (1, 11, 0, 0, 0),
        (12, 13, 2, 1, 1),
        (14, 19, 3, 2, 1),
        (20, 21, 4, 3, 2),
        (22, 99, 5, 4, 3)
    ]
    # Apply target augury: Survived the plague → Magical healing (use target's max Luck mod)
    try:
        from modules.utils import get_max_luck_mod  # late import to avoid any cycles
    except Exception:
        get_max_luck_mod = None
    aug_heal_bonus = 0
    try:
        eff_t = str((target.get('birth_augur') or {}).get('effect') or '').strip()
        if eff_t == 'Magical healing' and callable(get_max_luck_mod):
            aug_heal_bonus = int(get_max_luck_mod(target) or 0)
    except Exception:
        aug_heal_bonus = 0
    total = roll + mod + level + int(aug_heal_bonus)
    # Find dice to heal
    dice_to_heal = 0
    for row in lay_on_hands_table:
        if row[0] <= total <= row[1]:
            if relation == "same":
                dice_to_heal = row[2]
            elif relation == "adjacent":
                dice_to_heal = row[3]
            else:
                dice_to_heal = row[4]
            break

    # Disapproval overrides success: if a disapproval was triggered, treat as failure
    if dice_to_heal == 0 or disapproval_triggered:
        # Increase disapproval range
        disapproval = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = disapproval
        # Save cleric file
        with open(os.path.join(SAVE_FOLDER, f"{char_name}.json"), "w") as f:
            json.dump(char, f, indent=2)
        await ctx.send(f"❌ Lay on Hands failed (rolled {roll} + {mod} + {level} = {total}). Disapproval range is now {disapproval}.")
        return

    # Determine die type by target's class
    class_hit_die = {
        "warrior": 12,
        "dwarf": 12,
        "halfling": 6,
        "elf": 6,
        "thief": 6,
        "cleric": 8,
        "wizard": 4,
        "": 4,  # fallback for Lv0
        "lv0": 4
    }
    target_class = target.get("class", "").lower()
    die_type = class_hit_die.get(target_class, 4)
    # Cap dice to target's level or hit dice
    target_level = target.get("level", 0) or 1
    dice_to_heal = min(dice_to_heal, target_level)
    # Roll healing
    heals = [random.randint(1, die_type) for _ in range(dice_to_heal)]
    total_heal = sum(heals)
    # Update HP
    target_hp = target.get("hp", {})
    max_hp = target_hp.get("max", 1)
    current_hp = target_hp.get("current", 1)
    target_hp["current"] = min(max_hp, current_hp + total_heal)
    target["hp"] = target_hp
    with open(os.path.join(SAVE_FOLDER, f"{target_name}.json"), "w") as f:
        json.dump(target, f, indent=2)
    aug_text = f" + target augur {aug_heal_bonus:+}" if aug_heal_bonus else ""
    await ctx.send(f"🙏 Lay on Hands successful! {target_name} is healed for {total_heal} HP (rolled {roll} + {mod} + {level}{aug_text} = {total}, {dice_to_heal}d{die_type}: {heals}).")
#------------Divine aid-------------------------------
from discord.ui import View, Button, Modal, TextInput

@bot.command(name="da")
async def divine_aid(ctx, char_name: str = None, request: str = None):
    """Cleric requests Divine Aid from their deity, selecting difficulty.
    If no character name provided, prompt for it interactively."""
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Prompt for character name if not provided
    if not char_name:
        await ctx.send("Who is requesting Divine Aid? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            char_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Divine Aid cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    with open(filename, "r") as f:
        char = json.load(f)

    if char.get("class", "").lower() != "cleric":
        await ctx.send("❌ Only clerics can request Divine Aid.")
        return

    level = char.get("level", 1)
    personality = char.get("personality", 10)
    mod = get_modifier(personality)
    deity = char.get("god", "their deity")

    # We'll roll after the DC is chosen so the disapproval result is shown after the user selects DC

    class DCModal(Modal, title="Custom DC"):
        dc = TextInput(label="Enter the DC (10-30)", required=True)
        async def on_submit(self, interaction):
            try:
                custom_dc = int(self.dc.value)
                await finish_divine_aid(interaction, custom_dc)
            except Exception:
                await interaction.response.send_message("❌ Invalid DC entered.", ephemeral=True)

    async def finish_divine_aid(interaction, dc):
        # Roll now (after DC choice) so any Disapproval output appears after the user chooses DC
        roll = random.randint(1, 20)

        # Check disapproval trigger using the current range before the divine aid penalty
        orig_disapproval = char.get("disapproval_range", 1)
        disapproval_triggered = False
        if roll <= orig_disapproval:
            disapproval_triggered = True
            await _handle_disapproval_roll(char, filename, ctx, cause="Divine Aid")

        # Increase disapproval range by 10 for attempting divine aid (penalty)
        disapproval = orig_disapproval + 10
        char["disapproval_range"] = disapproval
        with open(filename, "w") as f:
            json.dump(char, f, indent=2)

        total = roll + level + mod
        await interaction.response.send_message(
            f"🙏 {char['name']} prays to {deity} for divine aid...\n"
            f"🎲 Roll: {roll} + Level {level} + PER mod {mod} = **{total}** vs DC {dc}\nDisapproval range is now {disapproval}."
        )
        # Disapproval overrides success: if triggered, treat as failure
        if disapproval_triggered:
            await ctx.send(f"❌ Divine aid denied by divine disapproval. {deity} does not intervene.")
        elif total >= dc:
            await ctx.send(f"✨ Divine aid granted! {deity} answers the prayer.")
        else:
            await ctx.send(f"❌ Divine aid denied. {deity} does not intervene.")

    class DCView(View):
        def __init__(self):
            super().__init__(timeout=60)
            self.value = None

        @discord.ui.button(label="Simple (DC 10)", style=discord.ButtonStyle.success)
        async def simple(self, interaction, button):
            await finish_divine_aid(interaction, 10)
            self.stop()

        @discord.ui.button(label="Extraordinary (DC 18)", style=discord.ButtonStyle.danger)
        async def extraordinary(self, interaction, button):
            await finish_divine_aid(interaction, 18)
            self.stop()

        @discord.ui.button(label="Custom DC", style=discord.ButtonStyle.primary)
        async def custom(self, interaction, button):
            await interaction.response.send_modal(DCModal())
            self.stop()

    await ctx.send(
        f"Select the difficulty for {char['name']}'s Divine Aid request:",
        view=DCView()
    )
        
#------------Delete Character-------------------------------
@bot.command(name="delete")
async def delete_character(ctx, *, char_name: str = None):
    if not char_name or not char_name.strip():
        await ctx.send("❗ You must specify a character name to delete. Example: `!delete Char1`")
        return
    filename = os.path.join("characters", f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    # Ownership check
    if not await is_owner(ctx, filename):
        return

    try:
        os.remove(filename)
        await ctx.send(f"🗑️ Character `{char_name}` has been deleted.")
    except Exception as e:
        await ctx.send(f"⚠️ Error deleting character: `{e}`")
        print(f"Delete Error: {e}")


#------------Turn Unholy-------------------------------
@bot.command(name="turn")
async def turn_unholy(ctx, cleric_name: str = None, target_token: str = None):
    """Interactively prompt for cleric name and target HD, then attempt to turn unholy creatures.
    On fail, disapproval_range +1; if raw d20 <= disapproval_range, trigger Disapproval Table."""
    import os, json, random

    def _extract_hd_from_entry(entry):
        """Return an integer HD value parsed from an initiative entry's 'hd' field if possible."""
        if not entry:
            return None
        hd_field = entry.get('hd')
        if not hd_field:
            return None
        try:
            s = str(hd_field).strip()
            m = re.match(r"^(\d+)\s*d", s, re.I)
            if m:
                return int(m.group(1))
            m2 = re.match(r"^(\d+)\b", s)
            if m2:
                return int(m2.group(1))
        except Exception:
            return None
        return None

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # If caller passed a cleric name as the first arg, use it; otherwise ask.
    if not cleric_name:
        await ctx.send("Who is attempting the Turn? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            cleric_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Turn cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{cleric_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{cleric_name}` not found.")
        return

    with open(filename, "r") as f:
        char = json.load(f)

    if char.get("class", "").lower() != "cleric":
        await ctx.send("❌ Only clerics can turn unholy creatures.")
        return

    # If a target token was supplied, try to resolve it to an initiative entry and extract its HD.
    target_hd = None
    if target_token and initiative.INITIATIVE_ORDER:
        token = target_token.strip().lower()
        match_entry = next((c for c in initiative.INITIATIVE_ORDER if c.get('name','').lower() == token or c.get('abbr','').lower() == token), None)
        if match_entry:
            parsed_hd = _extract_hd_from_entry(match_entry)
            if parsed_hd is not None:
                target_hd = parsed_hd
    # If we couldn't get HD from args/initiative, prompt the user as before
    if target_hd is None:
        await ctx.send("What is the Hit Dice (HD) of the target creature? Reply with a number (e.g., 1, 2, 3).")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            target_hd = int(msg.content.strip())
        except Exception:
            await ctx.send("⏳ Timeout or invalid HD. Turn cancelled.")
            return

    level = char.get("level", 1)
    personality = char.get("personality", 10)
    mod = get_modifier(personality)

    roll = random.randint(1, 20)
    total = roll + level + mod
    # Apply cleric augury: Righteous heart → Turn unholy checks (use cleric's max Luck mod)
    aug_turn_bonus = 0
    try:
        from modules.utils import get_max_luck_mod  # late import
        eff_c = str((char.get('birth_augur') or {}).get('effect') or '').strip()
        if eff_c == 'Turn unholy checks':
            aug_turn_bonus = int(get_max_luck_mod(char) or 0)
            total += aug_turn_bonus
    except Exception:
        aug_turn_bonus = 0

    # Disapproval trigger based on raw roll before applying failure penalty
    disapproval_triggered = False
    if roll <= char.get("disapproval_range", 1):
        disapproval_triggered = True
        await _handle_disapproval_roll(char, filename, ctx, cause="Turn Unholy")

    # Turning DC for HD: simple rule example — DC = 10 + target HD
    dc = 10 + int(target_hd)
    aug_text = f" + augur {aug_turn_bonus:+}" if aug_turn_bonus else ""
    await ctx.send(f"🔔 {char['name']} attempts to turn unholy (Roll: {roll} + L{level} + PER{mod}{aug_text} = {total} vs DC {dc})")

    # Disapproval overrides success: if triggered, treat as failure
    if disapproval_triggered:
        dis = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = dis
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
        except Exception:
            pass
        await ctx.send(f"❌ Turn failed due to divine disapproval. Disapproval range increased to {dis}.")
    elif total >= dc:
        await ctx.send(f"✨ Turn succeeded! Creatures of up to {target_hd} HD are affected.")
    else:
        # Failure: increase disapproval by 1
        dis = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = dis
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
        except Exception:
            pass
        await ctx.send(f"❌ Turn failed. Disapproval range increased to {dis}.")


#------------Edit-------------------------------
@bot.command(name='edit')
async def edit_character(ctx, char_name: str, action: str, field: str, *, value: str):
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        with open(filename, "r") as f:
            character = json.load(f)

        if action != "set":
            await ctx.send("❌ Invalid action. Use `set` to change a value.")
            return

        field = field.lower()

        # --- Name Change (Special Case: also rename file) ---
        if field == "name":
            new_name = value
            new_filename = os.path.join(SAVE_FOLDER, f"{new_name}.json")

            if os.path.exists(new_filename):
                await ctx.send(f"⚠️ A character named `{new_name}` already exists.")
                return

            character["name"] = new_name

            with open(new_filename, "w") as f:
                json.dump(character, f, indent=4)
            os.remove(filename)

            await ctx.send(f"✅ Character renamed to `{new_name}` and file updated.")
            return

        # --- Direct fields ---
        elif field in ["alignment", "occupation", "weapon"]:
            character[field] = value

        # --- Stats ---
        elif field.upper() in character["abilities"]:
            stat_name = field.upper()
            score = int(value)
            character["abilities"][stat_name]["score"] = score
            character["abilities"][stat_name]["mod"] = get_modifier(score)

            if stat_name == "AGI":
                agi_mod = character["abilities"]["AGI"]["mod"]
                armor_key = character.get("armor", "unarmored")
                armor_bonus = ARMOR_TABLE.get(armor_key, {"ac_bonus": 0})["ac_bonus"]
                shield_bonus = ARMOR_TABLE["shield"]["ac_bonus"] if character.get("shield") else 0
                character["ac"] = 10 + agi_mod + armor_bonus + shield_bonus
                character["saves"]["reflex"] = agi_mod

            elif stat_name == "STA":
                sta_mod = character["abilities"]["STA"]["mod"]
                character["saves"]["fortitude"] = sta_mod

            elif stat_name == "PER":
                per_mod = character["abilities"]["PER"]["mod"]
                character["saves"]["will"] = per_mod

        # --- HP, AC, CP ---
        elif field == "hp":
            parts = value.split("/")
            if len(parts) == 2:
                character["hp"]["current"] = int(parts[0].strip())
                character["hp"]["max"] = int(parts[1].strip())
            else:
                character["hp"]["current"] = character["hp"]["max"] = int(value.strip())


        # --- Saves ---
        elif field in ["reflex", "fortitude", "will"]:
            character["saves"][field] = int(value)

        # --- Armor ---
        elif field == "armor":
            armor_name = value.lower()
            if armor_name not in ARMOR_TABLE:
                await ctx.send(f"❌ Unknown armor type: `{value}`.")
                return
            character["armor"] = armor_name

            agi_mod = character["abilities"]["AGI"]["mod"]
            armor_bonus = ARMOR_TABLE[armor_name]["ac_bonus"]
            shield_bonus = ARMOR_TABLE["shield"]["ac_bonus"] if character.get("shield") else 0
            character["ac"] = 10 + agi_mod + armor_bonus + shield_bonus
            character["fumble_die"] = ARMOR_TABLE[armor_name]["fumble_die"]

        # --- Shield ---
        elif field == "shield":
            value = value.lower()
            if value not in ["true", "false"]:
                await ctx.send("❌ Use `true` or `false` to equip/unequip a shield.")
                return
            character["shield"] = value == "true"

            agi_mod = character["abilities"]["AGI"]["mod"]
            armor_key = character.get("armor", "unarmored")
            armor_bonus = ARMOR_TABLE.get(armor_key, {"ac_bonus": 0})["ac_bonus"]
            shield_bonus = ARMOR_TABLE["shield"]["ac_bonus"] if character["shield"] else 0
            character["ac"] = 10 + agi_mod + armor_bonus + shield_bonus

        # --- Save back to file ---
        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        await ctx.send(f"✅ `{field}` updated for `{char_name}`.")

    except Exception as e:
        await ctx.send(f"⚠️ Error editing character: `{e}`")
        print(f"Error: {e}")

#------View Character Sheet Command-----
@bot.command(name="sheet")
async def view_sheet(ctx, char_name: str = None):
    try:
        if not char_name or not char_name.strip():
            await ctx.send("What is the name of the character?")
            def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
            try:
                msg = await bot.wait_for("message", check=check, timeout=60)
                char_name = msg.content.strip()
            except:
                await ctx.send("⏳ Timeout. Try again.")
                return

        # sanitize provided name: strip outer quotes and whitespace
        if char_name:
            try:
                char_name = char_name.strip()
                if (char_name.startswith('"') and char_name.endswith('"')) or (char_name.startswith("'") and char_name.endswith("'")):
                    char_name = char_name[1:-1].strip()
            except Exception:
                pass

        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        with open(filename, "r") as f:
            char = json.load(f)

        cls = char.get("class", "Lv0")
        level = char.get("level", 0)
        alignment = char.get("alignment", "").lower()

        # Class title logic
        def get_title(cls, level, alignment):
            titles = {
                "cleric": {
                    "lawful": ["Acolyte", "Heathen-slayer", "Brother", "Curate", "Father"],
                    "neutral": ["Witness", "Pupil", "Chronicler", "Judge", "Druid"],
                    "chaotic": ["Zealot", "Convert", "Cultist", "Apostle", "High Priest"]
                },
                "thief": {
                    "lawful": ["Bravo", "Apprentice", "Rogue", "Capo", "Boss"],
                    "neutral": ["Beggar", "Footpad", "Cutpurse", "Burglar", "Master Thief"],
                    "chaotic": ["Beggar", "Footpad", "Cutpurse", "Burglar", "Master Thief"],
                },
            }
            lst = titles.get(cls.lower())
            if not lst:
                return None
            align_list = lst.get(alignment)
            if not align_list:
                # fallback: any alignment list
                for v in lst.values():
                    if v:
                        align_list = v
                        break
            try:
                idx = max(0, min(len(align_list)-1, level-1))
                return align_list[idx]
            except Exception:
                return None
    ANIMALS,
    OCCUPATIONS,
    WEAPON_TABLE,
    ARMOR_TABLE,
    EQUIPMENT_TABLE,
    HALFLING_LANGUAGE_TABLE,
    ELF_LANGUAGE_TABLE,
    DWARF_LANGUAGE_TABLE,
    LV0_LANGUAGE_TABLE,
    WIZARD_LANGUAGE_TABLE,
)
from dotenv import load_dotenv
from modules.utils import (
    roll_ability,
    get_modifier,
    consume_luck_and_save,
    get_luck_current,
    _parse_luck_value,
)
from core.config import SAVE_FOLDER
from core.helpers import is_owner
from utils.dice import roll_dice
import modules.initiative as initiative
from core.helpers import is_owner

from dotenv import load_dotenv
load_dotenv(dotenv_path='token.env')
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Register initiative commands
initiative.register(bot)

# --- Weapon UI (restored minimal implementation) ---
from discord.ui import Modal, TextInput, View, Button

CUSTOM_WEAPON_FILE = "custom_weapons.json"
if os.path.exists(CUSTOM_WEAPON_FILE):
    try:
        with open(CUSTOM_WEAPON_FILE, 'r', encoding='utf-8') as _wf:
            custom_weapons = json.load(_wf)
            WEAPON_TABLE.update(custom_weapons)
    except Exception:
        custom_weapons = {}
else:
    custom_weapons = {}

def _save_custom_weapons():
    try:
        with open(CUSTOM_WEAPON_FILE, 'w', encoding='utf-8') as _wf:
            json.dump({k: v for k, v in WEAPON_TABLE.items() if k in custom_weapons}, _wf, indent=4)
    except Exception:
        pass

class WeaponModal(Modal, title="Add Custom Weapon"):
    name = TextInput(label="Weapon Name", placeholder="e.g. frostbrand", required=True)
    damage = TextInput(label="Damage Dice", placeholder="e.g. 1d8+2", required=True)
    wtype = TextInput(label="Weapon Type", placeholder="melee / ranged", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        nm = self.name.value.strip().lower()
        dmg = self.damage.value.strip()
        typ = self.wtype.value.strip().lower()
        if nm in WEAPON_TABLE:
            await interaction.response.send_message(f"⚠️ `{nm}` already exists.", ephemeral=True)
            return
        data = {"damage": dmg, "type": typ}
        WEAPON_TABLE[nm] = data
        custom_weapons[nm] = data
        _save_custom_weapons()
        await interaction.response.send_message(f"✅ Added weapon `{nm}` ({dmg}, {typ}).")

class AddWeaponView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(Button(label="Add Weapon", style=discord.ButtonStyle.green, custom_id="add_weapon_button"))

# Event moved here after bot definition
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.data.get("custom_id") == "add_weapon_button":
        await interaction.response.send_modal(WeaponModal())

# Command to invoke weapon creation
@bot.command(name="addweapon")
async def add_custom_weapon(ctx):
    view = AddWeaponView()
    await ctx.send("🔧 Click the button to add a custom weapon:", view=view)
# --- Helper functions ---
## Local helper function implementations removed (now imported from modules.utils)


# Ensure listing cog is loaded and slash commands are synced when using this legacy entrypoint
@bot.event
async def on_ready():
    # Load the listing cog if available
    try:
        if 'cogs.listing' not in bot.extensions:
            await bot.load_extension('cogs.listing')
    except Exception:
        # Ignore if already loaded or missing
        pass
    # Sync app commands once
    if not getattr(bot, '_synced_app_commands', False):
        try:
            # Prefer per-guild sync for instant availability
            if bot.guilds:
                for g in list(bot.guilds):
                    try:
                        # copy global commands to guild, then sync
                        bot.tree.copy_global_to(guild=g)
                        await bot.tree.sync(guild=g)
                    except Exception:
                        continue
            else:
                await bot.tree.sync()
        except Exception:
            pass
        bot._synced_app_commands = True


# --------- List Characters (by Discord profile) ---------
@bot.command(name="mychars", aliases=["listchars", "chars"]) 
async def list_characters(ctx, member: discord.Member | None = None):
    """List all characters attached to your Discord profile (by owner id).

    Usage: !mychars  (admins may pass an @mention to list for another user)
    """
    target = member or ctx.author
    # Only allow listing others if the caller is admin
    if member and not ctx.author.guild_permissions.administrator:
        await ctx.send("🚫 Only administrators can list characters for other users.")
        return

    try:
        files = [f for f in os.listdir(SAVE_FOLDER) if f.endswith('.json')]
    except Exception as e:
        await ctx.send(f"⚠️ Could not read characters folder: {e}")
        return

    owned: list[tuple[str,int,int,int]] = []  # (name, hp_cur, hp_max, ac)
    errors: list[str] = []
    for fn in sorted(files):
        path = os.path.join(SAVE_FOLDER, fn)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            errors.append(fn)
            continue
        if str(data.get('owner')) != str(target.id):
            continue
        name = data.get('name') or os.path.splitext(fn)[0]
        # Extract a few basics for display
        try:
            hp_obj = data.get('hp') or {}
            hp_cur = int(hp_obj.get('current', hp_obj.get('max', 0)) or 0) if isinstance(hp_obj, dict) else int(hp_obj or 0)
            hp_max = int(hp_obj.get('max', hp_obj.get('current', hp_obj)) or 0) if isinstance(hp_obj, dict) else int(hp_obj or 0)
        except Exception:
            hp_cur = 0; hp_max = 0
        try:
            ac = int(data.get('ac', 10) or 10)
        except Exception:
            ac = 10
        owned.append((str(name), hp_cur, hp_max, ac))

    if not owned:
        label = f"{target.display_name}" if hasattr(target, 'display_name') else str(target)
        await ctx.send(f"📭 No characters found for {label}.")
        return

    # Build a compact list (chunked if necessary)
    lines: list[str] = []
    lines.append(f"🧾 Characters for {target.mention if hasattr(target,'mention') else target} ({len(owned)}):")
    for name, hp_cur, hp_max, ac in owned:
        hp_txt = f"{hp_cur}/{hp_max}" if hp_max else str(hp_cur)
        status = "💤" if hp_cur <= 0 else ""  # mark downed
        lines.append(f" • {name} — HP {hp_txt}, AC {ac} {status}")
    if errors:
        lines.append(f"(Skipped {len(errors)} unreadable file(s))")

    msg = "\n".join(lines)
    # Send in chunks under Discord message limit
    chunk = []
    size = 0
    for line in msg.splitlines():
        if size + len(line) + 1 > 1900:
            await ctx.send("```" + "\n".join(chunk) + "```")
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await ctx.send("```" + "\n".join(chunk) + "```")


@bot.command(name="c")
async def check_command(ctx, *args):
    """Ability check: `!c STR CharName [-b N] [-die NdM] [-dc N] [-burn]`"""
    # If called with no arguments, show supported thief skills and usage
    if len(args) == 0:
        thief_skills = [
            "backstab",
            "sneak silently",
            "hide in shadows",
            "pick pocket",
            "climb sheer surfaces",
            "pick lock",
            "find trap",
            "forge document",
            "disguise self",
            "read languages",
            "handle poison",
            "cast spell from scroll"
        ]
        await ctx.send(
            "🧭 Supported thief skills:\n" +
            ", ".join(thief_skills) +
            "\n\nUsage: `!c <STAT|skill> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn <N>]`\n" +
            "Example: `!c backstab Char1 -b 2 -burn 1 -dc 12`"
        )
        return

    if len(args) < 2:
        await ctx.send("❗ Usage: `!c <STAT> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn]`")
        return

    bonus = 0
    die_expr = "1d20"
    dc = None
    burn = 0

    # Determine stat and character name (support multi-word stats like 'sneak silently')
    stat = None
    char_name = None
    char_idx = None
    # look for an arg that matches an existing character file (common case)
    for idx in range(1, len(args)):
        candidate = args[idx]
        if os.path.exists(os.path.join(SAVE_FOLDER, f"{candidate}.json")):
            char_idx = idx
            break

    if char_idx is not None:
        stat = " ".join(args[:char_idx]).upper()
        char_name = args[char_idx]
        i = char_idx + 1
    else:
        # fallback to original behaviour: first token is stat, second is character
        if len(args) < 2:
            await ctx.send("❗ Usage: `!c <STAT> <Character> [-b <bonus>] [-die <NdM>] [-dc <N>] [-burn]`")
            return
        stat = args[0].upper()
        char_name = args[1]
        i = 2

    # parse flags
    while i < len(args):
        a = args[i]
        if a == "-b" and i + 1 < len(args):
            try:
                bonus = int(args[i+1])
            except Exception:
                await ctx.send(f"❗ Invalid bonus: `{args[i+1]}`")
                return
            i += 2
        elif a == "-die" and i + 1 < len(args):
            die_expr = args[i+1]
            i += 2
        elif a == "-dc" and i + 1 < len(args):
            try:
                dc = int(args[i+1])
            except Exception:
                await ctx.send(f"❗ Invalid DC: `{args[i+1]}`")
                return
            i += 2
        elif a == "-burn":
            # accept -burn or -burn N
            if i + 1 < len(args) and re.match(r"^\d+$", args[i+1]):
                burn = int(args[i+1])
                i += 2
            else:
                burn = 1
                i += 1
        else:
            i += 1

    # load character
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    with open(filename, "r") as f:
        char = json.load(f)

    # Support a set of thief skills as special checks (normalize input)
    stat_lower = stat.lower()
    norm = re.sub(r"[^a-z0-9]", "", stat_lower)
    thief_skill_map = {
        'backstab': 'Backstab',
        'sneaksilently': 'Sneak Silently',
        'hideinshadows': 'Hide in Shadows',
        'pickpocket': 'Pick Pocket',
        'climbsheersurfaces': 'Climb Sheer Surfaces',
        'picklock': 'Pick Lock',
        'findtrap': 'Find Trap',
        'forgedocument': 'Forge Document',
        'disguiseself': 'Disguise Self',
        'disguiseserlf': 'Disguise Self',  # tolerate common typo
        'readlanguages': 'Read Languages',
        'handlepoison': 'Handle Poison',
        'castspellfromscroll': 'Cast Spell from Scroll',
        'castscroll': 'Cast Spell from Scroll'
    }

    if norm in thief_skill_map:
        # Only thieves may use these skills
        if char.get('class', '').lower() != 'thief':
            await ctx.send(f"❗ The `{thief_skill_map[norm]}` skill is exclusive to thieves.")
            return

        # Robust skill bonus lookup: find matching key in char['skills'] by normalizing keys
        skills = char.get('skills', {}) if isinstance(char.get('skills', {}), dict) else {}
        skill_bonus = 0
        found = False
        for k, v in skills.items():
            k_norm = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if k_norm == norm:
                try:
                    skill_bonus = int(v)
                except Exception:
                    skill_bonus = 0
                found = True
                break

        if not found:
            # fallback to top-level fields like char['picklock'] or char[stat]
            try:
                skill_bonus = int(char.get(stat_lower, char.get(norm, 0)))
            except Exception:
                skill_bonus = 0

        # Roll and compute total (die_expr default), include -b bonus and optional burn
        dice_total, rolls = roll_dice(die_expr)
        total = dice_total + int(skill_bonus) + int(bonus)

        # Add ability modifier when the skill calls for it
        ability_map = {
            'sneaksilently': 'AGI',
            'hideinshadows': 'AGI',
            'pickpocket': 'AGI',
            'climbsheersurfaces': 'AGI',
            'picklock': 'AGI',
            'disabletrap': 'AGI',
            'forgedocument': 'AGI',
            'findtrap': 'INT',
            'readlanguages': 'INT',
            'castspellfromscroll': 'INT',
            'disguiseself': 'PER'
        }
        extra_mod = 0
        ab_key = ability_map.get(norm)
        if ab_key:
            try:
                extra_mod = int(char.get('abilities', {}).get(ab_key, {}).get('mod', 0))
            except Exception:
                extra_mod = 0
            total += extra_mod

        if burn and burn > 0:
            cur = get_luck_current(char)
            if burn > cur:
                await ctx.send(f"❌ `{char_name}` does not have {burn} luck to burn (current: {cur}).")
                return
            total += burn
            used = consume_luck_and_save(char, burn, filename)
            if char.get('luck', {}).get('current', 0) == 0:
                await ctx.send(f"Uh-oh! `{char_name}` is out of luck. Their fate is in the Judge's hand.")

        rolls_text = "+".join(str(r) for r in rolls)
        display_name = thief_skill_map.get(norm, stat.title())
        msg = f"🎲 {char_name} {display_name} check: `{die_expr}` = {rolls_text} ({dice_total}) + {skill_bonus:+} + {bonus:+} = **{total}**"
        if dc is not None:
            success = total >= dc
            msg += f" vs DC {dc} → {'Success' if success else 'Failure'}"

        await ctx.send(msg)
        # (Removed redundant sheet invocation and levelup debug remnants.)
        return

    abilities = char.get("abilities", {})
    if stat not in abilities:
        await ctx.send(f"❗ Ability `{stat}` not found on `{char_name}`. Available: {', '.join(abilities.keys())}")
        return

    stat_mod = int(abilities[stat].get("mod", 0))
    # Special-case: Luck checks are a flat d20 roll and succeed if the roll is
    # equal to or below the character's current luck value.
    if stat == "LCK":
        current_luck = get_luck_current(char)
        if burn and int(burn) > 0:
            await ctx.send("⚠️ Burning luck is not applicable to a Luck check.")
            return
        # Always a 1d20
        luck_roll = random.randint(1, 20)
        success = luck_roll <= current_luck
        await ctx.send(
            f"🎲 {char_name} Luck check: `1d20` = {luck_roll} vs current luck {current_luck} → {'✅ Success' if success else '❌ Failure'}"
        )
        return

    # roll (normal ability checks)
    dice_total, rolls = roll_dice(die_expr)
    total = dice_total + stat_mod + int(bonus)

    # (debug lines removed) burning logic will now proceed silently

    # Handle burning luck: subtract N from current luck and add N to the roll
    burn_note = ""
    if burn and burn > 0:
        cur = get_luck_current(char)
        if burn > cur:
            await ctx.send(f"❌ `{char_name}` does not have {burn} luck to burn (current: {cur}).")
            return
        # apply flat +N and consume
        total += burn
        used = consume_luck_and_save(char, burn, filename)
        burn_note = f" (burned {used} luck → +{used}; remaining luck: {char.get('luck', {}).get('current', 0)})"
        if char.get('luck', {}).get('current', 0) == 0:
            await ctx.send(f"Uh-oh! `{char_name}` is out of luck. Their fate is in the Judge's hand.")
    # persistence attempted; no debug output

    # result message
    rolls_text = "+".join(str(r) for r in rolls)
    msg = f"🎲 {char_name} {stat} check: `{die_expr}` = {rolls_text} ({dice_total}) + {stat_mod:+} + {bonus:+} {burn_note} = **{total}**"
    if dc is not None:
        success = total >= dc
        msg += f" vs DC {dc} → {'Success' if success else 'Failure'}"

    await ctx.send(msg)


@bot.command(name="language")
async def learn_languages(ctx):
    import random

    def load_character(user_id, name):
        filename = os.path.join(SAVE_FOLDER, f"{name}.json")
        if not os.path.exists(filename):
            return None, f"❌ Character `{name}` not found."
        with open(filename, "r") as f:
            data = json.load(f)
        if str(data.get("owner")) != str(user_id):
            return None, "🚫 You do not own this character."
        return data, filename

    def get_languages_from_table(table):
        roll = random.randint(1, 100)
        for rng, lang in table.items():
            if isinstance(rng, int):
                if roll == rng:
                    return lang
            elif roll in rng:
                return lang
        return None

    await ctx.send("📝 What is the name of the character?")
    def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=60)
        char_name = msg.content.strip()
        character, path = load_character(ctx.author.id, char_name)
        if not character:
            await ctx.send(path)
            return
    except:
        await ctx.send("⏳ Timeout. Try again.")
        return

    cls = character.get("class", "Lv0")
    alignment = character.get("alignment", "").lower()
    int_mod = character.get("abilities", {}).get("INT", {}).get("mod", 0)
    lck_mod = abs(character.get("abilities", {}).get("LCK", {}).get("mod", 0))
    augur = character.get("birth_augur", {}).get("sign", "").lower()
    occupation = character.get("occupation", "").lower()
    # Normalize known languages to lowercase for comparison
    known_langs_raw = character.get("languages", [])
    known_langs = set(l.lower() for l in known_langs_raw)
    new_langs = []

    # Default to 0 rolls
    rolls = 0
    bonus_langs = set()

    # Choose table and bonus language based on class or occupation
    if cls == "Lv0":
        rolls = int_mod
        if "birdsong" in augur:
            rolls += lck_mod

        # Always at least 1 roll for native tongue
        rolls = max(1, rolls)

        dwarf = "dwarven" in occupation or "dwarf" in cls.lower()
        elf = "elven" in occupation or "elf" in cls.lower()
        halfling = "halfling" in occupation or "halfling" in cls.lower()

        if dwarf:
            table = DWARF_LANGUAGE_TABLE
            bonus_langs.add("Dwarf")
        elif elf:
            table = ELF_LANGUAGE_TABLE
            bonus_langs.add("Elven")
        elif halfling:
            table = HALFLING_LANGUAGE_TABLE
            bonus_langs.add("Halfling")
        else:
            table = LV0_LANGUAGE_TABLE

    elif cls.lower() == "wizard":
        rolls = int_mod * 2
        table = WIZARD_LANGUAGE_TABLE
    else:
        await ctx.send("🧠 Only level 0 and wizards learn languages this way.")
        return

    # Check if any languages from this table are available (case-insensitive)
    available_langs = set(lang for rng, lang in table.items() if lang.lower() not in known_langs)
    # Also normalize bonus_langs for comparison
    bonus_langs_lower = set(l.lower() for l in bonus_langs)
    if not available_langs and not (bonus_langs_lower - known_langs):
        await ctx.send(f"📘 {char_name} already knows all possible languages from this table.")
        return

    while rolls > 0:
        lang = get_languages_from_table(table)

        if lang == "by_alignment":
            if alignment == "lawful":
                lang = "Law"
            elif alignment == "chaotic":
                lang = "Chaos"
            elif alignment == "neutral":
                lang = "Neutrality"
            else:
                continue  # Skip if alignment is unknown

        # Compare lowercased
        if lang and lang.lower() not in known_langs:
            known_langs.add(lang.lower())
            new_langs.append(lang)
            rolls -= 1


    # Now apply bonus race language (Dwarf, Elven, Halfling)
    for lang in bonus_langs:
        if lang.lower() not in known_langs:
            known_langs.add(lang.lower())
            new_langs.append(lang)

    # Save languages in original case (as much as possible)
    # Try to preserve original casing for already-known, and add new as-is
    all_langs = set(l.lower() for l in known_langs_raw)
    result_langs = list(known_langs_raw)
    for l in new_langs:
        if l not in result_langs and l.lower() not in all_langs:
            result_langs.append(l)
    character["languages"] = sorted(result_langs)
    with open(path, "w") as f:
        json.dump(character, f, indent=4)

    if new_langs:
        await ctx.send(f"🗣️ {char_name} learned: {', '.join(new_langs)}")
    else:
        await ctx.send(f"📘 {char_name} already knows all possible languages from this table.")


@bot.command(name="a")
async def attack(ctx, *args):
    """Deprecated direct attack function. Forwarding to CombatCog if loaded."""
    cog = bot.get_cog("CombatCog")
    if cog and hasattr(cog, "attack"):
        return await cog.attack(ctx, *args)
    await ctx.send("⚠️ CombatCog not loaded yet; attack command temporarily unavailable.")
    return

@bot.command(name="create")
async def create_character(ctx, *args):
    try:
        # Optional first arg: dice spec in the form '-NdM', '-NdMdlK' (drop lowest K), or '-NdMkhK' (keep highest K)
        dice_spec = "3d6"
        keep_mode = None  # 'dl' to drop lowest K, 'kh' to keep highest K
        keep_count = 0
        if len(args) >= 1 and isinstance(args[0], str):
            raw = args[0]
            # allow either '4d6dl1' or '-4d6dl1'
            if raw.startswith('-'):
                raw = raw[1:]
            m = re.match(r"^(\d+)d(\d+)((dl|kh)(\d+))?$", raw, re.I)
            if m:
                dice_spec = f"{m.group(1)}d{m.group(2)}"
                if m.group(4):
                    keep_mode = m.group(4).lower()
                    keep_count = int(m.group(5))
            else:
                await ctx.send(f"⚠️ Invalid dice spec `{args[0]}`; using default 3d6.")

        # Step 1: Roll stats (capture individual rolls and optionally drop lowest)
        stats = {}
        rolls_by_stat = {}
        kept_by_stat = {}
        m = re.match(r"^(\d+)d(\d+)$", dice_spec, re.I)
        if m:
            n = int(m.group(1))
            s = int(m.group(2))
        else:
            n, s = 3, 6
        for ability in ["STR", "AGI", "STA", "INT", "PER", "LCK"]:
            rolls = [random.randint(1, s) for _ in range(n)]
            # determine kept rolls based on keep_mode
            if keep_mode == 'dl' and keep_count and 0 < keep_count < len(rolls):
                # drop lowest keep_count dice
                kept = sorted(rolls)[keep_count:]
            elif keep_mode == 'kh' and keep_count and 0 < keep_count < len(rolls):
                # keep highest keep_count dice
                kept = sorted(rolls, reverse=True)[:keep_count]
            else:
                kept = rolls[:]
            total = sum(kept)
            stats[ability] = int(total)
            rolls_by_stat[ability] = rolls
            kept_by_stat[ability] = kept
        mods = {k: get_modifier(v) for k, v in stats.items()}
        max_luck_mod = mods["LCK"]

        # Step 2: Random occupation
        occupation = random.choice(OCCUPATIONS)
        weapon = occupation["weapon"]
        goods = occupation["goods"]
        if goods == "animal":
            goods = random.choice(ANIMALS)
        inventory = [weapon.split(" (")[0], goods]

        # Step 3: Augur
        augur = random.choice(AUGURIES)
        sign, effect = augur

        # Step 4: Initial values
        hp = max(1, random.randint(1, 4) + mods["STA"])
        ac = 10 + mods["AGI"]
        reflex = mods["AGI"]
        fort = mods["STA"]
        will = mods["PER"]
        initiative = mods["AGI"]
        speed = 30
        attack = 0

        # Apply augur effects
        if effect == "Harsh winter":
            attack += max_luck_mod
        if effect == "Lucky sign":
            reflex += max_luck_mod
            fort += max_luck_mod
            will += max_luck_mod
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
        if effect == "Birdsong":
            extra_langs = max_luck_mod
        else:
            extra_langs = 0

        # Step 5: Languages
        languages = []
        if extra_langs > 0:
            known_languages = ["Elvish", "Dwarvish", "Halfling", "Draconic", "Infernal", "Celestial", "Goblin", "Orc"]
            languages = random.sample(known_languages, min(extra_langs, len(known_languages)))

        # Step 6: Coin
        cp = sum(random.randint(1, 12) for _ in range(5))

        # Step 7: Unique name like Char1, Char2, etc.
        existing_files = [f for f in os.listdir(SAVE_FOLDER) if f.startswith("Char") and f.endswith(".json")]
        used_numbers = [int(f[4:-5]) for f in existing_files if f[4:-5].isdigit()]
        next_number = max(used_numbers, default=0) + 1
        character_name = f"Char{next_number}"

        # Step 8: Alignment
        alignment = random.choice(ALIGNMENTS)

        # Step 9: Create character
        # Initial luck comes from the LCK stat roll we just made.
        character = {
            "name": character_name,
            "alignment": alignment,
            "class": "Lv0",
            "owner": ctx.author.id,
            "occupation": occupation["name"],
            "weapon": weapon,
            "inventory": inventory,
            "birth_augur": {
                "sign": sign,
                "effect": effect
            },
            "max_luck_mod": max_luck_mod,
            "hp": {
                "current": hp,
                "max": hp
            },
            "luck": {
                "current": int(stats.get("LCK", 0)),
                "max": int(stats.get("LCK", 0))
            },
            "ac": ac,
            "saves": {
                "reflex": reflex,
                "fortitude": fort,
                "will": will
            },
            "initiative": initiative,
            "speed": speed,
            "attack": attack,
            "cp": cp,
            "abilities": {k: {"max": int(stats[k]), "current": int(stats[k]), "mod": int(mods[k])} for k in stats},
            "armor": "unarmored",
            "shield": False,
            "fumble_die": ARMOR_TABLE["unarmored"]["fumble_die"],
            "action_die": "1d20",
            "crit_die": "1d4",
            "crit_table": "I",
            "languages": languages
        }

        # Save character
        filename = os.path.join(SAVE_FOLDER, f"{character_name}.json")
        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        await ctx.send(f"✅ New level 0 character created: **{character_name}**")
        await view_sheet(ctx, char_name=character_name)
        # send an explicit blank line to ensure separation after the sheet
        try:
            await ctx.send('\u200b')
        except Exception:
            pass
        # Show the rolled dice for each stat
        try:
            rolls_lines = []
            for st in ["STR", "AGI", "STA", "INT", "PER", "LCK"]:
                all_rolls = rolls_by_stat.get(st, [])
                kept = kept_by_stat.get(st, all_rolls)
                # If a keep_mode was used (dl/kh), always show which dice were kept and which dropped
                if keep_mode:
                    temp_kept = list(kept)
                    dropped = []
                    for r in all_rolls:
                        if r in temp_kept:
                            temp_kept.remove(r)
                        else:
                            dropped.append(r)
                    dropped_str = '+'.join(map(str, dropped)) if dropped else 'none'
                    rolls_lines.append(f"{st}: {stats[st]} (kept: {'+'.join(map(str, kept))}; dropped: {dropped_str})")
                else:
                    rolls_lines.append(f"{st}: {stats[st]} ({'+'.join(map(str, all_rolls))})")
            header = f"🎲 Rolls ({dice_spec}{keep_mode+str(keep_count) if keep_mode else ''}): "
            # add blank line before and after for readability and send an explicit blank message after
            await ctx.send("\n" + header + " | ".join(rolls_lines) + "\n")
            try:
                await ctx.send('\u200b')
            except Exception:
                pass
        except Exception:
            pass
        # Reminder message (sent as its own message to ensure visibility)
        try:
            await ctx.send("\n" + f":loudspeaker: Remember to use `!edit \"{character_name}\" set <name>` to change the character's name and `!edit \"{character_name}\" set alignment Lawful/Neutral/Chaotic` to choose an alignment. After that you can use `!language` to roll on the language table.")
        except Exception:
            # non-fatal if send fails
            pass

    except Exception as e:
        await ctx.send(f"⚠️ Error creating character: `{e}`")
        print(f"Create Error: {e}")


#-------Command List-------
@bot.command(name="helpme")
async def helpme(ctx):
    help_text = (
        "**🧙‍♂️ DCC Bot Command List**\n\n"
        "🆕 **Create Character**\n"
        "`!create` — Generate a random level 0 DCC character.\n\n"

        "🧾 **View Character Sheet**\n"
        "`!sheet <name>` — View a character sheet by name.\n\n"

        "🛠️ **Edit Character Sheet**\n"
        "`!edit \"<name>\" set <field> <value>` — Edit any part of a character.\n"
        "Example: `!edit \"Lv0 Character 1\" set name Bartholomew`\n"
        "Editable fields: `name`, `alignment`, `occupation`, `weapon`, `hp`, `ac`, `cp`, ability scores (e.g. `STR`), `birth_augur`, saves (`reflex`, `will`, `fortitude`), `inventory`, `armor`, `shield`\n\n"

        "🎒 **Modify Inventory**\n"
        "`!in \"<name>\" + item1, item2` — Add items to inventory\n"
        "`!in \"<name>\" - item1, item2` — Remove items from inventory\n"
        "Example: `!in \"Lv0 Character 1\" + torch, crowbar`\n\n"

        "🛡️ **Equip Armor or Shield**\n"
        "`!armor <name>` — Choose armor from a list\n"
        "`!shield <name>` — Toggle shield on/off and update AC\n\n"

        
        "❤️ **HP Management**\n"
        "`!hp <name> +/-<number>` — Heal or damage a character.\n"
        "Example: `!hp \"Lv0 Character 1\" -2`\n\n"

        "🗑️ **Delete Character**\n"
        "`!delete <name>` — Permanently delete a character you own.\n\n"

        "ℹ️ **Note**: Enclose character names in quotes if they contain spaces.\n"
    )
    await ctx.send(help_text)


@bot.command(name="fixmods")
async def fixmods(ctx, char_name: str):
    """Admin helper: recompute ability modifiers from ability scores and adjust saves.
    Usage: !fixmods <CharacterName>
    """
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            character = json.load(f)
    except Exception as e:
        await ctx.send(f"⚠️ Could not read file: {e}")
        return

    # permission: only owner can run
    if str(character.get('owner')) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return

    abilities = character.get('abilities', {}) or {}
    changes = []
    for stat, data in list(abilities.items()):
        try:
            if isinstance(data, dict):
                # prefer 'current', then 'max', then 'score'
                score = data.get('current', data.get('max', data.get('score', None)))
                if score is None:
                    # fallback: if mod exists, leave it
                    continue
                mod = int(get_modifier(int(score)))
                old = data.get('mod')
                data['mod'] = mod
                changes.append(f"{stat}: {old}->{mod}")
            else:
                # plain numeric value interpreted as score
                try:
                    mod = int(get_modifier(int(data)))
                    abilities[stat] = {'current': int(data), 'max': int(data), 'mod': mod}
                    changes.append(f"{stat}: ->{mod}")
                except Exception:
                    continue
        except Exception:
            continue

    # Recompute saves from AGI/STA/PER
    agi_mod = int(abilities.get('AGI', abilities.get('Agi', {})).get('mod', 0) if isinstance(abilities.get('AGI', {}), dict) else 0)
    sta_mod = int(abilities.get('STA', abilities.get('Sta', {})).get('mod', 0) if isinstance(abilities.get('STA', {}), dict) else 0)
    per_mod = int(abilities.get('PER', abilities.get('Per', {})).get('mod', 0) if isinstance(abilities.get('PER', {}), dict) else 0)
    character.setdefault('saves', {})
    old_saves = dict(character.get('saves', {}))
    character['saves']['reflex'] = character['saves'].get('reflex', 0) + agi_mod
    character['saves']['fortitude'] = character['saves'].get('fortitude', 0) + sta_mod
    character['saves']['will'] = character['saves'].get('will', 0) + per_mod

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(character, f, indent=2)
    except Exception as e:
        await ctx.send(f"⚠️ Could not save file: {e}")
        return

    msg_lines = [f"✅ Recomputed mods for `{char_name}`."]
    if changes:
        msg_lines.append("Changes: " + ", ".join(changes))
    msg_lines.append(f"Saves: {old_saves} -> {character.get('saves', {})}")
    await ctx.send("\n".join(msg_lines))


@bot.command(name="dumpchar")
async def dumpchar(ctx, char_name: str):
    """Owner-only debug: dump abilities and saves and computed AGI/STA/PER mods."""
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            character = json.load(f)
    except Exception as e:
        await ctx.send(f"⚠️ Could not read file: {e}")
        return
    if str(character.get('owner')) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return

    abil = character.get('abilities', {}) or {}
    saves = character.get('saves', {}) or {}

    def comp_mod(key):
        v = abil.get(key)
        if v is None:
            v = abil.get(key.lower()) or abil.get(key.upper())
        if isinstance(v, dict):
            mod = v.get('mod')
            cur = v.get('current', v.get('max', v.get('score')))
            return {'raw': v, 'mod': mod, 'current': cur, 'computed_mod': int(get_modifier(int(cur or 0))) if cur is not None else None}
        try:
            if isinstance(v, (int, float)):
                return {'raw': v, 'mod': None, 'current': v, 'computed_mod': int(get_modifier(int(v)))}
            if isinstance(v, str) and v.isdigit():
                return {'raw': v, 'mod': None, 'current': int(v), 'computed_mod': int(get_modifier(int(v)))}
        except Exception:
            pass
        return {'raw': v, 'mod': None, 'current': None, 'computed_mod': None}

    agi = comp_mod('AGI')
    sta = comp_mod('STA')
    per = comp_mod('PER')

    lines = [
        f"Abilities (raw): {json.dumps(abil, default=str)}",
        f"Saves (raw): {json.dumps(saves, default=str)}",
        f"AGI -> {agi}",
        f"STA -> {sta}",
        f"PER -> {per}"
    ]
    # send in chunks if too long
    msg = "\n".join(lines)
    for chunk in [msg[i:i+1900] for i in range(0, len(msg), 1900)]:
        await ctx.send("```" + chunk + "```")
#------------Lay on hands-------------------------------
async def _handle_disapproval_roll(char, filename, ctx, cause="disapproval"):
    """Load Disapproval Table from Spells.json, roll, post result, and apply simple effects."""
    import os, json, random
    spells_json_path = os.path.join(os.path.dirname(__file__), "Spells.json")
    try:
        with open(spells_json_path, "r", encoding="utf-8") as sf:
            spells = json.load(sf)
    except Exception as e:
        await ctx.send(f"⚠️ Could not load spells file for disapproval table: {e}")
        return

    dis_table = spells.get("Disapproval Table", {})
    die = dis_table.get("die", "1d20")
    # simple dice parser like '1d20'
    try:
        cnt, sides = die.lower().split('d')
        cnt = int(cnt)
        sides = int(sides)
    except Exception:
        cnt, sides = 1, 20
    roll_total = sum(random.randint(1, sides) for _ in range(cnt))

    table = dis_table.get("table", {})
    result_text = None
    for key, val in table.items():
        if isinstance(key, str) and key.endswith('+'):
            base = int(key[:-1])
            if roll_total >= base:
                result_text = val
                break
        elif isinstance(key, str) and '-' in key:
            lo, hi = key.split('-')
            try:
                lo_i = int(lo)
                hi_i = int(hi)
                if lo_i <= roll_total <= hi_i:
                    result_text = val
                    break
            except Exception:
                continue
        else:
            try:
                if int(key) == roll_total:
                    result_text = val
                    break
            except Exception:
                continue

    if result_text is None:
        display = "(No disapproval table entry found)"
    elif isinstance(result_text, dict):
        display = result_text.get("text", str(result_text))
    else:
        display = str(result_text)

    # Announce deity disapproval if we know the cleric's god
    god_name = char.get("god") if isinstance(char, dict) else None
    if god_name:
        await ctx.send(f"⚠️ {god_name} disapproves of your actions.")
    await ctx.send(f"⚠️ Disapproval triggered by {cause}! Rolled {die} = **{roll_total}**\n> {display}")

    # Apply a couple obvious mechanical effects if present in text
    text_low = display.lower()
    if "disapproval range immediately increases by another point" in text_low or "disapproval range immediately increases" in text_low:
        char["disapproval_range"] = char.get("disapproval_range", 1) + 1
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
            await ctx.send(f"🔺 Disapproval range increased to {char['disapproval_range']}.")
        except Exception:
            pass


async def check_and_handle_disapproval(char, filename, ctx, roll, cause="disapproval"):
    """Return (triggered:bool, orig_disapproval:int). If triggered, call the disapproval table handler."""
    import json
    triggered = False
    orig = char.get("disapproval_range", 1) if isinstance(char, dict) else 1
    if roll <= orig:
        triggered = True
        await _handle_disapproval_roll(char, filename, ctx, cause=cause)
    return triggered, orig


def apply_disapproval_penalty(char, filename, amount):
    """Apply an amount to char['disapproval_range'], save the file, and return the new value."""
    import json
    try:
        char["disapproval_range"] = char.get("disapproval_range", 1) + int(amount)
        with open(filename, "w") as f:
            json.dump(char, f, indent=2)
        return char["disapproval_range"]
    except Exception:
        return char.get("disapproval_range", 1)

@bot.command(name="loh", aliases=["layonhands"])
async def layonhands(ctx, char_name: str = None, target_name: str = None):
    """Cleric heals self or another character using Lay on Hands (command: !loh).
    If no cleric name is provided, the bot will prompt for it."""
    import os, json, random
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Prompt for cleric name if not provided
    if not char_name:
        await ctx.send("Who is using Lay on Hands? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            char_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Lay on Hands cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return
    with open(filename, "r") as f:
        char = json.load(f)
    if char.get("class", "").lower() != "cleric":
        await ctx.send(f"❌ `{char_name}` is not a cleric.")
        return

    # Prompt for target name if not provided
    if not target_name:
        await ctx.send("Who is the target of Lay on Hands? Type the character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            target_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Lay on Hands cancelled.")
            return

    target_file = os.path.join(SAVE_FOLDER, f"{target_name}.json")
    if not os.path.exists(target_file):
        await ctx.send(f"❌ Target `{target_name}` not found.")
        return
    with open(target_file, "r") as f:
        target = json.load(f)
    cleric_alignment = char.get("alignment", "neutral").lower()
    target_alignment = target.get("alignment", "neutral").lower()
    roll = random.randint(1, 20)
    # If the raw d20 roll is within or below the cleric's disapproval range, trigger Disapproval Table
    cleric_file = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    disapproval_triggered = False
    if roll <= char.get("disapproval_range", 1):
        disapproval_triggered = True
        await _handle_disapproval_roll(char, cleric_file, ctx, cause="Lay on Hands")
    level = char.get("level", 1)
    personality = char.get("personality", 0)
    mod = get_modifier(personality)
    # Determine relationship
    if cleric_alignment == target_alignment:
        relation = "same"
    elif (cleric_alignment, target_alignment) in [("lawful", "neutral"), ("neutral", "lawful"), ("neutral", "chaotic"), ("chaotic", "neutral")]:
        relation = "adjacent"
    else:
        relation = "opposed"

    # DCC Lay on Hands table
    # Format: (min, max, same, adjacent, opposed)
    lay_on_hands_table = [
        (1, 11, 0, 0, 0),
        (12, 13, 2, 1, 1),
        (14, 19, 3, 2, 1),
        (20, 21, 4, 3, 2),
        (22, 99, 5, 4, 3)
    ]
    # Apply target augury: Survived the plague → Magical healing (use target's max Luck mod)
    try:
        from modules.utils import get_max_luck_mod  # late import to avoid any cycles
    except Exception:
        get_max_luck_mod = None
    aug_heal_bonus = 0
    try:
        eff_t = str((target.get('birth_augur') or {}).get('effect') or '').strip()
        if eff_t == 'Magical healing' and callable(get_max_luck_mod):
            aug_heal_bonus = int(get_max_luck_mod(target) or 0)
    except Exception:
        aug_heal_bonus = 0
    total = roll + mod + level + int(aug_heal_bonus)
    # Find dice to heal
    dice_to_heal = 0
    for row in lay_on_hands_table:
        if row[0] <= total <= row[1]:
            if relation == "same":
                dice_to_heal = row[2]
            elif relation == "adjacent":
                dice_to_heal = row[3]
            else:
                dice_to_heal = row[4]
            break

    # Disapproval overrides success: if a disapproval was triggered, treat as failure
    if dice_to_heal == 0 or disapproval_triggered:
        # Increase disapproval range
        disapproval = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = disapproval
        # Save cleric file
        with open(os.path.join(SAVE_FOLDER, f"{char_name}.json"), "w") as f:
            json.dump(char, f, indent=2)
        await ctx.send(f"❌ Lay on Hands failed (rolled {roll} + {mod} + {level} = {total}). Disapproval range is now {disapproval}.")
        return

    # Determine die type by target's class
    class_hit_die = {
        "warrior": 12,
        "dwarf": 12,
        "halfling": 6,
        "elf": 6,
        "thief": 6,
        "cleric": 8,
        "wizard": 4,
        "": 4,  # fallback for Lv0
        "lv0": 4
    }
    target_class = target.get("class", "").lower()
    die_type = class_hit_die.get(target_class, 4)
    # Cap dice to target's level or hit dice
    target_level = target.get("level", 0) or 1
    dice_to_heal = min(dice_to_heal, target_level)
    # Roll healing
    heals = [random.randint(1, die_type) for _ in range(dice_to_heal)]
    total_heal = sum(heals)
    # Update HP
    target_hp = target.get("hp", {})
    max_hp = target_hp.get("max", 1)
    current_hp = target_hp.get("current", 1)
    target_hp["current"] = min(max_hp, current_hp + total_heal)
    target["hp"] = target_hp
    with open(os.path.join(SAVE_FOLDER, f"{target_name}.json"), "w") as f:
        json.dump(target, f, indent=2)
    aug_text = f" + target augur {aug_heal_bonus:+}" if aug_heal_bonus else ""
    await ctx.send(f"🙏 Lay on Hands successful! {target_name} is healed for {total_heal} HP (rolled {roll} + {mod} + {level}{aug_text} = {total}, {dice_to_heal}d{die_type}: {heals}).")
#------------Divine aid-------------------------------
from discord.ui import View, Button, Modal, TextInput

@bot.command(name="da")
async def divine_aid(ctx, char_name: str = None, request: str = None):
    """Cleric requests Divine Aid from their deity, selecting difficulty.
    If no character name provided, prompt for it interactively."""
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Prompt for character name if not provided
    if not char_name:
        await ctx.send("Who is requesting Divine Aid? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            char_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Divine Aid cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    with open(filename, "r") as f:
        char = json.load(f)

    if char.get("class", "").lower() != "cleric":
        await ctx.send("❌ Only clerics can request Divine Aid.")
        return

    level = char.get("level", 1)
    personality = char.get("personality", 10)
    mod = get_modifier(personality)
    deity = char.get("god", "their deity")

    # We'll roll after the DC is chosen so the disapproval result is shown after the user selects DC

    class DCModal(Modal, title="Custom DC"):
        dc = TextInput(label="Enter the DC (10-30)", required=True)
        async def on_submit(self, interaction):
            try:
                custom_dc = int(self.dc.value)
                await finish_divine_aid(interaction, custom_dc)
            except Exception:
                await interaction.response.send_message("❌ Invalid DC entered.", ephemeral=True)

    async def finish_divine_aid(interaction, dc):
        # Roll now (after DC choice) so any Disapproval output appears after the user chooses DC
        roll = random.randint(1, 20)

        # Check disapproval trigger using the current range before the divine aid penalty
        orig_disapproval = char.get("disapproval_range", 1)
        disapproval_triggered = False
        if roll <= orig_disapproval:
            disapproval_triggered = True
            await _handle_disapproval_roll(char, filename, ctx, cause="Divine Aid")

        # Increase disapproval range by 10 for attempting divine aid (penalty)
        disapproval = orig_disapproval + 10
        char["disapproval_range"] = disapproval
        with open(filename, "w") as f:
            json.dump(char, f, indent=2)

        total = roll + level + mod
        await interaction.response.send_message(
            f"🙏 {char['name']} prays to {deity} for divine aid...\n"
            f"🎲 Roll: {roll} + Level {level} + PER mod {mod} = **{total}** vs DC {dc}\nDisapproval range is now {disapproval}."
        )
        # Disapproval overrides success: if triggered, treat as failure
        if disapproval_triggered:
            await ctx.send(f"❌ Divine aid denied by divine disapproval. {deity} does not intervene.")
        elif total >= dc:
            await ctx.send(f"✨ Divine aid granted! {deity} answers the prayer.")
        else:
            await ctx.send(f"❌ Divine aid denied. {deity} does not intervene.")

    class DCView(View):
        def __init__(self):
            super().__init__(timeout=60)
            self.value = None

        @discord.ui.button(label="Simple (DC 10)", style=discord.ButtonStyle.success)
        async def simple(self, interaction, button):
            await finish_divine_aid(interaction, 10)
            self.stop()

        @discord.ui.button(label="Extraordinary (DC 18)", style=discord.ButtonStyle.danger)
        async def extraordinary(self, interaction, button):
            await finish_divine_aid(interaction, 18)
            self.stop()

        @discord.ui.button(label="Custom DC", style=discord.ButtonStyle.primary)
        async def custom(self, interaction, button):
            await interaction.response.send_modal(DCModal())
            self.stop()

    await ctx.send(
        f"Select the difficulty for {char['name']}'s Divine Aid request:",
        view=DCView()
    )
        
#------------Delete Character-------------------------------
@bot.command(name="delete")
async def delete_character(ctx, *, char_name: str = None):
    if not char_name or not char_name.strip():
        await ctx.send("❗ You must specify a character name to delete. Example: `!delete Char1`")
        return
    filename = os.path.join("characters", f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    # Ownership check
    if not await is_owner(ctx, filename):
        return

    try:
        os.remove(filename)
        await ctx.send(f"🗑️ Character `{char_name}` has been deleted.")
    except Exception as e:
        await ctx.send(f"⚠️ Error deleting character: `{e}`")
        print(f"Delete Error: {e}")


#------------Turn Unholy-------------------------------
@bot.command(name="turn")
async def turn_unholy(ctx, cleric_name: str = None, target_token: str = None):
    """Interactively prompt for cleric name and target HD, then attempt to turn unholy creatures.
    On fail, disapproval_range +1; if raw d20 <= disapproval_range, trigger Disapproval Table."""
    import os, json, random

    def _extract_hd_from_entry(entry):
        """Return an integer HD value parsed from an initiative entry's 'hd' field if possible."""
        if not entry:
            return None
        hd_field = entry.get('hd')
        if not hd_field:
            return None
        try:
            s = str(hd_field).strip()
            m = re.match(r"^(\d+)\s*d", s, re.I)
            if m:
                return int(m.group(1))
            m2 = re.match(r"^(\d+)\b", s)
            if m2:
                return int(m2.group(1))
        except Exception:
            return None
        return None

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # If caller passed a cleric name as the first arg, use it; otherwise ask.
    if not cleric_name:
        await ctx.send("Who is attempting the Turn? Type the cleric's character name.")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            cleric_name = msg.content.strip()
        except Exception:
            await ctx.send("⏳ Timeout. Turn cancelled.")
            return

    filename = os.path.join(SAVE_FOLDER, f"{cleric_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{cleric_name}` not found.")
        return

    with open(filename, "r") as f:
        char = json.load(f)

    if char.get("class", "").lower() != "cleric":
        await ctx.send("❌ Only clerics can turn unholy creatures.")
        return

    # If a target token was supplied, try to resolve it to an initiative entry and extract its HD.
    target_hd = None
    if target_token and initiative.INITIATIVE_ORDER:
        token = target_token.strip().lower()
        match_entry = next((c for c in initiative.INITIATIVE_ORDER if c.get('name','').lower() == token or c.get('abbr','').lower() == token), None)
        if match_entry:
            parsed_hd = _extract_hd_from_entry(match_entry)
            if parsed_hd is not None:
                target_hd = parsed_hd
    # If we couldn't get HD from args/initiative, prompt the user as before
    if target_hd is None:
        await ctx.send("What is the Hit Dice (HD) of the target creature? Reply with a number (e.g., 1, 2, 3).")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
            target_hd = int(msg.content.strip())
        except Exception:
            await ctx.send("⏳ Timeout or invalid HD. Turn cancelled.")
            return

    level = char.get("level", 1)
    personality = char.get("personality", 10)
    mod = get_modifier(personality)

    roll = random.randint(1, 20)
    total = roll + level + mod
    # Apply cleric augury: Righteous heart → Turn unholy checks (use cleric's max Luck mod)
    aug_turn_bonus = 0
    try:
        from modules.utils import get_max_luck_mod  # late import
        eff_c = str((char.get('birth_augur') or {}).get('effect') or '').strip()
        if eff_c == 'Turn unholy checks':
            aug_turn_bonus = int(get_max_luck_mod(char) or 0)
            total += aug_turn_bonus
    except Exception:
        aug_turn_bonus = 0

    # Disapproval trigger based on raw roll before applying failure penalty
    disapproval_triggered = False
    if roll <= char.get("disapproval_range", 1):
        disapproval_triggered = True
        await _handle_disapproval_roll(char, filename, ctx, cause="Turn Unholy")

    # Turning DC for HD: simple rule example — DC = 10 + target HD
    dc = 10 + int(target_hd)
    aug_text = f" + augur {aug_turn_bonus:+}" if aug_turn_bonus else ""
    await ctx.send(f"🔔 {char['name']} attempts to turn unholy (Roll: {roll} + L{level} + PER{mod}{aug_text} = {total} vs DC {dc})")

    # Disapproval overrides success: if triggered, treat as failure
    if disapproval_triggered:
        dis = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = dis
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
        except Exception:
            pass
        await ctx.send(f"❌ Turn failed due to divine disapproval. Disapproval range increased to {dis}.")
    elif total >= dc:
        await ctx.send(f"✨ Turn succeeded! Creatures of up to {target_hd} HD are affected.")
    else:
        # Failure: increase disapproval by 1
        dis = char.get("disapproval_range", 1) + 1
        char["disapproval_range"] = dis
        try:
            with open(filename, "w") as f:
                json.dump(char, f, indent=2)
        except Exception:
            pass
        await ctx.send(f"❌ Turn failed. Disapproval range increased to {dis}.")


#------------Edit-------------------------------
@bot.command(name='edit')
async def edit_character(ctx, char_name: str, action: str, field: str, *, value: str):
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        with open(filename, "r") as f:
            character = json.load(f)

        if action != "set":
            await ctx.send("❌ Invalid action. Use `set` to change a value.")
            return

        field = field.lower()

        # --- Name Change (Special Case: also rename file) ---
        if field == "name":
            new_name = value
            new_filename = os.path.join(SAVE_FOLDER, f"{new_name}.json")

            if os.path.exists(new_filename):
                await ctx.send(f"⚠️ A character named `{new_name}` already exists.")
                return

            character["name"] = new_name

            with open(new_filename, "w") as f:
                json.dump(character, f, indent=4)
            os.remove(filename)

            await ctx.send(f"✅ Character renamed to `{new_name}` and file updated.")
            return

        # --- Direct fields ---
        elif field in ["alignment", "occupation", "weapon"]:
            character[field] = value

        # --- Stats ---
        elif field.upper() in character["abilities"]:
            stat_name = field.upper()
            score = int(value)
            character["abilities"][stat_name]["score"] = score
            character["abilities"][stat_name]["mod"] = get_modifier(score)

            if stat_name == "AGI":
        # if dict, prefer 'mod', else derive from current/max/score
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                try:
                    return int(v.get('mod', 0))
                except Exception:
                    return 0
        # if plain number, interpreted as score, compute modifier
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    sta_mod = _ability_mod_from_char(character, 'STA')
    # Hit die per class: Cleric 1d8, Warrior 1d12, Thief 1d6, Wizard 1d4 (default others 1d6)
    if char_class == "cleric":
        hit_die = 8
    elif char_class == "warrior":
        hit_die = 12
    elif char_class == "wizard":
        hit_die = 4
    else:
        hit_die = 6
    roll = random.randint(1, hit_die)
    hp_gain = max(1, roll + sta_mod)
    character["hp"]["max"] += hp_gain
    character["hp"]["current"] += hp_gain
    hp_message = f"(1d{hit_die}) {roll} +{sta_mod} = +{hp_gain} hp → {character['hp']['current']}/{character['hp']['max']}"

    # Step 8: Apply class features and level up
    if char_class == "cleric":
        titles = {
            1: {"lawful": "Acolyte", "neutral": "Witness", "chaotic": "Zealot"},
            2: {"lawful": "Heathen-slayer", "neutral": "Pupil", "chaotic": "Convert"},
            3: {"lawful": "Brother", "neutral": "Chronicler", "chaotic": "Cultist"},
            4: {"lawful": "Curate", "neutral": "Apostle", "chaotic": "Judge"},
            5: {"lawful": "Father", "neutral": "High priest", "chaotic": "Druid"}
        }
        # Level up logic
        if current_level == 0:
            character.update({
                "class": "Cleric",
                "god": god,
                "weapon_proficiencies": alignment_data[alignment]["weapons"],
                "weapon_training": alignment_data[alignment]["weapons"],
                "unholy_targets": alignment_data[alignment]["unholy"],
                "features": ["Divine Spellcasting", "Turn Unholy", "Disapproval"],
                "spells": {"level_1": chosen_spells},
                "disapproval_range": 1,
                "title": titles[1][alignment],
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d8",
                "crit_table": "III",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment,
                "attack_bonus": "+0"
            })
    elif char_class == "wizard":
        # Level up logic for Wizard at creation
        if current_level == 0:
            character.update({
                "class": "Wizard",
                "patron": patron,
                "features": ["Arcane Spellcasting"],
                "spells": {"level_1": chosen_wizard_lvl1},
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d6",
                "crit_table": "I",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment,
                "attack_bonus": "+0"
            })
        elif current_level == 1:
            # Level 2: +1 attack, 1d8 hp, +1 spell, new title
            character["spells"]["level_1"] = chosen_spells
            character["level"] = 2
            character["title"] = titles[2][alignment]
            character["attack_bonus"] = "+1"
        elif current_level == 2:
            # Level 3: +1 attack (total +2), crit die 1d10, +1 reflex, +1 will, 3 level 2 spells, new title
            character["level"] = 3
            character["title"] = titles[3][alignment]
            character["attack_bonus"] = "+2"
            character["crit_die"] = "1d10"
            character.setdefault("spells", {})["level_2"] = chosen_spells_lvl2
        elif current_level == 3:
            # Level 4: +1 fort, +1 1st-level spell, +1 2nd-level spell, new title
            character["level"] = 4
            character["title"] = titles[4][alignment]
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_2"] = chosen_spells_lvl2
        elif current_level == 4:
            # Level 5: +1 attack (total +3), crit die 1d12, +1 will, +1 2nd-level spell, 2 third-level spells, new title
            character["level"] = 5
            character["title"] = titles[5][alignment]
            character["attack_bonus"] = "+3"
            character["crit_die"] = "1d12"
            # Add +1 will save
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            # Update spells
            character["spells"]["level_2"] = chosen_spells_lvl2
            character.setdefault("spells", {})["level_3"] = chosen_spells_lvl3
        elif current_level == 5:
            # Level 6: +1 attack (total +4), action die 1d20+1d14, +1 reflex, +1 will, +1 first-level spell, +1 third-level spell
            character["level"] = 6
            # No new title, keep level 5 title
            character["attack_bonus"] = "+4"
            character["action_die"] = "1d20+1d14"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 6:
            # Level 7: +1 attack (total +5), crit die 1d14, action die 1d20+1d16, +1 fort, +1 2nd-level spell, +1 3rd-level spell, 1 4th-level spell
            character["level"] = 7
            character["attack_bonus"] = "+5"
            character["crit_die"] = "1d14"
            character["action_die"] = "1d20+1d16"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 7:
            # Level 8: +1 attack (total +6), action die 1d20+1d20, +1 reflex, +1 will, +1 2nd-level spell, +1 3rd-level spell, +1 4th-level spell
            character["level"] = 8
            character["attack_bonus"] = "+6"
            character["action_die"] = "1d20+1d20"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 8:
            # Level 9: +1 attack, crit die 1d16, reflex +1, +1 2nd-level spell, +1 4th-level spell, 1 5th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
            prev_spells_lvl5 = character.get("spells", {}).get("level_5", [])
            prev_spell_names_lvl5 = [s["name"] for s in prev_spells_lvl5] if prev_spells_lvl5 else []
            # 2nd-level spell
            available_lvl2 = [s for s in cleric_spells_lvl2 if s not in prev_spell_names_lvl2]
            if not available_lvl2:
                await ctx.send("⚠️ You already know all available 2nd-level spells!")
                return
            spell_list2 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl2))
            await ctx.send(f"🧙 Choose 1 more 2nd-level spell (or type `random`):\n{spell_list2}")
            try:
                msg2 = await bot.wait_for("message", check=check, timeout=60)
                content2 = msg2.content.strip()
                if content2.lower() == "random":
                    chosen2 = [random.choice(available_lvl2)]
                else:
                    names2 = [s.strip() for s in content2.split(",")]
                    if len(names2) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid2 = [n for n in names2 if n not in available_lvl2]
                    if invalid2:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid2)}. Please pick from the list.")
                        return
                    chosen2 = names2
                chosen_spells_lvl2 = prev_spells_lvl2 + [{"name": chosen2[0], **spells_data["spells"]["Cleric Spells"]["level 2"].get(chosen2[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 4th-level spell
            available_lvl4 = [s for s in cleric_spells_lvl4 if s not in prev_spell_names_lvl4]
            if not available_lvl4:
                await ctx.send("⚠️ You already know all available 4th-level spells!")
                return
            spell_list4 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl4))
            await ctx.send(f"🧙 Choose 1 more 4th-level spell (or type `random`):\n{spell_list4}")
            try:
                msg4 = await bot.wait_for("message", check=check, timeout=60)
                content4 = msg4.content.strip()
                if content4.lower() == "random":
                    chosen4 = [random.choice(available_lvl4)]
                else:
                    names4 = [s.strip() for s in content4.split(",")]
                    if len(names4) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid4 = [n for n in names4 if n not in available_lvl4]
                    if invalid4:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid4)}. Please pick from the list.")
                        return
                    chosen4 = names4
                chosen_spells_lvl4 = prev_spells_lvl4 + [{"name": chosen4[0], **spells_data["spells"]["Cleric Spells"]["level 4"].get(chosen4[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 5th-level spell
            available_lvl5 = [s for s in cleric_spells_lvl5 if s not in prev_spell_names_lvl5]
            if not available_lvl5:
                await ctx.send("⚠️ You already know all available 5th-level spells!")
                return
            spell_list5 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl5))
            await ctx.send(f"🧙 Choose 1 more 5th-level spell (or type `random`):\n{spell_list5}")
            try:
                msg5 = await bot.wait_for("message", check=check, timeout=60)
                content5 = msg5.content.strip()
                if content5.lower() == "random":
                    chosen5 = [random.choice(available_lvl5)]
                else:
                    names5 = [s.strip() for s in content5.split(",")]
                    if len(names5) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid5 = [n for n in names5 if n not in available_lvl5]
                    if invalid5:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid5)}. Please pick from the list.")
                        return
                    chosen5 = names5
                chosen_spells_lvl5 = prev_spells_lvl5 + [{"name": chosen5[0], **spells_data["spells"]["Cleric Spells"]["level 5"].get(chosen5[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            character["level"] = 10
            character["attack_bonus"] = "+7"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
            character["spells"]["level_4"] = chosen_spells_lvl4
            character["spells"]["level_5"] = chosen_spells_lvl5

    # No new title for level 6, keep level 5 title

    # Step 6: Merge saving throws (add bonuses for each level)
    saves = character.get("saves", {}) or {}
    save_bonuses = {
        "cleric": {"reflex": 0, "fortitude": 1, "will": 1},
        "thief": {"reflex": 1, "fortitude": 1, "will": 0}
    }.get(char_class, {})
    # helper to get ability mods from the character (reuse _ability_mod_from_char if available)
    try:
        agi_mod_for_saves = _ability_mod_from_char(character, 'AGI')
        sta_mod_for_saves = _ability_mod_from_char(character, 'STA')
        per_mod_for_saves = _ability_mod_from_char(character, 'PER')
    except Exception:
        # fallback: try reading stored ability mods
        abil = character.get('abilities', {}) or {}
        agi_mod_for_saves = int(abil.get('AGI', {}).get('mod', abil.get('agi', 0)) if isinstance(abil.get('AGI', {}), dict) else 0)
        sta_mod_for_saves = int(abil.get('STA', {}).get('mod', abil.get('sta', 0)) if isinstance(abil.get('STA', {}), dict) else 0)
        per_mod_for_saves = int(abil.get('PER', {}).get('mod', abil.get('per', 0)) if isinstance(abil.get('PER', {}), dict) else 0)

    if current_level == 0:
        # For brand new characters, compute saves as class base + ability modifiers
        saves = {
            'reflex': int(save_bonuses.get('reflex', 0)) + int(agi_mod),
        'fortitude': int(save_bonuses.get('fortitude', 0)) + int(sta_mod),
            'will': int(save_bonuses.get('will', 0)) + int(per_mod)
        }
    elif char_class == "cleric" and current_level == 2:
        # Level 3: +1 reflex, +1 will
        saves["reflex"] = saves.get("reflex", 0) + 1
        saves["will"] = saves.get("will", 0) + 1
    elif char_class == "cleric" and current_level == 3:
        # Level 4: +1 fort
        saves["fortitude"] = saves.get("fortitude", 0) + 1

    # Step 7: Roll HP
    def _ability_mod_from_char(ch, key):
        abil = ch.get('abilities', {}) or {}
        v = abil.get(key)
        # try common key variants
        if v is None:
            v = abil.get(key.upper()) or abil.get(key.lower())
        # if dict, prefer 'mod', else derive from current/max/score
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                try:
                    return int(v.get('mod', 0))
                except Exception:
                    return 0
        # if plain number, interpreted as score, compute modifier
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    sta_mod = _ability_mod_from_char(character, 'STA')
    # Hit die per class: Cleric 1d8, Warrior 1d12, Thief 1d6, Wizard 1d4 (default others 1d6)
    if char_class == "cleric":
        hit_die = 8
    elif char_class == "warrior":
        hit_die = 12
    elif char_class == "wizard":
        hit_die = 4
    else:
        hit_die = 6
    roll = random.randint(1, hit_die)
    hp_gain = max(1, roll + sta_mod)
    character["hp"]["max"] += hp_gain
    character["hp"]["current"] += hp_gain
    hp_message = f"(1d{hit_die}) {roll} +{sta_mod} = +{hp_gain} hp → {character['hp']['current']}/{character['hp']['max']}"

    # Step 8: Apply class features and level up
    if char_class == "cleric":
        titles = {
            1: {"lawful": "Acolyte", "neutral": "Witness", "chaotic": "Zealot"},
            2: {"lawful": "Heathen-slayer", "neutral": "Pupil", "chaotic": "Convert"},
            3: {"lawful": "Brother", "neutral": "Chronicler", "chaotic": "Cultist"},
            4: {"lawful": "Curate", "neutral": "Apostle", "chaotic": "Judge"},
            5: {"lawful": "Father", "neutral": "High priest", "chaotic": "Druid"}
        }
        # Level up logic
        if current_level == 0:
            character.update({
                "class": "Cleric",
                "god": god,
                "weapon_proficiencies": alignment_data[alignment]["weapons"],
                "weapon_training": alignment_data[alignment]["weapons"],
                "unholy_targets": alignment_data[alignment]["unholy"],
                "features": ["Divine Spellcasting", "Turn Unholy", "Disapproval"],
                "spells": {"level_1": chosen_spells},
                "disapproval_range": 1,
                "title": titles[1][alignment],
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d8",
                "crit_table": "III",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment,
                "attack_bonus": "+0"
            })
        elif current_level == 1:
            # Level 2: +1 attack, 1d8 hp, +1 spell, new title
            character["spells"]["level_1"] = chosen_spells
            character["level"] = 2
            character["title"] = titles[2][alignment]
            character["attack_bonus"] = "+1"
        elif current_level == 2:
            # Level 3: +1 attack (total +2), crit die 1d10, +1 reflex, +1 will, 3 level 2 spells, new title
            character["level"] = 3
            character["title"] = titles[3][alignment]
            character["attack_bonus"] = "+2"
            character["crit_die"] = "1d10"
            character.setdefault("spells", {})["level_2"] = chosen_spells_lvl2
        elif current_level == 3:
            # Level 4: +1 fort, +1 1st-level spell, +1 2nd-level spell, new title
            character["level"] = 4
            character["title"] = titles[4][alignment]
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_2"] = chosen_spells_lvl2
        elif current_level == 4:
            # Level 5: +1 attack (total +3), crit die 1d12, +1 will, +1 2nd-level spell, 2 third-level spells, new title
            character["level"] = 5
            character["title"] = titles[5][alignment]
            character["attack_bonus"] = "+3"
            character["crit_die"] = "1d12"
            # Add +1 will save
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            # Update spells
            character["spells"]["level_2"] = chosen_spells_lvl2
            character.setdefault("spells", {})["level_3"] = chosen_spells_lvl3
        elif current_level == 5:
            # Level 6: +1 attack (total +4), action die 1d20+1d14, +1 reflex, +1 will, +1 first-level spell, +1 third-level spell
            character["level"] = 6
            # No new title, keep level 5 title
            character["attack_bonus"] = "+4"
            character["action_die"] = "1d20+1d14"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 6:
            # Level 7: +1 attack (total +5), crit die 1d14, action die 1d20+1d16, +1 fort, +1 2nd-level spell, +1 3rd-level spell, 1 4th-level spell
            character["level"] = 7
            character["attack_bonus"] = "+5"
            character["crit_die"] = "1d14"
            character["action_die"] = "1d20+1d16"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 7:
            # Level 8: +1 attack (total +6), action die 1d20+1d20, +1 reflex, +1 will, +1 2nd-level spell, +1 3rd-level spell, +1 4th-level spell
            character["level"] = 8
            character["attack_bonus"] = "+6"
            character["action_die"] = "1d20+1d20"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 8:
            # Level 9: +1 attack, crit die 1d16, reflex +1, +1 2nd-level spell, +1 4th-level spell, 1 5th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
            prev_spells_lvl5 = character.get("spells", {}).get("level_5", [])
            prev_spell_names_lvl5 = [s["name"] for s in prev_spells_lvl5] if prev_spells_lvl5 else []
            # 2nd-level spell
            available_lvl2 = [s for s in cleric_spells_lvl2 if s not in prev_spell_names_lvl2]
            if not available_lvl2:
                await ctx.send("⚠️ You already know all available 2nd-level spells!")
                return
            spell_list2 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl2))
            await ctx.send(f"🧙 Choose 1 more 2nd-level spell (or type `random`):\n{spell_list2}")
            try:
                msg2 = await bot.wait_for("message", check=check, timeout=60)
                content2 = msg2.content.strip()
                if content2.lower() == "random":
                    chosen2 = [random.choice(available_lvl2)]
                else:
                    names2 = [s.strip() for s in content2.split(",")]
                    if len(names2) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid2 = [n for n in names2 if n not in available_lvl2]
                    if invalid2:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid2)}. Please pick from the list.")
                        return
                    chosen2 = names2
                chosen_spells_lvl2 = prev_spells_lvl2 + [{"name": chosen2[0], **spells_data["spells"]["Cleric Spells"]["level 2"].get(chosen2[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 4th-level spell
            available_lvl4 = [s for s in cleric_spells_lvl4 if s not in prev_spell_names_lvl4]
            if not available_lvl4:
                await ctx.send("⚠️ You already know all available 4th-level spells!")
                return
            spell_list4 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl4))
            await ctx.send(f"🧙 Choose 1 more 4th-level spell (or type `random`):\n{spell_list4}")
            try:
                msg4 = await bot.wait_for("message", check=check, timeout=60)
                content4 = msg4.content.strip()
                if content4.lower() == "random":
                    chosen4 = [random.choice(available_lvl4)]
                else:
                    names4 = [s.strip() for s in content4.split(",")]
                    if len(names4) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid4 = [n for n in names4 if n not in available_lvl4]
                    if invalid4:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid4)}. Please pick from the list.")
                        return
                    chosen4 = names4
                chosen_spells_lvl4 = prev_spells_lvl4 + [{"name": chosen4[0], **spells_data["spells"]["Cleric Spells"]["level 4"].get(chosen4[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 5th-level spell
            available_lvl5 = [s for s in cleric_spells_lvl5 if s not in prev_spell_names_lvl5]
            if not available_lvl5:
                await ctx.send("⚠️ You already know all available 5th-level spells!")
                return
            spell_list5 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl5))
            await ctx.send(f"🧙 Choose 1 more 5th-level spell (or type `random`):\n{spell_list5}")
            try:
                msg5 = await bot.wait_for("message", check=check, timeout=60)
                content5 = msg5.content.strip()
                if content5.lower() == "random":
                    chosen5 = [random.choice(available_lvl5)]
                else:
                    names5 = [s.strip() for s in content5.split(",")]
                    if len(names5) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid5 = [n for n in names5 if n not in available_lvl5]
                    if invalid5:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid5)}. Please pick from the list.")
                        return
                    chosen5 = names5
                chosen_spells_lvl5 = prev_spells_lvl5 + [{"name": chosen5[0], **spells_data["spells"]["Cleric Spells"]["level 5"].get(chosen5[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            character["level"] = 10
            character["attack_bonus"] = "+7"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
            character["spells"]["level_4"] = chosen_spells_lvl4
            character["spells"]["level_5"] = chosen_spells_lvl5

    # No new title for level 6, keep level 5 title

    # Step 6: Merge saving throws (add bonuses for each level)
    saves = character.get("saves", {}) or {}
    save_bonuses = {
        "cleric": {"reflex": 0, "fortitude": 1, "will": 1},
        "thief": {"reflex": 1, "fortitude": 1, "will": 0}
    }.get(char_class, {})
    # helper to get ability mods from the character (reuse _ability_mod_from_char if available)
    try:
        agi_mod_for_saves = _ability_mod_from_char(character, 'AGI')
        sta_mod_for_saves = _ability_mod_from_char(character, 'STA')
        per_mod_for_saves = _ability_mod_from_char(character, 'PER')
    except Exception:
        # fallback: try reading stored ability mods
        abil = character.get('abilities', {}) or {}
        agi_mod_for_saves = int(abil.get('AGI', {}).get('mod', abil.get('agi', 0)) if isinstance(abil.get('AGI', {}), dict) else 0)
        sta_mod_for_saves = int(abil.get('STA', {}).get('mod', abil.get('sta', 0)) if isinstance(abil.get('STA', {}), dict) else 0)
        per_mod_for_saves = int(abil.get('PER', {}).get('mod', abil.get('per', 0)) if isinstance(abil.get('PER', {}), dict) else 0)

    if current_level == 0:
        # For brand new characters, compute saves as class base + ability modifiers
        saves = {
            'reflex': int(save_bonuses.get('reflex', 0)) + int(agi_mod),
        'fortitude': int(save_bonuses.get('fortitude', 0)) + int(sta_mod),
            'will': int(save_bonuses.get('will', 0)) + int(per_mod)
        }
    elif char_class == "cleric" and current_level == 2:
        # Level 3: +1 reflex, +1 will
        saves["reflex"] = saves.get("reflex", 0) + 1
        saves["will"] = saves.get("will", 0) + 1
    elif char_class == "cleric" and current_level == 3:
        # Level 4: +1 fort
        saves["fortitude"] = saves.get("fortitude", 0) + 1

    # Step 7: Roll HP
    def _ability_mod_from_char(ch, key):
        abil = ch.get('abilities', {}) or {}
        v = abil.get(key)
        # try common key variants
        if v is None:
            v = abil.get(key.upper()) or abil.get(key.lower())
        # if dict, prefer 'mod', else derive from current/max/score
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                try:
                    return int(v.get('mod', 0))
                except Exception:
                    return 0
        # if plain number, interpreted as score, compute modifier
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    sta_mod = _ability_mod_from_char(character, 'STA')
    # Hit die per class: Cleric 1d8, Warrior 1d12, Thief 1d6, Wizard 1d4 (default others 1d6)
    if char_class == "cleric":
        hit_die = 8
    elif char_class == "warrior":
        hit_die = 12
    elif char_class == "wizard":
        hit_die = 4
    else:
        hit_die = 6
    roll = random.randint(1, hit_die)
    hp_gain = max(1, roll + sta_mod)
    character["hp"]["max"] += hp_gain
    character["hp"]["current"] += hp_gain
    hp_message = f"(1d{hit_die}) {roll} +{sta_mod} = +{hp_gain} hp → {character['hp']['current']}/{character['hp']['max']}"

    # Step 8: Apply class features and level up
    if char_class == "cleric":
        titles = {
            1: {"lawful": "Acolyte", "neutral": "Witness", "chaotic": "Zealot"},
            2: {"lawful": "Heathen-slayer", "neutral": "Pupil", "chaotic": "Convert"},
            3: {"lawful": "Brother", "neutral": "Chronicler", "chaotic": "Cultist"},
            4: {"lawful": "Curate", "neutral": "Apostle", "chaotic": "Judge"},
            5: {"lawful": "Father", "neutral": "High priest", "chaotic": "Druid"}
        }
        # Level up logic
        if current_level == 0:
            character.update({
                "class": "Cleric",
                "god": god,
                "weapon_proficiencies": alignment_data[alignment]["weapons"],
                "weapon_training": alignment_data[alignment]["weapons"],
                "unholy_targets": alignment_data[alignment]["unholy"],
                "features": ["Divine Spellcasting", "Turn Unholy", "Disapproval"],
                "spells": {"level_1": chosen_spells},
                "disapproval_range": 1,
                "title": titles[1][alignment],
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d8",
                "crit_table": "III",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment,
                "attack_bonus": "+0"
            })
        elif current_level == 1:
            # Level 2: +1 attack, 1d8 hp, +1 spell, new title
            character["spells"]["level_1"] = chosen_spells
            character["level"] = 2
            character["title"] = titles[2][alignment]
            character["attack_bonus"] = "+1"
        elif current_level == 2:
            # Level 3: +1 attack (total +2), crit die 1d10, +1 reflex, +1 will, 3 level 2 spells, new title
            character["level"] = 3
            character["title"] = titles[3][alignment]
            character["attack_bonus"] = "+2"
            character["crit_die"] = "1d10"
            character.setdefault("spells", {})["level_2"] = chosen_spells_lvl2
        elif current_level == 3:
            # Level 4: +1 fort, +1 1st-level spell, +1 2nd-level spell, new title
            character["level"] = 4
            character["title"] = titles[4][alignment]
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_2"] = chosen_spells_lvl2
        elif current_level == 4:
            # Level 5: +1 attack (total +3), crit die 1d12, +1 will, +1 2nd-level spell, 2 third-level spells, new title
            character["level"] = 5
            character["title"] = titles[5][alignment]
            character["attack_bonus"] = "+3"
            character["crit_die"] = "1d12"
            # Add +1 will save
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            # Update spells
            character["spells"]["level_2"] = chosen_spells_lvl2
            character.setdefault("spells", {})["level_3"] = chosen_spells_lvl3
        elif current_level == 5:
            # Level 6: +1 attack (total +4), action die 1d20+1d14, +1 reflex, +1 will, +1 first-level spell, +1 third-level spell
            character["level"] = 6
            # No new title, keep level 5 title
            character["attack_bonus"] = "+4"
            character["action_die"] = "1d20+1d14"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 6:
            # Level 7: +1 attack (total +5), crit die 1d14, action die 1d20+1d16, +1 fort, +1 2nd-level spell, +1 3rd-level spell, 1 4th-level spell
            character["level"] = 7
            character["attack_bonus"] = "+5"
            character["crit_die"] = "1d14"
            character["action_die"] = "1d20+1d16"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 7:
            # Level 8: +1 attack (total +6), action die 1d20+1d20, +1 reflex, +1 will, +1 2nd-level spell, +1 3rd-level spell, +1 4th-level spell
            character["level"] = 8
            character["attack_bonus"] = "+6"
            character["action_die"] = "1d20+1d20"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 8:
            # Level 9: +1 attack, crit die 1d16, reflex +1, +1 2nd-level spell, +1 4th-level spell, 1 5th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
            prev_spells_lvl5 = character.get("spells", {}).get("level_5", [])
            prev_spell_names_lvl5 = [s["name"] for s in prev_spells_lvl5] if prev_spells_lvl5 else []
            # 2nd-level spell
            available_lvl2 = [s for s in cleric_spells_lvl2 if s not in prev_spell_names_lvl2]
            if not available_lvl2:
                await ctx.send("⚠️ You already know all available 2nd-level spells!")
                return
            spell_list2 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl2))
            await ctx.send(f"🧙 Choose 1 more 2nd-level spell (or type `random`):\n{spell_list2}")
            try:
                msg2 = await bot.wait_for("message", check=check, timeout=60)
                content2 = msg2.content.strip()
                if content2.lower() == "random":
                    chosen2 = [random.choice(available_lvl2)]
                else:
                    names2 = [s.strip() for s in content2.split(",")]
                    if len(names2) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid2 = [n for n in names2 if n not in available_lvl2]
                    if invalid2:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid2)}. Please pick from the list.")
                        return
                    chosen2 = names2
                chosen_spells_lvl2 = prev_spells_lvl2 + [{"name": chosen2[0], **spells_data["spells"]["Cleric Spells"]["level 2"].get(chosen2[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 4th-level spell
            available_lvl4 = [s for s in cleric_spells_lvl4 if s not in prev_spell_names_lvl4]
            if not available_lvl4:
                await ctx.send("⚠️ You already know all available 4th-level spells!")
                return
            spell_list4 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl4))
            await ctx.send(f"🧙 Choose 1 more 4th-level spell (or type `random`):\n{spell_list4}")
            try:
                msg4 = await bot.wait_for("message", check=check, timeout=60)
                content4 = msg4.content.strip()
                if content4.lower() == "random":
                    chosen4 = [random.choice(available_lvl4)]
                else:
                    names4 = [s.strip() for s in content4.split(",")]
                    if len(names4) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid4 = [n for n in names4 if n not in available_lvl4]
                    if invalid4:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid4)}. Please pick from the list.")
                        return
                    chosen4 = names4
                chosen_spells_lvl4 = prev_spells_lvl4 + [{"name": chosen4[0], **spells_data["spells"]["Cleric Spells"]["level 4"].get(chosen4[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 5th-level spell
            available_lvl5 = [s for s in cleric_spells_lvl5 if s not in prev_spell_names_lvl5]
            if not available_lvl5:
                await ctx.send("⚠️ You already know all available 5th-level spells!")
                return
            spell_list5 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl5))
            await ctx.send(f"🧙 Choose 1 more 5th-level spell (or type `random`):\n{spell_list5}")
            try:
                msg5 = await bot.wait_for("message", check=check, timeout=60)
                content5 = msg5.content.strip()
                if content5.lower() == "random":
                    chosen5 = [random.choice(available_lvl5)]
                else:
                    names5 = [s.strip() for s in content5.split(",")]
                    if len(names5) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid5 = [n for n in names5 if n not in available_lvl5]
                    if invalid5:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid5)}. Please pick from the list.")
                        return
                    chosen5 = names5
                chosen_spells_lvl5 = prev_spells_lvl5 + [{"name": chosen5[0], **spells_data["spells"]["Cleric Spells"]["level 5"].get(chosen5[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            character["level"] = 10
            character["attack_bonus"] = "+7"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
            character["spells"]["level_4"] = chosen_spells_lvl4
            character["spells"]["level_5"] = chosen_spells_lvl5

    # No new title for level 6, keep level 5 title

    # Step 6: Merge saving throws (add bonuses for each level)
    saves = character.get("saves", {}) or {}
    save_bonuses = {
        "cleric": {"reflex": 0, "fortitude": 1, "will": 1},
        "thief": {"reflex": 1, "fortitude": 1, "will": 0}
    }.get(char_class, {})
    # helper to get ability mods from the character (reuse _ability_mod_from_char if available)
    try:
        agi_mod_for_saves = _ability_mod_from_char(character, 'AGI')
        sta_mod_for_saves = _ability_mod_from_char(character, 'STA')
        per_mod_for_saves = _ability_mod_from_char(character, 'PER')
    except Exception:
        # fallback: try reading stored ability mods
        abil = character.get('abilities', {}) or {}
        agi_mod_for_saves = int(abil.get('AGI', {}).get('mod', abil.get('agi', 0)) if isinstance(abil.get('AGI', {}), dict) else 0)
        sta_mod_for_saves = int(abil.get('STA', {}).get('mod', abil.get('sta', 0)) if isinstance(abil.get('STA', {}), dict) else 0)
        per_mod_for_saves = int(abil.get('PER', {}).get('mod', abil.get('per', 0)) if isinstance(abil.get('PER', {}), dict) else 0)

    if current_level == 0:
        # For brand new characters, compute saves as class base + ability modifiers
        saves = {
            'reflex': int(save_bonuses.get('reflex', 0)) + int(agi_mod),
        'fortitude': int(save_bonuses.get('fortitude', 0)) + int(sta_mod),
            'will': int(save_bonuses.get('will', 0)) + int(per_mod)
        }
    elif char_class == "cleric" and current_level == 2:
        # Level 3: +1 reflex, +1 will
        saves["reflex"] = saves.get("reflex", 0) + 1
        saves["will"] = saves.get("will", 0) + 1
    elif char_class == "cleric" and current_level == 3:
        # Level 4: +1 fort
        saves["fortitude"] = saves.get("fortitude", 0) + 1

    # Step 7: Roll HP
    def _ability_mod_from_char(ch, key):
        abil = ch.get('abilities', {}) or {}
        v = abil.get(key)
        # try common key variants
        if v is None:
            v = abil.get(key.upper()) or abil.get(key.lower())
        # if dict, prefer 'mod', else derive from current/max/score
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                try:
                    return int(v.get('mod', 0))
                except Exception:
                    return 0
        # if plain number, interpreted as score, compute modifier
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    sta_mod = _ability_mod_from_char(character, 'STA')
    # Hit die per class: Cleric 1d8, Warrior 1d12, Thief 1d6, Wizard 1d4 (default others 1d6)
    if char_class == "cleric":
        hit_die = 8
    elif char_class == "warrior":
        hit_die = 12
    elif char_class == "wizard":
        hit_die = 4
    else:
        hit_die = 6
    roll = random.randint(1, hit_die)
    hp_gain = max(1, roll + sta_mod)
    character["hp"]["max"] += hp_gain
    character["hp"]["current"] += hp_gain
    hp_message = f"(1d{hit_die}) {roll} +{sta_mod} = +{hp_gain} hp → {character['hp']['current']}/{character['hp']['max']}"

    # Step 8: Apply class features and level up
    if char_class == "cleric":
        titles = {
            1: {"lawful": "Acolyte", "neutral": "Witness", "chaotic": "Zealot"},
            2: {"lawful": "Heathen-slayer", "neutral": "Pupil", "chaotic": "Convert"},
            3: {"lawful": "Brother", "neutral": "Chronicler", "chaotic": "Cultist"},
            4: {"lawful": "Curate", "neutral": "Apostle", "chaotic": "Judge"},
            5: {"lawful": "Father", "neutral": "High priest", "chaotic": "Druid"}
        }
        # Level up logic
        if current_level == 0:
            character.update({
                "class": "Cleric",
                "god": god,
                "weapon_proficiencies": alignment_data[alignment]["weapons"],
                "weapon_training": alignment_data[alignment]["weapons"],
                "unholy_targets": alignment_data[alignment]["unholy"],
                "features": ["Divine Spellcasting", "Turn Unholy", "Disapproval"],
                "spells": {"level_1": chosen_spells},
                "disapproval_range": 1,
                "title": titles[1][alignment],
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d8",
                "crit_table": "III",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment,
                "attack_bonus": "+0"
            })
        elif current_level == 1:
            # Level 2: +1 attack, 1d8 hp, +1 spell, new title
            character["spells"]["level_1"] = chosen_spells
            character["level"] = 2
            character["title"] = titles[2][alignment]
            character["attack_bonus"] = "+1"
        elif current_level == 2:
            # Level 3: +1 attack (total +2), crit die 1d10, +1 reflex, +1 will, 3 level 2 spells, new title
            character["level"] = 3
            character["title"] = titles[3][alignment]
            character["attack_bonus"] = "+2"
            character["crit_die"] = "1d10"
            character.setdefault("spells", {})["level_2"] = chosen_spells_lvl2
        elif current_level == 3:
            # Level 4: +1 fort, +1 1st-level spell, +1 2nd-level spell, new title
            character["level"] = 4
            character["title"] = titles[4][alignment]
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_2"] = chosen_spells_lvl2
        elif current_level == 4:
            # Level 5: +1 attack (total +3), crit die 1d12, +1 will, +1 2nd-level spell, 2 third-level spells, new title
            character["level"] = 5
            character["title"] = titles[5][alignment]
            character["attack_bonus"] = "+3"
            character["crit_die"] = "1d12"
            # Add +1 will save
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            # Update spells
            character["spells"]["level_2"] = chosen_spells_lvl2
            character.setdefault("spells", {})["level_3"] = chosen_spells_lvl3
        elif current_level == 5:
            # Level 6: +1 attack (total +4), action die 1d20+1d14, +1 reflex, +1 will, +1 first-level spell, +1 third-level spell
            character["level"] = 6
            # No new title, keep level 5 title
            character["attack_bonus"] = "+4"
            character["action_die"] = "1d20+1d14"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 6:
            # Level 7: +1 attack (total +5), crit die 1d14, action die 1d20+1d16, +1 fort, +1 2nd-level spell, +1 3rd-level spell, 1 4th-level spell
            character["level"] = 7
            character["attack_bonus"] = "+5"
            character["crit_die"] = "1d14"
            character["action_die"] = "1d20+1d16"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 7:
            # Level 8: +1 attack (total +6), action die 1d20+1d20, +1 reflex, +1 will, +1 2nd-level spell, +1 3rd-level spell, +1 4th-level spell
            character["level"] = 8
            character["attack_bonus"] = "+6"
            character["action_die"] = "1d20+1d20"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_3"] = chosen_spells_lvl3
        elif current_level == 8:
            # Level 9: +1 attack, crit die 1d16, reflex +1, +1 2nd-level spell, +1 4th-level spell, 1 5th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
            prev_spells_lvl5 = character.get("spells", {}).get("level_5", [])
            prev_spell_names_lvl5 = [s["name"] for s in prev_spells_lvl5] if prev_spells_lvl5 else []
            # 2nd-level spell
            available_lvl2 = [s for s in cleric_spells_lvl2 if s not in prev_spell_names_lvl2]
            if not available_lvl2:
                await ctx.send("⚠️ You already know all available 2nd-level spells!")
                return
            spell_list2 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl2))
            await ctx.send(f"🧙 Choose 1 more 2nd-level spell (or type `random`):\n{spell_list2}")
            try:
                msg2 = await bot.wait_for("message", check=check, timeout=60)
                content2 = msg2.content.strip()
                if content2.lower() == "random":
                    chosen2 = [random.choice(available_lvl2)]
                else:
                    names2 = [s.strip() for s in content2.split(",")]
                    if len(names2) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid2 = [n for n in names2 if n not in available_lvl2]
                    if invalid2:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid2)}. Please pick from the list.")
                        return
                    chosen2 = names2
                chosen_spells_lvl2 = prev_spells_lvl2 + [{"name": chosen2[0], **spells_data["spells"]["Cleric Spells"]["level 2"].get(chosen2[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 4th-level spell
            available_lvl4 = [s for s in cleric_spells_lvl4 if s not in prev_spell_names_lvl4]
            if not available_lvl4:
                await ctx.send("⚠️ You already know all available 4th-level spells!")
                return
            spell_list4 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl4))
            await ctx.send(f"🧙 Choose 1 more 4th-level spell (or type `random`):\n{spell_list4}")
            try:
                msg4 = await bot.wait_for("message", check=check, timeout=60)
                content4 = msg4.content.strip()
                if content4.lower() == "random":
                    chosen4 = [random.choice(available_lvl4)]
                else:
                    names4 = [s.strip() for s in content4.split(",")]
                    if len(names4) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid4 = [n for n in names4 if n not in available_lvl4]
                    if invalid4:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid4)}. Please pick from the list.")
                        return
                    chosen4 = names4
                chosen_spells_lvl4 = prev_spells_lvl4 + [{"name": chosen4[0], **spells_data["spells"]["Cleric Spells"]["level 4"].get(chosen4[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 5th-level spell
            available_lvl5 = [s for s in cleric_spells_lvl5 if s not in prev_spell_names_lvl5]
            if not available_lvl5:
                await ctx.send("⚠️ You already know all available 5th-level spells!")
                return
            spell_list5 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl5))
            await ctx.send(f"🧙 Choose 1 more 5th-level spell (or type `random`):\n{spell_list5}")
            try:
                msg5 = await bot.wait_for("message", check=check, timeout=60)
                content5 = msg5.content.strip()
                if content5.lower() == "random":
                    chosen5 = [random.choice(available_lvl5)]
                else:
                    names5 = [s.strip() for s in content5.split(",")]
                    if len(names5) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid5 = [n for n in names5 if n not in available_lvl5]
                    if invalid5:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid5)}. Please pick from the list.")
                        return
                    chosen5 = names5
                chosen_spells_lvl5 = prev_spells_lvl5 + [{"name": chosen5[0], **spells_data["spells"]["Cleric Spells"]["level 5"].get(chosen5[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            character["level"] = 10
            character["attack_bonus"] = "+7"
            character["saves"]["fortitude"] = character["saves"].get("fortitude", 0) + 1
            character["saves"]["will"] = character["saves"].get("will", 0) + 1
            character["spells"]["level_1"] = chosen_spells
            character["spells"]["level_3"] = chosen_spells_lvl3
            character["spells"]["level_4"] = chosen_spells_lvl4
            character["spells"]["level_5"] = chosen_spells_lvl5

    # No new title for level 6, keep level 5 title

    # Step 6: Merge saving throws (add bonuses for each level)
    saves = character.get("saves", {}) or {}
    save_bonuses = {
        "cleric": {"reflex": 0, "fortitude": 1, "will": 1},
        "thief": {"reflex": 1, "fortitude": 1, "will": 0}
    }.get(char_class, {})
    # helper to get ability mods from the character (reuse _ability_mod_from_char if available)
    try:
        agi_mod_for_saves = _ability_mod_from_char(character, 'AGI')
        sta_mod_for_saves = _ability_mod_from_char(character, 'STA')
        per_mod_for_saves = _ability_mod_from_char(character, 'PER')
    except Exception:
        # fallback: try reading stored ability mods
        abil = character.get('abilities', {}) or {}
        agi_mod_for_saves = int(abil.get('AGI', {}).get('mod', abil.get('agi', 0)) if isinstance(abil.get('AGI', {}), dict) else 0)
        sta_mod_for_saves = int(abil.get('STA', {}).get('mod', abil.get('sta', 0)) if isinstance(abil.get('STA', {}), dict) else 0)
        per_mod_for_saves = int(abil.get('PER', {}).get('mod', abil.get('per', 0)) if isinstance(abil.get('PER', {}), dict) else 0)

    if current_level == 0:
        # For brand new characters, compute saves as class base + ability modifiers
        saves = {
            'reflex': int(save_bonuses.get('reflex', 0)) + int(agi_mod),
        'fortitude': int(save_bonuses.get('fortitude', 0)) + int(sta_mod),
            'will': int(save_bonuses.get('will', 0)) + int(per_mod)
        }
    elif char_class == "cleric" and current_level == 2:
        # Level 3: +1 reflex, +1 will
        saves["reflex"] = saves.get("reflex", 0) + 1
        saves["will"] = saves.get("will", 0) + 1
    elif char_class == "cleric" and current_level == 3:
        # Level 4: +1 fort
        saves["fortitude"] = saves.get("fortitude", 0) + 1

    # Step 7: Roll HP
    def _ability_mod_from_char(ch, key):
        abil = ch.get('abilities', {}) or {}
        v = abil.get(key)
        # try common key variants
        if v is None:
            v = abil.get(key.upper()) or abil.get(key.lower())
        # if dict, prefer 'mod', else derive from current/max/score
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                return int(v.get('mod', 0) or 0)
        # if plain number, interpreted as score, compute modifier
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():