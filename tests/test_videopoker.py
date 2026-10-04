import asyncio
from types import SimpleNamespace

from casino.data import guild_defaults
from casino.videopoker import evaluate_hand
from casino.videopoker import VideoPokerView


def test_default_bet_range():
    assert guild_defaults["Games"]["Videopoker"]["Min"] == 10
    assert guild_defaults["Games"]["Videopoker"]["Max"] == 500


def cards(ranks, suit=None):
    suits = (":clubs:", ":diamonds:", ":hearts:", ":spades:", ":clubs:")
    return [
        (suit or suits[index], rank)
        for index, rank in enumerate(ranks)
    ]


def test_royal_flush():
    assert evaluate_hand(cards([10, "Jack", "Queen", "King", "Ace"], suit=":clubs:")) == (
        "Royal Flush",
        250,
    )


def test_royal_flush_pays_800_at_the_configured_max_bet():
    class FakeResponse:
        async def edit_message(self, **kwargs):
            pass

    class EmptyDeck:
        def draw(self):
            raise AssertionError("Every card should be held.")

    async def run_test():
        hand = cards([10, "Jack", "Queen", "King", "Ace"], suit=":clubs:")
        view = VideoPokerView(42, hand, 500, 500, EmptyDeck())
        interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=FakeResponse())
        for button in view.card_buttons:
            await button.callback(interaction)
        await view.draw_button.callback(interaction)
        assert view.result[:2] == (True, 400_000)

    asyncio.run(run_test())


def test_straight_flush():
    assert evaluate_hand(cards([5, 6, 7, 8, 9], suit=":clubs:")) == ("Straight Flush", 50)


def test_four_of_a_kind():
    hand = [(":clubs:", 8), (":diamonds:", 8), (":hearts:", 8), (":spades:", 8), (":clubs:", 3)]
    assert evaluate_hand(hand) == ("Four of a Kind", 25)


def test_full_house():
    assert evaluate_hand(cards([4, 4, 4, 9, 9])) == ("Full House", 9)


def test_flush():
    assert evaluate_hand(cards([2, 5, 8, 10, "King"], suit=":clubs:")) == ("Flush", 6)


def test_straight_including_ace_low():
    assert evaluate_hand(cards(["Ace", 2, 3, 4, 5])) == ("Straight", 4)


def test_three_of_a_kind():
    assert evaluate_hand(cards([7, 7, 7, 2, 10])) == ("Three of a Kind", 3)


def test_two_pair():
    assert evaluate_hand(cards([4, 4, 9, 9, "Ace"])) == ("Two Pair", 2)


def test_jacks_or_better_pair():
    assert evaluate_hand(cards(["Jack", "Jack", 2, 5, 8])) == ("Jacks or Better", 1)


def test_low_pair_does_not_pay():
    assert evaluate_hand(cards([10, 10, 2, 5, 8])) == ("No winning hand", 0)


def test_hold_buttons_update_the_embed_in_place():
    class FakeResponse:
        def __init__(self):
            self.edits = []

        async def edit_message(self, **kwargs):
            self.edits.append(kwargs)

    class FakeDeck:
        def draw(self):
            raise AssertionError("A held card should not be redrawn.")

    async def run_test():
        view = VideoPokerView(
            42,
            [(":clubs:", "Jack"), (":diamonds:", "Jack"), (":hearts:", 2),
             (":spades:", 5), (":clubs:", 8)],
            100,
            500,
            FakeDeck(),
        )
        interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=FakeResponse())
        await view.card_buttons[0].callback(interaction)
        assert 0 in view.held
        assert view.card_buttons[0].style.name == "success"
        assert "HOLD" in interaction.response.edits[0]["embed"].fields[0].value

    asyncio.run(run_test())


def test_draw_keeps_held_cards_and_returns_payout():
    class FakeResponse:
        async def edit_message(self, **kwargs):
            pass

        async def send_message(self, *args, **kwargs):
            pass

    class FakeDeck:
        def __init__(self):
            self.drawn = iter([(":hearts:", 2), (":spades:", 5), (":clubs:", 8)])

        def draw(self):
            return next(self.drawn)

    async def run_test():
        hand = [
            (":clubs:", "Jack"),
            (":diamonds:", "Jack"),
            (":hearts:", 2),
            (":spades:", 5),
            (":clubs:", 8),
        ]
        view = VideoPokerView(42, hand, 100, 500, FakeDeck())
        interaction = SimpleNamespace(user=SimpleNamespace(id=42), response=FakeResponse())
        for index in (0, 1):
            await view.card_buttons[index].callback(interaction)
        await view.draw_button.callback(interaction)

        assert view.hand[:2] == hand[:2]
        assert view.result[:2] == (True, 100)
        assert all(button.disabled for button in view.children)

    asyncio.run(run_test())
