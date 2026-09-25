"""Command checks."""

import os
from discord.ext import commands


def admin_only():
    """Only lets users listed in ADMIN_IDS run the command."""
    admin_ids = {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    return commands.check(lambda ctx: str(ctx.author.id) in admin_ids)
