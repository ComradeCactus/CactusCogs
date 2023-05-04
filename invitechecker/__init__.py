from .invitechecker import InviteChecker

async def setup(bot):
    await bot.add_cog(InviteChecker(bot))