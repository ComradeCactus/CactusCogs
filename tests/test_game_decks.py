from casino.games import Blackjack, War


def test_each_blackjack_and_war_game_has_its_own_deck():
    first, second = Blackjack(), Blackjack()
    first.deck.deal(num=5)

    assert first.deck is not second.deck
    assert len(second.deck) == 0
    assert War(None).deck is not War(None).deck
