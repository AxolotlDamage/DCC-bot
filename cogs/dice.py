from __future__ import annotations
import re
import random
import json
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Tuple, Optional

from core import embeds
from core.hooks import HOOKS
import logging
logger = logging.getLogger('dccbot')

# DCC dice chain for potential future step-up/step-down features
DCC_CHAIN = [3,4,5,6,7,8,10,12,14,16,20,24,30]

ROLL_PATTERN = re.compile(
    r"^\s*([0-9]*)d(\d+)((?:[+-]d\d*)*)(?:(k|dl)([hl]?\d+|[hl]))?(?:([+-])\s*(\d+))?\s*$",
    re.IGNORECASE,
)
# Supports step-up / step-down tokens immediately after base die: +d / +d2 / -d / -d2
# Multiple sets separated by commas or spaces: "2d20+d k1+5, d7-1 3d6dl1 4d6+d2dl1"
SPLIT_PATTERN = re.compile(r"[\s,;]+")

# Explanation of notation implemented:
# NdXk1      keep highest 1 (classic 'k1')
# NdXkL1     keep lowest 1 (kL1) - optional variant
# NdXdrop1   drop lowest 1 (alias dropL1)
# NdXdropl2  drop lowest 2
# NdXdroph1  drop highest 1
# NdX+M / NdX-M modifier
# If just k or drop with h/l then count defaults to 1
# If only 'k' with a number assume keep highest that many

