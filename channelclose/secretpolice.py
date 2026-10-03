import discord
import re

from redbot.core import bank, commands, Config, checks

listener = getattr(commands.Cog, "listener", None)  # red 3.0 backwards compatibility support



if listener is None:  # thanks Sinbad
    def listener(name=None):
        return lambda x: x


class Secretpolice(commands.Cog):
    """A cog to enforce degeneracy."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1892347129837412)
        default_global = {
            "badwords": ["UWU", "OWO", "0W0"],
            "fine": 350
        }
        self.config.register_global(**default_global)

    @commands.has_permissions(manage_roles=True)
    @commands.command()
    async def registerpolice(self, ctx):
        """Register this server with the secret police."""
        default_guild = {
            "badwords": ["UwU", "OwO", "owo", "uwu", "OWO", "UWU"],
            "fine": 350
        }
        #await ctx.send(default_guild)
        self.config.register_guild(**default_guild)
        await ctx.send("Registered the secret police with this server.")

    @listener()
    async def on_message(self, message):
        channel = message.channel
        guild = message.guild
        author = message.author
        authorname = message.author.display_name
        if message.guild is None:
            return
        if message.author.id != self.bot.user.id:
            #if "UwU" in message.content:
            badwords = await self.config.badwords()
            if any(msg.lower() in message.content.lower() for msg in badwords):
                usrbal = await bank.get_balance(message.author)
                if usrbal >= 350:
                    currency = await bank.get_currency_name(guild)
                    newbal = await bank.withdraw_credits(message.author, 350)
                    nouwu = "{} has been fined **350 {}**!".format(authorname, currency)
                    embed = discord.Embed(description=nouwu)
                    embed.colour = discord.Colour.red()
                    embed.set_author(name="STOP RIGHT THERE!",icon_url="https://www.emoji.co.uk/files/twitter-emojis/travel-places-twitter/10882-oncoming-police-car.png")
                    embed.set_image(url="https://i.imgur.com/ouxI0Nd.jpg")
                    embed.set_footer(text="{} now has: {} {}. You degenerate.".format(
                        authorname, newbal, currency))
                    await channel.send(embed=embed)
                else:
                    currency = await bank.get_currency_name(guild)
                    jailrole = discord.utils.get(guild.roles, name="Shame Gulag")
                    nouwu = "{} is too poor to afford the 350 {} fine!\n{} has been sent to jail!".format(
                        authorname, currency, authorname)
                    embed = discord.Embed(description=nouwu)
                    embed.colour = discord.Colour.red()
                    embed.set_author(name="STOP RIGHT THERE!",icon_url="https://www.emoji.co.uk/files/twitter-emojis/travel-places-twitter/10882-oncoming-police-car.png")
                    embed.set_image(url="http://clipart-library.com/images_k/jail-cell-bars-transparent/jail-cell-bars-transparent-4.png")
                    embed.set_footer(text="This is what you deserve. You degenerate.")
                    await channel.send(embed=embed)
                    await author.add_roles(jailrole)
            return