from .redditwatch import RedditWatch


async def setup(bot):
    await bot.add_cog(RedditWatch(bot))