class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="roll", description="Roll dice; run blank for help")
    @app_commands.describe(
        expression="Dice expressions. Leave blank or '-' for help."
    )
    async def roll(self, interaction: discord.Interaction, expression: str = "-"):
        logger.info("/roll invoked by %s in guild %s", getattr(interaction.user, 'id', '?'), getattr(getattr(interaction, 'guild', None), 'id', None))
        expr_clean = (expression or "").strip()
        # If user asks for help or leaves blank, respond immediately and ephemeral
        if expr_clean in ("", "-", "help", "?"):
            await interaction.response.send_message(embed=self.help_embed(), ephemeral=True)
            return
        # Special: "/roll occupation" uses the 1-100 occupation table from occupations_full.json
        if expr_clean.lower() in ("occupation", "occ"):
            try:
                occ_path = Path(__file__).resolve().parents[1] / 'occupations_full.json'
                data = json.loads(occ_path.read_text(encoding='utf-8'))
                roll = random.randint(1, 100)
                entry = data.get(str(roll))
                if not entry:
                    raise KeyError(f"Missing key {roll} in occupations_full.json")
                name = entry.get('name', 'Unknown')
                weapon = entry.get('weapon', 'N/A')
                goods = entry.get('goods', 'N/A')
                desc = (
                    f"Rolled 1d100: **{roll}**\n"
                    f"Occupation: **{name}**\n"
                    f"Trained Weapon: {weapon}\n"
                    f"Trade Goods: {goods}"
                )
                await interaction.response.send_message(embed=embeds.info(desc, "Occupation Roll"), ephemeral=True)
            except Exception as e:
                logger.exception("/roll occupation failed: %s", e)
                msg = "Failed to roll occupation. Ensure occupations_full.json exists and is valid."
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            return
        parts = [p for p in SPLIT_PATTERN.split(expr_clean) if p]
        if not parts:
            await interaction.response.send_message(embed=self.help_embed(), ephemeral=True)
            return
        try:
            results = []
            total_sum = 0
            for part in parts:
                parsed = self.parse_expression(part)
                if not parsed:
                    results.append(f"`{part}` -> invalid")
                    continue
                count, sides, mode, mode_arg, sign, mod, original_sides = parsed
                roll_list = [random.randint(1, sides) for _ in range(count or 1)]
                original = roll_list.copy()
                kept, dropped = self.apply_mode(roll_list, mode, mode_arg)
                subtotal = sum(kept)
                if sign and mod:
                    subtotal = subtotal + (mod if sign == '+' else -mod)
                total_sum += subtotal
                segment = self.format_segment(part, original, kept, dropped, subtotal, sides, original_sides)
                results.append(segment)
            description = "\n".join(results)
            description += f"\n\nTotal Sum: **{total_sum}**"
            # embeds.info expects (desc, title); send immediate ephemeral for reliability
            try:
                await interaction.response.send_message(embed=embeds.info(description, "Roll Results"), ephemeral=True)
            except Exception as send_err:
                logger.warning("/roll send failed; falling back to plain text: %s", send_err)
                text = f"Roll Results\n\n{description}"
                if interaction.response.is_done():
                    await interaction.followup.send(text, ephemeral=True)
                else:
                    await interaction.response.send_message(text, ephemeral=True)
            await HOOKS.emit('dice.roll.completed', user_id=getattr(interaction.user, 'id', None), guild_id=getattr(getattr(interaction, 'guild', None), 'id', None), expression=expr_clean)
        except Exception as e:
            logger.exception("/roll failed: %s", e)
            if interaction.response.is_done():
                await interaction.followup.send("Sorry, the roll failed. Check your expression and try again.", ephemeral=True)
            else:
                await interaction.response.send_message("Sorry, the roll failed. Check your expression and try again.", ephemeral=True)
            await HOOKS.emit('dice.roll.failed', user_id=getattr(interaction.user, 'id', None), guild_id=getattr(getattr(interaction, 'guild', None), 'id', None), expression=expr_clean, error=str(e))

    @app_commands.command(name="rollping", description="Debug: check dice cog responsiveness")
    async def rollping(self, interaction: discord.Interaction):
        await interaction.response.send_message("Dice cog is active.", ephemeral=True)

    # --- Parsing and mechanics ---

    def parse_expression(self, text: str) -> Optional[Tuple[int, int, Optional[str], Optional[str], Optional[str], Optional[int], int]]:
        m = ROLL_PATTERN.match(text)
        if not m:
            return None
        count_raw, sides_raw, steps_cluster, mode_raw, mode_arg_raw, sign, mod_raw = m.groups()
        count = int(count_raw) if count_raw else 1
        sides = int(sides_raw)
        original_sides = sides
        # Apply step adjustments if base die in chain
        if steps_cluster and sides in DCC_CHAIN:
            step_tokens = re.findall(r"([+-])d(\d*)", steps_cluster)
            net_steps = 0
            for sign_tok, num_tok in step_tokens:
                magnitude = int(num_tok) if num_tok else 1
                net_steps += magnitude if sign_tok == '+' else -magnitude
            if net_steps != 0:
                idx = DCC_CHAIN.index(sides)
                new_idx = max(0, min(len(DCC_CHAIN)-1, idx + net_steps))
                sides = DCC_CHAIN[new_idx]
        mode = None
        mode_arg = None
        if mode_raw:
            mode = mode_raw.lower()  # 'k' or 'dl'
            if mode_arg_raw:
                mode_arg = mode_arg_raw.lower()
            else:
                # defaults: k -> highest 1, dl -> lowest 1
                mode_arg = 'h' if mode == 'k' else '1'
        # validate mode args
        if mode == 'k' and mode_arg:
            if mode_arg in ('h', 'l'):
                pass
            elif (mode_arg[0] in ('h', 'l')) and mode_arg[1:].isdigit():
                pass
            elif mode_arg.isdigit():
                pass
            else:
                return None
        if mode == 'dl' and mode_arg and not mode_arg.isdigit():
            # dl only accepts digits (drop lowest N)
            return None
        mod = int(mod_raw) if mod_raw else None
        return (count, sides, mode, mode_arg, sign, mod, original_sides)

    def apply_mode(self, rolls: List[int], mode: Optional[str], mode_arg: Optional[str]) -> Tuple[List[int], List[int]]:
        if not mode:
            return rolls, []
        # Interpret semantics
        sorted_pairs = sorted(enumerate(rolls), key=lambda x: x[1])  # ascending
        indices = [i for i,_ in sorted_pairs]
        values = [v for _,v in sorted_pairs]
        if mode == 'k':
            # keep highest unless mode_arg starts with 'l'
            if mode_arg and mode_arg.startswith('l'):
                n = 1
                tail = mode_arg[1:]
                if tail.isdigit():
                    n = int(tail)
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
            # drop lowest N dice (default 1)
            n = int(mode_arg) if (mode_arg and mode_arg.isdigit()) else 1
            if n >= len(rolls):  # avoid dropping all; keep at least one
                n = len(rolls) - 1
            drop_indices = indices[:n]
            drop_set = set(drop_indices)
            kept = [rolls[i] for i in range(len(rolls)) if i not in drop_set]
            dropped = [rolls[i] for i in range(len(rolls)) if i in drop_set]
            return kept, dropped
        return rolls, []

    def format_segment(self, expr: str, original: List[int], kept: List[int], dropped: List[int], subtotal: int, sides: int, original_sides: int) -> str:
        chain_note = ""
        if sides != original_sides:
            chain_note = f" (d{original_sides}→d{sides})"
        if not dropped:
            return f"`{expr}`{chain_note} -> {original} = {subtotal}"
        return f"`{expr}`{chain_note} -> {original} kept {kept} drop {dropped} = {subtotal}"

    def help_embed(self) -> discord.Embed:
        examples = [
            "3d6",
            "4d6dl1 (drop lowest)",
            "2d20k1+5 (keep highest 1, add 5)",
            "3d6+d (step up once along chain)",
            "d16-d2 (step down 2)",
            "4d8+d3dl1 (step to d20 then drop lowest)",
            "occupation (roll an occupation on the 1-100 table)",
        ]
        notation = (
            "Syntax: [N]d[S][step adjustments][mode][modifier]\n"
            "Step: +d / +d2 / -d / -d2 (chain d3>d4>d5>d6>d7>d8>d10>d12>d14>d16>d20>d24>d30)\n"
            "Mode: kN keep highest N | klN keep lowest N | dlN drop lowest N (dl defaults to 1)\n"
            "Modifier: +N or -N applied after keep/drop\n"
            "Multiple: separate expressions with space or comma."
        )
        # embeds.info(desc, title)
        e = embeds.info(notation + "\n\nExamples:\n" + "\n".join(examples), "Roll Help")
        return e

async def setup(bot: commands.Bot):
    cog = DiceCog(bot)
    await bot.add_cog(cog)
