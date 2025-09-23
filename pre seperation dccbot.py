import discord
from discord.ext import commands
import os
import json
import random
import re
import logging
from modules.data_constants import (
    ALIGNMENTS,
    AUGURIES,
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
    roll_dice,
    consume_luck_and_save,
    get_luck_current,
    _parse_luck_value,
)
import modules.initiative as initiative

# --- Logging Setup ---
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger('dccbot')

# Create character save folder if it doesn't exist
SAVE_FOLDER = "characters"
os.makedirs(SAVE_FOLDER, exist_ok=True)

# Load token from custom .env file
load_dotenv(dotenv_path='token.env')
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)
initiative.register(bot)

@bot.event
async def on_ready():
    try:
        guild_count = len(bot.guilds)
    except Exception:
        guild_count = 'unknown'
    logger.info(f"Bot ready as {bot.user} in {guild_count} guild(s)")
    # Optionally print to console for immediate visibility
    print(f"✅ Logged in as {bot.user} (guilds: {guild_count})")

@bot.before_invoke
async def log_command_usage(ctx):
    try:
        logger.info(
            "CMD user=%s guild=%s channel=%s command=%s args=%s",
            getattr(ctx.author, 'id', '?'),
            getattr(getattr(ctx.guild, 'id', None), '__str__', lambda: 'DM')(),
            getattr(ctx.channel, 'id', '?'),
            getattr(ctx.command, 'qualified_name', '?'),
            ctx.message.content
        )
    except Exception:
        pass

## Constants now imported from modules.data_constants

## Initiative commands moved to modules.initiative



## Removed large inline constant tables (Weapons, Armor, Equipment, Languages, Auguries, Animals, Occupations) in favor of imported module.

# Helper: Ownership check (with admin override)
async def is_owner(ctx, filename):
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character not found.")
        return False

    with open(filename, "r") as f:
        data = json.load(f)

    # Admins bypass ownership
    if ctx.author.guild_permissions.administrator:
        return True

    if str(data.get("owner")) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return False

    return True

import discord
from discord.ui import Button, View, Modal, TextInput

# Load custom weapons from file if it exists
CUSTOM_WEAPON_FILE = "custom_weapons.json"
if os.path.exists(CUSTOM_WEAPON_FILE):
    with open(CUSTOM_WEAPON_FILE, "r") as f:
        custom_weapons = json.load(f)
        WEAPON_TABLE.update(custom_weapons)
else:
    custom_weapons = {}

# Save function for custom weapons
def save_custom_weapons():
    with open(CUSTOM_WEAPON_FILE, "w") as f:
        json.dump({k: v for k, v in WEAPON_TABLE.items() if k in custom_weapons}, f, indent=4)

