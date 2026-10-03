from .channelclose import ChannelClose

def setup(bot):
    bot.add_cog(ChannelClose(bot))