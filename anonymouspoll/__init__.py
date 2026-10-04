from .anonymouspoll import AnonymousPoll


async def setup(bot):
    await bot.add_cog(AnonymousPoll(bot))
