import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from casino.data import Database
from casino.engine import GameEngine


def test_invalid_bet_message_uses_configured_range():
    class FakeContext:
        def __init__(self):
            self.author = SimpleNamespace(id=42)
            self.guild = SimpleNamespace(id=7)
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

    settings = {
        "Memberships": {},
        "Settings": {"Casino_Open": True},
        "Games": {
            "Coin": {"Open": True, "Access": 0, "Min": 12, "Max": 34},
        },
    }
    player_data = {"Membership": {"Name": "Basic"}}

    async def get_all(self, ctx, player):
        return settings, player_data

    async def run_test():
        ctx = FakeContext()
        engine = GameEngine("Coin", None, None, ctx, bet=11)
        with patch.object(Database, "get_all", get_all):
            assert not await engine.check_conditions()
        assert ctx.messages == ["Your bet must be between 12 and 34."]

    asyncio.run(run_test())
