from .stablediff import StableDiff

async def setup(bot):
    e = StableDiff(bot)
    await bot.add_cog(e)