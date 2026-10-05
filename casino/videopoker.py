from collections import Counter
import asyncio

import discord
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_number

from .deck import Deck
from .games import _EunuchAdvice

_ = Translator("Casino", __file__)

HAND_PAYOUTS = {
    "Royal Flush": 250,
    "Straight Flush": 50,
    "Four of a Kind": 25,
    "Full House": 9,
    "Flush": 6,
    "Straight": 4,
    "Three of a Kind": 3,
    "Two Pair": 2,
    "Jacks or Better": 1,
}

RANK_VALUES = {"Jack": 11, "Queen": 12, "King": 13, "Ace": 14}
RANK_LABELS = {"Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
SUIT_LABELS = {
    ":clubs:": "♣",
    ":diamonds:": "♦",
    ":hearts:": "♥",
    ":spades:": "♠",
}


def evaluate_hand(hand):
    values = [RANK_VALUES.get(rank, rank) for _, rank in hand]
    value_counts = Counter(values)
    distinct_values = set(values)
    flush = len({suit for suit, _ in hand}) == 1
    straight = len(distinct_values) == 5 and (
        max(values) - min(values) == 4 or distinct_values == {2, 3, 4, 5, 14}
    )

    if flush and distinct_values == {10, 11, 12, 13, 14}:
        return "Royal Flush", HAND_PAYOUTS["Royal Flush"]
    if flush and straight:
        return "Straight Flush", HAND_PAYOUTS["Straight Flush"]
    if 4 in value_counts.values():
        return "Four of a Kind", HAND_PAYOUTS["Four of a Kind"]
    if sorted(value_counts.values()) == [2, 3]:
        return "Full House", HAND_PAYOUTS["Full House"]
    if flush:
        return "Flush", HAND_PAYOUTS["Flush"]
    if straight:
        return "Straight", HAND_PAYOUTS["Straight"]
    if 3 in value_counts.values():
        return "Three of a Kind", HAND_PAYOUTS["Three of a Kind"]
    if list(value_counts.values()).count(2) == 2:
        return "Two Pair", HAND_PAYOUTS["Two Pair"]
    if list(value_counts.values()).count(2) == 1 and any(
        value >= 11 for value, count in value_counts.items() if count == 2
    ):
        return "Jacks or Better", HAND_PAYOUTS["Jacks or Better"]
    return "No winning hand", 0


def format_card(card):
    suit, rank = card
    return "{}{}".format(RANK_LABELS.get(rank, rank), SUIT_LABELS[suit])


class VideoPokerView(discord.ui.View):
    def __init__(self, owner_id, hand, bet, max_bet, deck, eunuch_ids=()):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.eunuch_ids = set(eunuch_ids)
        self.hand = list(hand)
        self.bet = bet
        self.max_bet = max_bet
        self.deck = deck
        self.held = set()
        self.result = None
        self.message = None
        self.finished = False
        self.card_buttons = []
        self.eunuch_holds = {}
        self.eunuch_draw_votes = set()
        self.vote_lock = asyncio.Lock()
        self.advice = _EunuchAdvice()

        for index, card in enumerate(self.hand):
            button = discord.ui.Button(
                label=format_card(card),
                style=discord.ButtonStyle.secondary,
                row=0,
            )
            button.callback = self._hold_callback(index)
            self.card_buttons.append(button)
            self.add_item(button)

        self.draw_button = discord.ui.Button(
            label=_("Draw"),
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.draw_button.callback = self._draw_callback
        self.add_item(self.draw_button)

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            and interaction.user.id not in self.eunuch_ids
        ):
            await interaction.response.send_message(
                _("Only the player can interact with this game."),
                ephemeral=True,
                delete_after=60,
            )
            return False
        return True

    def _advice_text(self):
        votes = []
        for user_id, held_cards in self.eunuch_holds.items():
            votes.extend(
                _("Hold {} (<@{}>)").format(format_card(self.hand[index]), user_id)
                for index in sorted(held_cards)
            )
        votes.extend(
            _("Draw (<@{}>)").format(user_id)
            for user_id in self.eunuch_draw_votes
        )
        advice = ", ".join(votes) if votes else _("No votes yet.")
        return _("Your eunuchs have offered you the following advice... {}").format(advice)

    async def start_player_advice(self, interaction):
        if self.eunuch_ids:
            await self.advice.start(interaction, self._advice_text())

    async def _update_player_advice(self):
        await self.advice.update(self._advice_text())

    def _hold_callback(self, index):
        async def callback(interaction):
            async with self.vote_lock:
                if self.finished:
                    await interaction.response.send_message(
                        _("This hand has already finished."),
                        ephemeral=True,
                        delete_after=60,
                    )
                    return
                user_id = interaction.user.id
                if user_id in self.eunuch_ids:
                    held_cards = self.eunuch_holds.setdefault(user_id, set())
                    card = format_card(self.hand[index])
                    if index in held_cards:
                        held_cards.remove(index)
                        selection = _("Stop recommending hold on {}").format(card)
                    else:
                        held_cards.add(index)
                        selection = _("Hold {}").format(card)
                    await self.advice.send_selection(interaction, selection)
                    await self._update_player_advice()
                    return

                if index in self.held:
                    self.held.remove(index)
                else:
                    self.held.add(index)
                self._update_card_buttons()
                await interaction.response.edit_message(
                    embed=self.hand_embed(), view=self
                )

        return callback

    async def _draw_callback(self, interaction):
        async with self.vote_lock:
            if self.finished:
                await interaction.response.send_message(
                    _("This hand has already finished."),
                    ephemeral=True,
                    delete_after=60,
                )
                return
            user_id = interaction.user.id
            if user_id in self.eunuch_ids:
                if user_id in self.eunuch_draw_votes:
                    self.eunuch_draw_votes.remove(user_id)
                    selection = _("Withdraw your draw recommendation")
                else:
                    self.eunuch_draw_votes.add(user_id)
                    selection = _("Draw")
                await self.advice.send_selection(interaction, selection)
                await self._update_player_advice()
                return

            self.finished = True
            for index in range(len(self.hand)):
                if index not in self.held:
                    self.hand[index] = self.deck.draw()

            hand_name, payout = evaluate_hand(self.hand)
            if hand_name == "Royal Flush" and self.bet == self.max_bet:
                payout = 800
            result_embed = self.result_embed(hand_name, payout)
            self.result = (payout > 0, self.bet * payout, result_embed, self.message)
            self._disable_items()
            await interaction.response.edit_message(embed=result_embed, view=self)
            self.stop()

    def _update_card_buttons(self):
        for index, button in enumerate(self.card_buttons):
            button.style = (
                discord.ButtonStyle.success
                if index in self.held
                else discord.ButtonStyle.secondary
            )

    def _disable_items(self):
        for item in self.children:
            item.disabled = True

    def hand_embed(self):
        cards = []
        for index, card in enumerate(self.hand):
            marker = _("HOLD") if index in self.held else _("Click to hold")
            cards.append("**{}** {} — {}".format(index + 1, format_card(card), marker))

        embed = discord.Embed(
            title=_("Video Poker | Jacks or Better"),
            description=_("Bet: **{}**\nSelect cards to hold, then press **Draw**.").format(
                humanize_number(self.bet)
            ),
            colour=0x287A52,
        )
        embed.add_field(name=_("Your hand"), value="\n".join(cards), inline=False)
        embed.add_field(
            name=_("Pay table (payout × bet)"),
            value="\n".join(
                "{} — **{}×{}**".format(
                    _(name),
                    multiplier,
                    _(" (800× at max bet)") if name == "Royal Flush" else "",
                )
                for name, multiplier in HAND_PAYOUTS.items()
            ),
            inline=False,
        )
        embed.set_footer(text=_("A pair of Jacks or better returns your bet."))
        return embed

    def result_embed(self, hand_name, payout):
        embed = discord.Embed(
            title=_("Video Poker | Jacks or Better"),
            description=_("Final hand: **{}**").format(_(hand_name)),
            colour=0x2ECC71 if payout else 0xE74C3C,
        )
        embed.add_field(
            name=_("Cards"),
            value="{}\n**{}**".format(
                "  ".join(format_card(card) for card in self.hand), _(hand_name)
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Payout"),
            value=_("{}× your bet").format(payout) if payout else _("No payout"),
            inline=False,
        )
        return embed

    async def on_timeout(self):
        self.finished = True
        self._disable_items()
        if self.message is not None:
            embed = self.hand_embed()
            embed.description = _("This hand expired. Refunding your bet...")
            await self.message.edit(embed=embed, view=self)


class VideoPoker:
    def __init__(self, max_bet, eunuch_ids=()):
        self.view = None
        self.max_bet = max_bet
        self.eunuch_ids = eunuch_ids

    async def play(self, ctx, bet):
        deck = Deck()
        hand = deck.deal(5)
        self.view = VideoPokerView(
            ctx.author.id,
            hand,
            bet,
            self.max_bet,
            deck,
            eunuch_ids=self.eunuch_ids,
        )
        await self.view.start_player_advice(getattr(ctx, "interaction", None))
        self.view.message = await ctx.send(embed=self.view.hand_embed(), view=self.view)

        if await self.view.wait():
            return None
        if self.view.result is None:
            raise RuntimeError("Video poker view ended without a hand result.")
        return self.view.result
