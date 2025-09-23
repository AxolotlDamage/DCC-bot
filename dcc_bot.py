import discord
from discord.ext import commands
import random
import os
from dotenv import load_dotenv

# Load token from custom .env file
load_dotenv(dotenv_path='token.env')
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Auguries ---
AUGURIES = [
    ("Harsh winter", "All attack rolls"),
    ("The bull", "Melee attack rolls"),
    ("Fortunate date", "Missile fire attack rolls"),
    ("Raised by wolves", "Unarmed attack rolls"),
    ("Conceived on horseback", "Mounted attack rolls"),
    ("Born on the battlefield", "Damage rolls"),
    ("Path of the bear", "Melee damage rolls"),
    ("Hawkeye", "Missile fire damage rolls"),
    ("Pack hunter", "Attack and damage rolls for 0-level starting weapon"),
    ("Born under the loom", "Skill checks (including thief skills)"),
    ("Fox's cunning", "Find/disable traps"),
    ("Four-leafed clover", "Find secret doors"),
    ("Seventh son", "Spell checks"),
    ("The raging storm", "Spell damage"),
    ("Righteous heart", "Turn unholy checks"),
    ("Survived the plague", "Magical healing"),
    ("Lucky sign", "Saving throws"),
    ("Guardian angel", "Saves vs traps"),
    ("Survived a spider bite", "Saves vs poison"),
    ("Struck by lightning", "Reflex saving throws"),
    ("Lived through famine", "Fortitude saving throws"),
    ("Resisted temptation", "Willpower saving throws"),
    ("Charmed house", "Armor Class"),
    ("Speed of the cobra", "Initiative"),
    ("Bountiful harvest", "Hit points (each level)"),
    ("Warrior’s arm", "Critical hit tables"),
    ("Unholy house", "Corruption rolls"),
    ("The Broken Star", "Fumbles"),
    ("Birdsong", "Number of languages"),
    ("Wild child", "Speed (+5/-5 ft per +1/-1)")
]

# --- Occupations (shortened for clarity) ---
OCCUPATIONS = [
    {"name": "Alchemist", "weapon": "staff (1d4)", "goods": "oil, 1 flask"},
    {"name": "Blacksmith", "weapon": "hammer (1d4)", "goods": "steel tongs"},
    {"name": "Farmer", "weapon": "pitchfork (1d4)", "goods": "hen"},
    {"name": "Grave digger", "weapon": "shovel (1d4)", "goods": "sack"},
    {"name": "Halfling chicken butcher", "weapon": "cleaver (1d6)", "goods": "chicken"},
    {"name": "Wizard's apprentice", "weapon": "dagger (1d4)", "goods": "spellbook"},
    # Add more occupations here...
]

# --- Character Creation Command ---
@bot.command(name='create')
async def create_character(ctx):
    try:
        # Pick a random augury
        sign, effect = random.choice(AUGURIES)

        # Pick a random occupation
        occupation = random.choice(OCCUPATIONS)

        # Create the message
        response = (
            f"**🧙 New Level 0 Character Created!**\n"
            f"**Occupation**: {occupation['name']}\n"
            f"**Weapon**: {occupation['weapon']}\n"
            f"**Trade Goods**: {occupation['goods']}\n"
            f"**Birth Augur**: {sign} — *{effect}*"
        )

        await ctx.send(response)

    except Exception as e:
        await ctx.send(f"⚠️ Error generating character: `{e}`")
        print(f"Error: {e}")

# --- Run the bot ---
if __name__ == '__main__':
    print("🧪 Running DCC Bot...")
    if not TOKEN:
        print("❌ No token found! Check your token.env file.")
    else:
        bot.run(TOKEN)
