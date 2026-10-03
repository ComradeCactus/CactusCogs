from .thecounter import TheCounter

async def setup(bot):
    e = TheCounter(bot)
    await bot.add_cog(e)