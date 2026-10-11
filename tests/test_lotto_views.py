import asyncio
from types import SimpleNamespace

from casino.casino import _LottoTicketsView


def test_lotto_ticket_view_pages_tickets_and_dismisses():
    class FakeResponse:
        def __init__(self):
            self.edits = []
            self.messages = []
            self.deferred = False

        async def edit_message(self, **kwargs):
            self.edits.append(kwargs)

        async def send_message(self, *args, **kwargs):
            self.messages.append((args, kwargs))

        async def defer(self):
            self.deferred = True

    class FakeMessage:
        def __init__(self):
            self.deleted = False

        async def delete(self):
            self.deleted = True

    def make_interaction(user_id):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=FakeResponse(),
            message=FakeMessage(),
        )

    async def run_test():
        tickets = [
            {
                "draw_date": "2026-10-10",
                "numbers": [number, 1, 2, 3, 4, 5],
            }
            for number in range(11)
        ]
        view = _LottoTicketsView(42, tickets)
        first_page = view.embed()

        assert "11" in first_page.title
        assert len(first_page.description.splitlines()) == 10
        assert "00 01 02 03 04 05" in first_page.description
        assert view.next_button.disabled is False
        assert view.previous_button.disabled is True

        interaction = make_interaction(42)
        await view.next_button.callback(interaction)
        second_page = interaction.response.edits[0]["embed"]
        assert len(second_page.description.splitlines()) == 1
        assert "10 01 02 03 04 05" in second_page.description
        assert "Page 2 of 2" in second_page.footer.text
        assert view.next_button.disabled is True
        assert view.previous_button.disabled is False

        stranger = make_interaction(7)
        assert not await view.interaction_check(stranger)
        assert stranger.response.messages[0][1]["ephemeral"]

        await view.dismiss(interaction)
        assert interaction.response.deferred
        assert interaction.message.deleted

    asyncio.run(run_test())
