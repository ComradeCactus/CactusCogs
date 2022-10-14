from .invitechecker import InviteChecker

def setup(bot):
    bot.add_cog(InviteChecker(bot))