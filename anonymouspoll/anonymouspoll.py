import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from redbot.core import Config, commands

DURATION_CHOICES = [
    app_commands.Choice(name=name, value=hours)
    for name, hours in (
        ("1 hour", 1),
        ("4 hours", 4),
        ("8 hours", 8),
        ("24 hours", 24),
        ("3 days", 72),
        ("1 week", 168),
        ("2 weeks", 336),
    )
]


DURATION_NAMES = {c.value: c.name for c in DURATION_CHOICES}
CHOICES_PER_STEP = 5
POLL_COOLDOWN_SECONDS = 3600


class PollState:
    """Poll details collected across the form's steps."""

    def __init__(self):
        self.question = ""
        self.duration = 24
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
    """Start native Discord polls without revealing who started them."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7310294856123, force_registration=True)
        self.config.register_guild(log_channel=None)
        self._last_poll = {}  # user id -> monotonic time of their last posted poll

    def _cooldown_message(self, user_id) -> Optional[str]:
        last = self._last_poll.get(user_id)
        if last is None:
            return None
        remaining = POLL_COOLDOWN_SECONDS - (time.monotonic() - last)
        if remaining <= 0:
            return None
        ready = int(time.time() + remaining)
        return f"You can start another poll <t:{ready}:R>. Polls are limited to one per hour."

    async def red_delete_data_for_user(self, **kwargs):
        return

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
    @app_commands.checks.bot_has_permissions(send_messages=True, send_polls=True)
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
        duration: int = 24,
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
    @app_commands.checks.bot_has_permissions(send_messages=True, send_polls=True)
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

        poll = discord.Poll(
            question=question,
            duration=timedelta(hours=duration),
            multiple=multiple,
        )
        for option in options:
            poll.add_answer(text=option)

        # Post via the channel (not the interaction) so Discord doesn't attribute the poll to the invoker.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        await interaction.channel.send("Someone has requested a poll!")
        message = await interaction.channel.send(poll=poll)
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
