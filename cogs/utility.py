"""Small utility commands."""

from discord.ext import commands


class Utility(commands.Cog):
    """Small utility commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    async def _ping_impl(self, ctx: commands.Context) -> None:
        await ctx.send("Pong! ✅")

    @commands.command()
    async def ping(self, ctx: commands.Context) -> None:
        """Checks that the bot is up."""
        await self._ping_impl(ctx)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
