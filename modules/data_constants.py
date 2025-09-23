# Core game data constants extracted from monolith
# NOTE: Keep pure data only (no side effects) so imports are cheap.

ALIGNMENTS = ["Lawful", "Neutral", "Chaotic"]

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

ANIMALS = ["sheep", "goat", "cow", "duck", "goose", "mule"]

OCCUPATIONS = [
    {"name": "Alchemist", "weapon": "staff", "goods": "oil, 1 flask"},
    {"name": "Animal Trainer", "weapon": "club", "goods": "pony"},
    # (Truncated for brevity: retain or externalize full list as needed)
]

WEAPON_TABLE = {
    "staff": {"damage": "1d4", "type": "melee", "two_handed": False},
    "club": {"damage": "1d4", "type": "melee", "two_handed": False},
    "hammer": {"damage": "1d4", "type": "melee", "two_handed": False},
    "razor": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "cleaver": {"damage": "1d6", "type": "melee", "two_handed": False},
    "short sword": {"damage": "1d6", "type": "melee", "two_handed": False},
    "cudgel": {"damage": "1d4", "type": "melee", "two_handed": False},
    "awl": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "crowbar": {"damage": "1d4", "type": "melee", "two_handed": False},
    "knife": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "shovel": {"damage": "1d4", "type": "melee", "two_handed": True},
    "chisel": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "pick": {"damage": "1d4", "type": "melee", "two_handed": False},
    "quill": {"damage": "1d4", "type": "ranged", "two_handed": False},
    "scissors": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "bow": {"damage": "1d6", "type": "ranged", "two_handed": True},
    "pitchfork": {"damage": "1d8", "type": "melee", "two_handed": True},
    "trowel": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "sling": {"damage": "1d4", "type": "ranged", "two_handed": False},
    "hand axe": {"damage": "1d6", "type": "melee", "two_handed": False},
    "shortbow": {"damage": "1d6", "type": "ranged", "two_handed": True},
    "dart": {"damage": "1d4", "type": "ranged", "two_handed": False},
    "longsword": {"damage": "1d8", "type": "melee", "two_handed": False},
    "mace": {"damage": "1d4", "type": "melee", "two_handed": False},
    "stick": {"damage": "1d4", "type": "melee", "two_handed": False},
    "spear": {"damage": "1d8", "type": "melee", "two_handed": True, "tags": ["mounted"]},
    "battleaxe": {"damage": "1d10", "type": "melee", "two_handed": True},
    "blackjack": {"damage": "1d3", "type": "melee", "two_handed": False, "tags": ["backstab"]},
    "blowgun": {"damage": "1d3", "type": "ranged", "two_handed": False, "tags": ["backstab"]},
    "crossbow": {"damage": "1d6", "type": "ranged", "two_handed": True},
    "garrote": {"damage": "1", "type": "melee", "two_handed": True, "tags": ["backstab"]},
    "lance": {"damage": "1d12", "type": "melee", "two_handed": True, "tags": ["mounted"]},
    "javelin": {"damage": "1d6", "type": "ranged", "two_handed": False},
    "polearm": {"damage": "1d10", "type": "melee", "two_handed": True},
    "two handed sword": {"damage": "1d10", "type": "melee", "two_handed": True},
    "warhammer": {"damage": "1d8", "type": "melee", "two_handed": False},
    "longbow": {"damage": "1d6", "type": "ranged", "two_handed": True},
    "dagger": {"damage": "1d4", "type": "melee", "two_handed": False, "tags": ["backstab"]}
}

ARMOR_TABLE = {
    "unarmored": {"ac_bonus": 0, "fumble_die": "d4"},
    "padded": {"ac_bonus": 1, "fumble_die": "d8"},
    "leather": {"ac_bonus": 2, "fumble_die": "d8"},
    "studded leather": {"ac_bonus": 3, "fumble_die": "d8"},
    "hide": {"ac_bonus": 3, "fumble_die": "d12"},
    "scale mail": {"ac_bonus": 4, "fumble_die": "d12"},
    "chainmail": {"ac_bonus": 5, "fumble_die": "d12"},
    "banded mail": {"ac_bonus": 6, "fumble_die": "d16"},
    "half-plate": {"ac_bonus": 7, "fumble_die": "d16"},
    "full plate": {"ac_bonus": 8, "fumble_die": "d16"},
    "shield": {"ac_bonus": 1, "fumble_die": None}
}

EQUIPMENT_TABLE = [
    "Backpack", "Candle", "Chain, 10’", "Chalk, 1 piece", "Chest, empty", "Crowbar",
    "Flask, empty", "Flint & steel", "Grappling hook", "Hammer, small", "Holy symbol",
    "Holy water, 1 vial", "Iron spikes", "Lantern", "Mirror, hand-sized", "Oil, 1 flask",
    "Pole, 10-foot", "Rations, 1 day", "Rope, 50’", "Sack, large", "Sack, small",
    "Thieves’ tools", "Torch", "Waterskin"
]

# Language tables (subset – fill out fully as needed)
HALFLING_LANGUAGE_TABLE = { range(1, 26): "by_alignment", }
ELF_LANGUAGE_TABLE = { range(1, 21): "by_alignment", }
DWARF_LANGUAGE_TABLE = { range(1, 21): "by_alignment", }
LV0_LANGUAGE_TABLE = { range(1, 21): "by_alignment", }
WIZARD_LANGUAGE_TABLE = { range(1, 11): "by_alignment", }

__all__ = [
    'ALIGNMENTS','AUGURIES','ANIMALS','OCCUPATIONS','WEAPON_TABLE','ARMOR_TABLE','EQUIPMENT_TABLE',
    'HALFLING_LANGUAGE_TABLE','ELF_LANGUAGE_TABLE','DWARF_LANGUAGE_TABLE','LV0_LANGUAGE_TABLE','WIZARD_LANGUAGE_TABLE'
]
