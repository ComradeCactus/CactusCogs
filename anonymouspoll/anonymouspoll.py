import asyncio
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks
from redbot.core import Config, commands

DURATION_CHOICES = [
    app_commands.Choice(name=name, value=minutes)
    for name, minutes in (
        ("5 minutes", 5),
        ("10 minutes", 10),
        ("30 minutes", 30),
        ("1 hour", 60),
        ("2 hours", 120),
        ("4 hours", 240),
        ("8 hours", 480),
        ("24 hours", 1440),
        ("3 days", 4320),
        ("1 week", 10080),
        ("2 weeks", 20160),
    )
]


DURATION_NAMES = {c.value: c.name for c in DURATION_CHOICES}
CHOICES_PER_STEP = 5
POLL_COOLDOWN_SECONDS = 3600


BAR_WIDTH = 24
VOTE_NOTICE_SECONDS = 30


def vote_counts(poll):
    counts = [0] * len(poll["options"])
    for choices in poll["votes"].values():
        for i in choices:
            counts[i] += 1
    return counts


def poll_embed(poll, final=False):
    counts = vote_counts(poll)
    voters = len(poll["votes"])
    total = sum(counts)
    top = max(counts)
    lines = []
    for n, (option, count) in enumerate(zip(poll["options"], counts), 1):
        share = count / total if total else 0
        filled = round(share * BAR_WIDTH)
        bar = "\u2593" * filled + "\u2591" * (BAR_WIDTH - filled)
        crown = " \N{TROPHY}" if final and top and count == top else ""
        lines.append(f"{n}. {option}{crown}\n`{bar}` {count} ({share:.0%})")
    embed = discord.Embed(
        title=poll["question"],
        description="\n\n".join(lines),
        colour=discord.Colour.dark_grey() if final else discord.Colour.blurple(),
    )
    kind = "Multiple answers allowed" if poll["multiple"] else "Pick one"
    status = "Poll closed" if final else f"Ends <t:{int(poll['end'])}:R>"
    embed.description += f"\n\n{status}"
    if poll["multiple"]:
        tally = f"{total} total vote{'s' if total != 1 else ''}"
    else:
        tally = f"{voters} voter{'s' if voters != 1 else ''}"
    embed.set_footer(text=f"{tally} | {kind}")
    return embed


