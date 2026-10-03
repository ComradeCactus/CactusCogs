import discord
from datetime import datetime, timezone, timedelta
import re
from redbot.core import Config, checks, commands, app_commands
import io, base64
from typing import Literal, Optional


class TheCounter(commands.Cog):
    """sigh. reset the counter."""

    def __init__(self, bot):
        self.config = Config.get_conf(self, identifier=982374098344412)
        default_global = {
            "banned_words": ['vore','v0re', 'v0r3'],
            "counter_last_date": '2023-11-03 23:14:15.537173',
            "last_offender": '',
        }
        self.config.register_global(**default_global)
        self.bot = bot
        self._cd = commands.CooldownMapping.from_cooldown(1, 60.0, commands.BucketType.user)

        def ratelimit_check(self, message):
            """Check if the user is rate limited."""
            bucket = self._cd.get_bucket(message)
            return bucket.update_rate_limit()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        msg = message.content.lower()
        banned_words = await self.config.banned_words()
        if re.search(fr"\b({'|'.join(banned_words)})\b", msg):
            currenttime = message.created_at.astimezone().replace(tzinfo=None)
            oldtime = datetime.strptime(await self.config.counter_last_date(), "%Y-%m-%d %H:%M:%S.%f")
            delta = currenttime - oldtime
            author = message.author.nick
            hours = delta.seconds//3600
            minutes = (delta.seconds//60)%60
            if author == None:
                author = message.author.global_name
            if minutes == 0:
                await message.reply("you couldn't even wait a minute, could you?", mention_author=True)
                await message.author.timeout(timedelta(seconds=60), reason=f"Couldn't wait a minute to say that word. You know the one.")
                await self.config.last_offender.set(message.author.id)
                await self.config.counter_last_date.set(str(currenttime))
                return
            await message.channel.send("😐 We were at {} days, {} hours, and {} minutes. Now it's at 0.\nCongratulations, {}. You did it.".format(delta.days, hours, minutes, author))
            await self.config.last_offender.set(message.author.id)
            await self.config.counter_last_date.set(str(currenttime))
            return