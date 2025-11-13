import logging
import os
import json
from pathlib import Path
import asyncio

from dotenv import load_dotenv  # type: ignore

import discord
from discord.ext import commands

from core.config import SAVE_FOLDER  # type: ignore
from core.hooks import HOOKS  # type: ignore
from core.helpers import is_owner  # type: ignore
from modules import initiative  # type: ignore
from core.permissions import check_roll_permission  # type: ignore
from storage.backup import create_backup  # type: ignore

# Basic logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('dccbot')

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

BOT_PREFIX = '!'

class DCCBot(commands.Bot):
    """Minimal launcher bot.

    Responsibilities kept intentionally lean:
    - Discover & load cogs in ./cogs (single pass)
    - Register initiative system
    - Sync slash commands
    """
    def __init__(self):
        super().__init__(command_prefix=BOT_PREFIX, intents=INTENTS)

    async def setup_hook(self):
        cogs_dir = Path(__file__).parent / 'cogs'
        if cogs_dir.exists():
            for py in sorted(cogs_dir.glob('*.py')):
                if py.name.startswith('_'):
                    continue
                mod_name = f'cogs.{py.stem}'
                try:
                    # In discord.py 2.x load_extension is awaitable when the extension exposes async setup()
                    await self.load_extension(mod_name)  # type: ignore
                    logger.info('Loaded cog module %s', mod_name)
                except Exception as e:
                    logger.exception('Failed loading %s: %s', mod_name, e)
        # Register initiative system
        initiative.register(self)  # type: ignore
        # Fallback: register /spell inline if the spells cog failed to load
        try:
            has_spell = any(getattr(cmd, 'name', None) == 'spell' for cmd in self.tree.get_commands())
        except Exception:
            has_spell = False
        if not has_spell:
            logger.warning('Spells cog not detected; registering fallback /spell command')

            def _root_dir() -> str:
                return str(Path(__file__).parent)

            def _load_spells() -> dict:
                try:
                    path = Path(_root_dir()) / 'Spells.json'
                    with open(path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception:
                    return {}

            def _bucket_for_class(klass: str) -> str | None:
                k = (klass or '').strip().lower()
                if k in {'wizard', 'mage', 'elf'}:
                    return 'Wizard Spells'
                if k == 'cleric':
                    return 'Cleric Spells'
                return None

            def _get_spell_blob(spells: dict, bucket: str, level: int, name: str) -> dict:
                return (
                    spells.get('spells', {})
                          .get(bucket, {})
                          .get(f'level {int(level)}', {})
                          .get(name, {})
                    or {}
                )

            def _find_spell_matches(spells: dict, name: str, bucket: str | None = None, level: int | None = None):
                out = []
                name_l = str(name or '').strip().lower()
                if not name_l:
                    return out
                buckets = [bucket] if bucket else ['Wizard Spells', 'Cleric Spells']
                lvls = [level] if level in (1,2,3,4,5) else [1,2,3,4,5]
                for b in buckets:
                    for lv in lvls:
                        pool = (spells.get('spells', {}).get(b, {}).get(f'level {lv}', {}) or {})
                        for nm in pool.keys():
                            if str(nm).lower() == name_l:
                                out.append((b, lv, str(nm)))
                return out

            @self.tree.command(name='spell', description='Show a spell description by class and level (class/level optional)')
            async def spell_cmd(interaction: discord.Interaction, spell: str, klass: str | None = None, level: int | None = None):
                data = _load_spells()
                bucket = _bucket_for_class(klass or '') if klass else None
                matches = _find_spell_matches(data, spell, bucket=bucket, level=level if level in (1,2,3,4,5) else None)
                if not matches:
                    await interaction.response.send_message('Spell not found. Try specifying class and level.', ephemeral=True)
                    return
                uniq = []
                seen = set()
                for t in matches:
                    if t in seen:
                        continue
                    seen.add(t)
                    uniq.append(t)
                if len(uniq) > 1:
                    bullets = []
                    for b, lv, nm in uniq[:10]:
                        label = 'Wizard' if b.startswith('Wizard') else 'Cleric'
                        bullets.append(f'• {nm} — {label} L{lv}')
                    extra = '' if len(uniq) <= 10 else f"\n… and {len(uniq)-10} more"
                    await interaction.response.send_message(
                        'Multiple matches. Please specify class and/or level.\n' + "\n".join(bullets) + extra,
                        ephemeral=True,
                    )
                    return
                bkt, lv, nm = uniq[0]
                blob = _get_spell_blob(data, bkt, lv, nm)
                label = 'Wizard' if bkt.startswith('Wizard') else 'Cleric'
                emb = discord.Embed(title=nm, description=f'{label} • Level {lv}', color=discord.Color.blurple())
                def _txt(v):
                    if isinstance(v, dict):
                        return str(v.get('text') or '')
                    if isinstance(v, list):
                        return "\n\n".join(str(x) for x in v)
                    return str(v or '')
                rng = _txt(blob.get('range'))
                dur = _txt(blob.get('duration'))
                cst = _txt(blob.get('casting_time'))
                sav = _txt(blob.get('save'))
                dsc = _txt(blob.get('description'))
                if rng: emb.add_field(name='Range', value=rng[:1024], inline=True)
                if dur: emb.add_field(name='Duration', value=dur[:1024], inline=True)
                if cst: emb.add_field(name='Casting Time', value=cst[:1024], inline=True)
                if sav: emb.add_field(name='Save', value=sav[:1024], inline=True)
                if dsc:
                    if len(dsc) <= 2048:
                        emb.description = (emb.description or '') + ("\n\n" if emb.description else '') + dsc
                    else:
                        emb.add_field(name='Description', value=dsc[:1024], inline=False)
                flags = []
                if 'corruption' in blob: flags.append('Corruption')
                if 'misfire' in blob: flags.append('Misfire')
                if 'manifestation' in blob: flags.append('Manifestation')
                if flags:
                    emb.add_field(name='Tables', value=', '.join(flags), inline=False)
                try:
                    await interaction.response.send_message(embed=emb)
                except Exception:
                    txt = [f"{nm} — {label} L{lv}"]
                    if rng: txt.append(f"Range: {rng}")
                    if dur: txt.append(f"Duration: {dur}")
                    if cst: txt.append(f"Casting Time: {cst}")
                    if sav: txt.append(f"Save: {sav}")
                    if dsc: txt.append(''); txt.append(dsc)
                    await interaction.response.send_message("\n".join(txt))
        # Install a tree-wide interaction gate
        async def _interaction_gate(interaction: discord.Interaction) -> bool:
            # Only gate slash command invocations
            if interaction.type.name != 'application_command':
                return True
            # Identify the target command
            cmd = interaction.command
            name = getattr(cmd, 'name', None)
            if name == 'roll':
                ok, reason = check_roll_permission(interaction)
                if not ok:
                    try:
                        await interaction.response.send_message(reason or "Not allowed.", ephemeral=True)
                    except Exception:
                        pass
                    return False
            return True
        self.tree.interaction_check = _interaction_gate
        # Sync slash commands (prefer fast per-guild availability)
        try:
            guild_id = os.getenv('GUILD_ID')
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                # Avoid duplicates: keep commands guild-scoped only during dev
                # Clear global definitions and sync only to the guild for instant updates
                try:
                    self.tree.clear_commands(guild=None)
                except Exception:
                    pass
                # Copy global commands to the guild and sync for instant availability
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info('Synced %d guild app commands for %s', len(synced), guild_id)
                await HOOKS.emit('commands.synced', scope='guild', guild_id=int(guild_id), count=len(synced))
            else:
                # Try a global sync (may take time to propagate), then also sync per-guild for immediacy
                try:
                    synced = await self.tree.sync()
                    logger.info('Synced %d global app commands', len(synced))
                    await HOOKS.emit('commands.synced', scope='global', count=len(synced))
                except Exception:
                    logger.warning('Global sync failed; proceeding with per-guild sync')
                # Perform per-guild sync for fast availability
                if self.guilds:
                    total = 0
                    for g in list(self.guilds):
                        try:
                            # copy ensures any global commands are present in guild scope
                            self.tree.copy_global_to(guild=g)
                        except Exception:
                            pass
                        try:
                            s = await self.tree.sync(guild=g)
                            total += len(s)
                        except Exception:
                            continue
                    logger.info('Per-guild synced app commands to %d guild(s)', len(self.guilds))
        except Exception:
            logger.exception('Failed syncing app commands')
        logger.info('Setup complete.')
        await HOOKS.emit('bot.ready')
        # Nightly backup stub
        if os.getenv('NIGHTLY_BACKUP_ENABLED', '0') == '1':
            self.loop.create_task(self._nightly_backup_task())

    async def _nightly_backup_task(self):
        """Simple nightly backup loop. Reads HH:MM UTC from NIGHTLY_BACKUP_UTC (default 03:00)."""
        from datetime import datetime, timedelta, timezone
        from storage.backup import create_backup
        target_str = os.getenv('NIGHTLY_BACKUP_UTC', '03:00')
        try:
            hh, mm = [int(x) for x in target_str.split(':', 1)]
        except Exception:
            hh, mm = 3, 0
        while True:
            now = datetime.now(timezone.utc)
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                path, count = create_backup()
                logger.info('Nightly backup created: %s (%d files)', path, count)
            except Exception as e:
                logger.exception('Nightly backup failed: %s', e)

bot = DCCBot()

@bot.check
async def global_owner_check(ctx: commands.Context):
    try:
        # Allow certain diagnostic/admin commands for non-owner (spellsync still checks admin perms)
        allow = {"debugapp", "spellsync", "help", "ping"}
        if ctx.command and ctx.command.name in allow:
            return True
    except Exception:
        pass
    return await is_owner(ctx)

@bot.event
async def on_ready():
    logger.info('Logged in as %s (%s)', bot.user, bot.user and bot.user.id)
    logger.info('Data folder: %s', SAVE_FOLDER)
    try:
        spell_present = any(getattr(cmd, 'name', None) == 'spell' for cmd in bot.tree.get_commands())
        logger.info('App command /spell present: %s', spell_present)
        logger.info('Guilds seen: %s', [g.id for g in bot.guilds])
    except Exception:
        pass


# ---- Slash Commands ----
@bot.tree.command(name="ping", description="Ping the bot to check latency")
async def ping_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)


