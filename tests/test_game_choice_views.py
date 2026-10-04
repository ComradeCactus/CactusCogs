import asyncio
from types import SimpleNamespace

import discord

from casino.games import _BoundChoiceView


def test_choice_view_only_accepts_the_calling_user():
    class FakeResponse:
        def __init__(self):
            self.messages = []
            self.edits = []

        async def send_message(self, *args, **kwargs):
            self.messages.append((args, kwargs))

        async def edit_message(self, **kwargs):
            self.edits.append(kwargs)

    async def run_test():
        view = _BoundChoiceView(
            42,
            (("hit", "Hit", discord.ButtonStyle.primary),),
        )
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=FakeResponse(),
        )
        assert not await view.interaction_check(stranger)
        assert stranger.response.messages[0][1]["ephemeral"]
        assert view.choice is None

        player = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=FakeResponse(),
        )
        assert await view.interaction_check(player)
        await view.children[0].callback(player)
        assert view.choice == "hit"
        assert player.response.edits
        assert view.children[0].disabled

    asyncio.run(run_test())