# Modal for weapon input
class WeaponModal(Modal, title="Add Custom Weapon"):
    name = TextInput(label="Weapon Name", placeholder="e.g. frostbrand", required=True)
    damage = TextInput(label="Damage Dice", placeholder="e.g. 1d8+2", required=True)
    type = TextInput(label="Weapon Type", placeholder="melee / ranged", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name.value.strip().lower()
        damage = self.damage.value.strip()
        wtype = self.type.value.strip().lower()

        if name in WEAPON_TABLE:
            await interaction.response.send_message(f"⚠️ `{name}` already exists in weapon table.", ephemeral=True)
            return

        weapon_data = {
            "damage": damage,
            "type": wtype
        }

        WEAPON_TABLE[name] = weapon_data
        custom_weapons[name] = weapon_data
        save_custom_weapons()

        await interaction.response.send_message(f"✅ Added custom weapon `{name}` with `{damage}` damage and `{wtype}` type.")

# Button to trigger modal
class AddWeaponView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(Button(label="Add Weapon", style=discord.ButtonStyle.green, custom_id="add_weapon_button"))

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
    # Uses initiative module state (initiative.INITIATIVE_ORDER, etc.)

    # Help message
    help_msg = (
        "**Attack Command Usage:**\n"
        "`!a <attacker> <weapon> -t <target> [-b <bonus to hit>] [-d <bonus to damage>] [-die <custom die>]`\n"
        "Example: `!a charles dagger -t goblin -b +2 -d +1 -die 1d24`"
    )


    interactive_flow_used = False
    selected_action_die = None
    if len(args) < 1:
        # Interactive UI flow when user runs just `!a`:
        # 1) Select one of the user's saved characters (buttons)
        # 2) Select a weapon for that character (buttons)
        # 3) Type targets and modifiers in chat (single message)
        # 4) If warrior/dwarf, type a deed declaration (single message)

        # Helper: gather characters owned by this Discord user
        user_chars = []
        try:
            for fn in os.listdir(SAVE_FOLDER):
                if not fn.lower().endswith('.json'):
                    continue
                p = os.path.join(SAVE_FOLDER, fn)
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    owner = data.get('owner')
                    if owner is None:
                        continue
                    # owner may be stored as int or str
                    if str(owner) == str(getattr(ctx.author, 'id', '')):
                        user_chars.append(data.get('name'))
                except Exception:
                    continue
        except Exception:
            user_chars = []

        # If no user characters, fallback to initiative actor if available
        if not user_chars:
            if initiative.INITIATIVE_ORDER and initiative.CURRENT_TURN_INDEX is not None:
                attacker_name = initiative.INITIATIVE_ORDER[initiative.CURRENT_TURN_INDEX]['name']
            else:
                await ctx.send(f"❗ Not enough arguments.\n{help_msg}")
                return
        else:
            # Character selection view
            class CharSelectView(discord.ui.View):
                def __init__(self, choices, timeout=60):
                    super().__init__(timeout=timeout)
                    self.value = None
                    for name in choices:
                        btn = discord.ui.Button(label=str(name), style=discord.ButtonStyle.primary)
                        async def cb(interaction, _name=name):
                            if interaction.user != ctx.author:
                                await interaction.response.send_message("This selection isn't yours.", ephemeral=True)
                                return
                            self.value = _name
                            try:
                                await interaction.response.edit_message(content=f"Selected: `{_name}`", view=None)
                            except Exception:
                                pass
                            self.stop()
                        btn.callback = cb
                        self.add_item(btn)
                    # Cancel button
                    cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary)
                    async def cancel_cb(interaction):
                        if interaction.user != ctx.author:
                            await interaction.response.send_message("Not your selection.", ephemeral=True)
                            return
                        try:
                            await interaction.response.edit_message(content='Selection cancelled.', view=None)
                        except Exception:
                            pass
                        self.stop()
                    cancel.callback = cancel_cb
                    self.add_item(cancel)

            view = CharSelectView(user_chars)
            try:
                await ctx.send(f"🔹 Select which character to use, {ctx.author.display_name}:", view=view)
                await view.wait()
            except Exception:
                await ctx.send("⏳ Selection timed out or failed. Use `!a <attacker> <weapon> -t <target>` instead.")
                return

            if not view.value:
                await ctx.send("No character selected. Cancelled.")
                return
            attacker_name = view.value

            # Load selected attacker
            filename = os.path.join(SAVE_FOLDER, f"{attacker_name}.json")
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    attacker = json.load(f)
            except Exception:
                await ctx.send(f"⚠️ Failed to load `{attacker_name}`. Cancelled.")
                return

            # Build weapon list for this attacker
            weapon_names = []
            if 'weapons' in attacker and isinstance(attacker['weapons'], list):
                weapon_names.extend([w.get('name') for w in attacker['weapons'] if isinstance(w, dict) and w.get('name')])
            if 'weapon' in attacker and isinstance(attacker['weapon'], str):
                weapon_names.append(attacker['weapon'])
            if 'inventory' in attacker:
                for item in attacker['inventory']:
                    if isinstance(item, dict) and item.get('name') in WEAPON_TABLE:
                        weapon_names.append(item['name'])
            if 'attacks' in attacker and isinstance(attacker['attacks'], list):
                weapon_names.extend([atk.get('name') for atk in attacker['attacks'] if isinstance(atk, dict) and atk.get('name')])

            if not weapon_names:
                await ctx.send(f"⚠️ No weapons or attacks found for `{attacker_name}`.")
                return

            # Weapon selection view
            class WeaponSelectView(discord.ui.View):
                def __init__(self, choices, timeout=60):
                    super().__init__(timeout=timeout)
                    self.value = None
                    for name in choices:
                        btn = discord.ui.Button(label=str(name), style=discord.ButtonStyle.primary)
                        async def cb(interaction, _name=name):
                            if interaction.user != ctx.author:
                                await interaction.response.send_message("This selection isn't yours.", ephemeral=True)
                                return
                            self.value = _name
                            try:
                                await interaction.response.edit_message(content=f"Selected weapon: `{_name}`", view=None)
                            except Exception:
                                pass
                            self.stop()
                        btn.callback = cb
                        self.add_item(btn)
                    cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary)
                    async def cancel_cb2(interaction):
                        if interaction.user != ctx.author:
                            await interaction.response.send_message("Not your selection.", ephemeral=True)
                            return
                        try:
                            await interaction.response.edit_message(content='Selection cancelled.', view=None)
                        except Exception:
                            pass
                        self.stop()
                    cancel.callback = cancel_cb2
                    self.add_item(cancel)

            wview = WeaponSelectView(weapon_names)
            try:
                await ctx.send(f"🧷 `{attacker_name}` has the following weapons — pick one:", view=wview)
                await wview.wait()
            except Exception:
                await ctx.send("⏳ Weapon selection timed out or failed. Cancelled.")
                return

            if not wview.value:
                await ctx.send("No weapon selected. Cancelled.")
                return
            attack_name = wview.value
            interactive_flow_used = True

            # Action die selection (buttons) when multiple dice are available
            # Gather action dice options from character
            action_dice_opts = []
            try:
                if isinstance(attacker.get('action_dice'), list) and attacker['action_dice']:
                    action_dice_opts = [str(a).strip() for a in attacker['action_dice'] if str(a).strip()]
                else:
                    act = str(attacker.get('action_die', '1d20'))
                    action_dice_opts = [d.strip() for d in act.replace(',', '+').split('+') if d.strip()]
            except Exception:
                action_dice_opts = []
            # Deduplicate while preserving order
            seen = set()
            uniq_opts = []
            for opt in action_dice_opts:
                lo = opt.lower()
                if lo not in seen:
                    seen.add(lo)
                    uniq_opts.append(opt)
            action_dice_opts = uniq_opts or ['1d20']

            if len(action_dice_opts) > 1:
                class ActionDieSelectView(discord.ui.View):
                    def __init__(self, choices, timeout=60):
                        super().__init__(timeout=timeout)
                        self.value = None
                        for die in choices:
                            btn = discord.ui.Button(label=str(die), style=discord.ButtonStyle.primary)
                            async def cb(interaction, _die=die):
                                if interaction.user != ctx.author:
                                    await interaction.response.send_message("Not your selection.", ephemeral=True)
                                    return
                                self.value = _die
                                try:
                                    await interaction.response.edit_message(content=f"🎲 Action die selected: `{_die}`", view=None)
                                except Exception:
                                    pass
                                self.stop()
                            btn.callback = cb
                            self.add_item(btn)
                        cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary)
                        async def cancel_cb(interaction):
                            if interaction.user != ctx.author:
                                await interaction.response.send_message("Not your selection.", ephemeral=True)
                                return
                            try:
                                await interaction.response.edit_message(content='Action die selection cancelled; using default.', view=None)
                            except Exception:
                                pass
                            self.stop()
                        cancel.callback = cancel_cb
                        self.add_item(cancel)

                adv = ActionDieSelectView(action_dice_opts)
                try:
                    await ctx.send(f"🎲 `{attacker_name}` has multiple action dice — pick one:", view=adv)
                    await adv.wait()
                except Exception:
                    await ctx.send("⏳ Action die selection timed out; using default.")
                selected_action_die = adv.value if adv.value else action_dice_opts[0]
            else:
                selected_action_die = action_dice_opts[0]

            # Ask for targets and modifiers as a typed message
            await ctx.send("✍️ Type targets and modifiers (e.g. `-t goblin -b 2 -d 1`) or `-` to skip targets:")
            def tm_check(m):
                return m.author == ctx.author and m.channel == ctx.channel
            try:
                msg = await bot.wait_for("message", check=tm_check, timeout=60)
                txt = msg.content.strip()
            except Exception:
                await ctx.send("⏳ No response; continuing without explicit targets.")
                txt = ""

            # Parse typed flags into local variables (simple parser)
            def parse_flags_from_text(text):
                t_targets = []
                t_bonus_hit = 0
                t_bonus_dmg = 0
                t_custom_die = None
                t_dc = None
                t_burn = False
                t_force_fumble = False
                toks = text.split()
                i2 = 0
                while i2 < len(toks):
                    a = toks[i2]
                    if a == '-t' and i2 + 1 < len(toks):
                        t_targets.extend([x.strip() for x in toks[i2+1].split(',') if x.strip()])
                        i2 += 2
                    elif a == '-b' and i2 + 1 < len(toks):
                        try:
                            t_bonus_hit = int(toks[i2+1])
                        except Exception:
                            t_bonus_hit = 0
                        i2 += 2
                    elif a == '-d' and i2 + 1 < len(toks):
                        try:
                            t_bonus_dmg = int(toks[i2+1])
                        except Exception:
                            t_bonus_dmg = 0
                        i2 += 2
                    elif a == '-die' and i2 + 1 < len(toks):
                        t_custom_die = toks[i2+1]
                        i2 += 2
                    elif a == '-dc' and i2 + 1 < len(toks):
                        try:
                            t_dc = int(toks[i2+1])
                        except Exception:
                            t_dc = None
                        i2 += 2
                    elif a == '-burn':
                        t_burn = True
                        i2 += 1
                    elif a == '-fumble':
                        t_force_fumble = True
                        i2 += 1
                    else:
                        i2 += 1
                return t_targets, t_bonus_hit, t_bonus_dmg, t_custom_die, t_dc, t_burn, t_force_fumble

            if txt and txt != '-':
                targets, bonus_hit, bonus_dmg, custom_die, dc, burn, force_fumble = parse_flags_from_text(txt)
            else:
                # Establish defaults when user skipped target/mod input
                targets, bonus_hit, bonus_dmg, custom_die, dc, burn, force_fumble = [], 0, 0, None, None, False, False

            # If user did not set -die, use the selected action die from the buttons
            if custom_die is None and selected_action_die:
                custom_die = selected_action_die

            # Deed declaration is no longer prompted here; UI or prior steps handle it.

    # attacker_name may have been set above when args were empty and initiative provided a default.
    # Preserve selections from interactive flow; otherwise, initialize defaults.
    if not interactive_flow_used:
        attack_name = None
        targets = []
        bonus_hit = 0
        bonus_dmg = 0
        custom_die = None
        dc = None
        burn = False
        force_fumble = False

    # Decide whether the first arg is an attacker name or a weapon name.
    # If a saved character exists with that name, treat it as attacker.
    # Otherwise, if initiative is active and there's a current turn, treat the first arg as the weapon
    # and use the current initiative character as the attacker.
    # Safely extract the first token if present
    if len(args) > 0:
        first = args[0]
    else:
        first = None

    # If a token exists, check whether it's a saved character file
    if first:
        candidate_file = os.path.join(SAVE_FOLDER, f"{first}.json")
        if os.path.exists(candidate_file):
            attacker_name = first
            # If a weapon is provided as the second arg, use it
            if len(args) > 1 and not args[1].startswith('-'):
                attack_name = args[1]
                i = 2
            else:
                i = 1
        else:
            # No saved character by that name. If initiative is active and a current turn exists,
            # use the current character as the attacker and treat the first arg as the weapon name.
                    if initiative.INITIATIVE_ORDER and initiative.CURRENT_TURN_INDEX is not None:
                        attacker_name = initiative.INITIATIVE_ORDER[initiative.CURRENT_TURN_INDEX]['name']
                attack_name = first
                i = 1
            else:
                # No initiative context: assume first arg was meant as attacker anyway
                attacker_name = first
                if len(args) > 1 and not args[1].startswith('-'):
                    attack_name = args[1]
                    i = 2
                else:
                    i = 1
    else:
        # No first token: we already set attacker_name (from initiative) above when appropriate.
        # Start parsing flags at position 0 (there are none).
        i = 0

    # Parse flags
    while i < len(args):
        arg = args[i]
        if arg == "-t" and i + 1 < len(args):
            targets.append(args[i + 1])
            i += 2
        elif arg == "-b" and i + 1 < len(args):
            try:
                bonus_hit = int(args[i + 1])
            except ValueError:
                await ctx.send(f"❗ Invalid bonus to hit: `{args[i + 1]}`. Use an integer.")
                return
            i += 2
        elif arg == "-d" and i + 1 < len(args):
            try:
                bonus_dmg = int(args[i + 1])
            except ValueError:
                await ctx.send(f"❗ Invalid bonus to damage: `{args[i + 1]}`. Use an integer.")
                return
            i += 2
        elif arg == "-die" and i + 1 < len(args):
            custom_die = args[i + 1]
            i += 2
        elif arg == "-dc" and i + 1 < len(args):
            try:
                dc = int(args[i + 1])
            except Exception:
                await ctx.send(f"❗ Invalid DC: `{args[i + 1]}`. Use an integer.")
                return
            i += 2
        elif arg == "-burn":
            # optional count: -burn N
            if i + 1 < len(args) and re.match(r"^\d+$", args[i+1]):
                try:
                    burn = int(args[i+1])
                    i += 2
                except Exception:
                    burn = 1
                    i += 1
            else:
                burn = 1
                i += 1
        elif arg == "-fumble":
            force_fumble = True
            i += 1
        else:
            i += 1


    # Only check for targets after weapon selection

    if initiative.INITIATIVE_ORDER:
        if not attacker_name:
            attacker_name = initiative.INITIATIVE_ORDER[initiative.CURRENT_TURN_INDEX]['name']
        attacker_entry = next((c for c in initiative.INITIATIVE_ORDER if c.get('name','').lower() == attacker_name.lower() or c.get('abbr','').lower() == attacker_name.lower()), None)
    else:
        if not attacker_name:
            await ctx.send("❓ Please specify a character name using `-<name>` when not in initiative.")
            return
        attacker_entry = None

    # Load character or monster
    filename = os.path.join(SAVE_FOLDER, f"{attacker_name}.json")
    if os.path.exists(filename):
        with open(filename, "r") as f:
            attacker = json.load(f)
    elif attacker_entry:
        attacker = attacker_entry
    else:
        await ctx.send(f"❌ Character or monster `{attacker_name}` not found.")
        return


    # If attack_name is missing, list available weapons and do not require a target
    def parse_attack_str(s: str):
        """Parse a simple attack string like 'slam +1 melee (1d4)' into components."""
        res = {'name': s, 'bonus': 0, 'type': 'melee', 'damage': None}
        try:
            s_clean = s.strip()
            # name is first word cluster until a + or 'melee'/'ranged' or '('
            m = re.match(r"^([A-Za-z0-9 _-]+?)(?:\s+([+-]?\d+))?(?:\s*(melee|ranged))?(?:\s*\((\d+d\d+)\))?$", s_clean, re.I)
            if m:
                name = m.group(1).strip()
                bonus = m.group(2)
                typ = m.group(3)
                dmg = m.group(4)
                res['name'] = name
                if bonus:
                    try:
                        res['bonus'] = int(bonus)
                    except:
                        res['bonus'] = 0
                if typ:
                    res['type'] = typ.lower()
                if dmg:
                    res['damage'] = dmg.lower()
        except Exception:
            pass
        return res

    if not attack_name and not interactive_flow_used:
        # If we already ran the interactive flow and selected a weapon, skip the weapon-listing fallback
        if interactive_flow_used:
            pass
        else:
            weapon_names = []
            
        if 'weapons' in attacker and isinstance(attacker['weapons'], list):
            weapon_names.extend([w.get('name') for w in attacker['weapons'] if isinstance(w, dict) and w.get('name')])
        # If attacker is a saved PC and there's an initiative snapshot with attack info, check it too
        if attacker_entry and attacker_entry is not attacker:
            if 'weapons' in attacker_entry and isinstance(attacker_entry['weapons'], list):
                weapon_names.extend([w.get('name') for w in attacker_entry['weapons'] if isinstance(w, dict) and w.get('name')])
        if 'weapon' in attacker and isinstance(attacker['weapon'], str):
            weapon_names.append(attacker['weapon'])
        if 'inventory' in attacker:
            for item in attacker['inventory']:
                if isinstance(item, dict) and item.get('name') in WEAPON_TABLE:
                    weapon_names.append(item['name'])
        if 'attacks' in attacker and isinstance(attacker['attacks'], list):
            weapon_names.extend([atk.get('name') for atk in attacker['attacks'] if isinstance(atk, dict) and atk.get('name')])
        if attacker_entry and attacker_entry is not attacker:
            if 'attacks' in attacker_entry and isinstance(attacker_entry['attacks'], list):
                weapon_names.extend([atk.get('name') for atk in attacker_entry['attacks'] if isinstance(atk, dict) and atk.get('name')])
        # Support single-string 'atk' fields on NPC entries (from !iadd)
        if 'atk' in attacker and isinstance(attacker['atk'], str) and attacker['atk'].strip():
            p_atk = parse_attack_str(attacker['atk'])
            if p_atk.get('name'):
                weapon_names.append(p_atk['name'])
            else:
                weapon_names.append(attacker['atk'])
        # Also check the initiative entry (may contain parsed fields) if different
        if attacker_entry and attacker_entry is not attacker:
            if 'atk' in attacker_entry and isinstance(attacker_entry['atk'], str) and attacker_entry['atk'].strip():
                p_atk2 = parse_attack_str(attacker_entry['atk'])
                if p_atk2.get('name'):
                    if p_atk2['name'] not in weapon_names:
                        weapon_names.append(p_atk2['name'])
                else:
                    if attacker_entry['atk'] not in weapon_names:
                        weapon_names.append(attacker_entry['atk'])

        if weapon_names:
            # If this path was reached via the interactive flow, send a short confirmation only
            if interactive_flow_used:
                try:
                    await ctx.send(f"🧷 Selected weapon: `{attack_name}` for `{attacker_name}`.")
                except Exception:
                    pass
            else:
                # If not interactive, show available weapons for manual invocation
                origin_note = " (current initiative actor)" if initiative.INITIATIVE_ORDER and initiative.CURRENT_TURN_INDEX is not None and attacker_name == initiative.INITIATIVE_ORDER[initiative.CURRENT_TURN_INDEX].get('name') else ""
                await ctx.send(f"🧷 `{attacker_name}`{origin_note} has the following weapons or attacks: {', '.join(weapon_names)}. Use `!a <attacker> <weapon> -t <target>` to attack.")
        else:
            await ctx.send(f"⚠️ No weapons or attacks found for `{attacker_name}`.")
        return

    # No longer require targets. If none are specified, just roll the attack and show the result.

    # Get weapon from attacker
    weapon = None
    if 'weapons' in attacker:
        for w in attacker['weapons']:
            if attack_name.lower() == w['name'].lower():
                weapon = w
                break

    if not weapon and 'weapon' in attacker and isinstance(attacker['weapon'], str):
        if attack_name.lower() == attacker['weapon'].lower():
            base = WEAPON_TABLE.get(attacker['weapon'].lower())
            if base is None:
                await ctx.send(f"⚠️ `{attack_name}` is not a valid weapon.")
                return
            weapon = {
                "name": attacker['weapon'],
                "damage": base["damage"],
                "type": base["type"]
            }

    if not weapon and 'inventory' in attacker:
        for item in attacker['inventory']:
            if isinstance(item, dict) and attack_name.lower() == item.get('name', '').lower():
                if item['name'].lower() in WEAPON_TABLE:
                    base = WEAPON_TABLE[item['name'].lower()]
                    weapon = {
                        "name": item['name'],
                        "damage": base["damage"],
                        "type": base["type"]
                    }
                    break

    if not weapon and 'attacks' in attacker:
        for atk in attacker['attacks']:
            if attack_name.lower() in atk['name'].lower():
                weapon = atk
                break

    # If still no weapon, support NPC 'atk' string (from !iadd). Parse and use it.
    if weapon is None and 'atk' in attacker and isinstance(attacker['atk'], str):
        p = parse_attack_str(attacker['atk'])
        if attack_name and attack_name.lower() in p['name'].lower() or (not attack_name and p['name']):
            # Build a weapon-like dict
            weapon = {
                'name': p['name'],
                'damage': p['damage'] or None,
                'type': p['type'] or 'melee'
            }
            # incorporate parsed attack bonus into attacker's attack_bonus so mod calc picks it up
            try:
                existing = int(str(attacker.get('attack_bonus', 0)).replace('+',''))
            except Exception:
                existing = 0
            try:
                attacker['attack_bonus'] = existing + int(p.get('bonus', 0))
            except Exception:
                attacker['attack_bonus'] = existing

    if weapon is None:
        await ctx.send(f"⚠️ `{attacker_name}` does not have a weapon named `{attack_name}` in inventory or equipped.")
        return

    # After attack resolution, update HP in JSON if character is a PC
    for tgt_name in targets:
        target_filename = os.path.join(SAVE_FOLDER, f"{tgt_name}.json")
        if os.path.exists(target_filename):
            try:
                with open(target_filename, "r") as tf:
                    target_data = json.load(tf)
                # If an initiative entry carried an HP snapshot, sync it back into the saved file.
                # Guard against missing keys to avoid KeyError.
                for entry in initiative.INITIATIVE_ORDER:
                    if entry.get('name', '').lower() == tgt_name.lower():
                        if 'hp' in entry:
                            # Ensure target_data has an 'hp' dict
                            if not isinstance(target_data.get('hp'), dict):
                                target_data['hp'] = {"max": entry['hp'], "current": entry['hp']}
                            else:
                                target_data['hp']['current'] = entry['hp']
                        break
                with open(target_filename, "w") as tf:
                    json.dump(target_data, tf, indent=4)
            except Exception as e:
                print(f"⚠️ Failed to update HP for {tgt_name}: {e}")

    # --- Determine modifiers (separate to-hit vs damage) ---
    melee = 'melee' in weapon['type'].lower()
    ranged = 'ranged' in weapon['type'].lower()
    ability_attack_mod = 0
    ability_damage_mod = 0
    if 'abilities' in attacker:
        try:
            if melee:
                str_mod = attacker['abilities'].get('STR', {}).get('mod', 0)
                ability_attack_mod += str_mod
                ability_damage_mod += str_mod
            elif ranged:
                agi_mod = attacker['abilities'].get('AGI', {}).get('mod', 0)
                ability_attack_mod += agi_mod
                # Ranged attacks generally do NOT add AGI to damage (leave damage_mod 0)
        except Exception:
            pass
    # Attack bonus (class/base attack) applies ONLY to hit
    attack_bonus_val = 0
    if 'attack_bonus' in attacker:
        try:
            attack_bonus_val = int(str(attacker['attack_bonus']).replace('+',''))
        except Exception:
            attack_bonus_val = 0
    elif 'attack' in attacker:
        # fallback legacy field
        try:
            attack_bonus_val = int(attacker.get('attack', 0))
        except Exception:
            attack_bonus_val = 0

    # Consolidated to-hit modifier (before optional deed/burn/bonus_hit)
    to_hit_base_mod = ability_attack_mod + attack_bonus_val


    # Determine which action die to use if not specified
    if not custom_die:
        # If interactive flow selected one, use it
        if interactive_flow_used and selected_action_die:
            custom_die = selected_action_die
        else:
            action_die = attacker.get("action_die", "1d20")
            # Support both + and , as delimiters
            dice_options = [d.strip() for d in action_die.replace(",", "+").split("+")]
            if len(dice_options) == 1:
                custom_die = dice_options[0]
            else:
                # Fallback to first option to avoid extra typing UI
                custom_die = dice_options[0]

    # Roll attack
    try:
        num, die = map(int, custom_die.lower().replace("d", " ").split())
    except:
        await ctx.send("❌ Invalid die format. Use like `1d20`.")
        return

    rolls = [random.randint(1, die) for _ in range(num)]
    raw_total = sum(rolls)
    roll = raw_total + to_hit_base_mod + bonus_hit

    # --- Deed die for Warriors and Dwarves: roll and add to attack and damage ---
    deed_roll_val = 0
    deed_text = ""
    try:
        char_class = attacker.get('class', '').lower()
        race = attacker.get('race', '').lower()
    except Exception:
        char_class = ''
        race = ''
    # Apply deed if PC's class is warrior or dwarf (dwarf may be a class in future)
    deed_declared = None
    if (char_class == 'warrior' or char_class == 'dwarf') and attacker.get('deed_die'):
        # Ask the player to declare their deed before rolling
        try:
            await ctx.send(f"❗ What will be your Mighty Deed of Arms, {attacker_name}? ")
            def deed_check(m):
                return m.author == ctx.author and m.channel == ctx.channel
            msg = await bot.wait_for("message", check=deed_check, timeout=60)
            text = msg.content.strip()
            if text and text != '-':
                deed_declared = text
                await ctx.send(f"📝 Deed recorded: `{deed_declared}`")
            else:
                await ctx.send("(No deed declared)")
        except Exception:
            # Timeout or other error — proceed without a declared deed
            deed_declared = None

        # deed_die may be like '1d6', 'd3', 'd10+2', or '1d8+1'
        raw_deed = str(attacker.get('deed_die', '')).lower()
        # split off additive modifiers
        parts = [p.strip() for p in raw_deed.split('+') if p.strip()]
        base = parts[0] if parts else ''
        add_modifier = 0
        if len(parts) > 1:
            try:
                add_modifier = sum(int(p) for p in parts[1:])
            except Exception:
                add_modifier = 0

        # normalize base like 'd6' -> '1d6' or '2d6'
        m = re.match(r"^(?:(\d+)?d(\d+))$", base)
        if m:
            dnum = int(m.group(1)) if m.group(1) else 1
            dsize = int(m.group(2))
        else:
            # fallback
            dnum, dsize = 1, 6

        deed_rolls = [random.randint(1, dsize) for _ in range(dnum)]
        raw_deed_sum = sum(deed_rolls)
        deed_roll_val = raw_deed_sum + add_modifier
        roll += deed_roll_val
        # show normalized deed expression and breakdown
        norm_expr = f"{dnum}d{dsize}" + (f"+{add_modifier}" if add_modifier else "")
        deed_text = f"\n🎴 Deed die: `{norm_expr}` → rolls: {deed_rolls}" + (f" + {add_modifier} = {deed_roll_val}" if add_modifier else f" = {deed_roll_val}")
        try:
            await ctx.send(deed_text)
        except Exception:
            pass

    # Check for fumble on natural 1
    if 1 in rolls or force_fumble:

        fumble_die = attacker.get("fumble_die", "1d4")  # Default to 1d4 if not set
        try:
            fumble_num, fumble_size = map(int, fumble_die.lower().replace("d", " ").split())
        except:
            fumble_num, fumble_size = 1, 4  # Fallback

        fumble_rolls = [random.randint(1, fumble_size) for _ in range(fumble_num)]
        raw_fumble = sum(fumble_rolls)
        luck_mod = attacker.get("abilities", {}).get("LCK", {}).get("mod", 0)
        fumble_result = max(0, raw_fumble - luck_mod)

        fumble_table = [
            "You miss wildly but miraculously cause no other damage.",
            "Your incompetent blow makes you the laughingstock of the party but otherwise causes no damage.",
            "You trip but may recover with a DC 10 Ref save; otherwise, you must spend the next round prone.",
            "Your weapon comes loose in your hand. You quickly grab it, but your grip is disrupted. You take a -2 penalty on your next attack roll.",
            "Your weapon is damaged: a bowstring breaks, a sword hilt falls off, or a crossbow firing mechanism jams. The weapon can be repaired with 10 minutes of work but is useless for now.",
            "You trip and fall, wasting this action. You are prone and must use an action to stand next round.",
            "Your weapon becomes entangled in your armor. You must spend your next round untangling them. In addition, your armor bonus is reduced by 1 until you spend 10 minutes refitting the tangled buckles and straps.",
            "You drop your weapon. You must retrieve it or draw a new one on your next action.",
            "You accidentally smash your weapon against a solid, unyielding object. Mundane weapons are ruined; magical weapons are not affected.",
            "You stumble and leave yourself wide open to attack. The next enemy that attacks you receives a +2 bonus on its attack roll.",
            "Your armor seizes up. You cannot move or attack for 1d3 rounds. (Unarmored characters unaffected.)",
            "You are off balance. Take -4 on your next attack roll.",
            "You accidentally swing at a random ally. Make an attack roll against that ally using the same attack die.",
            "You trip badly. Take 1d3 damage, are prone, and must use your next round to stand.",
            "You fall upside down and must fight from a prone position next round.",
            "You somehow wound yourself, taking normal damage.",
            "You hit yourself for normal damage +1, fall prone, and must make a DC 16 Agility check to stand."
        ]

        fumble_text = fumble_table[min(fumble_result, len(fumble_table) - 1)]

        await ctx.send(
            f"💀 **FUMBLE!** `{attacker_name}` rolled a natural 1!\n"
            f"🎲 Fumble roll: `{fumble_die}` = {raw_fumble} - LCK({luck_mod}) = **{fumble_result}**\n"
            f"📉 **Result**: {fumble_text}"
        )
        return  # End the attack here on fumble

    # Burning luck behavior: subtract N luck, add N to attack roll (flat), prevent negative luck
    burn_note = ""
    if burn and burn > 0:
        cur = get_luck_current(attacker)
        if burn > cur:
            await ctx.send(f"❌ `{attacker_name}` does not have {burn} luck to burn (current: {cur}).")
            return
        roll += int(burn)
        # persist
        try:
            consume_luck_and_save(attacker, burn, filename if os.path.exists(filename) else None)
            burn_note = f" (burned {burn} luck → +{burn}; remaining luck: {attacker.get('luck', {}).get('current', 0)})"
            if attacker.get('luck', {}).get('current', 0) == 0:
                await ctx.send(f"Uh-oh! `{attacker_name}` is out of luck. Their fate is in the Judge's hand.")
        except Exception:
            pass

    # Get damage die
    dmg_die = weapon.get("damage")
    if not dmg_die:
        await ctx.send(f"⚠️ No damage die found for `{attack_name}`.")
        return

    try:
        dmg_expr = dmg_die.lower().replace("d", " ").split('+')[0].split()
        if len(dmg_expr) == 2:
            dmg_num, dmg_size = map(int, dmg_expr)
        else:
            dmg_num, dmg_size = 1, int(dmg_expr[0])
    except:
        await ctx.send(f"⚠️ Invalid damage format `{dmg_die}`.")
        return

    dmg_rolls = [random.randint(1, dmg_size) for _ in range(dmg_num)]
    total_dmg = sum(dmg_rolls) + (ability_damage_mod if melee else 0) + bonus_dmg

    hit_results = []
    hit = False
    UPDATED_TARGET_HPS = {}
    for tgt_name in targets:
        from_file_target = False
    target_entry = next((c for c in initiative.INITIATIVE_ORDER if c.get('name','').lower() == tgt_name.lower() or c.get('abbr','').lower() == tgt_name.lower()), None)
        if not target_entry:
            # Attempt to load a saved character/monster as the target
            tfpath = os.path.join(SAVE_FOLDER, f"{tgt_name}.json")
            if os.path.exists(tfpath):
                try:
                    with open(tfpath, 'r', encoding='utf-8') as tf:
                        tdata = json.load(tf)
                    target_entry = {
                        'name': tdata.get('name', tgt_name),
                        'abbr': tdata.get('abbr', (tdata.get('name') or tgt_name)[:3] if (tdata.get('name') or tgt_name) else tgt_name[:3]),
                        'ac': tdata.get('ac', 10),
                        'owner': tdata.get('owner'),
                        'hd': tdata.get('hd')
                    }
                    hp_field = tdata.get('hp')
                    if isinstance(hp_field, dict):
                        cur = hp_field.get('current', hp_field.get('max', 0))
                        target_entry['hp'] = int(cur) if cur is not None else None
                    elif isinstance(hp_field, int):
                        target_entry['hp'] = int(hp_field)
                    else:
                        target_entry['hp'] = None
                    from_file_target = True
                except Exception:
                    target_entry = None
            if not target_entry:
                hit_results.append(f"❌ Target `{tgt_name}` not found.")
                continue

        ac = target_entry.get('ac', 10)
        hit = roll >= ac

        # Ensure target has hp set; if not, try to load from saved file or roll HD if available
        if target_entry.get('hp') is None:
            t_hp = None
            try:
                tfpath = os.path.join(SAVE_FOLDER, f"{tgt_name}.json")
                if os.path.exists(tfpath):
                    with open(tfpath, 'r') as tf:
                        tdata = json.load(tf)
                    hp_field = tdata.get('hp')
                    if isinstance(hp_field, dict):
                        t_hp = int(hp_field.get('current', hp_field.get('max', 0)))
                    elif isinstance(hp_field, int):
                        t_hp = int(hp_field)
            except Exception:
                t_hp = None
            if t_hp is None and target_entry.get('hd'):
                try:
                    mhd = re.search(r"(\d+)d(\d+)", str(target_entry.get('hd')))
                    if mhd:
                        hd_n = int(mhd.group(1))
                        hd_s = int(mhd.group(2))
                        t_hp = sum(random.randint(1, hd_s) for _ in range(hd_n))
                except Exception:
                    t_hp = None
            if t_hp is None:
                t_hp = 0
            target_entry['hp'] = int(t_hp)

        # Build readable roll strings to avoid nested f-strings
        rolls_str = ' + '.join(map(str, rolls))
        mod_label = (f"STR({ability_attack_mod})" if melee else (f"AGI({ability_attack_mod})" if ranged else str(ability_attack_mod)))
        # Show subtotal without deed, then append deed if present
        to_hit_no_deed = raw_total + to_hit_base_mod + bonus_hit
        attack_roll_repr = f"({custom_die}) {rolls_str} + {mod_label} + bonus({bonus_hit}) = {to_hit_no_deed}"
        full_attack_repr = attack_roll_repr + (f" + deed({deed_roll_val}) = {roll}" if deed_roll_val else f" = {to_hit_no_deed}")

        dmg_rolls_str = ' + '.join(map(str, dmg_rolls))
        dmg_extra = (f" + STR({ability_damage_mod})" if melee and ability_damage_mod else "")
        dmg_bonus = (f" + bonus({bonus_dmg})" if bonus_dmg else "")
        damage_repr = f"({dmg_die}) {dmg_rolls_str}{dmg_extra}{dmg_bonus} = {total_dmg}"

        if hit:
            # If a deed die was rolled, add its value to damage as well
            applied_dmg = total_dmg + (deed_roll_val if deed_roll_val else 0)
            target_entry['hp'] = max(0, int(target_entry['hp']) - applied_dmg)
            # Record for persistence if target came from file (not in initiative)
            if from_file_target:
                UPDATED_TARGET_HPS[tgt_name] = target_entry['hp']
            # If the deed roll qualifies (>=3) and the player declared a deed, show it as succeeded
            deed_clause = deed_text if deed_text else ""
            deed_success_text = ""
            if deed_declared and deed_roll_val and deed_roll_val >= 3:
                deed_success_text = f"\n✅ Mighty Deed succeeded: `{deed_declared}`"

            hit_results.append(
                f"🎯 `{attacker_name}` rolls `{full_attack_repr}` vs AC {ac} and **hits** `{tgt_name}` with `{attack_name}`!\n"
                f"💥 Damage roll: `{damage_repr}`" + (f" + deed({deed_roll_val}) = {applied_dmg}" if deed_roll_val else "") + "\n"
                f"🩸 `{tgt_name}` took **{applied_dmg}** damage." + deed_clause + deed_success_text
            )
            # DM the owner (person who added the monster) with its remaining HP
            owner_id = target_entry.get('owner')
            if owner_id:
                try:
                    uid = int(owner_id)
                    user = await bot.fetch_user(uid)
                    await user.send(f"🩸 `{tgt_name}` now has **{target_entry['hp']} HP** remaining in initiative.")
                except Exception:
                    # Ignore DM failures (user DMs disabled, fetch failed, etc.)
                    pass
            # If the target dropped to 0 HP, announce and remove it from initiative
            if target_entry['hp'] <= 0:
                hit_results.append(f"☠️ `{tgt_name}` has been defeated and is removed from initiative.")
                try:
                    idx = initiative.INITIATIVE_ORDER.index(target_entry)
                    initiative.INITIATIVE_ORDER.remove(target_entry)
                    if initiative.CURRENT_TURN_INDEX is not None:
                        if idx < initiative.CURRENT_TURN_INDEX:
                            initiative.CURRENT_TURN_INDEX -= 1
                        elif idx == initiative.CURRENT_TURN_INDEX:
                            initiative.CURRENT_TURN_INDEX = max(0, initiative.CURRENT_TURN_INDEX - 1)
                except ValueError:
                    pass
        else:
            note = ""
            if dc is not None:
                try:
                    note = f" vs DC {dc} → {'Success' if roll >= int(dc) else 'Failure'}"
                except Exception:
                    note = ""
            if burn_note and not note:
                note = burn_note
            elif burn_note and note:
                note = note + burn_note
            miss_extra = deed_text if deed_text else ""
            hit_results.append(
                f"❌ `{attacker_name}` rolls `{full_attack_repr}` vs AC {ac} and **misses** `{tgt_name}`.{note}" + miss_extra
            )

    # Persist updated HP for any non-initiative targets we hit
    if UPDATED_TARGET_HPS:
        for name, hp in UPDATED_TARGET_HPS.items():
            try:
                tfpath = os.path.join(SAVE_FOLDER, f"{name}.json")
                if os.path.exists(tfpath):
                    with open(tfpath, 'r', encoding='utf-8') as tf:
                        tdata = json.load(tf)
                    # Ensure hp field structure
                    if not isinstance(tdata.get('hp'), dict):
                        tdata['hp'] = {"max": hp, "current": hp}
                    else:
                        tdata['hp']['current'] = hp
                    with open(tfpath, 'w', encoding='utf-8') as tf:
                        json.dump(tdata, tf, indent=4)
            except Exception as e:
                print(f"⚠️ Failed to persist HP for {name}: {e}")

    if hit_results:
        await ctx.send("\n".join(hit_results))
    else:
    rolls_str = ' + '.join(map(str, rolls))
    mod_label = (f"STR({ability_attack_mod})" if melee else (f"AGI({ability_attack_mod})" if ranged else str(ability_attack_mod)))
    # When no explicit targets, show subtotal, deed breakdown, and damage preview
    to_hit_no_deed = raw_total + to_hit_base_mod + bonus_hit
        attack_roll_repr = f"({custom_die}) {rolls_str} + {mod_label} + bonus({bonus_hit}) = {to_hit_no_deed}"
        full_attack_repr = attack_roll_repr + (f" + deed({deed_roll_val}) = {roll}" if deed_roll_val else f" = {to_hit_no_deed}")
        # Damage preview
        dmg_preview = ""
        try:
            dmg_rolls_str = ' + '.join(map(str, dmg_rolls))
            dmg_extra = (f" + STR({ability_damage_mod})" if melee and ability_damage_mod else "")
            dmg_bonus = (f" + bonus({bonus_dmg})" if bonus_dmg else "")
            dmg_preview = f"\n💥 Damage roll: `({dmg_die}) {dmg_rolls_str}{dmg_extra}{dmg_bonus} = {total_dmg}`"
            if deed_roll_val:
                dmg_preview += f" + deed({deed_roll_val}) = {total_dmg + deed_roll_val}"
        except Exception:
            dmg_preview = ""

        await ctx.send(f"🎯 `{attacker_name}` rolled `{full_attack_repr}` with `{attack_name}`." + dmg_preview + (deed_text if deed_text else ""))

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
        if effect in ["Harsh winter", "Pack hunter"]:
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
    total = roll + mod + level
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
    await ctx.send(f"🙏 Lay on Hands successful! {target_name} is healed for {total_heal} HP (rolled {roll} + {mod} + {level} = {total}, {dice_to_heal}d{die_type}: {heals}).")
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

    # Disapproval trigger based on raw roll before applying failure penalty
    disapproval_triggered = False
    if roll <= char.get("disapproval_range", 1):
        disapproval_triggered = True
        await _handle_disapproval_roll(char, filename, ctx, cause="Turn Unholy")

    # Turning DC for HD: simple rule example — DC = 10 + target HD
    dc = 10 + int(target_hd)
    await ctx.send(f"🔔 {char['name']} attempts to turn unholy (Roll: {roll} + L{level} + PER{mod} = {total} vs DC {dc})")

    # Disapproval overrides success: if disapproval triggered, treat as failure
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
                    "neutral": ["Beggar", "Cutpurse", "Burglar", "Robber", "Swindler"],
                    "chaotic": ["Thug", "Murderer", "Cutthroat", "Executioner", "Assassin"]
                }
                ,
                "warrior": {
                    "lawful": ["Squire","Champion","Knight","Cavalier","Paladin",],
                    "neutral": ["Wildling","Barbarian","Berserker","Headman/Headwoman","Chieftain"],
                    "chaotic": ["Bandit","Brigand","Marauder","Ravager","Reaver"]
                }
            }
            try:
                cls_titles = titles.get(cls.lower(), {}) if isinstance(cls, str) else {}
                alignment_list = cls_titles.get(alignment, []) if isinstance(cls_titles, dict) else []
                if isinstance(alignment_list, list) and 1 <= level <= len(alignment_list):
                    return alignment_list[level-1]
            except Exception:
                pass
            return None

        title = get_title(cls, level, alignment)

        # Ability Scores
        abilities_dict = char.get("abilities", {})
        migrated = False
        mods_changed = False
        # Migrate legacy 'score' -> 'max'/'current' and ensure fields exist
        for k, v in list(abilities_dict.items()):
            # If old format (has 'score'), migrate to new shape
            if isinstance(v, dict) and "score" in v:
                score = int(v.get("score", 0))
                abilities_dict[k]["max"] = score
                abilities_dict[k]["current"] = score
                abilities_dict[k].pop("score", None)
                abilities_dict[k]["mod"] = int(get_modifier(score))
                migrated = True
            else:
                # Ensure both max and current exist
                if isinstance(v, dict):
                    if "max" not in v and "current" in v:
                        abilities_dict[k]["max"] = int(v.get("current", 0))
                    if "current" not in v and "max" in v:
                        abilities_dict[k]["current"] = int(v.get("max", 0))
                    # Compute modifier from current but only overwrite stored mod when it differs
                    try:
                        cur = int(abilities_dict[k].get("current", 0))
                    except:
                        cur = 0
                    computed_mod = int(get_modifier(cur))
                    old_mod = abilities_dict[k].get("mod")
                    # Normalize old_mod to int if possible
                    try:
                        old_mod_int = int(old_mod) if old_mod is not None else None
                    except Exception:
                        old_mod_int = None
                    if old_mod_int is None or old_mod_int != int(computed_mod):
                        abilities_dict[k]["mod"] = int(computed_mod)
                        mods_changed = True
                    else:
                        # keep existing int value
                        abilities_dict[k]["mod"] = int(old_mod_int)

        # If migration occurred or mods were adjusted, persist back to the character file
        if migrated or mods_changed:
            try:
                with open(filename, "w") as f:
                    json.dump(char, f, indent=4)
            except Exception:
                # Non-fatal: continue showing the sheet even if save fails
                pass

        # If a separate luck object exists, prefer its values for displayed LCK
        try:
            luck_obj = char.get('luck')
            if luck_obj is not None:
                # parse current and max from luck field (supports dict or strings like '10/10')
                if isinstance(luck_obj, dict):
                    lcur = _parse_luck_value(luck_obj.get('current', luck_obj.get('max', 0)))
                    lmax = _parse_luck_value(luck_obj.get('max', lcur))
                else:
                    lcur = _parse_luck_value(luck_obj)
                    lmax = lcur
                abilities_dict.setdefault('LCK', {})
                abilities_dict['LCK']['current'] = int(lcur)
                abilities_dict['LCK']['max'] = int(lmax)
                # recompute modifier from current
                try:
                    abilities_dict['LCK']['mod'] = int(get_modifier(int(abilities_dict['LCK'].get('current', 0))))
                except Exception:
                    abilities_dict['LCK']['mod'] = int(abilities_dict['LCK'].get('mod', 0))
        except Exception:
            pass

        abilities = "\n".join([
            f"> **{k}**: {v.get('current', 0)} / {v.get('max', 0)} ({v.get('mod', 0):+})"
            for k, v in abilities_dict.items()
        ])

        # Saves
        saves = "\n".join([
            f"> 🛡️ **{s.capitalize()}**: {val}"
            for s, val in char["saves"].items()
        ])

        # Inventory
        inventory_lines = []
        for item in char["inventory"]:
            if isinstance(item, dict) and "name" in item:
                name = item["name"]
                dmg = item.get("damage", "?")
                wtype = item.get("type", "unknown")
                inventory_lines.append(f"> 🎒 {name} ({dmg}, {wtype})")
            else:
                inventory_lines.append(f"> 🎒 {item}")
        inventory = "\n".join(inventory_lines)

        # Languages
        languages = ", ".join(char["languages"]) if char.get("languages") else "None"

        # Base sheet
        display_name = f"{title} {char['name']}" if title else char['name']
        sheet = (
            f"📜 **{display_name}**\n"

            f"> 🧭 Alignment: {char['alignment']}\n"
            f"> 🛠️ Occupation: {char['occupation']}\n"
        )

        if cls != "Lv0":
            # Show class with current level for easy visibility (keep title like 'Rogue' intact)
            sheet += f"> 🧪 Class: {cls} {level}"
            if title:
                sheet += f" ({title})"
            sheet += "\n\n"
        else:
            sheet += "\n"

        base_attack = char.get('attack', 0)
        att_str = f"+{base_attack}" if base_attack >= 0 else str(base_attack)
        sheet += (
            f"🛡️ **Defense**\n"
            f"> ❤️ **HP:** {char['hp']['current']} / {char['hp']['max']}\n"
            f"> 🛡️ **AC:** {char['ac']}\n"
            f"> 🗡️ **Att:** {att_str}\n\n"
            f"🎯 **Saving Throws**\n{saves}\n\n"
            f"📦 **Inventory**\n{inventory}\n"
            f"> ⚔️ **Weapon:** {char['weapon']}\n"
            f"> 🪙 **Copper:** {char['cp']}\n\n"
            f"🧬 **Abilities**\n{abilities}\n\n"
            f"🔮 **Birth Augur**\n"
            f"> *{char['birth_augur']['sign']}* — {char['birth_augur']['effect']} (Mod: {char.get('max_luck_mod', 0):+})\n\n"
            f"🗣️ **Languages**: {languages}\n\n"
        )

        # Cleric Extras
        if cls.lower() == "cleric":
            spells_dict = char.get("spells", {}).get("level_1", [])
            spell_names = ", ".join([s["name"] for s in spells_dict]) if spells_dict else "None"
            weapon_training = ", ".join(char.get("weapon_proficiencies", [])) or "None"
            unholy_list = ", ".join(char.get("unholy_targets", [])) or "None"

            sheet += (
                f"🙏 **__Cleric Abilities__**\n"
                f"> 🛐 **Deity:** {char.get('god', 'Unknown')}\n"
                f"> 🗡️ **Weapon Training:** {weapon_training}\n"
                f"> 📖 **Spells:** {spell_names}\n"
                f"> ⛔ **Disapproval Range:** {char.get('disapproval_range', 1)}\n"
                f"> ☠️ **Turn Unholy:** {unholy_list}\n"

                f"> 💥 **Crit Die:** {char.get('crit_die', '1d8')}\n"
                f"> 🎲 **Crit Table:** {char.get('crit_table', 'III')}\n"
                f"> ⚔️ **Attack Bonus:** {char.get('attack_bonus', '+0')}\n"
                f"> 🎭 **Action Die:** {char.get('action_die', '1d20')}\n\n"
            )


        # Thief Extras
        elif cls.lower() == "thief":
            weapon_training = ", ".join(char.get("weapon_proficiencies", [])) or "None"
            skills = char.get("skills", {})
            skills_text = "\n".join([f"> 🔧 {k}: {v}" for k, v in skills.items()]) if skills else "> 🔧 None"

            sheet += (
                f"🕵️ **__Thief Abilities__**\n"
                f"> 🗡️ **Weapon Training:** {weapon_training}\n"
                f"> 🕯️ **Thieves' Cant:** Yes\n"
                f"**{skills_text}**\n"
                f"> 🎲 **Luck Die:** {char.get('luck_die', '1d3')}\n"
                f"> 💥 **Crit Die:** {char.get('crit_die', '1d10')}\n"
                f"> 🎲 **Crit Table:** {char.get('crit_table', 'II')}\n"
                f"> ⚔️ **Attack Bonus:** {char.get('attack_bonus', '+0')}\n"
                f"> 🎭 **Action Die:** {char.get('action_die', '1d20')}\n\n"
            )



        await ctx.send(sheet)

    except Exception as e:
        await ctx.send(f"⚠️ Error displaying sheet: `{e}`")
        print(f"Sheet Error: {e}")