@bot.tree.command(name="help", description="Show available commands")
async def help_slash(interaction: discord.Interaction):
    try:
        # Basic help aggregator (slash + prefix subset)
        lines: list[str] = []
        # Slash commands (dedupe by name to avoid double listing)
        seen = set()
        for cmd in bot.tree.get_commands():
            if cmd.name in seen:
                continue
            seen.add(cmd.name)
            lines.append(f"/{cmd.name} - {cmd.description or 'No description'}")
        # A few prefix commands (public ones only)
        public_prefix = [c for c in bot.commands if not c.hidden and c.name in {"ping", "attack", "a", "create", "sheet"}]
        if public_prefix:
            lines.append("\nPrefix:")
            for c in public_prefix:
                lines.append(f"!{c.name} {c.signature}".rstrip())
        text = "\n".join(lines) or "No commands registered."
        await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)
    except Exception as e:
        logger.exception("/help failed: %s", e)
        # Always provide a minimal fallback so the user sees something
        await interaction.response.send_message("/ping, /roll — more coming soon.", ephemeral=True)


@bot.tree.command(name="sync", description="Admin: sync slash commands now")
@discord.app_commands.describe(
    scope="Sync scope: 'guild' for this server (fast) or 'global' for all (slower). Default uses GUILD_ID if set.",
    force="If true (guild only), clears stale guild commands before syncing."
)
async def sync_slash(interaction: discord.Interaction, scope: str | None = None, force: bool = False):
    # Defer immediately to keep the interaction token alive regardless of latency
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    # Only bot owner can use
    app_info = await bot.application_info()
    if interaction.user.id != app_info.owner.id:
        try:
            await interaction.followup.send("Not authorized.", ephemeral=True)
        except Exception:
            pass
        return
    try:
        chosen = (scope or '').strip().lower()
        guild_id = os.getenv('GUILD_ID')
        # Helper to summarize commands
        def _summarize(cmds: list[discord.app_commands.AppCommand]) -> str:
            try:
                names = ", ".join(sorted(c.name for c in cmds))
                if len(names) > 1500:
                    names = names[:1497] + '...'
                return names
            except Exception:
                return "(list unavailable)"

        if chosen == 'global' or (not chosen and not guild_id):
            synced = await bot.tree.sync()
            await interaction.followup.send(
                f"✅ Synced {len(synced)} global commands.\nNames: {_summarize(synced)}",
                ephemeral=True,
            )
            return

        # Guild path
        gid = int(guild_id) if guild_id else (interaction.guild and interaction.guild.id)
        if not gid:
            await interaction.followup.send("No guild found; provide scope='global' or set GUILD_ID.", ephemeral=True)
            return
        guild = discord.Object(id=int(gid))

        # Clear global commands to prevent duplicate global+guild entries
        try:
            bot.tree.clear_commands(guild=None)
        except Exception:
            pass
        # Optionally clear stale guild commands first
        if force:
            bot.tree.clear_commands(guild=guild)
        else:
            # default behavior: copy globals into guild before syncing
            bot.tree.copy_global_to(guild=guild)
        try:
            synced = await bot.tree.sync(guild=guild)
            await interaction.followup.send(
                f"✅ Synced {len(synced)} commands to guild {gid}.\nNames: {_summarize(synced)}",
                ephemeral=True,
            )
        except Exception as e:
            # Retry with clear+copy in case of name conflicts
            logger.warning("Guild sync failed (%s). Retrying with clear+copy.", e)
            bot.tree.clear_commands(guild=guild)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            await interaction.followup.send(
                f"⚠️ Initial sync failed; retried with clear+copy. Now synced {len(synced)} to guild {gid}.\nNames: {_summarize(synced)}",
                ephemeral=True,
            )
    except Exception as e:
        logger.exception("/sync failed: %s", e)
        try:
            await interaction.followup.send("Sync failed. Check logs.", ephemeral=True)
        except Exception:
            pass

