import discord
import requests
import json
import asyncio
from redbot.core import Config, checks, commands, app_commands
from PIL import Image
import io, base64
from typing import Literal, Optional

class StableDiff(commands.Cog):
    """query stable diffusion to make for art. 
    requires the scheduler/enqueue function in 
    stable-diffusion-webui"""

    def __init__(self, bot):
        self.config = Config.get_conf(self, identifier=98237409834)
        default_global = {
            "stablediffhost" : "NOTSET",
        }

        self.config.register_global(**default_global)
        self.bot = bot

    @commands.hybrid_group(name="webui", description="Manage the connection to Stable Diffusion")
    @checks.is_owner()
    async def webui(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Need subcommand", ephemeral=True)
    
    @webui.command(name="getwebui", description="Show the host for stable diffusion")
    async def getwebui(self, ctx: commands.Context):
        host = await self.config.stablediffhost()
        await  ctx.send(str(host), ephemeral=True)
        
    @webui.command(name="setwebui", description="Set the host for stable diffusion")
    async def webuiurl(self, ctx, host: str):
        """Set the stable-diffusion-webui host"""
        await self.config.stablediffhost.set(host)
        await ctx.send("Set host to {}".format(host), ephemeral=True)
    
    @commands.hybrid_group(name="generate", 
                           aliases=["stablediffusion","sd"], 
                           description="Create art with stable diffusion")
    async def generate(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Need subcommand", ephemeral=True)

    @generate.command(name="fromprompt", description="Generate art from a prompt")
    @discord.app_commands.describe(positiveprompt = "The prompt to generate art from", 
                                   negativeprompt = "Things you don't want to see in the generated image",
                                   seed = "The seed to use for the generation, can be used to continue a previous generation",
                                   sampler = "The sampling method to use. Play around! (default: Euler a)")
    @discord.app_commands.choices(sampler = [
        discord.app_commands.Choice(name="Euler a", value="Euler a"),
        discord.app_commands.Choice(name="DPM2 a", value="DPM2 a"),
        discord.app_commands.Choice(name="DPM++ 2S a", value="DPM++ 2S a"),
        discord.app_commands.Choice(name="DPM++ 2S a Karras", value="DPM++ 2S a Karras"),
        discord.app_commands.Choice(name="DPM++ 2M", value="DPM++ 2M"),
        discord.app_commands.Choice(name="DPM++ 2M Karras", value="DPM++ 2M Karras")
    ])
    async def stablediffusion(
        self, 
        ctx: commands.Context, 
        positiveprompt: str, 
        negativeprompt: Optional[str] = "", 
        seed: Optional[str] = "-1",
        sampler: Optional[str] = "Euler a"):
        """Generate art from a prompt"""

        origmessage = ctx.message
        stablediffhost = await self.config.stablediffhost()
        if stablediffhost.endswith("/"):
            stablediffhost = stablediffhost[:-1]
        txt2img = stablediffhost + "/agent-scheduler/v1/queue/txt2img"
        taskquery = stablediffhost + "/agent-scheduler/v1/results/"
        queuequery = stablediffhost + "/agent-scheduler/v1/queue/"
        #await ctx.send("Sending request to {} with prompts: {}, {}".format(txt2img,positiveprompt, negativeprompt), ephemeral=True)
        prompt = {'prompt': positiveprompt, 'negative_prompt': negativeprompt, 'seed': seed, 'sampler_name': sampler}
        response = requests.post(txt2img, json=prompt)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            await ctx.send("Error: {}".format(e), ephemeral=True)
            return

        #await self.bot.send.typing(ctx.channel)
        taskid = json.loads(response.content).get('task_id')
        await ctx.send("Prompt accepted by stable diffusion, waiting for task to finish...\n(Task ID: {})".format(taskid), ephemeral=True)
        
        ready = False
        queue = []
        sleepcount = 0
        await asyncio.sleep(2)
        while not ready:
            queueresult = requests.get(queuequery)
            queuecontent = json.loads(queueresult.content)
            for id in queuecontent['pending_tasks']:
                queue.append(id['id'])
            if taskid not in queue:
                ready = True
                break
            await asyncio.sleep(5)
            queue = []
            sleepcount += 1
            if sleepcount > 120:
                await ctx.send("Error: Task timed out for {} - it probably finished anyway, use /generate gettask and the id.".format(taskid), ephemeral=True)
                return

        result = requests.get((taskquery + taskid))
        resultcontent = json.loads(result.content)
        image = resultcontent['data'][0]['image']
        headerindex = image.index(',')+1
        b64decoded = base64.b64decode(image[headerindex:])
        filename = taskid + ".png"
        await ctx.channel.send("Generation complete for {}! (Task ID: {})".format(origmessage.author.name, taskid),
                       file = discord.File(io.BytesIO(b64decoded), 
                       filename=filename), mention_author=True)
        await ctx.channel.send("###Generation details###\nPrompt: {}".format(resultcontent['data'][0]['infotext']))
        
        
    @generate.command(name="gettask", description="retrieve a previous task by ID")
    async def gettask(self, ctx: commands.Context, taskid: str):
        origmessage = ctx.message
        stablediffhost = await self.config.stablediffhost()
        if stablediffhost.endswith("/"):
            stablediffhost = stablediffhost[:-1]
        taskquery = stablediffhost + "/agent-scheduler/v1/results/"
        result = requests.get((taskquery + taskid))
        resultcontent = json.loads(result.content)
        image = resultcontent['data'][0]['image']
        headerindex = image.index(',')+1
        #await ctx.send("Header Index: {}, Image Sample: {}".format(headerindex, image[0:50]))
        b64decoded = base64.b64decode(image[headerindex:])
        filename = taskid + ".png"
        await ctx.send("Previously generated image for {} ({}):".format(origmessage.author.name, taskid),
                        file = discord.File(io.BytesIO(b64decoded), 
                        filename=filename), mention_author=True, reference=origmessage)
        await ctx.channel.send("###Generation details###\nPrompt: {}".format(resultcontent['data'][0]['infotext']))
    
    @generate.command(name="currentmodel", description="Show the current running model")
    async def currentmodel(self, ctx: commands.Context):
        stablediffhost = await self.config.stablediffhost()
        if stablediffhost.endswith("/"):
            stablediffhost = stablediffhost[:-1]
        configendpoint = stablediffhost + "/config"
        result = requests.get(configendpoint)
        resultcontent = json.loads(result.content)
        model = resultcontent['components'][1]['props']['value']
        await ctx.send("Current model: {}".format(model))