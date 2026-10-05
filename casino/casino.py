# Developed by Redjumpman for Redbot.

# Standard Library
import asyncio
import calendar
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Final, Literal
from operator import itemgetter


# Casino
from . import utils
from .data import Database
from .games import Core, Blackjack, Double, War
from .utils import is_input_unsupported
from .engine import GameEngine
from .lotto import (
    LottoStore,
    classify_tickets,
    draw_numbers,
    draw_at_date,
    next_draw_at,
    parse_draw_time,
    quick_pick,
    split_jackpot,
)
from .videopoker import VideoPoker

# Red
from redbot.core.i18n import Translator
from redbot.core import bank, commands, checks, data_manager
from redbot.core.errors import BalanceTooHigh
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import box, humanize_number
from redbot.core.utils.predicates import MessagePredicate

# Discord
import discord

# Third-Party Libraries
from tabulate import tabulate

__version__ = "2.4.12"
__author__ = "Redjumpman"

_ = Translator("Casino", __file__)

log = logging.getLogger("red.jumper-plugins.casino")
_SCHEMA_VERSION: Final[int] = 2
LOTTO_TICKET_PRICE: Final[int] = 50
LOTTO_UNORDERED_JACKPOT: Final[int] = 1_000_000
LOTTO_EXACT_JACKPOT: Final[int] = 10_000_000
LOTTO_THREE_ORDERED_PRIZE: Final[int] = 150_000
LOTTO_THREE_ANY_PRIZE: Final[int] = 50_000
LOTTO_TIER_LABELS: Final[dict] = {
    "exact": "the exact-order jackpot",
    "unordered": "the any-order jackpot",
    "three_ordered": "matching 3 in order",
    "three_any": "matching 3 numbers",
}


