from re import A
from tabnanny import verbose
import discord
from deepdiff import DeepDiff
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.commands import Cog, Context


class InviteChecker(Cog):
    """check your vibe, my dude"""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9811198108111169420 , force_registration=True)
        default_guild = {"channel": "",
        "invitelinks": []
        }

        self.config.register_guild(**default_guild)


    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return
    
    def make_invite_dict(self, invites):
        storedinvites = "{"
        for invite in invites:
            storedinvites += "'"+ invite.id + "':" + str(invite.uses) + ", "
        if storedinvites.endswith(','):
            storedinvites = storedinvites[::-1] + "}"
        else:
            storedinvites += "}"
        invitedict = eval(storedinvites)
        return invitedict

    @commands.group(aliases=["invitelog","invlog"])
    @checks.mod_or_permissions(administrator=True)
    async def invitelogger(self, ctx):
        """Change the settings for the invite logger"""
        if ctx.invoked_subcommand is None:
            pass

    @invitelogger.command()
    @checks.mod_or_permissions(administrator=True)
    async def setchannel(self, ctx: Context, chan: discord.TextChannel):
        """Choose the channel to send invite log messages to"""
        guild = ctx.guild
        try:
            await self.config.guild(guild).channel.set(chan.id)
            await ctx.maybe_send_embed("Channel set to " + chan.name)
        except:
            await ctx.maybe_send_embed("Invalid channel, please try again.")

    @invitelogger.command()
    @checks.mod_or_permissions(administrator=True)
    async def getchannel(self, ctx: Context):
        """Get the channel the logger will post to."""
        guild = ctx.guild
        current = await self.config.guild(guild).channel()
        await ctx.maybe_send_embed("Current channel is: <#" + str(current)  + ">")

    @invitelogger.command()
    @checks.mod_or_permissions(administrator=True)
    async def syncinvites(self, ctx: Context):
        """Synchronize the config file with live data."""
        guild = ctx.guild
        invites = await guild.invites()
        invitedict = self.make_invite_dict(invites)
        await self.config.guild(guild).invitelinks.set(invitedict)
        await ctx.maybe_send_embed("Successfully synchronized invites to JSON file.")

    @invitelogger.command()
    @checks.mod_or_permissions(administrator=True)
    async def stats(self, ctx:Context):
        """Get current invite stats saved in the file."""
        guild = ctx.guild
        invitedata = await self.config.guild(guild).invitelinks()
        msg = ""
        for id, uses in invitedata.items():
            msg += "ID: " + str(id) + " - Uses: " + str(uses) + "\n"
        await ctx.maybe_send_embed(msg)

    @invitelogger.command()
    @checks.is_owner()
    async def dev(self, member: discord.Member):
        guild = member.guild
        member = member.author
        channel = guild.get_channel(await self.config.guild(guild).channel())
        fileinvites = await self.config.guild(guild).invitelinks()
        newinvites = self.make_invite_dict(await guild.invites())
        difference = DeepDiff(fileinvites,newinvites)
        invcode = ""
        if difference == {}:
            sendstr = "{} {} joined but I couldn't detect the invite, probably used a custom URL.".format(
                member, member.id
            )
            if await self.bot.embed_requested(channel, member):
                    await channel.send(
                        embed=discord.Embed(
                            description=sendstr, color=(await self.bot.get_embed_color(channel))
                        )
                    )
            else:
                await channel.send(sendstr)
            return
        for invite in difference['values_changed']:
            invite = invite.replace("root['","")
            invcode = invite.replace("']","")
            await channel.send(invcode)
        output = "{} joined using invite: {}".format(
            member, invcode
        )
        if await self.bot.embed_requested(channel, member):
            await channel.send(
                embed=discord.Embed(
                    description=output, color=(await self.bot.get_embed_color(channel))
                )
            )
        else:
             await channel.send(output)
        #await self.config.guild(guild).invitelinks.set(newinvites)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        channel = guild.get_channel(await self.config.guild(guild).channel())
        fileinvites = await self.config.guild(guild).invitelinks()
        newinvites = self.make_invite_dict(await guild.invites())
        difference = DeepDiff(fileinvites,newinvites)
        invcode = ""
        if difference == {}:
            sendstr = "{} {} joined but I couldn't detect the invite, probably used a custom URL.".format(
                member, member.id
            )
            if await self.bot.embed_requested(channel, member):
                    await channel.send(
                        embed=discord.Embed(
                            description=sendstr, color=(await self.bot.get_embed_color(channel))
                        )
                    )
            else:
                await channel.send(sendstr)
            return
        for invite in difference['values_changed']:
            invite = invite.replace("root['","")
            invcode = invite.replace("']","")
            #await channel.send(invcode)
        output = "{} joined using invite: {}".format(
            member, invcode
        )
        if await self.bot.embed_requested(channel, member):
            await channel.send(
                embed=discord.Embed(
                    description=output, color=(await self.bot.get_embed_color(channel))
                )
            )
        else:
             await channel.send(output)
        await self.config.guild(guild).invitelinks.set(newinvites)