@bot.tree.command(name="backup", description="Admin: create a characters backup now")
async def backup_slash(interaction: discord.Interaction):
    app_info = await bot.application_info()
    if interaction.user.id != app_info.owner.id:
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    try:
        path, count = create_backup()
        await interaction.response.send_message(f"✅ Backup created: `{path}` ({count} files)", ephemeral=True)
    except Exception as e:
        logger.exception("/backup failed: %s", e)
        await interaction.response.send_message("Backup failed. Check logs.", ephemeral=True)

# --- Slash: spellsync (admin) ---
@bot.tree.command(name="spellsync", description="Admin: force-sync app commands to this guild")
async def spellsync_slash(interaction: discord.Interaction):
    member = interaction.guild and interaction.guild.get_member(interaction.user.id)
    if not (member and member.guild_permissions.administrator):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    try:
        try:
            bot.tree.copy_global_to(guild=interaction.guild)
        except Exception:
            pass
        synced = await bot.tree.sync(guild=interaction.guild)
        names = ", ".join(sorted(c.name for c in synced))
        if len(names) > 1800:
            names = names[:1797] + '…'
        await interaction.response.send_message(f"✅ Synced {len(synced)} commands to guild {interaction.guild.id}.\nNames: {names}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Sync failed: {e}", ephemeral=True)

