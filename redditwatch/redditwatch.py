import asyncio
import logging
import re
import time

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands


log = logging.getLogger("red.cactuscogs.redditwatch")
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
REDDIT_URL = "https://www.reddit.com/r/{}/new.json?limit=25"


class RedditWatch(commands.Cog):
    """Post new submissions from a subreddit to a configured channel."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=814603927451820, force_registration=True)
        self.config.register_guild(
            subreddit=None, channel=None, interval=5, last_post=None, last_checked=None
        )

    async def cog_load(self):
        self.check_subreddits.start()

    async def cog_unload(self):
        self.check_subreddits.cancel()

    @commands.hybrid_group(name="redditwatch", invoke_without_command=True)
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def redditwatch(self, ctx: commands.Context):
        """Configure Reddit notifications for this server."""
        await ctx.send(
            "Use `redditwatch setsubreddit <name>` and `redditwatch setchannel #channel` to configure notifications.",
            ephemeral=True,
        )

    @redditwatch.command(name="setsubreddit")
    async def set_subreddit(self, ctx: commands.Context, subreddit: str):
        """Choose the subreddit to watch."""
        subreddit = subreddit.strip()
        if subreddit.startswith("r/"):
            subreddit = subreddit[2:]
        if not SUBREDDIT_RE.fullmatch(subreddit):
            await ctx.send("Enter a subreddit name containing 2-21 letters, numbers, or underscores.", ephemeral=True)
            return

        guild_config = self.config.guild(ctx.guild)
        await guild_config.subreddit.set(subreddit)
        await guild_config.last_post.set(None)
        await guild_config.last_checked.set(None)
        await ctx.send(
            f"Now watching r/{subreddit}. The current newest post will be used as the starting point.",
            ephemeral=True,
        )

    @redditwatch.command(name="setchannel")
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Choose where new submissions will be posted."""
        await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.send(f"Reddit posts will be sent to {channel.mention}.", ephemeral=True)

    @redditwatch.command(name="setinterval")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Set how often to check Reddit, in minutes."""
        if minutes < 1:
            await ctx.send("The polling interval must be at least 1 minute.", ephemeral=True)
            return

        await self.config.guild(ctx.guild).interval.set(minutes)
        await ctx.send(f"Reddit will be checked every {minutes} minute(s).", ephemeral=True)

    @redditwatch.command(name="status")
    async def status(self, ctx: commands.Context):
        """Show the current Reddit notification settings."""
        settings = await self.config.guild(ctx.guild).all()
        subreddit = settings["subreddit"]
        channel_id = settings["channel"]
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        await ctx.send(
            f"Subreddit: {f'r/{subreddit}' if subreddit else 'not set'}\n"
            f"Channel: {channel.mention if channel else 'not set'}\n"
            f"Interval: every {settings['interval']} minute(s)",
            ephemeral=True,
        )

    async def _fetch_posts(self, session: aiohttp.ClientSession, subreddit: str):
        async with session.get(REDDIT_URL.format(subreddit)) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

        try:
            children = payload["data"]["children"]
            posts = []
            for child in children:
                post = child["data"]
                post_id = post["id"]
                title = post["title"]
                permalink = post["permalink"]
                if not all(isinstance(value, str) and value for value in (post_id, title, permalink)):
                    raise ValueError("Reddit returned a post with invalid fields.")
                posts.append(
                    {
                        "id": post_id,
                        "title": title,
                        "url": f"https://www.reddit.com{permalink}",
                    }
                )
            return posts
        except (KeyError, TypeError) as exc:
            raise ValueError("Reddit returned an unexpected response.") from exc

    @tasks.loop(seconds=30)
    async def check_subreddits(self):
        configs = []
        now = time.time()
        for guild in self.bot.guilds:
            guild_config = self.config.guild(guild)
            settings = await guild_config.all()
            if settings["subreddit"] and settings["channel"]:
                last_checked = settings["last_checked"]
                if (
                    last_checked is not None
                    and now - last_checked < settings["interval"] * 60
                ):
                    continue
                await guild_config.last_checked.set(now)
                configs.append((guild, settings))

        if not configs:
            return

        async with aiohttp.ClientSession(
            headers={"User-Agent": "CactusCogsRedditWatch/1.0"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            fetched = {}
            failed = set()
            for guild, settings in configs:
                subreddit = settings["subreddit"]
                try:
                    if subreddit not in fetched:
                        if subreddit in failed:
                            continue
                        fetched[subreddit] = await self._fetch_posts(session, subreddit)
                    posts = fetched[subreddit]
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    failed.add(subreddit)
                    log.exception("Failed to fetch new posts for r/%s.", subreddit)
                    continue

                if not posts:
                    continue

                guild_config = self.config.guild(guild)
                last_post = settings["last_post"]
                if last_post is None:
                    await guild_config.last_post.set(posts[0]["id"])
                    continue

                channel = guild.get_channel(settings["channel"])
                if not isinstance(channel, discord.TextChannel):
                    log.warning("Configured Reddit notification channel is missing in guild %s.", guild.id)
                    continue

                new_posts = []
                for post in posts:
                    if post["id"] == last_post:
                        break
                    new_posts.append(post)

                for post in reversed(new_posts):
                    try:
                        await channel.send(
                            f"**{post['title'][:500]}**\n{post['url']}",
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        log.exception(
                            "Failed to post Reddit submission %s in guild %s.",
                            post["id"],
                            guild.id,
                        )
                        break
                    await guild_config.last_post.set(post["id"])

    @check_subreddits.before_loop
    async def before_check_subreddits(self):
        await self.bot.wait_until_red_ready()
