from .imageresponder import ImageResponder

async def setup(bot):
    await bot.add_cog(ImageResponder(bot))