from .vibecheck import VibeCheck

async def setup(bot):
        await bot.add_cog(VibeCheck(bot))