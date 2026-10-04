from .hockeyschedule import HockeySchedule

async def setup(bot):
    e = HockeySchedule(bot)
    await bot.add_cog(e)