import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


def test_win_embed_formats_balances_above_32_bit_range():
    async def run_test():
        amount = 2**40 + 123
        ctx = SimpleNamespace(
            author=SimpleNamespace(name="player"),
            guild=SimpleNamespace(id=7),
        )
        engine = GameEngine("Blackjack", None, None, ctx, bet=amount)
        settings = {"Settings": {"Casino_Name": "Test"}}

        with patch(
            "casino.engine.bank.get_balance",
            new=AsyncMock(return_value=amount),
        ):
            with patch(
                "casino.engine.bank.get_currency_name",
                new=AsyncMock(return_value="credits"),
            ):
                embed = await engine.build_embed(
                    "Blackjack result",
                    settings,
                    True,
                    total=amount,
                    bonus="(+0)",
                )

        assert f"{amount:,}" in embed.fields[-1].value
        assert f"{amount:,} credits" in embed.fields[-1].value

    asyncio.run(run_test())