# --- Slash: debugapp ---
@bot.tree.command(name="debugapp", description="Debug: show app commands, guilds, and env hint")
async def debugapp_slash(interaction: discord.Interaction):
    try:
        cmds = bot.tree.get_commands()
        names = sorted(getattr(c, 'name', '?') for c in cmds)
        guilds = [g.id for g in bot.guilds]
        gid = os.getenv('GUILD_ID')
        text = "\n".join([
            f"Guilds: {guilds}",
            f"GUILD_ID env: {gid!r}",
            f"Commands ({len(names)}): {', '.join(names)}",
        ])
        await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"debugapp failed: {e}", ephemeral=True)

## (Removed redundant prefix ping command; use /ping instead.)


    @bot.tree.command(name="mychars", description="List characters attached to your profile (or a specified user, admin-only)")
    async def mychars_slash(interaction: discord.Interaction, user: discord.User | None = None):
        target = user or interaction.user
        # Only admins can list another user's characters
        if user is not None:
            member = interaction.guild and interaction.guild.get_member(interaction.user.id)
            if not (member and member.guild_permissions.administrator):
                await interaction.response.send_message("Only administrators can list characters for other users.", ephemeral=True)
                return
        try:
            files = [f for f in os.listdir(SAVE_FOLDER) if f.endswith('.json')]
        except Exception as e:
            await interaction.response.send_message(f"Could not read characters folder: {e}", ephemeral=True)
            return
        owned: list[tuple[str,int,int,int]] = []
        for fn in sorted(files):
            path = os.path.join(SAVE_FOLDER, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            if str(data.get('owner')) != str(target.id):
                continue
            name = data.get('name') or os.path.splitext(fn)[0]
            hp_cur = 0; hp_max = 0
            hp = data.get('hp')
            if isinstance(hp, dict):
                try:
                    hp_cur = int(hp.get('current', 0) or 0)
                    hp_max = int(hp.get('max', 0) or 0)
                except Exception:
                    pass
            elif isinstance(hp, int):
                hp_cur = hp_max = int(hp)
            try:
                ac = int(data.get('ac', 10) or 10)
            except Exception:
                ac = 10
            owned.append((str(name), hp_cur, hp_max, ac))
        if not owned:
            await interaction.response.send_message("No characters found.", ephemeral=True)
            return
        lines = [f"Characters for {target.mention if hasattr(target, 'mention') else target} ({len(owned)}):"]
        for name, hp_cur, hp_max, ac in owned:
            hp_txt = f"{hp_cur}/{hp_max}" if hp_max else str(hp_cur)
            lines.append(f"• {name} — HP {hp_txt}, AC {ac}")
        text = "\n".join(lines)
        await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)


    @bot.tree.command(name="deletechar", description="Delete a character you own (admins can delete any)")
    async def deletechar_slash(interaction: discord.Interaction, name: str):
        path = os.path.join(SAVE_FOLDER, f"{name}.json")
        if not os.path.exists(path):
            await interaction.response.send_message(f"Character '{name}' not found.", ephemeral=True)
            return
        # Permission: owner or admin
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
            await interaction.response.send_message(f"Deleted '{name}'.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Delete failed: {e}", ephemeral=True)

    # --- Prefix: spellsync (admin) ---
    @bot.command(name="spellsync", help="Admin: force-sync app commands to this guild")
    @commands.has_guild_permissions(administrator=True)
    async def spellsync_prefix(ctx: commands.Context):
        try:
            if not ctx.guild:
                await ctx.send("Run this in a server channel.")
                return
            # Ensure /spell fallback exists if cog failed
            try:
                has_spell = any(getattr(cmd, 'name', None) == 'spell' for cmd in bot.tree.get_commands())
            except Exception:
                has_spell = False
            if not has_spell:
                await ctx.send("Registering fallback /spell, then syncing…")
                # define a trivial no-op to trigger setup_hook fallback on next restart
            # Copy globals to guild and sync
            try:
                bot.tree.copy_global_to(guild=ctx.guild)
            except Exception:
                pass
            synced = await bot.tree.sync(guild=ctx.guild)
            names = ", ".join(sorted(c.name for c in synced))
            if len(names) > 1800:
                names = names[:1797] + '…'
            await ctx.send(f"✅ Synced {len(synced)} commands to guild {ctx.guild.id}.\nNames: {names}")
        except Exception as e:
            await ctx.send(f"Sync failed: {e}")

    # --- Prefix: debugapp (list commands + env) ---
    @bot.command(name="debugapp", help="Debug: show app commands, guilds, and env hint")
    async def debugapp_prefix(ctx: commands.Context):
        try:
            cmds = bot.tree.get_commands()
            names = sorted(getattr(c, 'name', '?') for c in cmds)
            guilds = [g.id for g in bot.guilds]
            gid = os.getenv('GUILD_ID')
            lines = [
                f"Guilds: {guilds}",
                f"GUILD_ID env: {gid!r}",
                f"Commands ({len(names)}): {', '.join(names)}",
            ]
            msg = "\n".join(lines)
            await ctx.send("```\n" + msg + "\n```")
        except Exception as e:
            await ctx.send(f"debugapp failed: {e}")

def main():
    # Load environment variables from token.env (if present) then standard .env fallback
    try:
        load_dotenv(dotenv_path='token.env')  # silent if file missing
    except Exception:
        pass
    token = os.getenv('DISCORD_TOKEN') or os.getenv('BOT_TOKEN')
    if not token:
        raise SystemExit('DISCORD_TOKEN environment variable not set')
    bot.run(token)

if __name__ == '__main__':
    main()