@bot.command(name="in")
async def modify_inventory(ctx, char_name: str, action: str, *, items: str):
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        with open(filename, "r") as f:
            character = json.load(f)

        # Parse items
        item_list = [item.strip() for item in items.split(",")]
        current_inventory = character.get("inventory", [])

        if action == "+":
            added = []
            for item in item_list:
                item_lower = item.lower()

                # Check if already in inventory
                already_in = any(
                    (isinstance(i, str) and i.lower() == item_lower) or
                    (isinstance(i, dict) and i.get("name", "").lower() == item_lower)
                    for i in current_inventory
                )
                if already_in:
                    continue

               # Load custom weapons if available
                custom_weapons_path = os.path.join(SAVE_FOLDER, "custom_weapons.json")
                if os.path.exists(custom_weapons_path):
                    with open(custom_weapons_path, "r") as cw:
                        custom_weapons = json.load(cw)
                else:
                    custom_weapons = {}

                all_weapons = {**WEAPON_TABLE, **custom_weapons}

                # Add weapon as dict if it's a known weapon (from either list)
                if item_lower in all_weapons:
                    weapon_info = all_weapons[item_lower]
                    current_inventory.append({
                        "name": item_lower,
                        "damage": weapon_info["damage"],
                        "type": weapon_info["type"]
                    })
                else:
                    current_inventory.append(item)

                added.append(item)


            character["inventory"] = current_inventory
            if added:
                await ctx.send(f"🎒 Added to `{char_name}`'s inventory: {', '.join(added)}")
            else:
                await ctx.send("⚠️ No new items were added (duplicates ignored).")

        elif action == "-":
            removed = []
            for item in item_list:
                lower = item.lower()
                for i in current_inventory[:]:
                    if (isinstance(i, dict) and i.get("name", "").lower() == lower) or (i == item):
                        current_inventory.remove(i)
                        removed.append(item)
                        break

            character["inventory"] = current_inventory

            if removed:
                await ctx.send(f"🗑️ Removed from `{char_name}`'s inventory: {', '.join(removed)}")
            else:
                await ctx.send("⚠️ None of those items were found in inventory.")

        else:
            await ctx.send("❌ Invalid action. Use `+` to add or `-` to remove items.")
            return

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

    except Exception as e:
        await ctx.send(f"⚠️ Error modifying inventory: `{e}`")
        print(f"Inventory Error: {e}")


