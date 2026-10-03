from .stonks import Stonks

async def setup(bot):
    e = Stonks(bot)
    await bot.add_cog(e)