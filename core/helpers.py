import os, json
from typing import Any
from discord.ext import commands
from core.config import SAVE_FOLDER

async def is_owner(ctx: commands.Context, filename: str) -> bool:
    if not os.path.exists(filename):
        await ctx.send("❌ Character not found.")
        return False
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    if ctx.author.guild_permissions.administrator:
        return True
    if str(data.get("owner")) != str(ctx.author.id):
        await ctx.send("🚫 You do not own this character.")
        return False
    return True

__all__ = ["is_owner"]
