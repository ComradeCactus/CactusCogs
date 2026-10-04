from .sentientpants import SentientPants

async def setup(bot):
    e = SentientPants(bot)
    await bot.add_cog(e)