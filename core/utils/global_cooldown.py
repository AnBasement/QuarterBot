"""Per-user command cooldown.

This check only enforces it; core/bot.py's on_command_error sends the reply.
"""

from discord.ext import commands


def setup_global_cooldown(bot, rate=1, per=5):
    """Allows each user `rate` commands per `per` seconds, across all commands."""
    cooldown = commands.CooldownMapping.from_cooldown(
        rate, per, commands.BucketType.user
    )

    @bot.check
    async def global_cooldown(ctx: commands.Context):
        bucket = cooldown.get_bucket(ctx.message)
        if bucket is None:
            return True
        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise commands.CommandOnCooldown(
                bucket, retry_after, commands.BucketType.user
            )
        return True
