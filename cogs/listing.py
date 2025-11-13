import os
import json
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore


# Slash command group: /list ...
list_group = app_commands.Group(name="list", description="List various things (characters, spells, rules, etc.)")


@list_group.command(name="characters", description="List your characters or another user's characters")
@app_commands.describe(user="User to list characters for (defaults to you)")
async def list_characters(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    # Scan character files owned by target
    names: List[str] = []
    try:
        for entry in os.scandir(SAVE_FOLDER):
            if not entry.is_file() or not entry.name.lower().endswith('.json'):
                continue
            try:
                with open(entry.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if str(data.get('owner')) == str(target.id):
                    nm = data.get('name') or os.path.splitext(entry.name)[0]
                    names.append(str(nm))
            except Exception:
                continue
    except FileNotFoundError:
        pass

    if not names:
        await interaction.response.send_message(f"No characters found for {target.mention}.", ephemeral=True)
        return

    names.sort(key=lambda s: s.lower())
    # Chunk output if too long for a single message
    header = f"Characters for {target.mention}:\n"
    body = "\n".join(f"- {n}" for n in names)
    text = header + body
    if len(text) <= 1900:
        await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)
    else:
        # Split into multiple messages
        await interaction.response.send_message(f"```\n{header}\n```", ephemeral=True)
        chunk = []
        size = 0
        for n in names:
            line = f"- {n}\n"
            if size + len(line) > 1900 and chunk:
                await interaction.followup.send("```\n" + ''.join(chunk) + "```", ephemeral=True)
                chunk = []
                size = 0
            chunk.append(line)
            size += len(line)
        if chunk:
            await interaction.followup.send("```\n" + ''.join(chunk) + "```", ephemeral=True)


@list_group.command(name="delete", description="Delete a character you own (admins can delete any)")
@app_commands.describe(name="Character name to delete")
async def delete_character_slash(interaction: discord.Interaction, name: str):
    # Only owner or guild admin can delete
    # Resolve file path by direct name or by scanning the folder for matching 'name'
    direct = os.path.join(SAVE_FOLDER, f"{name}.json")
    path = direct if os.path.exists(direct) else None
    if not path:
        # try case-insensitive match on the 'name' field
        try:
            for entry in os.scandir(SAVE_FOLDER):
                if not entry.is_file() or not entry.name.lower().endswith('.json'):
                    continue
                try:
                    with open(entry.path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if str(data.get('name','')).strip().lower() == name.strip().lower():
                        path = entry.path
                        break
                except Exception:
                    continue
        except FileNotFoundError:
            pass
    if not path or not os.path.exists(path):
        await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
        return

    # Permission check
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        await interaction.response.send_message(f"Could not read file: {e}", ephemeral=True)
        return
    member = interaction.guild and interaction.guild.get_member(interaction.user.id)
    is_admin = bool(member and member.guild_permissions.administrator)
    if not is_admin and str(data.get('owner')) != str(interaction.user.id):
        await interaction.response.send_message("You do not own this character.", ephemeral=True)
        return

    try:
        os.remove(path)
        await interaction.response.send_message(f"Deleted '{data.get('name', name)}'.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Delete failed: {e}", ephemeral=True)


class ListCog(commands.Cog):
    """Container cog for list-related utilities."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure commands are visible immediately by syncing to each guild once
        if self._synced:
            return
        try:
            if self.bot.guilds:
                for g in list(self.bot.guilds):
                    try:
                        await self.bot.tree.sync(guild=g)
                    except Exception:
                        continue
            else:
                # Fallback to global sync
                try:
                    await self.bot.tree.sync()
                except Exception:
                    pass
        finally:
            self._synced = True

    @app_commands.command(name="listsync", description="Admin: sync list commands to this guild now")
    async def listsync_slash(self, interaction: discord.Interaction):
        # Only allow admins to run
        member = interaction.guild and interaction.guild.get_member(interaction.user.id)
        if not (member and member.guild_permissions.administrator):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        try:
            if interaction.guild:
                # register group to this guild (safe if already present) and sync
                try:
                    interaction.client.tree.add_command(list_group, guild=interaction.guild)
                except Exception:
                    pass
                await interaction.client.tree.sync(guild=interaction.guild)
                await interaction.response.send_message("List commands synced to this guild.", ephemeral=True)
            else:
                await interaction.client.tree.sync()
                await interaction.response.send_message("List commands synced globally.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Sync failed: {e}", ephemeral=True)

    @commands.command(name="listsync", help="Admin: sync list commands to this guild")
    @commands.has_guild_permissions(administrator=True)
    async def listsync_prefix(self, ctx: commands.Context):
        try:
            if ctx.guild:
                try:
                    self.bot.tree.add_command(list_group, guild=ctx.guild)
                except Exception:
                    pass
                await self.bot.tree.sync(guild=ctx.guild)
                await ctx.send("List commands synced to this guild.")
            else:
                await self.bot.tree.sync()
                await ctx.send("List commands synced globally.")
        except Exception as e:
            await ctx.send(f"Sync failed: {e}")

    @commands.command(name="listchars", help="List your characters or another member's characters")
    async def listchars_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        names: List[str] = []
        try:
            for entry in os.scandir(SAVE_FOLDER):
                if not entry.is_file() or not entry.name.lower().endswith('.json'):
                    continue
                try:
                    with open(entry.path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if str(data.get('owner')) == str(target.id):
                        nm = data.get('name') or os.path.splitext(entry.name)[0]
                        names.append(str(nm))
                except Exception:
                    continue
        except FileNotFoundError:
            pass

        if not names:
            await ctx.send(f"No characters found for {target.mention}.")
            return
        names.sort(key=lambda s: s.lower())
        text = "\n".join(f"- {n}" for n in names)
        # send in code block, chunk if needed
        chunks = [text[i:i+1800] for i in range(0, len(text), 1800)]
        for i, ch in enumerate(chunks):
            if i == 0:
                await ctx.send(f"Characters for {target.mention}:\n```\n{ch}\n```")
            else:
                await ctx.send(f"```\n{ch}\n```")

    @commands.command(name="deletechar", help="Delete a character you own (admins can delete any)")
    @commands.guild_only()
    async def deletechar_prefix(self, ctx: commands.Context, *, name: str):
        # resolve path
        direct = os.path.join(SAVE_FOLDER, f"{name}.json")
        path = direct if os.path.exists(direct) else None
        if not path:
            try:
                for entry in os.scandir(SAVE_FOLDER):
                    if not entry.is_file() or not entry.name.lower().endswith('.json'):
                        continue
                    try:
                        with open(entry.path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if str(data.get('name','')).strip().lower() == name.strip().lower():
                            path = entry.path
                            break
                    except Exception:
                        continue
            except FileNotFoundError:
                pass
        if not path or not os.path.exists(path):
            await ctx.send(f"Character '{name}' not found.")
            return

        # permission: owner or admin
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            await ctx.send(f"Could not read file: {e}")
            return
        is_admin = bool(ctx.author.guild_permissions and ctx.author.guild_permissions.administrator)
        if not is_admin and str(data.get('owner')) != str(ctx.author.id):
            await ctx.send("You do not own this character.")
            return
        try:
            os.remove(path)
            await ctx.send(f"Deleted '{data.get('name', name)}'.")
        except Exception as e:
            await ctx.send(f"Delete failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ListCog(bot))
    # Register the /list group with its subcommands
    added_globally = False
    try:
        bot.tree.add_command(list_group)
        added_globally = True
    except Exception:
        # If already added (on reload), ignore
        pass
    # Also register per-guild and sync for instant availability
    try:
        if bot.guilds:
            for g in list(bot.guilds):
                try:
                    bot.tree.add_command(list_group, guild=g)
                except Exception:
                    pass
                try:
                    bot.tree.copy_global_to(guild=g)
                except Exception:
                    pass
                try:
                    await bot.tree.sync(guild=g)
                except Exception:
                    pass
        elif not added_globally:
            # Fallback to global sync if no guilds seen yet
            try:
                await bot.tree.sync()
            except Exception:
                pass
    except Exception:
        pass