class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"anonpoll:vote:(?P<index>\d+)"):
    """Persistent vote button. Which poll it belongs to comes from the message it's on."""

    def __init__(self, index, label="Vote"):
        super().__init__(
            discord.ui.Button(
                label=label[:80], custom_id=f"anonpoll:vote:{index}", style=discord.ButtonStyle.primary
            )
        )
        self.index = index

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["index"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.get_cog("AnonymousPoll")._vote(interaction, self.index)


def vote_view(options):
    view = discord.ui.View(timeout=None)
    for i, option in enumerate(options):
        view.add_item(VoteButton(i, f"{i + 1}. {option}"))
    return view


class PollState:
    """Poll details collected across the form's steps."""

    def __init__(self):
        self.question = ""
        self.duration = 1440
        self.multiple = False
        self.count = 2
        self.options = ["", ""]

    def set_count(self, count):
        self.count = count
        self.options = (self.options + [""] * count)[:count]

    def step_range(self, step):
        start = step * CHOICES_PER_STEP
        return start, min(start + CHOICES_PER_STEP, self.count)

    def range_label(self, step):
        start, end = self.step_range(step)
        label = f"{start + 1}-{end}"
        return f"{label} (\N{FACE WITH TEARS OF JOY})" if label == "6-7" else label

    @property
    def last_step(self):
        return (self.count - 1) // CHOICES_PER_STEP

    def preview(self):
        choices = "\n".join(f"{n}. {o}" for n, o in enumerate(self.options, 1))
        return (
            "**Here's your poll. Ready to post?**\n"
            f"> **{self.question}**\n{choices}\n\n"
            f"Duration: {DURATION_NAMES[self.duration]} | "
            f"Multiple answers: {'yes' if self.multiple else 'no'}"
        )


async def respond(interaction: discord.Interaction, **kwargs):
    """Edit the ephemeral form message if there is one, otherwise send a new one."""
    if interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(ephemeral=True, **kwargs)


async def show_step(cog, interaction, state, step):
    if step > state.last_step:
        await respond(interaction, content=state.preview(), view=PreviewView(cog, state))
        return
    start, end = state.step_range(step)
    await respond(
        interaction,
        content=f"Next up: choices {state.range_label(step)} of {state.count}.",
        view=StepView(cog, state, step),
    )


class PollModal(discord.ui.Modal, title="Anonymous poll"):
    def __init__(self, cog, state):
        super().__init__()
        self.cog = cog
        self.state = state
        self.question = discord.ui.TextInput(max_length=300, default=state.question or None)
        self.duration = discord.ui.Select(
            options=[
                discord.SelectOption(
                    label=c.name, value=str(c.value), default=c.value == state.duration
                )
                for c in DURATION_CHOICES
            ]
        )
        self.multiple = discord.ui.Checkbox(default=state.multiple)
        self.count = discord.ui.Select(
            options=[
                discord.SelectOption(label=str(n), value=str(n), default=n == state.count)
                for n in range(2, 11)
            ]
        )
        self.add_item(discord.ui.Label(text="Question", component=self.question))
        self.add_item(discord.ui.Label(text="Duration", component=self.duration))
        self.add_item(discord.ui.Label(text="Allow multiple answers", component=self.multiple))
        self.add_item(discord.ui.Label(text="How many choices?", component=self.count))

    async def on_submit(self, interaction: discord.Interaction):
        self.state.question = self.question.value
        self.state.duration = int(self.duration.values[0])
        self.state.multiple = self.multiple.value
        self.state.set_count(int(self.count.values[0]))
        await show_step(self.cog, interaction, self.state, 0)


class ChoicesModal(discord.ui.Modal):
    def __init__(self, cog, state, step):
        start, end = state.step_range(step)
        super().__init__(title=f"Choices {state.range_label(step)} of {state.count}")
        self.cog = cog
        self.state = state
        self.step = step
        self.start = start
        self.inputs = [
            discord.ui.TextInput(max_length=50, default=state.options[i] or None)
            for i in range(start, end)
        ]
        for i, item in zip(range(start, end), self.inputs):
            self.add_item(discord.ui.Label(text=f"Choice {i + 1}", component=item))

    async def on_submit(self, interaction: discord.Interaction):
        values = [i.value.strip() for i in self.inputs]
        if not all(values):
            await interaction.response.send_message(
                "Every choice needs some text. Press the button to try again.", ephemeral=True
            )
            return
        self.state.options[self.start : self.start + len(values)] = values
        await show_step(self.cog, interaction, self.state, self.step + 1)


class StepView(discord.ui.View):
    def __init__(self, cog, state, step):
        super().__init__(timeout=600)
        self.cog = cog
        self.state = state
        self.step = step

    @discord.ui.button(label="Enter choices", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChoicesModal(self.cog, self.state, self.step))


class PreviewView(discord.ui.View):
    def __init__(self, cog, state):
        super().__init__(timeout=600)
        self.cog = cog
        self.state = state

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Poll cancelled.", view=None)

    @discord.ui.button(label="Edit poll", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PollModal(self.cog, self.state))

    @discord.ui.button(label="Post it!", style=discord.ButtonStyle.primary)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        state = self.state
        await interaction.response.edit_message(content="Posting your poll...", view=None)
        await self.cog._post_poll(
            interaction, state.question, state.options, state.duration, state.multiple
        )


class AnonymousPoll(commands.Cog):
    """Start polls without revealing who started them or who voted for what."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7310294856123, force_registration=True)
        self.config.register_guild(log_channel=None)
        # message id (str) -> poll dict; kept in config so polls survive restarts
        self.config.register_global(polls={})
        self._last_poll = {}  # user id -> monotonic time of their last posted poll
        self._lock = asyncio.Lock()
        self._notices = {}  # (poll message id, user id) -> (ephemeral notice, delete task)

    async def cog_load(self):
        self.bot.add_dynamic_items(VoteButton)
        self.check_polls.start()

    async def cog_unload(self):
        self.check_polls.cancel()
        for _, task in self._notices.values():
            task.cancel()
        self.bot.remove_dynamic_items(VoteButton)

    @tasks.loop(seconds=30)
    async def check_polls(self):
        now = time.time()
        polls = await self.config.polls()
        for message_id, poll in polls.items():
            if poll["end"] <= now:
                await self._finish(message_id)

    @check_polls.before_loop
    async def before_check_polls(self):
        await self.bot.wait_until_red_ready()

    async def _vote(self, interaction, index):
        async with self._lock:
            polls = await self.config.polls()
            poll = polls.get(str(interaction.message.id))
            if poll is None or poll["end"] <= time.time():
                await interaction.response.send_message("This poll has ended.", ephemeral=True)
                return
            user = str(interaction.user.id)
            current = poll["votes"].get(user, [])
            if poll["multiple"]:
                current = [i for i in current if i != index] if index in current else current + [index]
            else:
                current = [] if current == [index] else [index]
            if current:
                poll["votes"][user] = current
            else:
                poll["votes"].pop(user, None)
            await self.config.polls.set(polls)
        await interaction.response.edit_message(embed=poll_embed(poll))
        if current:
            chosen = ", ".join(poll["options"][i] for i in sorted(current))
            msg = f"Your vote: **{chosen}**"
        else:
            msg = "Your vote has been removed."
        msg += f"\n-# This message will self-destruct <t:{int(time.time()) + VOTE_NOTICE_SECONDS}:R>."
        key = (interaction.message.id, interaction.user.id)
        notice = None
        old = self._notices.pop(key, None)
        if old is not None:
            old_message, old_task = old
            old_task.cancel()
            try:
                notice = await old_message.edit(content=msg)
            except discord.HTTPException:
                pass
        if notice is None:
            notice = await interaction.followup.send(msg, ephemeral=True, wait=True)
        self._notices[key] = (notice, asyncio.create_task(self._delete_later(key, notice)))

    async def _delete_later(self, key, message):
        await asyncio.sleep(VOTE_NOTICE_SECONDS)
        self._notices.pop(key, None)
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    async def _finish(self, message_id):
        async with self._lock:
            polls = await self.config.polls()
            poll = polls.pop(message_id, None)
            if poll is None:
                return
            await self.config.polls.set(polls)
        channel = self.bot.get_channel(poll["channel"])
        if channel is None:
            return
        message = None
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=poll_embed(poll, final=True), view=None)
        except discord.HTTPException:
            pass
        results = poll_embed(poll, final=True)
        results.title = f"Poll results: {poll['question']}"
        try:
            await channel.send(
                embed=results,
                reference=message.to_reference(fail_if_not_exists=False) if message else None,
            )
        except discord.HTTPException:
            pass

    def _cooldown_message(self, user_id) -> Optional[str]:
        last = self._last_poll.get(user_id)
        if last is None:
            return None
        remaining = POLL_COOLDOWN_SECONDS - (time.monotonic() - last)
        if remaining <= 0:
            return None
        ready = int(time.time() + remaining)
        return f"You can start another poll <t:{ready}:R>. Polls are limited to one per hour."

    async def red_delete_data_for_user(self, *, requester, user_id):
        self._last_poll.pop(user_id, None)
        for key in [k for k in self._notices if k[1] == user_id]:
            notice, task = self._notices.pop(key)
            task.cancel()
            try:
                await notice.delete()
            except discord.HTTPException:
                pass

        changed = []
        async with self._lock:
            polls = await self.config.polls()
            for message_id, poll in polls.items():
                if poll["votes"].pop(str(user_id), None) is not None:
                    changed.append((message_id, poll))
            if changed:
                await self.config.polls.set(polls)

        # Refresh the public embeds so the removed vote no longer counts.
        for message_id, poll in changed:
            channel = self.bot.get_channel(poll["channel"])
            if channel is None:
                continue
            try:
                await channel.get_partial_message(int(message_id)).edit(embed=poll_embed(poll))
            except discord.HTTPException:
                pass

    @app_commands.command(name="anonymouspoll-text", description="Start a poll without showing your name, using command options.")
    @app_commands.describe(
        question="What do you want to ask?",
        answer1="Choice 1",
        answer2="Choice 2",
        answer3="Choice 3 (optional)",
        answer4="Choice 4 (optional)",
        answer5="Choice 5 (optional)",
        answer6="Choice 6 (optional)",
        answer7="Choice 7 (optional)",
        answer8="Choice 8 (optional)",
        answer9="Choice 9 (optional)",
        answer10="Choice 10 (optional)",
        duration="How long the poll stays open (default: 24 hours)",
        multiple="Let people pick more than one answer (default: no)",
    )
    @app_commands.choices(duration=DURATION_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(send_messages=True, embed_links=True)
    async def anonymouspoll_text(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 1, 300],
        answer1: app_commands.Range[str, 1, 55],
        answer2: app_commands.Range[str, 1, 55],
        answer3: Optional[app_commands.Range[str, 1, 55]] = None,
        answer4: Optional[app_commands.Range[str, 1, 55]] = None,
        answer5: Optional[app_commands.Range[str, 1, 55]] = None,
        answer6: Optional[app_commands.Range[str, 1, 55]] = None,
        answer7: Optional[app_commands.Range[str, 1, 55]] = None,
        answer8: Optional[app_commands.Range[str, 1, 55]] = None,
        answer9: Optional[app_commands.Range[str, 1, 55]] = None,
        answer10: Optional[app_commands.Range[str, 1, 55]] = None,
        duration: int = 1440,
        multiple: bool = False,
    ):
        cooldown = self._cooldown_message(interaction.user.id)
        if cooldown:
            await interaction.response.send_message(cooldown, ephemeral=True)
            return

        options = [
            a.strip()
            for a in (
                answer1, answer2, answer3, answer4, answer5,
                answer6, answer7, answer8, answer9, answer10,
            )
            if a and a.strip()
        ]
        if len(options) < 2:
            await interaction.response.send_message(
                "Please fill in at least the first two choices.", ephemeral=True
            )
            return

        await self._post_poll(interaction, question, options, duration, multiple)

    @app_commands.command(
        name="anonymouspoll",
        description="Start a poll without showing your name, using a pop-up form.",
    )
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(send_messages=True, embed_links=True)
    async def anonymouspoll(self, interaction: discord.Interaction):
        cooldown = self._cooldown_message(interaction.user.id)
        if cooldown:
            await interaction.response.send_message(cooldown, ephemeral=True)
            return
        await interaction.response.send_modal(PollModal(self, PollState()))

    async def _post_poll(self, interaction, question, options, duration, multiple):
        cooldown = self._cooldown_message(interaction.user.id)
        if cooldown:
            if interaction.message is not None:
                await interaction.response.edit_message(content=cooldown, view=None)
            else:
                await interaction.response.send_message(cooldown, ephemeral=True)
            return

        poll = {
            "question": question,
            "options": options,
            "multiple": multiple,
            "end": time.time() + duration * 60,
            "channel": interaction.channel.id,
            "votes": {},
        }

        # Post via the channel (not the interaction) so Discord doesn't attribute the poll to the invoker.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        await interaction.channel.send("Someone has requested a poll!")
        async with self._lock:
            message = await interaction.channel.send(embed=poll_embed(poll), view=vote_view(options))
            async with self.config.polls() as polls:
                polls[str(message.id)] = poll
        self._last_poll[interaction.user.id] = time.monotonic()
        if interaction.message is not None:
            await interaction.edit_original_response(content="Your poll has been posted!")
        else:
            await interaction.followup.send("Your poll has been posted!", ephemeral=True)

        log_channel_id = await self.config.guild(interaction.guild).log_channel()
        log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel is not None:
            try:
                await log_channel.send(
                    f"{interaction.user.mention} (`{interaction.user.id}`) started a poll "
                    f"in {interaction.channel.mention}: {message.jump_url}\n> {question}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

    @app_commands.command(
        name="anonymouspoll-log",
        description="Admins: choose a channel that records who starts polls (leave empty to turn off).",
    )
    @app_commands.describe(channel="Where to log poll starters. Leave empty to stop logging.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def anonymouspoll_log(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        await self.config.guild(interaction.guild).log_channel.set(channel.id if channel else None)
        if channel:
            msg = f"Done! I'll log who starts each poll in {channel.mention}."
        else:
            msg = "Done! I'll no longer log who starts polls."
        await interaction.response.send_message(msg, ephemeral=True)
