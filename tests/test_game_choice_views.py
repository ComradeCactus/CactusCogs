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
        assert stranger.response.messages[0][0][0] == (
            "Only the player can interact with this game."
        )
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


def test_eunuch_votes_update_private_player_advice_without_changing_choice():
    class FakeResponse:
        def __init__(self):
            self.messages = []
            self.edits = []
            self.deferred = False

        async def send_message(self, *args, **kwargs):
            self.messages.append((args, kwargs))

        async def edit_message(self, **kwargs):
            self.edits.append(kwargs)

        async def defer(self, **kwargs):
            self.deferred = True

    class FakeMessage:
        def __init__(self, content):
            self.content = content
            self.delete_delay = None
            self.edits = []

        async def edit(self, **kwargs):
            self.edits.append(kwargs)
            self.content = kwargs["content"]

        async def delete(self, delay=None):
            self.delete_delay = delay

    class FakeFollowup:
        def __init__(self):
            self.messages = []

        async def send(self, content, **kwargs):
            message = FakeMessage(content)
            self.messages.append((message, kwargs))
            return message

    def interaction(user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=FakeResponse(),
            followup=FakeFollowup(),
        )

    async def run_test():
        view = _BoundChoiceView(
            42,
            (
                ("hit", "Hit", discord.ButtonStyle.primary),
                ("stay", "Stay", discord.ButtonStyle.secondary),
            ),
            eunuch_ids=(7, 8),
        )
        assert all(button.label != "Eunuch Advice" for button in view.children)
        hit_button, stay_button = view.children[:2]

        player = interaction(42)
        await view._send_player_advice(player)
        advice_message, advice_kwargs = player.followup.messages[0]
        assert advice_kwargs["ephemeral"]
        assert advice_message.delete_delay == 60
        assert advice_message.content == (
            "Your eunuchs have offered you the following advice... No votes yet."
        )

        first_eunuch = interaction(7)
        await hit_button.callback(first_eunuch)
        assert first_eunuch.response.messages[0][0][0] == (
            "Your selection has been sent: Hit"
        )
        assert first_eunuch.response.messages[0][1]["ephemeral"]
        assert first_eunuch.response.messages[0][1]["delete_after"] == 60
        assert advice_message.content.endswith("Hit (<@7>)")

        await stay_button.callback(first_eunuch)
        second_eunuch = interaction(8)
        await stay_button.callback(second_eunuch)
        assert advice_message.content.endswith("Stay (<@7>), Stay (<@8>)")
        assert view.choice is None

        await hit_button.callback(player)
        assert view.choice == "hit"
        assert all(item.disabled for item in view.children)

    asyncio.run(run_test())
    asyncio.run(run_test())

def test_player_advice_follows_the_public_game_prompt():
    class ImmediateView(_BoundChoiceView):
        async def wait(self):
            return True

    class FakeMessage:
        async def delete(self, delay=None):
            pass

    class FakeFollowup:
        async def send(self, content, **kwargs):
            events.append(("advice", kwargs["ephemeral"]))
            return FakeMessage()

    class FakeContext:
        author = SimpleNamespace(mention="<@42>")

        def __init__(self):
            self.interaction = SimpleNamespace(followup=FakeFollowup())

        async def send(self, **kwargs):
            events.append("game")
            return FakeMessage()

    async def run_test():
        view = ImmediateView(
            42,
            (("hit", "Hit", discord.ButtonStyle.primary),),
            eunuch_ids=(7,),
        )
        result = await view.prompt(FakeContext(), discord.Embed())
        assert result[0] is None
        assert events == ["game", ("advice", True)]

    events = []
    asyncio.run(run_test())


def test_scheming_eunuch_user_commands_are_hybrid_subcommands():
    from redbot.core import commands

    from casino.casino import Casino

    users = Casino.casino_users
    assert isinstance(users, commands.HybridGroup)
    assert {command.name for command in users.commands} == {"add", "remove", "list"}
    assert {
        command.name for command in users.app_command.commands
    } == {"add", "remove", "list"}
