import traceback
import logging
import discord
from discord.ext import commands
from discord import app_commands

from core import embeds  # type: ignore
from core.helpers import is_owner  # type: ignore

logger = logging.getLogger('errors')

TRANSIENT = (
    commands.CommandNotFound,
    commands.BadArgument,
    commands.MissingRequiredArgument,
)

class ErrorHandlerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        orig = getattr(error, 'original', error)
        if isinstance(orig, commands.CommandNotFound):
            return  # silent ignore
        if isinstance(orig, TRANSIENT):
            await ctx.reply(embed=embeds.error(str(orig)))
            return
        # Unexpected
        logger.exception('Unhandled command error: %s', orig)
        if await is_owner(ctx):
            tb = ''.join(traceback.format_exception(type(orig), orig, orig.__traceback__))
            await ctx.reply(embed=embeds.error('Unexpected error occurred.'), mention_author=False)
            # Owner DM attempt
            try:
                await ctx.author.send(f'Error in command {ctx.command}:```\n{tb[:1800]}\n```')
            except Exception:
                pass
        else:
            await ctx.reply(embed=embeds.error('An unexpected error occurred. The owner has been notified.'))

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        logger.info('Slash command completed: /%s by %s', command.name, interaction.user)

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        orig = getattr(error, 'original', error)
        if isinstance(orig, app_commands.CommandInvokeError):
            logger.exception('App command invoke error: %s', orig)
        # Reply or followup gracefully
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embeds.error(str(orig) or 'Error.'), ephemeral=True)
            else:
                await interaction.response.send_message(embed=embeds.error(str(orig) or 'Error.'), ephemeral=True)
        except Exception:
            pass

async def setup(bot: commands.Bot):
    await bot.add_cog(ErrorHandlerCog(bot))