@bot.command(name="equip")
async def equip_weapon(ctx, char_name: str, *, weapon_name: str):
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    weapon_name = weapon_name.lower()
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    try:
        with open(filename, "r") as f:
            character = json.load(f)

        inventory = character.get("inventory", [])
        found_index = None
        new_weapon = None

        # Search inventory for weapon
        for i, item in enumerate(inventory):
            item_name = item if isinstance(item, str) else item.get("name", "").lower()
            if item_name == weapon_name:
                found_index = i
                if isinstance(item, str) and weapon_name in WEAPON_TABLE:
                    weapon_info = WEAPON_TABLE[weapon_name]
                    inventory[i] = {
                        "name": weapon_name,
                        "damage": weapon_info["damage"],
                        "type": weapon_info["type"]
                    }
                new_weapon = inventory[i]
                break

        if new_weapon is None:
            await ctx.send(f"⚠️ `{char_name}` does not have `{weapon_name}` in their inventory.")
            return

        # Remove new weapon from inventory
        inventory.pop(found_index)

        # Backup current weapon into inventory
        current_weapon = character.get("weapon")
        if current_weapon:
            if isinstance(current_weapon, str) and current_weapon.lower() in WEAPON_TABLE:
                w = current_weapon.lower()
                character["inventory"].append({
                    "name": w,
                    "damage": WEAPON_TABLE[w]["damage"],
                    "type": WEAPON_TABLE[w]["type"]
                })
            elif isinstance(current_weapon, dict):
                character["inventory"].append(current_weapon)

        # Equip new weapon
        character["weapon"] = new_weapon
        character["inventory"] = inventory

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        await ctx.send(f"🗡️ `{char_name}` has equipped `{weapon_name}` and stored the previous weapon in inventory.")

    except Exception as e:
        await ctx.send(f"⚠️ Error equipping weapon: `{e}`")
        print(f"[EQUIP ERROR] {e}")





#------Adjust HP----------
@bot.command(name="hp")
async def adjust_hp(ctx, char_name: str, amount: str):
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        # Parse +N or -N
        if not (amount.startswith("+") or amount.startswith("-")):
            await ctx.send("❌ Use + or - to heal or damage (e.g., `+2`, `-3`).")
            return

        delta = int(amount)
        with open(filename, "r") as f:
            character = json.load(f)

        current = character["hp"]["current"]
        max_hp = character["hp"]["max"]
        new_hp = max(0, min(current + delta, max_hp))

        character["hp"]["current"] = new_hp

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        symbol = "❤️" if delta > 0 else "💀"
        verb = "healed" if delta > 0 else "took damage"
        await ctx.send(f"{symbol} `{char_name}` {verb} ({delta:+}). HP is now `{new_hp}/{max_hp}`.")

    except Exception as e:
        await ctx.send(f"⚠️ Error updating HP: `{e}`")
        print(f"HP Error: {e}")


#------Adjust Ability Current (temporary / wounds)----------
@bot.command(name="ab")
async def adjust_ability_current(ctx, stat: str, char_name: str, amount: str = None):
    """Modify ability current value (e.g., temporary damage/healing)."""
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        if not amount:
            await ctx.send("❌ Missing modifier. Usage: `!ab <STAT> <Name> +/-N` — Example: `!ab STR \"Char1\" -2`")
            return

        if not (amount.startswith("+") or amount.startswith("-")):
            await ctx.send("❌ Use + or - to modify ability (e.g., `+2`, `-1`).")
            return

        delta = int(amount)
        with open(filename, "r") as f:
            character = json.load(f)

        abilities = character.get("abilities", {})
        # find stat key case-insensitively
        stat_key = None
        for k in abilities.keys():
            if k.lower() == stat.lower():
                stat_key = k
                break
        if not stat_key:
            for k in abilities.keys():
                if k.lower().startswith(stat.lower()):
                    stat_key = k
                    break

        if not stat_key:
            await ctx.send(f"❌ Ability `{stat}` not found on `{char_name}`. Available: {', '.join(abilities.keys())}")
            return

        cur = int(abilities[stat_key].get("current", abilities[stat_key].get("max", 0)))
        mx = int(abilities[stat_key].get("max", cur))
        new_cur = max(0, min(mx, cur + delta))
        new_mod = int(get_modifier(new_cur))

        character["abilities"][stat_key]["current"] = int(new_cur)
        character["abilities"][stat_key]["mod"] = int(new_mod)

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        symbol = "🧬" if delta > 0 else "🔻"
        verb = "increased" if delta > 0 else "decreased"
        await ctx.send(f"{symbol} `{char_name}` {stat_key} {verb} ({delta:+}) → {new_cur} / {mx} ({new_mod:+}).")

    except Exception as e:
        await ctx.send(f"⚠️ Error updating ability: `{e}`")
        print(f"Ability Error: {e}")


#------Adjust Ability Max (permanent / base)----------
@bot.command(name="abmax")
async def adjust_ability_max(ctx, stat: str, char_name: str, amount: str = None):
    """Modify ability max/base value."""
    try:
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
        if not os.path.exists(filename):
            await ctx.send(f"❌ Character `{char_name}` not found.")
            return

        if not amount:
            await ctx.send("❌ Missing modifier. Usage: `!abmax <STAT> <Name> +/-N` — Example: `!abmax STR \"Char1\" +1`")
            return

        if not (amount.startswith("+") or amount.startswith("-")):
            await ctx.send("❌ Use + or - to modify ability (e.g., `+2`, `-1`).")
            return

        delta = int(amount)
        with open(filename, "r") as f:
            character = json.load(f)

        abilities = character.get("abilities", {})
        # find stat key case-insensitively
        stat_key = None
        for k in abilities.keys():
            if k.lower() == stat.lower():
                stat_key = k
                break
        if not stat_key:
            for k in abilities.keys():
                if k.lower().startswith(stat.lower()):
                    stat_key = k
                    break

        if not stat_key:
            await ctx.send(f"❌ Ability `{stat}` not found on `{char_name}`. Available: {', '.join(abilities.keys())}")
            return

        mx = int(abilities[stat_key].get("max", abilities[stat_key].get("current", 0)))
        new_max = max(1, mx + delta)

        # Option: if current > new_max, clamp current to new_max
        cur = int(abilities[stat_key].get("current", new_max))
        if cur > new_max:
            cur = new_max

        new_mod = int(get_modifier(cur))

        character["abilities"][stat_key]["max"] = int(new_max)
        character["abilities"][stat_key]["current"] = int(cur)
        character["abilities"][stat_key]["mod"] = int(new_mod)

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        symbol = "⚖️"
        await ctx.send(f"{symbol} `{char_name}` {stat_key} max set → {new_max}. Current: {cur} ({new_mod:+}).")

    except Exception as e:
        await ctx.send(f"⚠️ Error updating ability max: `{e}`")
        print(f"Ability Max Error: {e}")


#---------Apply Armour---------------
@bot.command(name='armor')
async def choose_armor(ctx, *, char_name: str):
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    with open(filename, "r") as f:
        character = json.load(f)

    class ArmorButton(Button):
        def __init__(self, armor_name):
            super().__init__(label=armor_name.title(), style=discord.ButtonStyle.primary, custom_id=armor_name)
            self.armor_name = armor_name

        async def callback(self, interaction: discord.Interaction):
            if interaction.user != ctx.author:
                await interaction.response.send_message("🚫 This button isn’t for you!", ephemeral=True)
                return

            # Apply armor
            character["armor"] = self.armor_name
            agi_mod = character["abilities"]["AGI"]["mod"]
            armor_bonus = ARMOR_TABLE[self.armor_name]["ac_bonus"]
            shield_bonus = ARMOR_TABLE["shield"]["ac_bonus"] if character.get("shield") else 0
            character["ac"] = 10 + agi_mod + armor_bonus + shield_bonus
            character["fumble_die"] = ARMOR_TABLE[self.armor_name]["fumble_die"]

            with open(filename, "w") as f:
                json.dump(character, f, indent=4)

            await interaction.response.send_message(
                f"✅ `{char_name}` is now wearing **{self.armor_name.title()}**!\n"
                f"> 🛡️ AC: `{character['ac']}`\n"
                f"> 🎲 Fumble Die: `{character['fumble_die']}`",
                ephemeral=False
            )
            self.view.stop()

    class ArmorView(View):
        def __init__(self):
            super().__init__(timeout=60)
            for armor in ARMOR_TABLE:
                if armor != "shield":
                    self.add_item(ArmorButton(armor))

            self.add_item(Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel"))

        async def interaction_check(self, interaction: discord.Interaction):
            return interaction.user == ctx.author

        async def on_timeout(self):
            await ctx.send("⏱️ Armor selection timed out.")

    await ctx.send(f"🧱 **Choose armor for `{char_name}`**", view=ArmorView())


async def _prompt_for_character(ctx, prompt_text: str = "What is the name of the character?"):
    await ctx.send(prompt_text)
    def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=60)
        return msg.content.strip()
    except Exception:
        await ctx.send("⏳ Timeout. Try again.")
        return None


