#yoinked some of the ideas from https://github.com/aikaterna/aikaterna-cogs/tree/v3/chatchart
#and used https://github.com/TrustyJAID/Trusty-cogs/tree/master/insult for inspiration on the user stuff
#this is all pretty terrible
import discord
from redbot.core import Config
import requests
import json
from redbot.core import commands
from redbot.core import checks

BaseCog = getattr(commands, "Cog", object)

class VibeCheck(BaseCog):
    """check your vibe, my dude"""
    
    def __init__(self, bot):
        #idk if this identifier is used I just slammed the keypad a few times
        self.config = Config.get_conf(self, identifier=35103512135)
        default_global = {
            "watson_api_url": "NOTSET",
            "watson_api_key": "NOTSET"
        }
        self.config.register_global(**default_global)

        self.bot = bot
    
    #if someone can manage to pull the API key in cleartext I'll buy you a beer
    @commands.command()
    @checks.is_owner()
    async def setwatsonkey(self, ctx, value):
        await self.config.watson_api_key.set(value)
        await ctx.send("Set Watson API Key.")

    @commands.command()
    @checks.is_owner()
    async def setwatsonurl(self, ctx, value):
        await self.config.watson_api_url.set(value)
        await ctx.send("Set Watson API URL.")

    @commands.command()
    @checks.is_owner()
    async def getwatsoninfo(self, ctx):
        watsonurl = await self.config.watson_api_url()
        watsonkey = await self.config.watson_api_key()
        if watsonkey == "NOTSET":
            watsonkey = "key is not set!"
        else:
            watsonkey = "Watson key is set."
        await ctx.send("Watson URL is: {} and the {}".format(watsonurl, watsonkey))

    @commands.command()
    @checks.is_owner()
    async def clearwatsoninfo(self, ctx):
        await self.config.watson_api_url.set("NOTSET")
        await self.config.watson_api_key.set("NOTSET")
        await ctx.send("Cleared all Watson API data.")

    @commands.command()
    @commands.cooldown(1, 10)
    async def checkvibe(self, ctx, user: discord.Member = None, checklimit=200):
        emb = discord.Embed(description="lemme just check your vibe...", colour=0x00ccff)
        emb.set_thumbnail(url="https://i.ytimg.com/vi/mYugwGNOIeM/maxresdefault.jpg")
        em = await ctx.send(embed=emb)

        #I did this because it breaks when I have my name set to unicode shit it's kinda funny but also not
        if user == None:
            user = ctx.message.author
            userid = ctx.message.author.id
        else:
            userid = user.id

        vibedata = []
        textchannels = []

        if checklimit < 100:
            await em.delete()
            await ctx.send("needs to be more than 100 messages my dude")
            return

        #it was 4am and I didn't want to do this right so this is Good Enough(tm)
        try:
            for channel in ctx.message.guild.channels:
                if 'text' in channel.type:
                    textchannels.append(channel)

            for channel in textchannels:
                async for msg in channel.history(limit=checklimit):
                    if msg.author.id == userid:
                        if "checkvibe" in msg.content:
                            pass
                        else:
                            vibedata.append(msg.content)
        except discord.errors.Forbidden:
            pass

        #this will probably change in the future, but whatever, this version of the API works
        vibecheckurl = await self.config.watson_api_url() + "/v3/tone?version=2017-09-21&Content-Type=application/json"
        vibecheckkey = await self.config.watson_api_key()
        vibejson = json.dumps({"text": ' '.join(vibedata)})
    
        watsonresult = requests.post(vibecheckurl, data=vibejson, auth=('apikey', vibecheckkey))
        #what the fuck is watson doing???
        #print(watsonresult.text)
        vibetonedata = json.loads(watsonresult.text)
        totaltone = vibetonedata["document_tone"]["tones"]

        #if watson doesn't return anything
        if len(totaltone) == 0:
            await em.delete()
            await ctx.send("i can't check that vibe... maybe check more messages?")
            return

        tone1score = totaltone[0]["score"]*100
        tone1id = totaltone[0]["tone_name"]
        #shitty hack to get around watson only returning one value in the tones
        tone2score = 0.000
        tone2id = ""
        if len(totaltone) >= 2:
            tone2score = totaltone[1]["score"]*100
            tone2id = totaltone[1]["tone_name"]
        #end shitty hack
        vcresult = "passed"
        tnurl = "https://i.ytimg.com/vi/F0D1xwn0Kyc/maxresdefault.jpg"
        color = 0x00ff1a
        sadboi = ""

        #these are definitely not good vibes
        failtones = ["Sadness","Fear","Anger", "Tentative"]

        if any(badtone in tone1id for badtone in failtones):
            if tone1score > tone2score:
                vcresult = "failed"
                tnurl = "https://i.ytimg.com/vi/QKfkMqqNwWg/maxresdefault.jpg"
                color = 0xff0000
                sadboi = "\n\nto fix your vibe:\nhttps://youtu.be/HgzGwKwLmgM\nor\nhttps://youtu.be/LdOQ6qtoQ4I?t=12"

        if tone2id != "":
            if any(badtone in tone2id for badtone in failtones):
                if tone2score > tone1score:
                    vcresult = "failed"
                    tnurl = "https://i.ytimg.com/vi/QKfkMqqNwWg/maxresdefault.jpg"
                    color = 0xff0000
                    sadboi = "\n\nto fix your vibe:\nhttps://youtu.be/HgzGwKwLmgM\nor\nhttps://youtu.be/LdOQ6qtoQ4I?t=12"

        #begin another shitty hack to display SOMETHING that makes sense
        if tone2score == 0.000:
            tone2score = "a big fat nothin "
        #end shitty hack

        await em.delete()
        
        emb2 = discord.Embed(description="{}'s vibe check: {}\n\ni'm pickin up {}% {}\nand {}% {} {}".format(user.mention, vcresult, tone1score, tone1id, tone2score, tone2id, sadboi), colour=color)
        emb2.set_thumbnail(url=tnurl)
        em2 = await ctx.send(embed=emb2)
