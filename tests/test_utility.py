"""Tests for utility.py"""

from unittest.mock import MagicMock
import pytest
from cogs.utility import Utility


@pytest.mark.asyncio
async def test_ping_command():
    bot = MagicMock()
    cog = Utility(bot)

    class DummyCtx:
        """Simple dummy ctx that captures sent messages."""

        def __init__(self):
            self.sent = None

        async def send(self, msg):
            """Capture the message that is sent."""
            self.sent = msg

    ctx = DummyCtx()
    await cog._ping_impl(ctx)  # pylint: disable=protected-access
    assert ctx.sent == "Pong! ✅"