async def _apply_rest(ctx, char_name: str, heal_per_night: int, rest_type: str):
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    with open(filename, "r") as f:
        character = json.load(f)

    # HP recovery
    hp_obj = character.get("hp", {"current": 0, "max": 0})
    old_hp = int(hp_obj.get("current", 0))
    max_hp = int(hp_obj.get("max", 0))
    new_hp = max(0, min(max_hp, old_hp + heal_per_night))
    character["hp"]["current"] = new_hp

    # Ability recovery (except Luck)
    abilities = character.get("abilities", {})
    healed_abilities = []
    for k, v in abilities.items():
        # skip luck
        if k.upper() == "LCK":
            continue
        cur = int(v.get("current", v.get("max", 0)))
        mx = int(v.get("max", cur))
        new_cur = max(0, min(mx, cur + heal_per_night))
        if new_cur != cur:
            healed_abilities.append((k, cur, new_cur))
        character["abilities"][k]["current"] = int(new_cur)
    character["abilities"][k]["mod"] = int(get_modifier(new_cur))

    # Reset cleric disapproval to 1 on rest
    disapproval_reset = False
    try:
        if character.get("class", "").lower() == "cleric":
            if "disapproval_range" in character and character.get("disapproval_range") != 1:
                character["disapproval_range"] = 1
                disapproval_reset = True
    except Exception:
        # ignore and continue
        pass

    # Thief: recover luck each night equal to level (cap at luck.max)
    try:
        if character.get('class', '').lower() == 'thief':
            lvl = int(character.get('level', 0))
            if lvl > 0:
                luck = character.setdefault('luck', {'current': 0, 'max': 0})
                try:
                    cur = int(luck.get('current', 0))
                except Exception:
                    cur = 0
                try:
                    mx = int(luck.get('max', 0))
                except Exception:
                    mx = cur
                add = min(lvl, max(0, mx - cur))
                if add > 0:
                    character['luck']['current'] = cur + add
    except Exception:
        pass

    # Persist changes
    try:
        with open(filename, "w") as f:
            json.dump(character, f, indent=4)
    except Exception as e:
        await ctx.send(f"⚠️ Failed to save character after rest: `{e}`")
        return

    # Build reply
    parts = []
    if new_hp != old_hp:
        parts.append(f"❤️ HP: `{old_hp}` → `{new_hp}` (max {max_hp})")
    else:
        parts.append(f"❤️ HP: `{old_hp}` (max {max_hp}) — no change")

    if healed_abilities:
        ab_lines = ", ".join([f"{s}: {o} → {n}" for s, o, n in healed_abilities])
        parts.append(f"🧬 Abilities healed: {ab_lines}")
    else:
        parts.append("🧬 Abilities healed: none")

    # Luck note
    if "LCK" in abilities:
        parts.append("🍀 LCK does not heal naturally.")

    # Critical wounds note
    if character.get("critical_wounds"):
        parts.append("⚠️ Critical wounds: some heal only when associated damage is removed; permanent crits require magic or GM ruling.")

    if disapproval_reset:
        parts.append("🙏 Cleric disapproval range reset to 1 by rest.")

    await ctx.send(f"🛌 {rest_type} complete for `{char_name}`.\n" + "\n".join(parts))


@bot.command(name="rest")
async def rest_command(ctx, *, char_name: str = None):
    """Normal rest: heals 1 HP and 1 point of non-Luck ability damage."""
    if not char_name or not char_name.strip():
        char_name = await _prompt_for_character(ctx)
        if not char_name:
            return
    await _apply_rest(ctx, char_name, heal_per_night=1, rest_type="Normal rest")


@bot.command(name="bedrest")
async def bedrest_command(ctx, *, char_name: str = None):
    """Bed rest: heals 2 HP and 2 points of non-Luck ability damage."""
    if not char_name or not char_name.strip():
        char_name = await _prompt_for_character(ctx)
        if not char_name:
            return
    await _apply_rest(ctx, char_name, heal_per_night=2, rest_type="Bed rest")

#---------Toggle Shield---------------
from discord.ui import Button, View