class Casino(Database, commands.Cog):
    __slots__ = ("bot", "cycle_task", "lotto_task", "lotto_store", "lotto_draw_tasks")

    def __init__(self, bot):
        self.bot = bot
        self.cycle_task = None
        self.lotto_task = None
        self.lotto_store = None
        self.lotto_draw_tasks = {}
        super().__init__()

    async def cog_load(self):
        self.lotto_store = LottoStore(data_manager.cog_data_path(self) / "lotto.json")
        self.cycle_task = asyncio.create_task(self.membership_updater())
        self.lotto_task = asyncio.create_task(self.lotto_scheduler())
        self.migration_task = asyncio.create_task(
            self.data_schema_migration(
                from_version=await self.config.schema_version(), to_version=_SCHEMA_VERSION
            )
        )

    async def red_delete_data_for_user(
        self, *, requester: Literal["discord", "owner", "user", "user_strict"], user_id: int
    ):
        await super().config.user_from_id(user_id).clear()
        all_users = await super().config.all_users()
        for other_user_id, user_data in all_users.items():
            eunuchs = user_data.get("Scheming_Eunuchs", [])
            if user_id in eunuchs:
                await self.config.user_from_id(int(other_user_id)).Scheming_Eunuchs.set(
                    [eunuch_id for eunuch_id in eunuchs if eunuch_id != user_id]
                )

        all_members = await super().config.all_members()
        async for guild_id, guild_data in AsyncIter(all_members.items(), steps=100):
            if user_id in guild_data:
                await super().config.member_from_ids(guild_id, user_id).clear()
            for other_user_id, member_data in guild_data.items():
                eunuchs = member_data.get("Scheming_Eunuchs", [])
                if user_id in eunuchs:
                    await self.config.member_from_ids(
                        int(guild_id), int(other_user_id)
                    ).Scheming_Eunuchs.set(
                        [eunuch_id for eunuch_id in eunuchs if eunuch_id != user_id]
                    )
        removed_tickets = await self.lotto_store.delete_user(user_id)
        for guild_id, tickets in removed_tickets.items():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            role_id = await self.config.guild_from_id(int(guild_id)).lotto_role()
            role = guild.get_role(role_id) if role_id else None
            await self._remove_expired_lotto_roles(guild, tickets, role)

    # --------------------------------------------------------------------------------------------------

    @commands.hybrid_command(
        description="Bet your full balance for a chance at a jackpot."
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        multiplier="The jackpot multiplier; must be at least 2."
    )
    async def allin(self, ctx: commands.Context, multiplier: int):
        """Bets all your currency for a chance to win big!

        The higher your multiplier the lower your odds of winning.
        """
        if multiplier < 2:
            return await ctx.send("Your multiplier must be 2 or higher.")

        bet = await bank.get_balance(ctx.author)
        await Core(self.old_message_cache).play_allin(ctx, bet, multiplier)

    @commands.hybrid_command(
        name="blackjack",
        aliases=["bj", "21"],
        description="Play a hand of blackjack.",
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def _blackjack(self, ctx, bet: int):
        """Play a game of blackjack.

        Blackjack supports doubling down, but not split.
        """
        await ctx.defer()
        eunuchs = await self._scheming_eunuchs(ctx)
        await Blackjack(eunuchs).play(ctx, bet)

    async def _scheming_eunuchs(self, ctx):
        player_data = await super().get_data(ctx, player=ctx.author)
        return await player_data.Scheming_Eunuchs()

    @commands.hybrid_command(description="Play a modified pass-line craps game.")
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def craps(self, ctx: commands.Context, bet: int):
        """Plays a modified version of craps

        The player wins 7x their bet on a come-out roll of 7.
        A comeout roll of 11 is an automatic win (standard mutlipliers apply).
        The player will lose on a comeout roll of 2, 3, or 12.
        Otherwise a point will be established. The player will keep
        rolling until they hit a 7 (and lose) or their point number.

        Every bet is considered a 'Pass Line' bet.
        """

        await Core(self.old_message_cache).play_craps(ctx, bet)

    @commands.hybrid_command(description="Bet on a coin flip.")
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits.",
        choice="Choose heads or tails.",
    )
    @discord.app_commands.choices(
        choice=[
            discord.app_commands.Choice(name="Heads", value="heads"),
            discord.app_commands.Choice(name="Tails", value="tails"),
        ]
    )
    async def coin(self, ctx: commands.Context, bet: int, choice: str):
        """Coin flip game with a 50/50 chance to win.

        Pick heads or tails and place your bet.
        """
        if choice.lower() not in ("heads", "tails", "h", "t"):
            return await ctx.send("You must bet heads or tails.")

        await Core(self.old_message_cache).play_coin(ctx, bet, choice)

    @commands.hybrid_command(description="Guess which cup hides the coin.")
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits.",
        cup="Choose cup 1, 2, or 3.",
    )
    @discord.app_commands.choices(
        cup=[
            discord.app_commands.Choice(name="Cup 1", value="1"),
            discord.app_commands.Choice(name="Cup 2", value="2"),
            discord.app_commands.Choice(name="Cup 3", value="3"),
        ]
    )
    async def cups(self, ctx: commands.Context, bet: int, cup: str):
        """Guess which cup of three is hiding the coin.

        Must pick 1, 2, or 3.
        """
        await Core(self.old_message_cache).play_cups(ctx, bet, cup)

    @commands.hybrid_command(description="Roll a pair of dice and bet on the outcome.")
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def dice(self, ctx: commands.Context, bet: int):
        """Roll a set of dice and win on 2, 7, 11, 12.

        Just place a bet. No need to pick a number.
        """
        await Core(self.old_message_cache).play_dice(ctx, bet)

    @commands.hybrid_command(
        aliases=["don", "x2"],
        description="Play Double or Nothing and cash out before losing.",
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def double(self, ctx: commands.Context, bet: int):
        """Play a game of Double Or Nothing.

        Continue to try to double your bet until
        you cash out or lose it all.
        """
        await ctx.defer()
        eunuchs = await self._scheming_eunuchs(ctx)
        await Double(eunuchs).play(ctx, bet)

    @commands.hybrid_command(
        aliases=["hl"],
        description="Bet whether the dice total is high, low, or seven.",
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits.",
        choice="Choose high, low, or 7.",
    )
    @discord.app_commands.choices(
        choice=[
            discord.app_commands.Choice(name="High", value="high"),
            discord.app_commands.Choice(name="Low", value="low"),
            discord.app_commands.Choice(name="7", value="7"),
        ]
    )
    async def hilo(self, ctx: commands.Context, bet: int, choice: str):
        """Pick high, low, or 7 in a dice rolling game.

        Acceptable choices are high, hi, low, lo, 7, or seven.
        """
        await Core(self.old_message_cache).play_hilo(ctx, bet, choice)

    @commands.hybrid_command(description="Play a modified game of War.")
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def war(self, ctx: commands.Context, bet: int):
        """Play a modified game of war."""
        await War(self.old_message_cache).play(ctx, bet)

    @commands.hybrid_command(
        name="videopoker",
        aliases=["vp"],
        description="Play a hand of Jacks or Better video poker.",
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        bet="Wager in credits. Use /casino info to see this game's current limits."
    )
    async def videopoker(self, ctx: commands.Context, bet: int):
        """Play Jacks or Better video poker.

        Hold cards, then draw replacements. Payouts are based on the 9/6 pay table.
        """
        await ctx.defer()
        engine = GameEngine("Videopoker", None, None, ctx, bet)
        if not await engine.check_conditions():
            return

        settings = await (await engine.get_data(ctx)).all()
        eunuchs = await self._scheming_eunuchs(ctx)
        game = VideoPoker(
            settings["Games"]["Videopoker"]["Max"],
            eunuch_ids=eunuchs,
        )
        result = await game.play(ctx, bet)
        if result is None:
            try:
                await bank.deposit_credits(ctx.author, bet)
            except BalanceTooHigh as exc:
                await bank.set_balance(ctx.author, exc.max_balance)
            balance = await bank.get_balance(ctx.author)
            currency = await bank.get_currency_name(ctx.guild)
            embed = discord.Embed(
                title=_("Video Poker | Hand expired"),
                description=_("Your bet of {} {} was refunded.").format(
                    humanize_number(bet), currency
                ),
                colour=0x666666,
            )
            embed.add_field(
                name=_("Remaining Balance"),
                value="{} {}".format(humanize_number(balance), currency),
            )
            await game.view.message.edit(embed=embed, view=None)
            return

        await engine.game_teardown(result, force_edit=True)

    @commands.hybrid_command(
        hidden=True,
        description="Owner-only test command for simulated blackjack hands.",
    )
    @commands.is_owner()
    @discord.app_commands.describe(
        bet="The test wager.",
        hands="Player and dealer hands separated by a vertical bar.",
    )
    async def bjmock(self, ctx, bet: int, *, hands: str):
        """Test function for blackjack

        This will mock the blackjack game, allowing you to insert a player hand
        and a dealer hand.

        Example: [p]bjmock 50 :clubs: 10, :diamonds: 10 | :clubs: Ace, :clubs: Queen
        """
        ph, dh = hands.split(" | ")
        ph = [(x[0], int(x[2:])) if x[2:].isdigit() else (x[0], x[2:]) for x in ph.split(", ")]
        dh = [(x[0], int(x[2:])) if x[2:].isdigit() else (x[0], x[2:]) for x in dh.split(", ")]
        await Blackjack().mock(ctx, bet, ph, dh)

    # --------------------------------------------------------------------------------------------------

    @commands.hybrid_group(
        description="View casino information, player statistics, and manage casino users.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def casino(self, ctx):
        """Interacts with the Casino system.

        Use help on Casino (upper case) for more commands.
        """
        pass

    @casino.group(
        name="users",
        description="Manage your designated scheming eunuchs.",
        invoke_without_command=True,
    )
    async def casino_users(self, ctx: commands.Context):
        """Manage the users who can vote on your in-game choices."""
        await ctx.send_help()

    @casino_users.command(
        name="add",
        description="Designate a user as one of your scheming eunuchs.",
    )
    @discord.app_commands.describe(
        user="The user to allow to vote on your Blackjack and Double choices."
    )
    async def add_eunuch(self, ctx: commands.Context, user: discord.User):
        if user.id == ctx.author.id:
            return await ctx.send(_("You cannot designate yourself as a scheming eunuch."))

        player_data = await super().get_data(ctx, player=ctx.author)
        eunuchs = await player_data.Scheming_Eunuchs()
        if user.id in eunuchs:
            return await ctx.send(_("{} is already one of your scheming eunuchs.").format(user))
        if len(eunuchs) >= 2:
            return await ctx.send(_("You can designate no more than two scheming eunuchs."))

        eunuchs.append(user.id)
        await player_data.Scheming_Eunuchs.set(eunuchs)
        await ctx.send(_("{} has been designated as one of your scheming eunuchs.").format(user))

    @casino_users.command(
        name="remove",
        description="Remove a user's scheming eunuch designation.",
    )
    @discord.app_commands.describe(
        user="The scheming eunuch to remove."
    )
    async def remove_eunuch(self, ctx: commands.Context, user: discord.User):
        player_data = await super().get_data(ctx, player=ctx.author)
        eunuchs = await player_data.Scheming_Eunuchs()
        if user.id not in eunuchs:
            return await ctx.send(_("{} is not one of your scheming eunuchs.").format(user))

        eunuchs.remove(user.id)
        await player_data.Scheming_Eunuchs.set(eunuchs)
        await ctx.send(_("{} is no longer one of your scheming eunuchs.").format(user))

    @casino_users.command(
        name="list",
        description="List your scheming eunuchs.",
    )
    async def list_eunuchs(self, ctx: commands.Context):
        player_data = await super().get_data(ctx, player=ctx.author)
        eunuchs = await player_data.Scheming_Eunuchs()
        if not eunuchs:
            return await ctx.send(_("You have not designated any scheming eunuchs."))
        await ctx.send(
            _("Your scheming eunuchs: {}").format(
                ", ".join("<@{}>".format(user_id) for user_id in eunuchs)
            )
        )

    @casino.command(description="View the available casino memberships.")
    async def memberships(self, ctx):
        """Displays a list of server/global memberships."""
        data = await super().get_data(ctx)
        settings = await data.all()
        memberships = list(settings["Memberships"].keys())

        if not memberships:
            return await ctx.send(_("There are no memberships to display."))

        await ctx.send(
            _("Which of the following memberships would you like to know more about?\n`{}`").format(
                utils.fmt_join(memberships)
            )
        )

        pred = MessagePredicate.contained_in(memberships, ctx=ctx)

        try:
            membership = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No Response."))

        games = settings["Games"]
        perks = settings["Memberships"][membership.content]
        playable = [x for x, y in games.items() if y["Access"] <= perks["Access"]]

        reqs = _("Credits: {Credits}\nRole: {Role}\nDays on Server: {DOS}").format(**perks)
        color = utils.color_lookup(perks["Color"])
        desc = _(
            "Access: {Access}\n"
            "Cooldown Reduction: {Reduction} seconds\n"
            "Bonus Multiplier: {Bonus}x\n"
            "Color: {Color}"
        ).format(**perks)

        info = _(
            "Memberships are automatically assigned to players when they meet it's "
            "requirements. If a player meets multiple membership requirements, they will be "
            "assigned the one with the highest access level. If a membership is assigned "
            "manually however, then the updater will skip that player until their membership "
            "has been revoked."
        )

        # Embed
        embed = discord.Embed(colour=color, description=desc)
        embed.title = membership.content
        embed.add_field(name=_("Playable Games"), value="\n".join(playable))
        embed.add_field(name=_("Requirements"), value=reqs)
        embed.set_footer(text=info)
        await ctx.send(embed=embed)

    @casino.command(description="Release a user's pending casino winnings.")
    @checks.admin_or_permissions(administrator=True)
    @discord.app_commands.describe(player="The user whose pending winnings to release.")
    async def releasecredits(self, ctx, player: discord.User):
        """Approves pending currency for a user.

        If this casino has maximum winnings threshold set, and a user makes a bet that
        exceeds this amount, then they will have those credits with held. This command will
        Allow you to release those credits back to the user. This system is designed to limit
        earnings when a player may have found a way to cheat a game.
        """

        player_data = await super().get_data(ctx, player=player)
        amount = await player_data.Pending_Credits()

        if amount <= 0:
            return await ctx.send(_("This user doesn't have any credits pending."))

        await ctx.send(
            _("{} has {} credits pending. Would you like to release this amount?").format(player.name, amount)
        )

        pred = MessagePredicate.yes_or_no(ctx=ctx)
        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No response. Action canceled."))

        if choice.content.lower() == "yes":
            try:
                await bank.deposit_credits(player, amount)
                await player_data.Pending_Credits.clear()
                await ctx.send(
                    _(
                        "{0.mention} Your pending amount of {1} has been approved by "
                        "{2.name}, and was deposited into your account."
                    ).format(player, amount, ctx.author)
                )
            except BalanceTooHigh as e:
                await ctx.send(
                    _(
                        "{0.mention} Your pending amount of {1} has been approved by "
                        "{2.name}, but could not be deposited because your balance is at "
                        "the maximum amount of credits."
                    ).format(player, amount, ctx.author)
                )
        else:
            await ctx.send(_("Action canceled."))

    @casino.command(description="Reset a user's casino cooldowns, statistics, or all data.")
    @checks.admin_or_permissions(administrator=True)
    @discord.app_commands.describe(user="The server member whose casino data to reset.")
    async def resetuser(self, ctx: commands.Context, user: discord.Member):
        """Reset a user's cooldowns, stats, or everything."""

        if await super().casino_is_global() and not await ctx.bot.is_owner(ctx.author):
            return await ctx.send(_("While the casino is in global mode, only the bot owner may use this command."))

        options = (_("cooldowns"), _("stats"), _("all"))
        await ctx.send(_("What would you like to reset?\n`{}`.").format(utils.fmt_join(options)))

        pred = MessagePredicate.lower_contained_in(options, ctx=ctx)
        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No response. Action canceled."))

        if choice.content.lower() == _("cooldowns"):
            await super()._reset_player_cooldowns(ctx, user)
        elif choice.content.lower() == _("stats"):
            await super()._reset_player_stats(ctx, user)
        else:
            await super()._reset_player_all(ctx, user)

    @casino.command(description="Reset casino settings, games, cooldowns, memberships, or all.")
    @checks.admin_or_permissions(administrator=True)
    async def resetinstance(self, ctx: commands.Context):
        """Reset global/server cooldowns, settings, memberships, or everything."""
        if await super().casino_is_global() and not await ctx.bot.is_owner(ctx.author):
            return await ctx.send(_("While the casino is in global mode, only the bot owner may use this command."))

        options = (_("settings"), _("games"), _("cooldowns"), _("memberships"), _("all"))
        await ctx.send(_("What would you like to reset?\n`{}`.").format(utils.fmt_join(options)))
        pred = MessagePredicate.lower_contained_in(options, ctx=ctx)
        await ctx.send(_("What would you like to reset?\n`{}`.").format(utils.fmt_join(options)))

        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No response. Action canceled."))

        if choice.content.lower() == _("cooldowns"):
            await super()._reset_cooldowns(ctx)
        elif choice.content.lower() == _("settings"):
            await super()._reset_settings(ctx)
        elif choice.content.lower() == _("games"):
            await super()._reset_games(ctx)
        elif choice.content.lower() == _("memberships"):
            await super()._reset_memberships(ctx)
        else:
            await super()._reset_all_settings(ctx)

    @casino.command(description="Wipe all casino and user data.")
    @checks.is_owner()
    async def wipe(self, ctx: commands.Context):
        """Completely wipes casino data."""
        await ctx.send(
            _(
                "You are about to delete all casino and user data from the bot. Are you "
                "sure this is what you wish to do?"
            )
        )

        pred = MessagePredicate.yes_or_no(ctx=ctx)
        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No Response. Action canceled."))

        if choice.content.lower() == "yes":
            tickets_by_guild = {
                guild.id: await self.lotto_store.tickets(guild.id)
                for guild in self.bot.guilds
            }
            drawing_tasks = list(self.lotto_draw_tasks.values())
            for task in drawing_tasks:
                task.cancel()
            if drawing_tasks:
                await asyncio.gather(*drawing_tasks, return_exceptions=True)
            self.lotto_draw_tasks.clear()
            await self.lotto_store.clear()
            for guild in self.bot.guilds:
                role_id = await self.config.guild(guild).lotto_role()
                role = guild.get_role(role_id) if role_id else None
                await self._remove_expired_lotto_roles(
                    guild, tickets_by_guild[guild.id], role
                )
            return await super()._wipe_casino(ctx)
        else:
            return await ctx.send(_("Wipe canceled."))

    @casino.command(description="Manually assign a casino membership to a user.")
    @checks.admin_or_permissions(administrator=True)
    @discord.app_commands.describe(
        player="The user to assign the membership to.",
        membership="The registered membership name to assign.",
    )
    async def assignmem(
        self,
        ctx: commands.Context,
        player: discord.User,
        *,
        membership: str,
    ):
        """Manually assigns a membership to a user.

        Users who are assigned a membership no longer need to meet the
        requirements set. However, if the membership is revoked, then the
        user will need to meet the requirements as usual.

        """
        settings = await super().get_data(ctx)
        memberships = await settings.Memberships.all()
        if membership not in memberships:
            return await ctx.send(_("{} is not a registered membership.").format(membership))

        player_instance = await super().get_data(ctx, player=player)
        await player_instance.Membership.set({"Name": membership, "Assigned": True})

        msg = _("{0.name} ({0.id}) manually assigned {1.name} ({1.id}) the {2} membership.").format(
            ctx.author, player, membership
        )
        await ctx.send(msg)

    @casino.command(description="Revoke a user's manually assigned casino membership.")
    @checks.admin_or_permissions(administrator=True)
    @discord.app_commands.describe(player="The user whose assigned membership to revoke.")
    async def revokemem(self, ctx: commands.Context, player: discord.User):
        """Revoke an assigned membership.

        Members will still keep this membership until the next auto cycle (5mins).
        At that time, they will be re-evaluated and downgraded/upgraded appropriately.
        """
        player_data = await super().get_data(ctx, player=player)

        if not await player_data.Membership.Assigned():
            return await ctx.send(_("{} has no assigned membership.").format(player.name))
        else:
            await player_data.Membership.set({"Name": "Basic", "Assigned": False})
        return await ctx.send(
            _(
                "{} has unassigned {}'s membership. They have been set "
                "to `Basic` until the next membership update cycle."
            ).format(ctx.author.name, player.name)
        )

    @casino.command(description="Show the casino administration command list.")
    @checks.admin_or_permissions(administrator=True)
    async def admin(self, ctx: commands.Context):
        """A list of Admin level and above commands for Casino."""
        cmd_list = []
        cmd_list2 = []
        for cmd in ctx.bot.get_command("casino").commands:
            if cmd.requires.privilege_level.name == "ADMIN":
                if await cmd.requires.verify(ctx):
                    cmd_list.append((cmd.qualified_name, cmd.short_doc))

        for cmd in ctx.bot.get_command("casinoset").commands:
            if await cmd.requires.verify(ctx):
                cmd_list2.append((cmd.qualified_name, cmd.short_doc))
        cmd_list = "\n".join(["**{}** - {}".format(x, y) for x, y in cmd_list])
        cmd_list2 = "\n".join(["**{}** - {}".format(x, y) for x, y in cmd_list2])
        wiki = "[Casino Wiki](https://github.com/Redjumpman/Jumper-Plugins/wiki/Casino-RedV3)"
        embed = discord.Embed(colour=0xFF0000, description=wiki)
        embed.set_author(name="Casino Admin Panel", icon_url=ctx.bot.user.display_avatar.url)
        embed.add_field(name="__Casino__", value=cmd_list)
        embed.add_field(name="__Casino Settings__", value=cmd_list2)
        embed.set_footer(text=_("With great power, comes great responsibility."))
        await ctx.send(embed=embed)

    async def _casino_info_text(self, ctx):
        instance = await super().get_data(ctx)
        settings = await instance.Settings.all()
        game_data = await instance.Games.all()

        t = sorted(
            [
                [x] + [b for a, b in sorted(y.items(), key=itemgetter(0)) if a != "Cooldown"]
                for x, y in game_data.items()
            ]
        )
        cool = [
            utils.cooldown_formatter(y["Cooldown"])
            for x, y in sorted(game_data.items(), key=itemgetter(0))
        ]
        table = [x + [y] for x, y in zip(t, cool)]

        headers = (_("Game"), _("Access"), _("Max"), _("Min"), _("Payout"), _("On"), _("CD"))
        t = tabulate(table, headers=headers)
        msg = _(
            "{}\n\n"
            "Casino Name: {Casino_Name} Casino\n"
            "Casino Open: {Casino_Open}\n"
            "Global: {Global}\n"
            "Payout Limit ON: {Payout_Switch}\n"
            "Payout Limit: {Payout_Limit}"
        ).format(t, **settings)
        return box(msg, lang="cpp")

    @casino.command(
        description="Show casino settings, games, and current betting limits."
    )
    async def info(self, ctx: commands.Context):
        """Shows information about Casino.

        Displays a list of games with their set parameters:
        Access Levels, Maximum and Minimum bets, if it's open to play,
        cooldowns, and multipliers. It also displays settings for the
        server (or global) if enabled.
        """
        await ctx.send(await self._casino_info_text(ctx))

    @commands.hybrid_command(
        name="lotto",
        description="View your lotto tickets or buy them for 50 credits each.",
    )
    @commands.guild_only()
    @discord.app_commands.describe(
        ticket_count="Number of 50-credit tickets to buy. Omit to view your tickets.",
        number1="Optional first number for one custom ticket (00-99).",
        number2="Optional second number for one custom ticket (00-99).",
        number3="Optional third number for one custom ticket (00-99).",
        number4="Optional fourth number for one custom ticket (00-99).",
        number5="Optional fifth number for one custom ticket (00-99).",
        number6="Optional sixth number for one custom ticket (00-99).",
    )
    async def lotto(
        self,
        ctx: commands.Context,
        ticket_count: Optional[int] = None,
        number1: Optional[int] = None,
        number2: Optional[int] = None,
        number3: Optional[int] = None,
        number4: Optional[int] = None,
        number5: Optional[int] = None,
        number6: Optional[int] = None,
    ):
        """Buy quick-pick tickets or view your active tickets.

        Provide all six numbers to buy one custom ticket. If ticket_count is
        greater than one, the remaining tickets will be quick picks.
        """
        await ctx.defer()
        number_fields = (number1, number2, number3, number4, number5, number6)
        numbers_provided = [number is not None for number in number_fields]

        if any(numbers_provided) and not all(numbers_provided):
            return await ctx.send(_("Provide all six custom numbers, or none."))

        custom_numbers = None
        if all(numbers_provided):
            custom_numbers = list(number_fields)
            if any(number < 0 or number > 99 for number in custom_numbers):
                return await ctx.send(_("Lotto numbers must be between 00 and 99."))
            if len(set(custom_numbers)) != 6:
                return await ctx.send(_("A ticket cannot contain duplicate numbers."))

        if ticket_count is None and custom_numbers is None:
            return await self._send_lotto_tickets(ctx)
        if ticket_count is None:
            ticket_count = 1
        if ticket_count < 1:
            return await ctx.send(_("Buy at least one ticket."))
        guild_config = self.config.guild(ctx.guild)
        draw_time_text = await guild_config.lotto_time()
        channel_id = await guild_config.lotto_channel()
        role_id = await guild_config.lotto_role()
        try:
            draw_time = parse_draw_time(draw_time_text)
        except ValueError:
            return await ctx.send(
                _("The lotto drawing time is invalid. Ask an administrator to reconfigure it.")
            )

        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        role = ctx.guild.get_role(role_id) if role_id else None
        if channel is None or role is None:
            return await ctx.send(
                _("Lotto is not configured yet. Ask an administrator to set its channel and role.")
            )

        total_cost = ticket_count * LOTTO_TICKET_PRICE
        if not await bank.can_spend(ctx.author, total_cost):
            return await ctx.send(
                _("You need {} credits to buy {} lotto ticket(s).").format(
                    humanize_number(total_cost), humanize_number(ticket_count)
                )
            )

        tickets_to_add = []
        if custom_numbers is not None:
            tickets_to_add.append(custom_numbers)
        tickets_to_add.extend(
            quick_pick() for __ in range(ticket_count - len(tickets_to_add))
        )

        try:
            await bank.withdraw_credits(ctx.author, total_cost)
        except ValueError:
            return await ctx.send(_("You do not have enough credits for that purchase."))

        try:
            draw_date, __ = await self.lotto_store.add_tickets(
                ctx.guild.id,
                draw_time,
                ctx.author.id,
                tickets_to_add,
            )
        except (OSError, ValueError):
            try:
                await bank.deposit_credits(ctx.author, total_cost)
            except BalanceTooHigh as exc:
                await bank.set_balance(ctx.author, exc.max_balance)
            log.exception(
                "Could not save lotto tickets for user %s in guild %s",
                ctx.author.id,
                ctx.guild.id,
            )
            return await ctx.send(
                _("The ticket purchase could not be saved. Your credits were refunded.")
            )

        warning = ""
        if role not in ctx.author.roles:
            try:
                await ctx.author.add_roles(role, reason="Purchased lotto tickets")
            except discord.HTTPException:
                log.exception(
                    "Could not assign lotto holder role %s to user %s in guild %s",
                    role.id,
                    ctx.author.id,
                    ctx.guild.id,
                )
                warning = _(
                    " The tickets were saved, but I could not assign the holder role. "
                    "Please ask an administrator to check my role permissions."
                )

        draw_at = draw_at_date(datetime.fromisoformat(draw_date).date(), draw_time)
        await ctx.send(
            _("Purchased {} ticket(s) for {} credits. Drawing: <t:{}:F>.{}").format(
                humanize_number(ticket_count),
                humanize_number(total_cost),
                int(draw_at.timestamp()),
                warning,
            )
        )
        return await self._send_lotto_tickets(ctx)

    async def _send_lotto_tickets(self, ctx):
        today = datetime.now(timezone.utc).date().isoformat()
        tickets = await self.lotto_store.tickets_for_user(
            ctx.guild.id, ctx.author.id, today
        )
        if not tickets:
            return await ctx.send(_("You have no active lotto tickets."))

        tickets.sort(key=lambda ticket: ticket["draw_date"])
        header = _("You have {} active lotto ticket(s):").format(len(tickets))
        chunks = []
        current = header
        for ticket in tickets:
            numbers = " ".join("{:02d}".format(number) for number in ticket["numbers"])
            line = "\n{}: {}".format(ticket["draw_date"], numbers)
            if len(current) + len(line) > 1800:
                chunks.append(current)
                current = ""
            current += line
        if current:
            chunks.append(current)

        for chunk in chunks:
            await ctx.send(chunk)

    @commands.hybrid_group(
        name="lottoadmin",
        description="Configure and inspect daily lotto drawings.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def lottoadmin(self, ctx: commands.Context):
        await ctx.send(await self._lotto_status(ctx.guild))

    @lottoadmin.command(name="time", description="Set the daily drawing time in UTC.")
    @discord.app_commands.describe(draw_time="24-hour UTC time, for example 20:30.")
    async def lottoadmin_time(self, ctx: commands.Context, draw_time: str):
        try:
            parse_draw_time(draw_time)
        except ValueError:
            return await ctx.send(_("Use 24-hour HH:MM format, for example 20:30."))
        await self.config.guild(ctx.guild).lotto_time.set(draw_time)
        await ctx.send(_("Daily lotto drawing time set to {} UTC.").format(draw_time))

    @lottoadmin.command(name="channel", description="Set the daily drawing channel.")
    @discord.app_commands.describe(channel="Where the drawing and results will be posted.")
    async def lottoadmin_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        await self.config.guild(ctx.guild).lotto_channel.set(channel.id)
        await ctx.send(_("Lotto drawings will be posted in {}.").format(channel.mention))

    @lottoadmin.command(name="role", description="Set the lotto ticket holder role.")
    @discord.app_commands.describe(role="Role assigned to users with active lotto tickets.")
    async def lottoadmin_role(self, ctx: commands.Context, role: discord.Role):
        bot_member = ctx.guild.me
        if (
            role.is_default()
            or not bot_member.guild_permissions.manage_roles
            or (
                not role.mentionable
                and not bot_member.guild_permissions.mention_everyone
            )
            or role >= bot_member.top_role
        ):
            return await ctx.send(
                _(
                    "I need Manage Roles permission, permission to ping this role "
                    "(or the role must be mentionable), and my top role must be above it."
                )
            )

        guild_config = self.config.guild(ctx.guild)
        old_role_id = await guild_config.lotto_role()
        await guild_config.lotto_role.set(role.id)
        tickets = await self.lotto_store.tickets(ctx.guild.id)
        today = datetime.now(timezone.utc).date().isoformat()
        user_ids = {
            ticket["user_id"]
            for ticket in tickets
            if ticket["draw_date"] >= today
        }
        old_role = ctx.guild.get_role(old_role_id) if old_role_id else None
        for user_id in user_ids:
            member = await self._resolve_lotto_member(ctx.guild, user_id)
            if member is None:
                continue
            if old_role and old_role.id != role.id and old_role in member.roles:
                await member.remove_roles(old_role, reason="Lotto holder role changed")
            if role not in member.roles:
                await member.add_roles(role, reason="Active lotto ticket holder")

        await ctx.send(_("Lotto ticket holder role set to {}.").format(role.mention))

    @lottoadmin.command(name="status", description="Show lotto configuration and next drawing.")
    async def lottoadmin_status(self, ctx: commands.Context):
        await ctx.send(await self._lotto_status(ctx.guild))

    async def _lotto_status(self, guild):
        guild_config = self.config.guild(guild)
        draw_time_text = await guild_config.lotto_time()
        channel_id = await guild_config.lotto_channel()
        role_id = await guild_config.lotto_role()
        channel = guild.get_channel(channel_id) if channel_id else None
        role = guild.get_role(role_id) if role_id else None
        next_draw = next_draw_at(
            parse_draw_time(draw_time_text),
            last_drawn=await self.lotto_store.last_drawn(guild.id),
        )
        channel_text = channel.mention if channel else _("Not configured")
        role_text = role.mention if role else _("Not configured")
        return _(
            "**Daily drawing:** {0} UTC\n"
            "**Channel:** {1}\n"
            "**Ticket holder role:** {2}\n"
            "**Next drawing:** <t:{3}:F> (<t:{3}:R>)\n"
            "**Ticket price:** {4} credits\n"
            "**Any-order jackpot:** {5} credits\n"
            "**Exact-order jackpot:** {6} credits\n"
            "**Match 3 in order (anywhere):** {7} credits each\n"
            "**Match 3 (any order):** {8} credits each"
        ).format(
            draw_time_text,
            channel_text,
            role_text,
            int(next_draw.timestamp()),
            humanize_number(LOTTO_TICKET_PRICE),
            humanize_number(LOTTO_UNORDERED_JACKPOT),
            humanize_number(LOTTO_EXACT_JACKPOT),
            humanize_number(LOTTO_THREE_ORDERED_PRIZE),
            humanize_number(LOTTO_THREE_ANY_PRIZE),
        )

    async def lotto_scheduler(self):
        await self.bot.wait_until_red_ready()
        while True:
            for guild in self.bot.guilds:
                try:
                    await self._process_lotto_guild(
                        guild, datetime.now(timezone.utc)
                    )
                except (discord.HTTPException, OSError, ValueError):
                    log.exception(
                        "Could not process lotto for guild %s",
                        guild.id,
                    )
            await asyncio.sleep(10)

    async def _process_lotto_guild(self, guild, now):
        draw_date = now.date().isoformat()
        expired = await self.lotto_store.expire_before(guild.id, draw_date)
        guild_config = self.config.guild(guild)
        role_id = await guild_config.lotto_role()
        role = guild.get_role(role_id) if role_id else None
        if expired:
            await self._remove_expired_lotto_roles(guild, expired, role)

        draw_time = parse_draw_time(await guild_config.lotto_time())
        channel_id = await guild_config.lotto_channel()
        if not channel_id or not role_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None or role is None:
            return
        task = self.lotto_draw_tasks.get(guild.id)
        if task is not None and not task.done():
            return

        pending_draw_dates = await self.lotto_store.drawing_dates(guild.id)
        for pending_date in pending_draw_dates:
            if pending_date <= draw_date:
                self._start_lotto_drawing(
                    guild, channel, role, pending_date, draw_time
                )
                return

        scheduled = draw_at_date(now.date(), draw_time)
        if now < scheduled:
            return
        last_drawn = await self.lotto_store.last_drawn(guild.id)
        if last_drawn is not None and last_drawn >= draw_date:
            return
        self._start_lotto_drawing(guild, channel, role, draw_date, draw_time)

    def _start_lotto_drawing(self, guild, channel, role, draw_date, draw_time):
        task = asyncio.create_task(
            self._run_lotto_drawing(guild, channel, role, draw_date, draw_time)
        )
        self.lotto_draw_tasks[guild.id] = task
        task.add_done_callback(
            lambda completed, guild_id=guild.id: self._lotto_drawing_done(
                guild_id, completed
            )
        )

    def _lotto_drawing_done(self, guild_id, task):
        if self.lotto_draw_tasks.get(guild_id) is task:
            del self.lotto_draw_tasks[guild_id]
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log.error(
                "Lotto drawing failed for guild %s",
                guild_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run_lotto_drawing(self, guild, channel, role, draw_date, draw_time):
        drawing = await self.lotto_store.begin_drawing(
            guild.id, draw_date, draw_numbers()
        )
        tickets = await self.lotto_store.tickets(guild.id, draw_date)
        drawn_numbers = drawing["numbers"]
        await channel.send(
            "{} The drawing will start soon!".format(role.mention),
            allowed_mentions=discord.AllowedMentions(
                roles=True, users=False, everyone=False
            ),
        )

        embed = discord.Embed(
            title=_("Daily Lotto Drawing"),
            description=_("Numbers will be revealed every 10 seconds."),
            colour=0x3366CC,
        )
        for index in range(6):
            embed.add_field(name=_("Number {}").format(index + 1), value="—")
        message = await channel.send(embed=embed)

        for index, number in enumerate(drawn_numbers):
            await asyncio.sleep(10)
            embed.set_field_at(
                index,
                name=_("Number {}").format(index + 1),
                value="**{:02d}**".format(number),
            )
            await message.edit(embed=embed)

        winners = classify_tickets(tickets, drawn_numbers)
        awards = {}
        # Jackpots are split between winning tickets; the smaller prizes are paid per ticket.
        payouts = [
            ("exact", split_jackpot(LOTTO_EXACT_JACKPOT, winners["exact"])),
            ("unordered", split_jackpot(LOTTO_UNORDERED_JACKPOT, winners["unordered"])),
            (
                "three_ordered",
                [(t, LOTTO_THREE_ORDERED_PRIZE) for t in winners["three_ordered"]],
            ),
            ("three_any", [(t, LOTTO_THREE_ANY_PRIZE) for t in winners["three_any"]]),
        ]
        for tier, ticket_amounts in payouts:
            for ticket, amount in ticket_amounts:
                ticket_id = ticket["ticket_id"]
                settlement = drawing["settlements"].get(ticket_id)
                if settlement is None:
                    paid, pending = await self._deposit_lotto_winnings(
                        guild, ticket["user_id"], amount
                    )
                    settlement = await self.lotto_store.settle_ticket(
                        guild.id, draw_date, ticket_id, paid, pending
                    )
                    drawing["settlements"][ticket_id] = settlement
                else:
                    paid = settlement["paid"]
                    pending = settlement["pending"]
                award = awards.setdefault(
                    ticket["user_id"],
                    {
                        key: 0
                        for name in LOTTO_TIER_LABELS
                        for key in (name + "_paid", name + "_pending")
                    },
                )
                award["{}_paid".format(tier)] += paid
                award["{}_pending".format(tier)] += pending

        expired = await self.lotto_store.complete_drawing(guild.id, draw_date)
        await self._remove_expired_lotto_roles(guild, expired, role)

        if awards:
            lines = []
            for user_id, award in sorted(awards.items()):
                details = []
                for name, label in LOTTO_TIER_LABELS.items():
                    if award[name + "_paid"]:
                        details.append(
                            _("{} credits for {}").format(
                                humanize_number(award[name + "_paid"]), _(label)
                            )
                        )
                pending = sum(award[name + "_pending"] for name in LOTTO_TIER_LABELS)
                if pending:
                    details.append(
                        _("{} credits are pending due to an account balance limit").format(
                            humanize_number(pending)
                        )
                    )
                lines.append("<@{}> — {}".format(user_id, "; ".join(details)))
            current = "🎉🎊🥳🎉🎊🥳"
            for line in lines:
                if len(current) + len(line) + 1 > 1900:
                    await channel.send(
                        current,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=False, everyone=False
                        ),
                    )
                    current = ""
                current += ("\n" if current else "") + line
            if current:
                await channel.send(
                    current,
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False
                    ),
                )
        else:
            next_draw = next_draw_at(draw_time, last_drawn=draw_date)
            await channel.send(
                "No winners today! Try again <t:{}:R>".format(
                    int(next_draw.timestamp())
                )
            )

    async def _deposit_lotto_winnings(self, guild, user_id, amount):
        member = await self._resolve_lotto_member(guild, user_id)
        if member is None:
            await self._add_lotto_pending(guild.id, user_id, amount)
            return 0, amount

        try:
            await bank.deposit_credits(member, amount)
        except BalanceTooHigh as exc:
            balance = await bank.get_balance(member)
            available = max(0, exc.max_balance - balance)
            paid = min(amount, available)
            if paid:
                await bank.set_balance(member, balance + paid)
            pending = amount - paid
            if pending:
                await self._add_lotto_pending(guild.id, user_id, pending)
            return paid, pending
        return amount, 0

    async def _add_lotto_pending(self, guild_id, user_id, amount):
        if await self.casino_is_global():
            player_data = self.config.user_from_id(user_id)
        else:
            player_data = self.config.member_from_ids(guild_id, user_id)
        current = await player_data.Pending_Credits()
        await player_data.Pending_Credits.set(current + amount)

    async def _resolve_lotto_member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def _remove_expired_lotto_roles(self, guild, expired_tickets, role):
        if role is None:
            return
        active_tickets = await self.lotto_store.tickets(guild.id)
        active_users = {ticket["user_id"] for ticket in active_tickets}
        expired_users = {ticket["user_id"] for ticket in expired_tickets}
        for user_id in expired_users - active_users:
            member = await self._resolve_lotto_member(guild, user_id)
            if member is None or role not in member.roles:
                continue
            try:
                await member.remove_roles(role, reason="Lotto tickets expired")
            except discord.HTTPException:
                log.exception(
                    "Could not remove lotto holder role %s from user %s in guild %s",
                    role.id,
                    user_id,
                    guild.id,
                )

    @casino.command(description="Show a user's casino play statistics.")
    @discord.app_commands.describe(player="The user whose casino statistics to show.")
    async def stats(
        self, ctx: commands.Context, player: discord.User = None
    ):
        """Shows your play statistics for Casino"""
        if player is None:
            player = ctx.author

        casino = await super().get_data(ctx)
        casino_name = await casino.Settings.Casino_Name()

        coro = await super().get_data(ctx, player=player)
        player_data = await coro.all()

        mem, perks = await super()._get_player_membership(ctx, player)
        color = utils.color_lookup(perks["Color"])

        games = sorted(await casino.Games.all())
        played = [y for x, y in sorted(player_data["Played"].items(), key=itemgetter(0))]
        won = [y for x, y in sorted(player_data["Won"].items(), key=itemgetter(0))]
        cool_items = [y for x, y in sorted(player_data["Cooldowns"].items(), key=itemgetter(0))]

        reduction = perks["Reduction"]
        fmt_reduct = utils.cooldown_formatter(reduction)
        cooldowns = self.parse_cooldowns(ctx, cool_items, reduction)
        description = _(
            "Membership: {0}\nAccess Level: {Access}\nCooldown Reduction: {1}\nBonus Multiplier: {Bonus}x"
        ).format(mem, fmt_reduct, **perks)

        headers = ("Games", "Played", "Won", "Cooldowns")
        table = tabulate(zip(games, played, won, cooldowns), headers=headers)
        disclaimer = _("Wins do not take into calculation pushed bets or surrenders.")

        # Embed
        embed = discord.Embed(colour=color, description=description)
        embed.title = _("{} Casino").format(casino_name)
        embed.set_author(name=str(player), icon_url=player.display_avatar.url)
        embed.add_field(name="\u200b", value="\u200b")
        embed.add_field(name="-" * 65, value=box(table, lang="md"))
        embed.set_footer(text=disclaimer)
        await ctx.send(embed=embed)

    @casino.command(description="Create, edit, or delete casino memberships.")
    @commands.max_concurrency(1, commands.BucketType.guild)
    @checks.admin_or_permissions(administrator=True)
    async def memdesigner(self, ctx: commands.Context):
        """A process to create, edit, and delete memberships."""
        timeout = ctx.send(_("Process timed out. Exiting membership process."))

        await ctx.send(_("Do you wish to `create`, `edit`, or `delete` an existing membership?"))

        pred = MessagePredicate.lower_contained_in(("edit", "create", "delete"), ctx=ctx)
        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await timeout

        await Membership(ctx, timeout, choice.content.lower()).process()

    @casino.command(description="Show the installed Casino cog version.")
    async def version(self, ctx: commands.Context):
        """Shows the current Casino version."""
        await ctx.send("Casino is running version {}.".format(__version__))

    # --------------------------------------------------------------------------------------------------

    async def global_casino_only(ctx):
        if await ctx.cog.config.Settings.Global() and not await ctx.bot.is_owner(ctx.author):
            return False
        else:
            return True

    @commands.check(global_casino_only)
    @commands.hybrid_group(
        description="Configure casino settings and game rules.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def casinoset(self, ctx: commands.Context):
        """Changes Casino settings"""
        pass

    @casinoset.command(
        name="oldstyle",
        description="Toggle between editing game messages and sending new ones.",
    )
    async def change_style(self, ctx: commands.Context):
        """Toggle between editing and sending new messages for casino games.."""

        current = await self.old_message_cache.get_guild(guild=ctx.guild)
        await self.old_message_cache.set_guild(guild=ctx.guild, set_to=not current)

        await ctx.send(
            _("Casino message type set to {type}.").format(
                type=_("**edit existing message**") if current else _("**send new message**")
            )
        )

    @casinoset.command(
        name="mode",
        description="Switch the casino between global and server-local modes.",
    )
    @checks.is_owner()
    async def mode(self, ctx: commands.Context):
        """Toggles Casino between global and local modes.

        When casino is set to local mode, each server will have its own
        unique data, and admin level commands can be used on that server.

        When casino is set to global mode, data is linked between all servers
        the bot is connected to. In addition, admin level commands can only be
        used by the owner or co-owners.
        """

        mode = "global" if await super().casino_is_global() else "local"
        alt = "local" if mode == "global" else "global"
        await ctx.send(
            _("Casino is currently set to {} mode. Would you like to change to {} mode instead?").format(mode, alt)
        )
        pred = MessagePredicate.yes_or_no(ctx=ctx)

        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No response. Action canceled."))
        if choice.content.lower() != _("yes"):
            return await ctx.send(_("Casino will remain {}.").format(mode))

        await ctx.send(
            _(
                "Changing casino to {0} will **DELETE ALL** current casino data. Are "
                "you sure you wish to make casino {0}?"
            ).format(alt)
        )
        try:
            final = await ctx.bot.wait_for("message", timeout=25.0, check=pred)
        except asyncio.TimeoutError:
            return await ctx.send(_("No response. Action canceled."))

        if final.content.lower() == _("yes"):
            if not await bank.is_global() and alt == "global":
                return await ctx.send(
                    "You cannot make casino global while economy is "
                    "in local mode. To change your economy to global "
                    "use `{}bankset toggleglobal`".format(ctx.prefix)
                )
            await super().change_mode(alt)
            await ctx.send(_("Casino data deleted! Casino mode now set to {}.").format(alt))
        else:
            await ctx.send(_("Casino will remain {}.").format(mode))

    @casinoset.command(description="Set the winnings threshold for payout review.")
    @discord.app_commands.describe(limit="Winnings above this amount are held for review.")
    async def payoutlimit(self, ctx: commands.Context, limit: int):
        """Sets a payout limit.

        Users who exceed this amount will have their winnings witheld until they are
        reviewed and approved by the appropriate authority. Limits are only triggered if
        payout limits are ON. To turn on payout limits, use payouttoggle.
        """

        if limit < 0 or is_input_unsupported(limit):
            return await ctx.send(_("Go home. You're drunk."))

        settings = await super().get_data(ctx)
        await settings.Settings.Payout_Limit.set(limit)
        msg = _("{0.name} ({0.id}) set the payout limit to {1}.").format(ctx.author, limit)
        await ctx.send(msg)

    @casinoset.command(description="Toggle review limits for casino winnings.")
    async def payouttoggle(self, ctx: commands.Context):
        """Turns on a payout limit.

        The payout limit will withhold winnings from players until they are approved by the
        appropriate authority. To set the limit, use payoutlimit.
        """
        settings = await super().get_data(ctx)
        status = await settings.Settings.Payout_Switch()
        await settings.Settings.Payout_Switch.set(not status)
        msg = _("{0.name} ({0.id}) turned the payout limit {1}.").format(ctx.author, "OFF" if status else "ON")
        await ctx.send(msg)

    @casinoset.command(description="Open or close the casino for games.")
    async def toggle(self, ctx: commands.Context):
        """Opens and closes the Casino for use.

        This command only restricts the use of playing games.
        """
        settings = await super().get_data(ctx)
        name = await settings.Settings.Casino_Name()

        status = await settings.Settings.Casino_Open()
        await settings.Settings.Casino_Open.set(not status)
        msg = _("{0.name} ({0.id}) {2} the {1} Casino.").format(ctx.author, name, "closed" if status else "opened")
        await ctx.send(msg)

    @casinoset.command(description="Set the casino's display name.")
    @discord.app_commands.describe(name="The casino name, up to 30 characters.")
    async def name(self, ctx: commands.Context, *, name: str):
        """Sets the name of the Casino.

        The casino name may only be 30 characters in length.
        """
        if len(name) > 30:
            return await ctx.send(_("Your Casino name must be 30 characters or less."))

        settings = await super().get_data(ctx)
        await settings.Settings.Casino_Name.set(name)
        msg = _("{0.name} ({0.id}) set the casino name to {1}.").format(ctx.author, name)
        await ctx.send(msg)

    @casinoset.command(description="Set a game's payout multiplier.")
    @discord.app_commands.describe(
        game="The casino game to configure.",
        multiplier="The payout multiplier to apply.",
    )
    async def multiplier(self, ctx: commands.Context, game: str, multiplier: float):
        """Sets the payout multiplier for a game.
        """
        settings = await super().get_data(ctx)
        games = await settings.Games.all()
        if is_input_unsupported(multiplier):
            return await ctx.send(_("Go home. You're drunk."))

        if game.title() == "Allin" or game.title() == "Double":
            return await ctx.send(_("This games's multiplier is determined by the user."))

        if not await self.basic_check(ctx, game, games, multiplier):
            return

        await settings.Games.set_raw(game.title(), "Multiplier", value=multiplier)
        msg = _("{0.name} ({0.id}) set {1}'s multiplier to {2}.").format(ctx.author, game.title(), multiplier)
        if multiplier == 0:
            msg += _(
                "\n\nWait a minute...Zero?! Really... I'm a bot and that's more "
                "heartless than me! ... who hurt you human?"
            )
        await ctx.send(msg)

    @casinoset.command(description="Set a game's cooldown duration.")
    @discord.app_commands.describe(
        game="The casino game to configure.",
        cooldown="Cooldown in seconds or DD:HH:MM:SS format.",
    )
    async def cooldown(self, ctx: commands.Context, game: str, cooldown: str):
        """Sets the cooldown for a game.

        You can use the format DD:HH:MM:SS to set a time, or just simply
        type the number of seconds.
        """
        settings = await super().get_data(ctx)
        games = await settings.Games.all()

        try:
            seconds = utils.time_converter(cooldown)
        except ValueError:
            return await ctx.send(_("Invalid cooldown format. Must be an integer or in HH:MM:SS style."))

        if seconds < 0:
            return await ctx.send(_("Nice try McFly, but this isn't Back to the Future."))

        if game.title() not in games:
            return await ctx.send(
                _("Invalid game name. Must be one of the following:\n`{}`.").format(utils.fmt_join(list(games)))
            )

        await settings.Games.set_raw(game.title(), "Cooldown", value=seconds)
        cool = utils.cooldown_formatter(seconds)
        msg = _("{0.name} ({0.id}) set {1}'s cooldown to {2}.").format(ctx.author, game.title(), cool)
        await ctx.send(msg)

    @casinoset.command(name="min", description="Set a game's minimum bet.")
    @discord.app_commands.describe(game="The casino game to configure.", minimum="The minimum bet.")
    async def _min(self, ctx: commands.Context, game: str, minimum: int):
        """Sets the minimum bid for a game."""
        settings = await super().get_data(ctx)
        games = await settings.Games.all()

        if not await self.basic_check(ctx, game, games, minimum):
            return

        if is_input_unsupported(minimum):
            return await ctx.send(_("Go home. You're drunk."))

        if game.title() == "Allin":
            return await ctx.send(_("You cannot set a minimum bid for Allin."))

        if minimum > games[game.title()]["Max"]:
            return await ctx.send(_("You can't set a minimum higher than the game's maximum bid."))

        await settings.Games.set_raw(game.title(), "Min", value=minimum)
        msg = _("{0.name} ({0.id}) set {1}'s minimum bid to {2}.").format(ctx.author, game.title(), minimum)
        await ctx.send(msg)

    @casinoset.command(name="max", description="Set a game's maximum bet.")
    @discord.app_commands.describe(game="The casino game to configure.", maximum="The maximum bet.")
    async def _max(self, ctx: commands.Context, game: str, maximum: int):
        """Sets the maximum bid for a game."""
        settings = await super().get_data(ctx)
        games = await settings.Games.all()

        if not await self.basic_check(ctx, game, games, maximum):
            return

        if is_input_unsupported(maximum):
            return await ctx.send(_("Go home. You're drunk."))

        if game.title() == "Allin":
            return await ctx.send(_("You cannot set a maximum bid for Allin."))

        if maximum < games[game.title()]["Min"]:
            return await ctx.send(_("You can't set a maximum lower than the game's minimum bid."))

        await settings.Games.set_raw(game.title(), "Max", value=maximum)
        msg = _("{0.name} ({0.id}) set {1}'s maximum bid to {2}.").format(ctx.author, game.title(), maximum)
        await ctx.send(msg)

    @casinoset.command(description="Set the membership access level required for a game.")
    @discord.app_commands.describe(
        game="The casino game to configure.",
        access="The required membership access level.",
    )
    async def access(self, ctx, game: str, access: int):
        """Sets the access level required to play a game.

        Access levels are used in conjunction with memberships. To read more on using
        access levels and memberships please refer to the casino wiki."""
        data = await super().get_data(ctx)
        games = await data.Games.all()

        if not await self.basic_check(ctx, game, games, access):
            return

        if is_input_unsupported(access):
            return await ctx.send(_("Go home. You're drunk."))

        await data.Games.set_raw(game.title(), "Access", value=access)
        msg = _("{0.name} ({0.id}) changed the access level for {1} to {2}.").format(ctx.author, game, access)
        await ctx.send(msg)

    @casinoset.command(description="Open or close a specific game.")
    @discord.app_commands.describe(game="The casino game to open or close.")
    async def gametoggle(self, ctx, game: str):
        """Opens/Closes a specific game for use."""
        instance = await super().get_data(ctx)
        games = await instance.Games.all()
        if game.title() not in games:
            return await ctx.send("Invalid game name.")

        status = await instance.Games.get_raw(game.title(), "Open")
        await instance.Games.set_raw(game.title(), "Open", value=(not status))
        msg = _("{0.name} ({0.id}) {2} the game {1}.").format(ctx.author, game, "closed" if status else "opened")
        await ctx.send(msg)

    # --------------------------------------------------------------------------------------------------

    async def membership_updater(self):
        await self.bot.wait_until_red_ready()
        try:
            while True:
                await asyncio.sleep(300)  # Wait 5 minutes to cycle again
                is_global = await super().casino_is_global()
                if is_global:
                    await self.global_updater()
                else:
                    await self.local_updater()
        except Exception:
            log.error("Casino error in membership_updater:\n", exc_info=True)

    async def global_updater(self):
        while True:
            users = await self.config.all_users()
            if not users:
                break
            memberships = await self.config.Memberships.all()
            if not memberships:
                break
            for user in users:
                user_obj = self.bot.get_user(user)
                if not user_obj:
                    # user isn't in the cache so we can probably
                    # ignore them without issue
                    continue
                async with self.config.user(user_obj).Membership() as user_data:
                    if user_data["Name"] not in memberships:
                        user_data["Name"] = "Basic"
                        user_data["Assigned"] = False
                await self.process_user(memberships, user_obj, _global=True)
            break

    async def local_updater(self):
        while True:
            guilds = await self.config.all_guilds()
            if not guilds:
                break
            for guild in guilds:
                guild_obj = self.bot.get_guild(guild)
                if not guild_obj:
                    continue
                users = await self.config.all_members(guild_obj)
                if not users:
                    continue
                memberships = await self.config.guild(guild_obj).Memberships.all()
                if not memberships:
                    continue
                for user in users:
                    user_obj = guild_obj.get_member(user)
                    if not user_obj:
                        continue
                    async with self.config.member(user_obj).Membership() as user_data:
                        if user_data["Name"] not in memberships:
                            user_data["Name"] = "Basic"
                            user_data["Assigned"] = False
                    await self.process_user(memberships, user_obj)
            break

    async def process_user(self, memberships, user, _global=False):
        qualified = []
        try:
            bal = await bank.get_balance(user)
        except AttributeError:
            log.error(
                "Casino is in global mode, while economy is in local mode. "
                "Economy must be global if Casino is global. Either change casino "
                "back to local with the casinoset mode command or make your economy "
                "global with the bankset toggleglobal command."
            )
            return
        for name, requirements in memberships.items():
            if _global:
                if requirements["Credits"] and bal < requirements["Credits"]:
                    continue
                elif (
                    requirements["DOS"]
                    and requirements["DOS"] > (user.created_at.now() - user.created_at).days
                ):
                    continue
                else:
                    qualified.append((name, requirements["Access"]))
            else:
                role_list = [x.name for x in user.roles]
                role_list += [x.mention for x in user.roles]
                if requirements["Credits"] and bal < requirements["Credits"]:
                    continue
                elif requirements["Role"] and requirements["Role"] not in role_list:
                    continue
                elif (
                    requirements["DOS"]
                    and requirements["DOS"] > (user.joined_at.now() - user.joined_at).days
                ):
                    continue
                else:
                    qualified.append((name, requirements["Access"]))

        membership = max(qualified, key=itemgetter(1))[0] if qualified else "Basic"
        if _global:
            async with self.config.user(user).Membership() as data:
                data["Name"] = membership
                data["Assigned"] = False
        else:
            async with self.config.member(user).Membership() as data:
                data["Name"] = membership
                data["Assigned"] = False

    @staticmethod
    async def basic_check(ctx, game, games, base):
        if game.title() not in games:
            await ctx.send(
                "Invalid game name. Must be on of the following:\n`{}`".format(utils.fmt_join(list(games)))
            )
            return False
        elif base < 0:
            await ctx.send(_("Go home. You're drunk."))
            return False
        else:
            return True

    @staticmethod
    def parse_cooldowns(ctx, cooldowns, reduction):
        now = calendar.timegm(ctx.message.created_at.utctimetuple())
        results = []
        for cooldown in cooldowns:
            seconds = int((cooldown + reduction - now))
            results.append(utils.cooldown_formatter(seconds, custom_msg="<Ready to Play!>"))
        return results

    def cog_unload(self):
        self.__unload()

    def __unload(self):
        if self.cycle_task:
            self.cycle_task.cancel()
        if self.lotto_task:
            self.lotto_task.cancel()
        for task in self.lotto_draw_tasks.values():
            task.cancel()
        if self.migration_task:
            self.migration_task.cancel()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        if not self.cog_ready_event.is_set():
            async with ctx.typing():
                await self.cog_ready_event.wait()


class Membership(Database):
    """This class handles membership processing."""

    __slots__ = ("ctx", "timeout", "cancel", "mode", "coro")

    colors = {
        _("blue"): "blue",
        _("red"): "red",
        _("green"): "green",
        _("orange"): "orange",
        _("purple"): "purple",
        _("yellow"): "yellow",
        _("turquoise"): "turquoise",
        _("teal"): "teal",
        _("magenta"): "magenta",
        _("pink"): "pink",
        _("white"): "white",
    }

    requirements = (_("days on server"), _("credits"), _("role"))

    def __init__(self, ctx, timeout, mode):
        self.ctx = ctx
        self.timeout = timeout
        self.cancel = ctx.prefix + _("cancel")
        self.mode = mode
        self.coro = None
        super().__init__()

    def switcher(self):
        if self.mode == "edit":
            return self.editor
        elif self.mode == "create":
            return self.creator
        else:
            return self.delete

    async def process(self):
        action = self.switcher()
        instance = await super().get_data(self.ctx)
        self.coro = instance.Memberships
        try:
            await action()
        except asyncio.TimeoutError:
            await self.timeout
        except ExitProcess:
            await self.ctx.send(_("Process exited."))

    async def delete(self):
        memberships = await self.coro.all()

        def mem_check(m):
            valid_name = m.content
            return (
                m.author == self.ctx.author
                and valid_name in memberships
                or valid_name == self.cancel
            )

        if not memberships:
            await self.ctx.send(_("There are no memberships to delete."))
            raise ExitProcess()

        await self.ctx.send(
            _("Which membership would you like to delete?\n`{}`").format(utils.fmt_join(list(memberships.keys())))
        )
        membership = await self.ctx.bot.wait_for("message", timeout=25.0, check=mem_check)

        if membership.content == self.cancel:
            raise ExitProcess()
        await self.ctx.send(
            _("Are you sure you wish to delete `{}`? This cannot be reverted.").format(membership.content)
        )

        choice = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=MessagePredicate.yes_or_no(ctx=self.ctx)
        )
        if choice.content.lower() == self.cancel:
            raise ExitProcess()
        elif choice.content.lower() == "yes":
            name = membership.content
            async with self.coro() as data:
                del data[name]
            await self.ctx.send(_("{} has been deleted.").format(membership.content))
        else:
            await self.ctx.send(_("Deletion canceled."))

    async def creator(self):

        await self.ctx.send(
            _(
                "You are about to create a new membership. You may exit this "
                "process at any time by typing `{}cancel`."
            ).format(self.ctx.prefix)
        )

        data = dict.fromkeys(("Access", "Bonus", "Color", "Credits", "Role", "DOS", "Reduction"))

        name, valid_name = await self.set_name()
        await self.set_access(data)
        await self.set_color(data)
        await self.set_reduction(data)
        await self.set_bonus(data)
        await self.req_loop(data)

        async with self.coro() as mem:
            mem[valid_name] = data
        embed = self.build_embed(name, data)
        await self.ctx.send(embed=embed)
        raise ExitProcess()

    async def editor(self):
        memberships = await self.coro.all()

        def mem_check(m):
            return (
                m.author == self.ctx.author
                and m.content in memberships
                or m.content == self.cancel
            )

        if not memberships:
            await self.ctx.send(_("There are no memberships to edit."))
            raise ExitProcess()

        await self.ctx.send(
            _("Which of the following memberships would you like to edit?\n`{}`").format(
                utils.fmt_join(list(memberships.keys()))
            )
        )

        membership = await self.ctx.bot.wait_for("message", timeout=25.0, check=mem_check)
        if membership.content == self.cancel:
            raise ExitProcess()

        attrs = (_("Requirements"), _("Name"), _("Access"), _("Color"), _("Reduction"), _("Bonus"))
        await self.ctx.send(
            _("Which of the following attributes would you like to edit?\n`{}`").format(utils.fmt_join(attrs))
        )

        pred = MessagePredicate.lower_contained_in(
            (
                _("requirements"),
                _("access"),
                _("color"),
                _("name"),
                _("reduction"),
                _("bonus"),
                self.cancel,
            ),
            ctx=self.ctx,
        )
        attribute = await self.ctx.bot.wait_for("message", timeout=25.0, check=pred)

        valid_name = membership.content
        if attribute.content.lower() == self.cancel:
            raise ExitProcess()
        elif attribute.content.lower() == _("requirements"):
            await self.req_loop(valid_name)
        elif attribute.content.lower() == _("access"):
            await self.set_access(valid_name)
        elif attribute.content.lower() == _("bonus"):
            await self.set_bonus(valid_name)
        elif attribute.content.lower() == _("reduction"):
            await self.set_reduction(valid_name)
        elif attribute.content.lower() == _("color"):
            await self.set_color(valid_name)
        elif attribute.content.lower() == _("name"):
            await self.set_name(valid_name)
        else:
            await self.set_color(valid_name)

        await self.ctx.send(_("Would you like to edit another membership?"))

        choice = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=MessagePredicate.yes_or_no(ctx=self.ctx)
        )
        if choice.content.lower() == _("yes"):
            await self.editor()
        else:
            raise ExitProcess()

    async def set_color(self, membership):
        await self.ctx.send(_("What color would you like to set?\n`{}`").format(utils.fmt_join(list(self.colors))))

        color_list = list(self.colors)
        color_list.append(str(self.cancel))
        pred = MessagePredicate.lower_contained_in(color_list, ctx=self.ctx)
        color = await self.ctx.bot.wait_for("message", timeout=25.0, check=pred)

        if color.content.lower() == self.cancel:
            raise ExitProcess()

        if self.mode == "create":
            membership["Color"] = color.content.lower()
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Color"] = color.content.lower()

        await self.ctx.send(_("Color set to {}.").format(color.content.lower()))

    async def set_name(self, membership=None):
        memberships = await self.coro.all()

        def mem_check(m):
            if not m.channel == self.ctx.channel and m.author == self.ctx.author:
                return False
            if m.author == self.ctx.author:
                if m.content == self.cancel:
                    raise ExitProcess
                conditions = (
                    m.content not in memberships,
                    (True if re.match("^[a-zA-Z0-9 -]*$", m.content) else False),
                )
                if all(conditions):
                    return True
                else:
                    return False
            else:
                return False

        await self.ctx.send(_("What name would you like to set?"))
        name = await self.ctx.bot.wait_for("message", timeout=25.0, check=mem_check)

        if name.content == self.cancel:
            raise ExitProcess()

        valid_name = name.content
        if self.mode == "create":
            return name.content, valid_name

        async with self.coro() as membership_data:
            membership_data[valid_name] = membership_data.pop(membership)

        await self.ctx.send(_("Name set to {}.").format(name.content))

    async def set_access(self, membership):
        await self.ctx.send(_("What access level would you like to set?"))
        access = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=self.positive_int_predicate
        )

        user_input = int(access.content)
        if is_input_unsupported(user_input):
            await self.ctx.send(_("Can't set the reduction to this value."))
            return

        if self.mode == "create":
            membership["Access"] = user_input
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Access"] = user_input

        await self.ctx.send(_("Access set to {}.").format(user_input))

    async def set_reduction(self, membership):
        await self.ctx.send(_("What is the cooldown reduction of this membership in seconds?"))
        reduction = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=self.positive_int_predicate
        )

        user_input = int(reduction.content)
        if is_input_unsupported(user_input):
            await self.ctx.send(_("Can't set the reduction to this value."))
            return

        if self.mode == "create":
            membership["Reduction"] = user_input
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Reduction"] = user_input

    async def set_bonus(self, membership):
        await self.ctx.send(_("What is the bonus payout multiplier for this membership?\n*Defaults to 1.0*"))
        bonus = await self.ctx.bot.wait_for("message", timeout=25.0, check=self.positive_float_predicate)

        if bonus.content.lower() == self.cancel:
            raise ExitProcess
        user_input = bonus.content
        if is_input_unsupported(user_input):
            await self.ctx.send(_("Can't set the bonus multiplier to this value."))
            return

        if self.mode == "create":
            membership["Bonus"] = float(user_input)
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Bonus"] = float(bonus.content)

        await self.ctx.send(_("Bonus multiplier set to {}.").format(bonus.content))

    async def req_loop(self, membership):
        while True:
            await self.ctx.send(
                _("Which requirement would you like to add or modify?\n`{}`").format(
                    utils.fmt_join(self.requirements)
                )
            )

            pred = MessagePredicate.lower_contained_in(
                (_("credits"), _("role"), _("dos"), _("days on server"), self.cancel), ctx=self.ctx
            )

            req = await self.ctx.bot.wait_for("message", timeout=25.0, check=pred)
            if req.content.lower() == self.cancel:
                raise ExitProcess()
            elif req.content.lower() == _("credits"):
                await self.credits_requirement(membership)
            elif req.content.lower() == _("role"):
                await self.role_requirement(membership)
            else:
                await self.dos_requirement(membership)

            await self.ctx.send(_("Would you like to continue adding or modifying requirements?"))

            choice = await self.ctx.bot.wait_for(
                "message", timeout=25.0, check=MessagePredicate.yes_or_no(ctx=self.ctx)
            )
            if choice.content.lower() == _("no"):
                break
            elif choice.content.lower() == self.cancel:
                raise ExitProcess()
            else:
                continue

    async def credits_requirement(self, membership):
        await self.ctx.send(_("How many credits does this membership require?"))

        amount = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=self.positive_int_predicate
        )

        amount = int(amount.content)
        if is_input_unsupported(amount):
            await self.ctx.send(_("Can't set the credit requirement to this value."))
            return
        if self.mode == "create":
            membership["Credits"] = amount
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Credits"] = amount

        await self.ctx.send(_("Credits requirement set to {}.").format(humanize_number(amount)))

    async def role_requirement(self, membership):
        await self.ctx.send(
            _(
                "What role does this membership require?\n"
                "*Note this is skipped in global mode. If you set this as the only "
                "requirement in global, it will be accessible to everyone!*"
            )
        )
        pred = MessagePredicate.valid_role(ctx=self.ctx)
        role = await self.ctx.bot.wait_for("message", timeout=25.0, check=pred)

        if self.mode == "create":
            membership["Role"] = role.content
            return

        async with self.coro() as membership_data:
            membership_data[membership]["Role"] = role.content

        await self.ctx.send(_("Role requirement set to {}.").format(role.content))

    async def dos_requirement(self, membership):
        await self.ctx.send(
            _(
                "How many days on server does this membership require?\n"
                "*Note in global mode this will calculate based on when the user "
                "account was created.*"
            )
        )
        days = await self.ctx.bot.wait_for(
            "message", timeout=25.0, check=self.positive_int_predicate
        )

        if self.mode == "create":
            membership["DOS"] = int(days.content)
            return

        async with self.coro() as membership_data:
            membership_data[membership]["DOS"] = int(days.content)
        await self.ctx.send(_("Time requirement set to {}.").format(days.content))

    @staticmethod
    def build_embed(name, data):
        description = _(
            "Membership sucessfully created.\n\n"
            "**Name:** {0}\n"
            "**Access:** {Access}\n"
            "**Bonus:** {Bonus}x\n"
            "**Reduction:** {Reduction} seconds\n"
            "**Color:** {Color}\n"
            "**Credits Required:** {Credits}\n"
            "**Role Required:** {Role}\n"
            "**Days on Server/Discord Required:** {DOS}"
        ).format(name, **data)
        return discord.Embed(colour=0x2CD22C, description=description)

    def positive_int_predicate(self, m: discord.Message):
        if not m.channel == self.ctx.channel and m.author == self.ctx.author:
            return False
        if m.author == self.ctx.author:
            if m.content == self.cancel:
                raise ExitProcess
        try:
            int(m.content)
        except ValueError:
            return False
        if int(m.content) < 1:
            return False
        else:
            return True

    def positive_float_predicate(self, m: discord.Message):
        if not m.channel == self.ctx.channel and m.author == self.ctx.author:
            return False
        if m.author == self.ctx.author:
            if m.content == self.cancel:
                raise ExitProcess
        try:
            float(m.content)
        except ValueError:
            return False
        if float(m.content) > 0:
            return True
        else:
            return False


class ExitProcess(Exception):
    pass