@bot.command(name='shield')
async def toggle_shield(ctx, *, char_name: str):
    filename = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(filename):
        await ctx.send(f"❌ Character `{char_name}` not found.")
        return

    with open(filename, "r") as f:
        character = json.load(f)

    current_state = character.get("shield", False)
    verb = "unequip" if current_state else "equip"
    prompt = f"🛡️ `{char_name}` currently has shield {'equipped' if current_state else 'unequipped'}.\nWould you like to **{verb}** it?"

    # --- Button logic ---
    async def confirm_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("⛔ Only the command user can interact.", ephemeral=True)
            return

        # Toggle shield status
        character["shield"] = not current_state

        # Recalculate AC
        agi_mod = character["abilities"]["AGI"]["mod"]
        armor_key = character.get("armor", "unarmored")
        armor_bonus = ARMOR_TABLE.get(armor_key, {"ac_bonus": 0})["ac_bonus"]
        shield_bonus = ARMOR_TABLE["shield"]["ac_bonus"] if character["shield"] else 0
        character["ac"] = 10 + agi_mod + armor_bonus + shield_bonus

        with open(filename, "w") as f:
            json.dump(character, f, indent=4)

        status = "equipped ✅" if character["shield"] else "unequipped ❌"
        await interaction.response.edit_message(
            content=f"🛡️ Shield has been {status}. New AC: `{character['ac']}`.",
            view=None
        )

    async def cancel_callback(interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("⛔ Only the command user can interact.", ephemeral=True)
            return
        await interaction.response.edit_message(content="❎ Action canceled.", view=None)

    confirm_button = Button(label="Yes", style=discord.ButtonStyle.success)
    cancel_button = Button(label="No", style=discord.ButtonStyle.danger)
    confirm_button.callback = confirm_callback
    cancel_button.callback = cancel_callback

    view = View()
    view.add_item(confirm_button)
    view.add_item(cancel_button)

    await ctx.send(prompt, view=view)

@bot.command(name="levelup")
async def levelup(ctx, *, char_name: str = None):
    import os
    import json
    import random
    from discord.ui import Button, View

    def load_character(user_id, name):
        filename = os.path.join(SAVE_FOLDER, f"{name}.json")
        if not os.path.exists(filename):
            return None, f"❌ Character `{name}` not found."
        with open(filename, "r") as f:
            data = json.load(f)
        if str(data.get("owner")) != str(user_id):
            return None, "🚫 You do not own this character."
        return data, filename

    alignment_data = {
        "lawful": {
            "gods": ["Shul", "Klazath", "Ulesh", "Choranus", "Daenthar", "Gorhan", "Justicia", "Aristemis"],
            "weapons": ["club", "mace", "sling", "staff", "warhammer"],
            "unholy": ["undead", "demons", "devils", "chaotic extraplanar creatures", "basilisk", "medusa", "Chaos Primes", "chaotic humanoids", "chaotic dragons"]
        },
        "neutral": {
            "gods": ["Amun Tor", "Ildavir", "Pelagia", "Cthulhu"],
            "weapons": ["dagger", "mace", "sling", "staff", "sword"],
            "unholy": ["mundane animals", "undead", "demons", "devils", "basilisk", "medusa", "lycanthropes", "otyughs", "slimes"]
        },
        "chaotic": {
            "gods": ["Ahriman", "Hidden Lord", "Azi Dahaka", "Bobugbubilz", "Cadixtat", "Nimlurun", "Malotoch"],
            "weapons": ["axe", "bow", "dagger", "dart", "flail"],
            "unholy": ["angels", "paladins", "lawful dragons", "Lords of Law", "Lawful Primes", "Law-aligned humanoids"]
        }
    }

    # Load cleric spells from Spells.json
    spells_json_path = os.path.join(os.path.dirname(__file__), "Spells.json")
    with open(spells_json_path, "r", encoding="utf-8") as f:
        spells_data = json.load(f)
    cleric_spells = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 1", {}).keys())
    cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())

    # Step 1: Character name (accept provided arg or prompt)
    def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        if not char_name:
            await ctx.send("🔍 What is the name of the character you want to level up?")
            name_msg = await bot.wait_for("message", check=check, timeout=60)
            char_name = name_msg.content.strip()

        character, filepath = load_character(ctx.author.id, char_name)
        if not character:
            await ctx.send(filepath)
            return

        # Save a pre-levelup backup so the levelup can be undone if needed
        try:
            from copy import deepcopy
            backup_dir = os.path.join(SAVE_FOLDER, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            pre_backup_path = os.path.join(backup_dir, f"{char_name}.prelevelup.json")
            # dump the exact original file contents so we can restore later
            with open(pre_backup_path, 'w', encoding='utf-8') as bf:
                json.dump(deepcopy(character), bf, indent=4)
        except Exception:
            # Non-fatal: continue even if backup fails
            pass
    except Exception:
        await ctx.send("⏳ Timeout. Please try again.")
        return

    # Debug: announce we've loaded the character and are beginning level-up processing
    try:
        await ctx.send(f"🔔 Levelup: preparing to level `{char_name}` (current level: {character.get('level', 0)})")
    except Exception:
        pass
    try:
        print(f"[LEVELUP DEBUG] preparing to level {char_name} (level {character.get('level', 0)})")
    except Exception:
        pass
    current_level = character.get("level", 0)
    if current_level >= 10:
        await ctx.send(f"⚠️ `{char_name}` is already level {character['level']} or higher. Level 10 is the maximum supported.")
        return
    prev_spells = character.get("spells", {}).get("level_1", []) if current_level >= 1 else []
    prev_spell_names = [s["name"] for s in prev_spells]
    prev_spells_lvl2 = character.get("spells", {}).get("level_2", []) if current_level >= 3 else []
    prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2]

    # Step 2-4: Only prompt for class, alignment, and deity if level 0 (new character)
    patron = None  # will hold wizard patron if selected
    if current_level == 0:
        # Step 2: Class selection
        class ClassView(View):
            def __init__(self):
                super().__init__(timeout=60)
                self.value = None

            @discord.ui.button(label="Cleric", style=discord.ButtonStyle.primary)
            async def cleric(self, interaction, button):
                self.value = "cleric"
                await interaction.response.defer()
                self.stop()

            @discord.ui.button(label="Thief", style=discord.ButtonStyle.primary)
            async def thief(self, interaction, button):
                self.value = "thief"
                await interaction.response.defer()
                self.stop()

            @discord.ui.button(label="Warrior", style=discord.ButtonStyle.primary)
            async def warrior(self, interaction, button):
                self.value = "warrior"
                await interaction.response.defer()
                self.stop()

            @discord.ui.button(label="Wizard", style=discord.ButtonStyle.primary)
            async def wizard(self, interaction, button):
                self.value = "wizard"
                await interaction.response.defer()
                self.stop()

        view = ClassView()
        await ctx.send("📜 Choose your class:", view=view)
        await view.wait()
        char_class = view.value
        if not char_class:
            await ctx.send("⏳ No class selected.")
            return

        # Step 3: Alignment selection
        class AlignView(View):
            def __init__(self):
                super().__init__(timeout=60)
                self.choice = None

            @discord.ui.button(label="Lawful", style=discord.ButtonStyle.success)
            async def lawful(self, interaction, button):
                self.choice = "lawful"
                await interaction.response.defer()
                self.stop()

            @discord.ui.button(label="Neutral", style=discord.ButtonStyle.primary)
            async def neutral(self, interaction, button):
                self.choice = "neutral"
                await interaction.response.defer()
                self.stop()

            @discord.ui.button(label="Chaotic", style=discord.ButtonStyle.danger)
            async def chaotic(self, interaction, button):
                self.choice = "chaotic"
                await interaction.response.defer()
                self.stop()

        av = AlignView()
        await ctx.send("⚖️ Choose alignment:", view=av)
        await av.wait()
        alignment = av.choice
        if not alignment:
            await ctx.send("⏳ No alignment selected.")
            return

        # Step 4: God selection (Cleric only) / Patron selection (Wizard only)
        god = None
        if char_class == "cleric":
            god_list = alignment_data[alignment]["gods"]

            class GodView(View):
                def __init__(self, gods):
                    super().__init__(timeout=90)
                    self.value = None
                    for g in gods:
                        button = Button(label=g, style=discord.ButtonStyle.secondary)
                        button.callback = self.make_callback(g)
                        self.add_item(button)

                def make_callback(self, god_name):
                    async def callback(interaction):
                        self.value = god_name
                        await interaction.response.defer()
                        self.stop()
                    return callback

                @discord.ui.button(label="Custom", style=discord.ButtonStyle.danger)
                async def custom_god(self, interaction, button):
                    await interaction.response.send_message("✍️ Type your custom god's name:", ephemeral=True)
                    try:
                        msg = await bot.wait_for("message", check=check, timeout=60)
                        self.value = msg.content.strip()
                    except:
                        self.value = None
                    self.stop()

            gv = GodView(god_list)
            await ctx.send(f"🙏 Choose a god (or click **Custom** to type one):", view=gv)
            await gv.wait()
            god = gv.value
            if not god:
                await ctx.send("⏳ No god selected.")
                return
        elif char_class == "wizard":
            # Patron selection for new level 1 wizard
            patrons_path = os.path.join(os.path.dirname(__file__), 'wizard_patrons.json')
            patrons = []
            try:
                if os.path.exists(patrons_path):
                    with open(patrons_path, 'r', encoding='utf-8') as pf:
                        data = json.load(pf)
                        # Flexible: support list of strings OR list of objects with 'name'
                        if isinstance(data, list):
                            for entry in data:
                                if isinstance(entry, str):
                                    patrons.append(entry)
                                elif isinstance(entry, dict) and entry.get('name'):
                                    patrons.append(str(entry['name']))
                        elif isinstance(data, dict):
                            # If dict, take keys or values depending on structure
                            if 'patrons' in data and isinstance(data['patrons'], list):
                                for entry in data['patrons']:
                                    if isinstance(entry, str):
                                        patrons.append(entry)
                                    elif isinstance(entry, dict) and entry.get('name'):
                                        patrons.append(str(entry['name']))
                            else:
                                patrons.extend([str(k) for k in data.keys()])
            except Exception:
                patrons = []
            # Fallback default patrons if file missing/empty
            if not patrons:
                patrons = ["Sezrekan", "The King of Elfland", "The Three Fates", "The Lord of All Flesh", "The Queen of Elfland"]

            # Discord has a max of 25 components per view; truncate beyond 24 patrons (plus custom button)
            patrons = patrons[:24]

            class PatronView(View):
                def __init__(self, plist):
                    super().__init__(timeout=90)
                    self.value = None
                    for p in plist:
                        btn = Button(label=p, style=discord.ButtonStyle.secondary)
                        async def cb(interaction, _p=p):
                            if interaction.user != ctx.author:
                                await interaction.response.send_message("⛔ Not your selection.", ephemeral=True)
                                return
                            self.value = _p
                            await interaction.response.defer()
                            self.stop()
                        btn.callback = cb
                        self.add_item(btn)

                @discord.ui.button(label="Custom", style=discord.ButtonStyle.danger)
                async def custom_patron(self, interaction, button):
                    if interaction.user != ctx.author:
                        await interaction.response.send_message("⛔ Not your selection.", ephemeral=True)
                        return
                    await interaction.response.send_message("✍️ Type your custom patron name:", ephemeral=True)
                    try:
                        msg = await bot.wait_for("message", check=check, timeout=60)
                        self.value = msg.content.strip()
                    except Exception:
                        self.value = None
                    self.stop()

            pv = PatronView(patrons)
            await ctx.send("🔮 Choose a wizard patron (or Custom):", view=pv)
            await pv.wait()
            patron = pv.value
            if not patron:
                await ctx.send("⏳ No patron selected.")
                return
    else:
        # Use saved values for class, alignment, and god
        char_class = character.get("class", "cleric").lower()
        alignment = character.get("alignment", "lawful").lower()
        god = character.get("god", None)
        patron = character.get("patron")

    # Step 5: Cleric spell selection
    chosen_spells = []
    chosen_spells_lvl2 = []
    if char_class == "cleric":
        # Debug: report resolved class/alignment/god
        try:
            await ctx.send(f"🔔 Levelup branch: class={char_class}, alignment={alignment}, god={god}, current_level={current_level}")
        except Exception:
            pass
        try:
            print(f"[LEVELUP DEBUG] branch cleric: class={char_class}, alignment={alignment}, god={god}, level={current_level}")
        except Exception:
            pass
        prev_spells = character.get("spells", {}).get("level_1", []) if current_level >= 1 else []
        prev_spell_names = [s["name"] for s in prev_spells]
        prev_spells_lvl2 = character.get("spells", {}).get("level_2", []) if current_level >= 3 else []
        prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2]
        if current_level == 0:
            spell_list = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(cleric_spells))
            while True:
                await ctx.send(f"🧙 Choose 4 spells (comma separated) or type `random`:\n{spell_list}")
                try:
                    msg = await bot.wait_for("message", check=check, timeout=120)
                    content = msg.content.strip()
                    if content.lower() == "random":
                        chosen = random.sample(cleric_spells, 4)
                    else:
                        names = [s.strip() for s in content.split(",")]
                        if len(names) != 4:
                            await ctx.send("⚠️ You must pick exactly 4 spells.")
                            continue
                        invalid = [n for n in names if n not in cleric_spells]
                        if invalid:
                            await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid)}. Please pick from the list.")
                            continue
                        if len(set(names)) != 4:
                            await ctx.send("⚠️ You cannot pick the same spell more than once. Please choose 4 different spells.")
                            continue
                        chosen = names
                    # Save full spell data for each chosen spell
                    chosen_spells = []
                    for spell_name in chosen:
                        spell_data = spells_data["spells"]["Cleric Spells"]["level 1"].get(spell_name, {})
                        chosen_spells.append({"name": spell_name, **spell_data})
                    break
                except Exception as e:
                    await ctx.send(f"⏳ Timeout or error: {e}")
                    return
        elif current_level == 1:
            # Level 2: choose 1 more spell, no duplicates
            available_spells = [s for s in cleric_spells if s not in prev_spell_names]
            if not available_spells:
                await ctx.send("⚠️ You already know all available 1st-level spells!")
                return
            spell_list = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_spells))
            await ctx.send(f"🧙 Choose 1 more 1st-level spell (or type `random`):\n{spell_list}")
            try:
                msg = await bot.wait_for("message", check=check, timeout=60)
                content = msg.content.strip()
                if content.lower() == "random":
                    chosen = [random.choice(available_spells)]
                else:
                    names = [s.strip() for s in content.split(",")]
                    if len(names) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid = [n for n in names if n not in available_spells]
                    if invalid:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid)}. Please pick from the list.")
                        return
                    chosen = names
                # Save full spell data for the new spell
                chosen_spells = prev_spells + [{"name": chosen[0], **spells_data["spells"]["Cleric Spells"]["level 1"].get(chosen[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
        elif current_level == 2:
            # Level 3: choose 3 unique level 2 spells
            available_lvl2 = [s for s in cleric_spells_lvl2 if s not in prev_spell_names_lvl2]
            if len(available_lvl2) < 3:
                await ctx.send("⚠️ Not enough unique level 2 spells available!")
                return
            spell_list = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl2))
            while True:
                await ctx.send(f"🧙 Choose 3 second-level spells (comma separated) or type `random`:\n{spell_list}")
                try:
                    msg = await bot.wait_for("message", check=check, timeout=120)
                    content = msg.content.strip()
                    if content.lower() == "random":
                        chosen2 = random.sample(available_lvl2, 3)
                    else:
                        names2 = [s.strip() for s in content.split(",")]
                        if len(names2) != 3:
                            await ctx.send("⚠️ You must pick exactly 3 spells.")
                            continue
                        invalid2 = [n for n in names2 if n not in available_lvl2]
                        if invalid2:
                            await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid2)}. Please pick from the list.")
                            continue
                        if len(set(names2)) != 3:
                            await ctx.send("⚠️ You cannot pick the same spell more than once. Please choose 3 different spells.")
                            continue
                        chosen2 = names2
                    # Save full spell data for each chosen spell
                    chosen_spells_lvl2 = []
                    for spell_name in chosen2:
                        spell_data = spells_data["spells"]["Cleric Spells"]["level 2"].get(spell_name, {})
                        chosen_spells_lvl2.append({"name": spell_name, **spell_data})
                    break
                except Exception as e:
                    await ctx.send(f"⏳ Timeout or error: {e}")
                    return
        elif current_level == 3:
            # Level 4: +1 fort, +1 1st-level spell, +1 2nd-level spell
            # 1st-level spell
            available_lvl1 = [s for s in cleric_spells if s not in prev_spell_names]
            if not available_lvl1:
                await ctx.send("⚠️ You already know all available 1st-level spells!")
                return
            spell_list1 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl1))
            await ctx.send(f"🧙 Choose 1 more 1st-level spell (or type `random`):\n{spell_list1}")
            try:
                msg1 = await bot.wait_for("message", check=check, timeout=60)
                content1 = msg1.content.strip()
                if content1.lower() == "random":
                    chosen1 = [random.choice(available_lvl1)]
                else:
                    names1 = [s.strip() for s in content1.split(",")]
                    if len(names1) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid1 = [n for n in names1 if n not in available_lvl1]
                    if invalid1:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid1)}. Please pick from the list.")
                        return
                    chosen1 = names1
                chosen_spells = prev_spells + [{"name": chosen1[0], **spells_data["spells"]["Cleric Spells"]["level 1"].get(chosen1[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
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
        elif current_level == 4:
            # Level 5: +1 attack (total +3), crit die 1d12, +1 will, +1 2nd-level spell, 2 third-level spells, new title
            # Load 3rd-level spells
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            # Debug: entering cleric level 5 (current_level==4) branch
            try:
                await ctx.send("🔔 Entering cleric level-up branch for level 5 (choose spells soon)")
            except Exception:
                pass
            try:
                print(f"[LEVELUP DEBUG] cleric current_level==4: preparing spell prompts for {char_name}")
            except Exception:
                pass
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
            # 3rd-level spells (pick 2, no duplicates)
            available_lvl3 = [s for s in cleric_spells_lvl3 if s not in prev_spell_names_lvl3]
            if len(available_lvl3) < 2:
                await ctx.send("⚠️ Not enough unique 3rd-level spells available!")
                return
            spell_list3 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl3))
            while True:
                await ctx.send(f"🧙 Choose 2 third-level spells (comma separated) or type `random`:\n{spell_list3}")
                try:
                    msg3 = await bot.wait_for("message", check=check, timeout=120)
                    content3 = msg3.content.strip()
                    if content3.lower() == "random":
                        chosen3 = random.sample(available_lvl3, 2)
                    else:
                        names3 = [s.strip() for s in content3.split(",")]
                        if len(names3) != 2:
                            await ctx.send("⚠️ You must pick exactly 2 spells.")
                            continue
                        invalid3 = [n for n in names3 if n not in available_lvl3]
                        if invalid3:
                            await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid3)}. Please pick from the list.")
                            continue
                        if len(set(names3)) != 2:
                            await ctx.send("⚠️ You cannot pick the same spell more than once. Please choose 2 different spells.")
                            continue
                        chosen3 = names3
                    # Save full spell data for each chosen spell
                    chosen_spells_lvl3 = []
                    for spell_name in chosen3:
                        spell_data = spells_data["spells"]["Cleric Spells"]["level 3"].get(spell_name, {})
                        chosen_spells_lvl3.append({"name": spell_name, **spell_data})
                    break
                except Exception as e:
                    await ctx.send(f"⏳ Timeout or error: {e}")
                    return
        elif current_level == 5:
            # Level 6: +1 attack (total +4), action die 1d20+1d14, +1 reflex, +1 will, +1 first-level spell, +1 third-level spell
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            prev_spells_lvl1 = character.get("spells", {}).get("level_1", [])
            prev_spell_names_lvl1 = [s["name"] for s in prev_spells_lvl1] if prev_spells_lvl1 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            # 1st-level spell
            available_lvl1 = [s for s in cleric_spells if s not in prev_spell_names_lvl1]
            if not available_lvl1:
                await ctx.send("⚠️ You already know all available 1st-level spells!")
                return
            spell_list1 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl1))
            await ctx.send(f"🧙 Choose 1 more 1st-level spell (or type `random`):\n{spell_list1}")
            try:
                msg1 = await bot.wait_for("message", check=check, timeout=60)
                content1 = msg1.content.strip()
                if content1.lower() == "random":
                    chosen1 = [random.choice(available_lvl1)]
                else:
                    names1 = [s.strip() for s in content1.split(",")]
                    if len(names1) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid1 = [n for n in names1 if n not in available_lvl1]
                    if invalid1:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid1)}. Please pick from the list.")
                        return
                    chosen1 = names1
                chosen_spells = prev_spells_lvl1 + [{"name": chosen1[0], **spells_data["spells"]["Cleric Spells"]["level 1"].get(chosen1[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 3rd-level spell
            available_lvl3 = [s for s in cleric_spells_lvl3 if s not in prev_spell_names_lvl3]
            if not available_lvl3:
                await ctx.send("⚠️ You already know all available 3rd-level spells!")
                return
            spell_list3 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl3))
            await ctx.send(f"🧙 Choose 1 more 3rd-level spell (or type `random`):\n{spell_list3}")
            try:
                msg3 = await bot.wait_for("message", check=check, timeout=60)
                content3 = msg3.content.strip()
                if content3.lower() == "random":
                    chosen3 = [random.choice(available_lvl3)]
                else:
                    names3 = [s.strip() for s in content3.split(",")]
                    if len(names3) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid3 = [n for n in names3 if n not in available_lvl3]
                    if invalid3:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid3)}. Please pick from the list.")
                        return
                    chosen3 = names3
                chosen_spells_lvl3 = prev_spells_lvl3 + [{"name": chosen3[0], **spells_data["spells"]["Cleric Spells"]["level 3"].get(chosen3[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
        elif current_level == 6:
            # Level 7: +1 attack (total +5), crit die 1d14, action die 1d20+1d16, +1 fort, +1 2nd-level spell, +1 3rd-level spell, 1 4th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
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
            # 3rd-level spell
            available_lvl3 = [s for s in cleric_spells_lvl3 if s not in prev_spell_names_lvl3]
            if not available_lvl3:
                await ctx.send("⚠️ You already know all available 3rd-level spells!")
                return
            spell_list3 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl3))
            await ctx.send(f"🧙 Choose 1 more 3rd-level spell (or type `random`):\n{spell_list3}")
            try:
                msg3 = await bot.wait_for("message", check=check, timeout=60)
                content3 = msg3.content.strip()
                if content3.lower() == "random":
                    chosen3 = [random.choice(available_lvl3)]
                else:
                    names3 = [s.strip() for s in content3.split(",")]
                    if len(names3) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid3 = [n for n in names3 if n not in available_lvl3]
                    if invalid3:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid3)}. Please pick from the list.")
                        return
                    chosen3 = names3
                chosen_spells_lvl3 = prev_spells_lvl3 + [{"name": chosen3[0], **spells_data["spells"]["Cleric Spells"]["level 3"].get(chosen3[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 4th-level spell
            available_lvl4 = [s for s in cleric_spells_lvl4 if s not in prev_spell_names_lvl4]
            if not available_lvl4:
                await ctx.send("⚠️ You already know all available 4th-level spells!")
                return
            spell_list4 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl4))
            await ctx.send(f"🧙 Choose 1 4th-level spell (or type `random`):\n{spell_list4}")
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
                chosen_spells_lvl4 = [{"name": chosen4[0], **spells_data["spells"]["Cleric Spells"]["level 4"].get(chosen4[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
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
        agi_mod_for_saves = int(abil.get('AGI', {}).get('mod', abil.get('agi', 0)) if isinstance(abil.get('AGI', {}), dict) else abil.get('AGI', 0))
        sta_mod_for_saves = int(abil.get('STA', {}).get('mod', abil.get('sta', 0)) if isinstance(abil.get('STA', {}), dict) else abil.get('STA', 0))
        per_mod_for_saves = int(abil.get('PER', {}).get('mod', abil.get('per', 0)) if isinstance(abil.get('PER', {}), dict) else abil.get('PER', 0))

    if current_level == 0:
        # For brand new characters, compute saves as class base + ability modifiers
        saves = {
            'reflex': int(save_bonuses.get('reflex', 0)) + int(agi_mod_for_saves),
        'fortitude': int(save_bonuses.get('fortitude', 0)) + int(sta_mod_for_saves),
            'will': int(save_bonuses.get('will', 0)) + int(per_mod_for_saves)
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
        # if plain number, treat as score and compute modifier
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
        elif current_level == 8:
            # Level 9: +1 attack, crit die 1d16, reflex +1, +1 2nd-level spell, +1 4th-level spell, 1 5th-level spell
            cleric_spells_lvl2 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 2", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl2 = character.get("spells", {}).get("level_2", [])
            prev_spell_names_lvl2 = [s["name"] for s in prev_spells_lvl2] if prev_spells_lvl2 else []
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
            await ctx.send(f"🧙 Choose 1 5th-level spell (or type `random`):\n{spell_list5}")
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
                chosen_spells_lvl5 = [{"name": chosen5[0], **spells_data["spells"]["Cleric Spells"]["level 5"].get(chosen5[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            character["level"] = 9
            character["attack_bonus"] = "+6"
            character["crit_die"] = "1d16"
            character["saves"]["reflex"] = character["saves"].get("reflex", 0) + 1
            character["spells"]["level_2"] = chosen_spells_lvl2
            character["spells"]["level_4"] = chosen_spells_lvl4
            character["spells"]["level_5"] = chosen_spells_lvl5
        elif current_level == 9:
            # Level 10: +1 attack, +1 fort, +1 will, +1 first-level spell, +1 third-level spell, +1 fourth-level spell, +1 fifth-level spell
            cleric_spells_lvl1 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 1", {}).keys())
            cleric_spells_lvl3 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 3", {}).keys())
            cleric_spells_lvl4 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 4", {}).keys())
            cleric_spells_lvl5 = list(spells_data.get("spells", {}).get("Cleric Spells", {}).get("level 5", {}).keys())
            prev_spells_lvl1 = character.get("spells", {}).get("level_1", [])
            prev_spell_names_lvl1 = [s["name"] for s in prev_spells_lvl1] if prev_spells_lvl1 else []
            prev_spells_lvl3 = character.get("spells", {}).get("level_3", [])
            prev_spell_names_lvl3 = [s["name"] for s in prev_spells_lvl3] if prev_spells_lvl3 else []
            prev_spells_lvl4 = character.get("spells", {}).get("level_4", [])
            prev_spell_names_lvl4 = [s["name"] for s in prev_spells_lvl4] if prev_spells_lvl4 else []
            prev_spells_lvl5 = character.get("spells", {}).get("level_5", [])
            prev_spell_names_lvl5 = [s["name"] for s in prev_spells_lvl5] if prev_spells_lvl5 else []
            # 1st-level spell
            available_lvl1 = [s for s in cleric_spells_lvl1 if s not in prev_spell_names_lvl1]
            if not available_lvl1:
                await ctx.send("⚠️ You already know all available 1st-level spells!")
                return
            spell_list1 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl1))
            await ctx.send(f"🧙 Choose 1 more 1st-level spell (or type `random`):\n{spell_list1}")
            try:
                msg1 = await bot.wait_for("message", check=check, timeout=60)
                content1 = msg1.content.strip()
                if content1.lower() == "random":
                    chosen1 = [random.choice(available_lvl1)]
                else:
                    names1 = [s.strip() for s in content1.split(",")]
                    if len(names1) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid1 = [n for n in names1 if n not in available_lvl1]
                    if invalid1:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid1)}. Please pick from the list.")
                        return
                    chosen1 = names1
                chosen_spells = prev_spells_lvl1 + [{"name": chosen1[0], **spells_data["spells"]["Cleric Spells"]["level 1"].get(chosen1[0], {})}]
            except Exception as e:
                await ctx.send(f"⏳ Timeout or error: {e}")
                return
            # 3rd-level spell
            available_lvl3 = [s for s in cleric_spells_lvl3 if s not in prev_spell_names_lvl3]
            if not available_lvl3:
                await ctx.send("⚠️ You already know all available 3rd-level spells!")
                return
            spell_list3 = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(available_lvl3))
            await ctx.send(f"🧙 Choose 1 more 3rd-level spell (or type `random`):\n{spell_list3}")
            try:
                msg3 = await bot.wait_for("message", check=check, timeout=60)
                content3 = msg3.content.strip()
                if content3.lower() == "random":
                    chosen3 = [random.choice(available_lvl3)]
                else:
                    names3 = [s.strip() for s in content3.split(",")]
                    if len(names3) != 1:
                        await ctx.send("⚠️ You must pick exactly 1 spell.")
                        return
                    invalid3 = [n for n in names3 if n not in available_lvl3]
                    if invalid3:
                        await ctx.send(f"⚠️ Invalid spell(s): {', '.join(invalid3)}. Please pick from the list.")
                        return
                    chosen3 = names3
                chosen_spells_lvl3 = prev_spells_lvl3 + [{"name": chosen3[0], **spells_data["spells"]["Cleric Spells"]["level 3"].get(chosen3[0], {})}]
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

    elif char_class == "warrior":
        # Warrior progression: deed die, crit die/table, action dice, and saves are totals at level
        weapons = [
            "battleaxe","club","crossbow","dagger","dart","handaxe","javelin","longbow",
            "longsword","mace","polearm","shortbow","short sword","sling","spear","staff",
            "two-handed sword","warhammer"
        ]

        warrior_saves_by_level = {
            1: {"reflex": 1, "fortitude": 1, "will": 0},
            2: {"reflex": 1, "fortitude": 1, "will": 0},
            3: {"reflex": 2, "fortitude": 2, "will": 1},
            4: {"reflex": 2, "fortitude": 2, "will": 1},
            5: {"reflex": 3, "fortitude": 3, "will": 1},
            6: {"reflex": 4, "fortitude": 4, "will": 2},
            7: {"reflex": 4, "fortitude": 4, "will": 2},
            8: {"reflex": 5, "fortitude": 5, "will": 2},
            9: {"reflex": 5, "fortitude": 5, "will": 3},
            10:{"reflex": 6, "fortitude": 6, "will": 3}
        }

        warrior_progress = {
            1: {"deed": "d3",    "crit_die": "1d12",    "crit_table": "III", "action": "1d20"},
            2: {"deed": "d4",    "crit_die": "1d12",    "crit_table": "III", "action": "1d20"},
            3: {"deed": "d5",    "crit_die": "1d14",    "crit_table": "III", "action": "1d20"},
            4: {"deed": "d6",    "crit_die": "1d16",    "crit_table": "IV",  "action": "1d20"},
            5: {"deed": "d7",    "crit_die": "1d20",    "crit_table": "IV",  "action": "1d20"},
            6: {"deed": "d8",    "crit_die": "1d24",    "crit_table": "V",   "action": "1d20+1d14"},
            7: {"deed": "d10+1", "crit_die": "1d30",    "crit_table": "V",   "action": "1d20+1d16"},
            8: {"deed": "d10+2", "crit_die": "1d30+2",  "crit_table": "V",   "action": "1d20+1d20"},
            9: {"deed": "d10+3", "crit_die": "1d30+4",  "crit_table": "V",   "action": "1d20+1d20"},
            10:{"deed": "d10+4", "crit_die": "2d20",    "crit_table": "V",   "action": "1d20+1d20+1d14"}
        }

        # Titles per alignment for each warrior level
        warrior_titles = {
            1: {"lawful": "Squire",    "chaotic": "Wildling",  "neutral": "Rover"},
            2: {"lawful": "Champion",  "chaotic": "Barbarian", "neutral": "Nomad"},
            3: {"lawful": "Knight",    "chaotic": "Reaver",    "neutral": "Mercenary"},
            4: {"lawful": "Cavalier",  "chaotic": "Ravager",   "neutral": "Soldier"},
            5: {"lawful": "Paladin",   "chaotic": "Marauder",  "neutral": "Warrior"},
            6: {"lawful": "Warlord",   "chaotic": "Destroyer", "neutral": "Veteran"},
            7: {"lawful": "General",   "chaotic": "Warmonger", "neutral": "Swordmaster"},
            8: {"lawful": "Lord",      "chaotic": "Khan",      "neutral": "Champion"},
            9: {"lawful": "Baron",     "chaotic": "Tyrant",    "neutral": "Commander"},
            10:{"lawful": "King",      "chaotic": "Warlord",   "neutral": "Patriarch/Matriarch"}
        }

        new_lvl = current_level + 1
        prog = warrior_progress.get(new_lvl)
        if not prog:
            await ctx.send(f"⚠️ Warrior progression not defined for level {new_lvl}.")
            return

        if current_level == 0:
            # initial creation
            action_val = prog.get("action", "1d20")
            if isinstance(action_val, list):
                action_die = action_val[0]
                action_dice = action_val
            else:
                # action specified as string like "1d20+1d14"
                action_dice = [a.strip() for a in str(action_val).split("+") if a.strip()]
                action_die = action_dice[0] if action_dice else "1d20"
            # resolve title from alignment (alignment variable from earlier in scope)
            try:
                title_for_level = warrior_titles.get(1, {}).get(alignment, None)
            except Exception:
                title_for_level = None

            # compute saves with ability modifiers
            try:
                agi_mod = _ability_mod_from_char(character, 'AGI')
            except Exception:
                agi_mod = int(character.get('abilities', {}).get('AGI', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('AGI'), dict) else 0)
            try:
                sta_mod = _ability_mod_from_char(character, 'STA')
            except Exception:
                sta_mod = int(character.get('abilities', {}).get('STA', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('STA'), dict) else 0)
            try:
                per_mod = _ability_mod_from_char(character, 'PER')
            except Exception:
                per_mod = int(character.get('abilities', {}).get('PER', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('PER'), dict) else 0)

            base_saves = warrior_saves_by_level.get(1, {}) or {}
            computed_saves = {
                'reflex': int(base_saves.get('reflex', 0)) + int(agi_mod),
                'fortitude': int(base_saves.get('fortitude', 0)) + int(sta_mod),
                'will': int(base_saves.get('will', 0)) + int(per_mod)
            }

            character.update({
                "class": "Warrior",
                "weapon_proficiencies": weapons,
                "features": ["Mighty Deed of Arms", "Weapon Training"],
                "deed_die": prog["deed"],
                "crit_die": prog["crit_die"],
                "crit_table": prog["crit_table"],
                "action_die": action_die,
                "action_dice": action_dice,
                "level": 1,
                "title": title_for_level,
                "hit_die": "1d12",
                "saves": computed_saves
            })
        else:
            # level increment: set totals for the new level (not cumulative)
            character["level"] = new_lvl
            character["deed_die"] = prog["deed"]
            character["crit_die"] = prog["crit_die"]
            character["crit_table"] = prog["crit_table"]
            action_val = prog.get("action", character.get("action_die", "1d20"))
            if isinstance(action_val, list):
                action_die = action_val[0]
                action_dice = action_val
            else:
                action_dice = [a.strip() for a in str(action_val).split("+") if a.strip()]
                action_die = action_dice[0] if action_dice else character.get("action_die", "1d20")
            character["action_die"] = action_die
            character["action_dice"] = action_dice
            # update title for this level
            try:
                character["title"] = warrior_titles.get(new_lvl, {}).get(alignment, character.get("title"))
            except Exception:
                pass
            character["hit_die"] = "1d12"
            # compute saves for this new level including ability mods
            try:
                agi_mod = _ability_mod_from_char(character, 'AGI')
            except Exception:
                agi_mod = int(character.get('abilities', {}).get('AGI', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('AGI'), dict) else 0)
            try:
                sta_mod = _ability_mod_from_char(character, 'STA')
            except Exception:
                sta_mod = int(character.get('abilities', {}).get('STA', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('STA'), dict) else 0)
            try:
                per_mod = _ability_mod_from_char(character, 'PER')
            except Exception:
                per_mod = int(character.get('abilities', {}).get('PER', {}).get('mod', 0) if isinstance(character.get('abilities', {}).get('PER'), dict) else 0)

            base_saves = warrior_saves_by_level.get(new_lvl, {}) or {}
            character["saves"] = {
                'reflex': int(base_saves.get('reflex', 0)) + int(agi_mod),
                'fortitude': int(base_saves.get('fortitude', 0)) + int(sta_mod),
                'will': int(base_saves.get('will', 0)) + int(per_mod)
            }

        # Note: attack bonuses are provided via the deed die rolled at attack time; no static +atk set here.

    elif char_class == "thief":
        # ensure changes summary var exists for message assembly
        changes_one_line = "No visible changes."

        thief_skills = {
            "lawful": {
                "Backstab": 1, "Sneak silently": 1, "Hide in shadows": 3, "Pick pocket": 1,
                "Climb sheer surfaces": 3, "Pick lock": 1, "Find trap": 3, "Disable trap": 3,
                "Forge document": 0, "Disguise self": 0, "Read languages": 0, "Handle poison": 0,
                "Cast spell from scroll": "1d10"
            },
            "chaotic": {
                "Backstab": 3, "Sneak silently": 3, "Hide in shadows": 1, "Pick pocket": 0,
                "Climb sheer surfaces": 1, "Pick lock": 1, "Find trap": 1, "Disable trap": 0,
                "Forge document": 0, "Disguise self": 3, "Read languages": 0, "Handle poison": 3,
                "Cast spell from scroll": "1d10"
            },
            "neutral": {
                "Backstab": 0, "Sneak silently": 3, "Hide in shadows": 1, "Pick pocket": 3,
                "Climb sheer surfaces": 3, "Pick lock": 1, "Find trap": 1, "Disable trap": 1,
                "Forge document": 3, "Disguise self": 0, "Read languages": 0, "Handle poison": 0,
                "Cast spell from scroll": "1d12"
            }
        }

        titles = {
            "lawful": "Bravo",
            "neutral": "Beggar",
            "chaotic": "Thug"
        }

        if current_level == 0:
        # initial saves will be set in the saves merge step (Step 6) to avoid double-counting ability modifiers

            character.update({
                "class": "Thief",
                "weapon_proficiencies": [
                    "blackjack", "blowgun", "crossbow", "dagger", "dart", "garrote",
                    "longsword", "short sword", "sling", "staff"
                ],
                "features": ["Thieves' Cant", f"Thieves' Skills ({titles[alignment]} Path)"],
                "luck_die": "1d3",
                # Initialize skills according to level 1 values (will be bumped on levelup)
                # normalize skill keys to lowercase for consistent storage
                "skills": {k.lower(): v for k, v in thief_skills[alignment].items()},
                "title": titles[alignment],
                "level": 1,
                "hit_die": f"1d{hit_die}",
                "crit_die": "1d10",
                "crit_table": "II",
                "action_die": "1d20",
                "saves": saves,
                "alignment": alignment
            })
        else:
            # Level up a thief by one level and apply progression tables (clamped to 10)
            new_lvl = min(10, int(current_level) + 1)

            # capture old values for a concise changes summary
            old_attack = character.get('attack_bonus')
            old_crit = character.get('crit_die')
            old_action = character.get('action_die')
            old_luck = character.get('luck_die')
            old_title = character.get('title')
            old_saves = dict(character.get('saves', {}))
            old_skills = dict(character.get('skills', {}))

            # Titles (only up to level 5 provided)
            titles_by_level = {
                1: {"lawful": "Bravo", "chaotic": "Thug", "neutral": "Beggar"},
                2: {"lawful": "Apprentice", "chaotic": "Murderer", "neutral": "Cutpurse"},
                3: {"lawful": "Rogue", "chaotic": "Cutthroat", "neutral": "Burglar"},
                4: {"lawful": "Capo", "chaotic": "Executioner", "neutral": "Robber"},
                5: {"lawful": "Boss", "chaotic": "Assassin", "neutral": "Swindler"}
            }

            # Thief progression table (levels 1..10)
            thief_progress = {
                1: {"attack": 0, "crit_die": "1d10", "action_die": "1d20", "luck_die": "d3", "ref": 1, "fort": 1, "will": 0},
                2: {"attack": 1, "crit_die": "1d12", "action_die": "1d20", "luck_die": "d4", "ref": 1, "fort": 1, "will": 0},
                3: {"attack": 2, "crit_die": "1d14", "action_die": "1d20", "luck_die": "d5", "ref": 2, "fort": 1, "will": 1},
                4: {"attack": 2, "crit_die": "1d16", "action_die": "1d20", "luck_die": "d6", "ref": 2, "fort": 2, "will": 1},
                5: {"attack": 3, "crit_die": "1d20", "action_die": "1d20", "luck_die": "d7", "ref": 3, "fort": 2, "will": 1},
                6: {"attack": 4, "crit_die": "1d24", "action_die": "1d20+1d14", "luck_die": "d8", "ref": 4, "fort": 2, "will": 2},
                7: {"attack": 5, "crit_die": "1d30", "action_die": "1d20+1d16", "luck_die": "d10", "ref": 4, "fort": 3, "will": 2},
                8: {"attack": 5, "crit_die": "1d30+2", "action_die": "1d20+1d20", "luck_die": "d12", "ref": 5, "fort": 3, "will": 2},
                9: {"attack": 6, "crit_die": "1d30+4", "action_die": "1d20+1d20", "luck_die": "d14", "ref": 5, "fort": 3, "will": 3},
                10:{"attack": 7, "crit_die": "1d30+6", "action_die": "1d20+1d20", "luck_die": "d16", "ref": 6, "fort": 4, "will": 3}
            }

            # Skill tables per alignment (levels 1..10)
            skill_table = {
                'lawful': {
                    'Backstab':        [1,3,5,7,8,9,10,11,12,13],
                    'Sneak silently':  [1,3,5,7,8,9,10,11,12,13],
                    'Hide in shadows': [3,5,7,8,9,11,12,13,14,15],
                    'Pick pocket':     [1,3,5,7,8,9,10,11,12,13],
                    'Climb sheer surfaces':[3,5,7,8,9,11,12,13,14,15],
                    'Pick lock':       [1,3,5,7,8,9,10,11,12,13],
                    'Find trap':       [3,5,7,8,9,11,12,13,14,15],
                    'Disable trap':    [3,5,7,8,9,11,12,13,14,15],
                    'Forge document':  [0,0,1,2,3,4,5,6,7,8],
                    'Disguise self':   [0,1,2,3,4,5,6,7,8,9],
                    'Read languages':  [0,0,1,2,3,4,5,6,7,8],
                    'Handle poison':   [0,1,2,3,4,5,6,7,8,9],
                    'Cast from scroll':["d10","d10","d12","d12","d14","d14","d16","d16","d20","d20"]
                },
                'chaotic': {
                    'Backstab':        [3,5,7,8,9,11,12,13,14,15],
                    'Sneak silently':  [3,5,7,8,9,11,12,13,14,15],
                    'Hide in shadows': [1,3,5,7,8,9,10,11,12,13],
                    'Pick pocket':     [0,1,2,3,4,5,6,7,8,9],
                    'Climb sheer surfaces':[1,3,5,7,8,9,10,11,12,13],
                    'Pick lock':       [1,3,5,7,8,9,10,11,12,13],
                    'Find trap':       [1,3,5,7,8,9,10,11,12,13],
                    'Disable trap':    [0,1,2,3,4,5,6,7,8,9],
                    'Forge document':  [0,0,1,2,3,4,5,6,7,8],
                    'Disguise self':   [3,5,7,8,9,11,12,13,14,15],
                    'Read languages':  [0,0,1,2,3,4,5,6,7,8],
                    'Handle poison':   [3,5,7,8,9,11,12,13,14,15],
                    'Cast from scroll':["d10","d10","d12","d12","d14","d14","d16","d16","d20","d20"]
                },
                'neutral': {
                    'Backstab':        [0,1,2,3,4,5,6,7,8,9],
                    'Sneak silently':  [3,5,7,8,9,11,12,13,14,15],
                    'Hide in shadows': [1,3,5,7,8,9,10,11,12,13],
                    'Pick pocket':     [3,5,7,8,9,11,12,13,14,15],
                    'Climb sheer surfaces':[3,5,7,8,9,11,12,13,14,15],
                    'Pick lock':       [1,3,5,7,8,9,10,11,12,13],
                    'Find trap':       [1,3,5,7,8,9,10,11,12,13],
                    'Disable trap':    [1,3,5,7,8,9,10,11,12,13],
                    'Forge document':  [3,5,7,8,9,11,12,13,14,15],
                    'Disguise self':   [0,0,1,2,3,4,5,6,7,8],
                    'Read languages':  [0,1,2,3,4,5,6,7,8,9],
                    'Handle poison':   [0,0,1,2,3,4,5,6,7,8],
                    'Cast from scroll':["d12","d12","d14","d14","d16","d16","d20","d20","d20","d20"]
                }
            }

            prog = thief_progress[new_lvl]
            # Attack bonus and dice
            character['attack_bonus'] = f"+{prog['attack']}"
            character['crit_die'] = prog['crit_die']
            character['action_die'] = prog['action_die']
            # Normalize luck_die to '1dN' format if needed (e.g. 'd4' -> '1d4')
            luck_raw = str(prog.get('luck_die', ''))
            if luck_raw.startswith('d'):
                luck_norm = '1' + luck_raw
            else:
                luck_norm = luck_raw
            character['luck_die'] = luck_norm
            # Saves: add the class progression values to the character's ability modifiers
            character.setdefault('saves', {})
            abil = character.get('abilities', {})
            def _extract_mod(ab_key):
                v = abil.get(ab_key)
                # try common variants
                if v is None:
                    v = abil.get(ab_key.lower()) if isinstance(ab_key, str) else None
                # If stored as dict with 'mod', use it
                if isinstance(v, dict):
                    try:
                        return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
                    except Exception:
                        try:
                            return int(v.get('mod', 0))
                        except Exception:
                            return 0
                # If stored as plain int (score), compute modifier
                try:
                    if isinstance(v, (int, float)):
                        return int(get_modifier(int(v)))
                    if isinstance(v, str) and v.isdigit():
                        return int(get_modifier(int(v)))
                except Exception:
                    pass
                return 0

            agi_mod = _extract_mod('AGI')
            sta_mod = _extract_mod('STA')
            per_mod = _extract_mod('PER')

            character['saves']['reflex'] = int(prog.get('ref', 0)) + int(agi_mod)
            character['saves']['fortitude'] = int(prog.get('fort', 0)) + int(sta_mod)
            character['saves']['will'] = int(prog.get('will', 0)) + int(per_mod)

            # Title
            if new_lvl in titles_by_level:
                character['title'] = titles_by_level[new_lvl].get(alignment, character.get('title'))

            # Skills
            chosen_table = skill_table.get(alignment, skill_table['neutral'])
            character.setdefault('skills', {})
            for skill_name, vals in chosen_table.items():
                val = vals[new_lvl-1]
                # normalize saved skill keys to lowercase
                key_norm = skill_name.lower()
                character['skills'][key_norm] = val

            # Persist new level
            character['level'] = new_lvl
            # Build a compact one-line changes summary
            changes = []
            if old_attack != character.get('attack_bonus'):
                changes.append(f"Atk {old_attack}->{character.get('attack_bonus')}")
            if old_crit != character.get('crit_die'):
                changes.append(f"Crit {old_crit}->{character.get('crit_die')}")
            if old_action != character.get('action_die'):
                changes.append(f"Act {old_action}->{character.get('action_die')}")
            if old_luck != character.get('luck_die'):
                changes.append(f"Luck {old_luck}->{character.get('luck_die')}")
            if old_title != character.get('title'):
                changes.append(f"Title {old_title}->{character.get('title')}")
            if old_saves != character.get('saves'):
                changes.append("Saves updated")
            if old_skills != character.get('skills'):
                changes.append("Skills updated")
            changes_one_line = "; ".join(changes) if changes else "No visible changes."

    try:
        with open(filepath, "w") as f:
            json.dump(character, f, indent=4)
        level_str = character["level"] if char_class == "cleric" else character.get("level", 1)
        msg = (
            f"✅ `{char_name}` is now a **{char_class.title()} Level {level_str}**!\n"
            f"❤️ HP Gained: {hp_message}\n"
        )
        if char_class == "cleric":
            msg += f"📜 1st-level Spells: {', '.join(s['name'] for s in character['spells'].get('level_1', []))}\n"
            if character["level"] >= 3:
                msg += f"📜 2nd-level Spells: {', '.join(s['name'] for s in character['spells'].get('level_2', []))}\n"
            if character["level"] >= 5:
                msg += f"📜 3rd-level Spells: {', '.join(s['name'] for s in character['spells'].get('level_3', []))}\n"
            if character["level"] >= 7:
                msg += f"📜 4th-level Spells: {', '.join(s['name'] for s in character['spells'].get('level_4', []))}\n"
            msg += f"🙏 God: {character.get('god', god)}\n"
            msg += f"🏅 Title: {character.get('title','')}\n"
            msg += f"⚔️ Attack Bonus: {character.get('attack_bonus','+0')}\n"
            msg += f"🎲 Crit Die: {character.get('crit_die','1d8')}\n"
            msg += f"🎲 Action Die: {character.get('action_die','1d20')}\n"
            msg += f"🛡️ Saves: {character.get('saves',{})}\n"
        # Append wizard patron info
        if char_class == "wizard":
            if patron and not character.get('patron'):
                character['patron'] = patron
            if character.get('patron'):
                msg += f"🔮 Patron: {character.get('patron')}\n"
        # Append one-line changes summary for thieves
        if char_class == "thief":
            msg += f"\n🔧 Changes: {changes_one_line}"
        await ctx.send(msg)
        # Attempt to post updated sheet immediately after level-up
        try:
            try:
                await ctx.send("🔎 Posting updated sheet...")
            except Exception:
                pass
            # Prefer direct call to the sheet handler (avoids command parsing issues)
            try:
                await view_sheet(ctx, char_name=char_name)
            except Exception as ex_direct:
                # Fallback: try invoking the command object
                print(f"[LEVELUP DEBUG] direct view_sheet failed: {ex_direct}")
                try:
                    cmd = bot.get_command("sheet")
                    if cmd:
                        await ctx.invoke(cmd, char_name=char_name)
                    else:
                        try:
                            await ctx.send("⚠️ Could not find `!sheet` command to invoke.")
                        except Exception:
                            pass
                except Exception as ex_invoke:
                    print(f"[LEVELUP DEBUG] ctx.invoke(sheet) failed: {ex_invoke}")
                    try:
                        await ctx.send(f"⚠️ Failed to post updated sheet: {ex_invoke}")
                    except Exception:
                        pass
        except Exception as e:
            # Non-fatal: report to console and continue
            print(f"[LEVELUP DEBUG] unexpected error posting sheet: {e}")
            try:
                await ctx.send(f"⚠️ Failed to post updated sheet: {e}")
            except Exception:
                pass
    except Exception as e:
        await ctx.send(f"⚠️ Failed to save character: {e}")


@bot.command()
async def myid(ctx):
    await ctx.send(f"Your user ID is {ctx.author.id}")


@bot.command(name="undo_levelup")
async def undo_levelup(ctx, *, char_name: str = None):
    """Owner-only: restore the most recent pre-levelup backup for a character.
    Usage: !undo_levelup Char2
    """
    # owner-only safety
    if str(ctx.author.id) != str(os.getenv('OWNER_ID', ctx.author.id)):
        # allow file owner as well
        # attempt to check saved owner in character file
        filename = os.path.join(SAVE_FOLDER, f"{char_name}.json") if char_name else None
        if not filename or not os.path.exists(filename):
            await ctx.send("🚫 You are not authorized to run this command.")
            return
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                c = json.load(f)
            if str(c.get('owner')) != str(ctx.author.id):
                await ctx.send("🚫 You are not authorized to undo this levelup.")
                return
        except Exception:
            await ctx.send("🚫 You are not authorized to undo this levelup.")
            return

    if not char_name:
        await ctx.send("Which character should I restore? Usage: `!undo_levelup CharName`")
        return

    backup_path = os.path.join(SAVE_FOLDER, 'backups', f"{char_name}.prelevelup.json")
    target_path = os.path.join(SAVE_FOLDER, f"{char_name}.json")
    if not os.path.exists(backup_path):
        await ctx.send(f"❌ No pre-levelup backup found for `{char_name}`.")
        return
    if not os.path.exists(target_path):
        await ctx.send(f"❌ Character file `{char_name}.json` not found.")
        return

    try:
        with open(backup_path, 'r', encoding='utf-8') as bf:
            data = json.load(bf)
        # overwrite the live character file with the backup
        with open(target_path, 'w', encoding='utf-8') as tf:
            json.dump(data, tf, indent=4)
        await ctx.send(f"✅ Restored `{char_name}` from pre-levelup backup.")
    except Exception as e:
        await ctx.send(f"⚠️ Failed to restore backup: {e}")


@bot.command(name="simulate_levelup")
async def simulate_levelup(ctx, *, char_name: str = None):
    """Owner-only: simulate the next level-up for a character and display computed saves (no file changes)."""
    if not char_name:
        await ctx.send("Which character? Usage: `!simulate_levelup CharName`")
        return
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
    # owner check: allow file owner or bot owner env
    if str(character.get('owner')) != str(ctx.author.id) and str(ctx.author.id) != str(os.getenv('OWNER_ID', ctx.author.id)):
        await ctx.send("🚫 You are not authorized to simulate this character's levelup.")
        return

    cur_level = int(character.get('level', 0))
    new_level = min(10, cur_level + 1)
    cls = character.get('class', 'Lv0')
    char_class = cls.lower() if isinstance(cls, str) else str(cls).lower()

    # helper to extract ability mod
    def extract_mod(ch, key):
        abil = ch.get('abilities', {}) or {}
        v = abil.get(key)
        if v is None:
            v = abil.get(key.upper()) or abil.get(key.lower())
        if isinstance(v, dict):
            try:
                return int(v.get('mod', get_modifier(int(v.get('current', v.get('max', 0)) or 0))))
            except Exception:
                return int(v.get('mod', 0) or 0)
        try:
            if isinstance(v, (int, float)):
                return int(get_modifier(int(v)))
            if isinstance(v, str) and v.isdigit():
                return int(get_modifier(int(v)))
        except Exception:
            pass
        return 0

    agi_mod = extract_mod(character, 'AGI')
    sta_mod = extract_mod(character, 'STA')
    per_mod = extract_mod(character, 'PER')

    # class base mapping (used in Step 6)
    save_bases_by_class = {
        'cleric': {'reflex': 0, 'fortitude': 1, 'will': 1},
        'thief': {'reflex': 1, 'fortitude': 1, 'will': 0}
    }
    class_bases = save_bases_by_class.get(char_class, {})

    # default: step6 behavior for new chars
    simulated = {}
    if cur_level == 0:
        simulated['reflex'] = int(class_bases.get('reflex', 0)) + agi_mod
        simulated['fortitude'] = int(class_bases.get('fortitude', 0)) + sta_mod
        simulated['will'] = int(class_bases.get('will', 0)) + per_mod
        reason = 'Initial (level 0 → 1) canonical computation: class base + ability mods.'
    else:
        # For thief, use thief_progress table if available
        if char_class == 'thief':
            thief_progress = {
                1: {"ref": 1, "fort": 1, "will": 0},
                2: {"ref": 1, "fort": 1, "will": 0},
                3: {"ref": 2, "fort": 1, "will": 1},
                4: {"ref": 2, "fort": 2, "will": 1},
                5: {"ref": 3, "fort": 2, "will": 1},
                6: {"ref": 4, "fort": 2, "will": 2},
                7: {"ref": 4, "fort": 3, "will": 2},
                8: {"ref": 5, "fort": 3, "will": 2},
                9: {"ref": 5, "fort": 3, "will": 3},
                10:{"ref": 6, "fort": 4, "will": 3}
            }
            prog = thief_progress.get(new_level, {})
            simulated['reflex'] = int(prog.get('ref', 0)) + agi_mod
            simulated['fortitude'] = int(prog.get('fort', 0)) + sta_mod
            simulated['will'] = int(prog.get('will', 0)) + per_mod
            reason = f"Thief progression for level {new_level} (prog base + ability mods)."
        else:
            # Generic fallback: apply Step6 incremental rules used in code
            simulated['reflex'] = (character.get('saves', {}).get('reflex', 0))
            simulated['fortitude'] = (character.get('saves', {}).get('fortitude', 0))
            simulated['will'] = (character.get('saves', {}).get('will', 0))
            # apply cleric special increments if applicable
            if char_class == 'cleric' and cur_level == 2:
                simulated['reflex'] = simulated.get('reflex', 0) + 1
                simulated['will'] = simulated.get('will', 0) + 1
                reason = 'Cleric level 3 special increment (+1 reflex, +1 will).'
            elif char_class == 'cleric' and cur_level == 3:
                simulated['fortitude'] = simulated.get('fortitude', 0) + 1
                reason = 'Cleric level 4 special increment (+1 fortitude).'
            else:
                reason = 'No special-step6 changes for this class/level; fallback to existing saved values.'

    # assemble message
    cur_saves = character.get('saves', {}) or {}
    lines = [f"Simulation for `{char_name}`: level {cur_level} -> {new_level} ({char_class}).",
             f"Current saves: {cur_saves}",
             f"Ability mods used: AGI={agi_mod}, STA={sta_mod}, PER={per_mod}",
             f"Computed saves for next level: {simulated}",
             f"Reason: {reason}"]
    await ctx.send("\n".join(lines))

# --- Run the bot ---
if __name__ == '__main__':
    print("🧪 Running DCC Bot...")
    if not TOKEN:
        print("❌ No token found! Check your token.env file.")
    else:
        bot.run(TOKEN)

# ---------------- Error Handling -----------------
@bot.event
async def on_command_error(ctx, error):
    from discord.ext import commands as _commands
    # Ignore command not found to reduce noise
    if isinstance(error, _commands.CommandNotFound):
        return
    if isinstance(error, _commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`")
        return
    if isinstance(error, _commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}")
        return
    if isinstance(error, _commands.CheckFailure):
        await ctx.send("🚫 You lack permission to use this command.")
        return
    if isinstance(error, _commands.CommandOnCooldown):
        await ctx.send(f"⏳ Cooldown active. Try again in {error.retry_after:.1f}s.")
        return
    # Unexpected error: log full stack and notify user
    logger.exception("Unhandled command error: %s", error)
    try:
        await ctx.send("⚠️ An unexpected error occurred. The incident was logged.")
    except Exception:
        pass
