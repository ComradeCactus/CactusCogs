import discord
import requests
import json
import asyncio
from redbot.core import Config, checks, commands, app_commands
from PIL import Image
import io, base64
from typing import Literal, Optional



class SentientPants(commands.Cog):
    """query stable diffusion to make for art. 
    requires the scheduler/enqueue function in 
    stable-diffusion-webui"""

    def __init__(self, bot):
        self.config = Config.get_conf(self, identifier=98237409834)
        default_global = {
            "stablediffhost" : "NOTSET",
            "sdxl" : False,
        }

        self.config.register_global(**default_global)
        self.bot = bot
        self.conversational = False


    @commands.hybrid_group(name="sentience", 
                            aliases=["alive","conversational"], 
                            description="Turn on conversation mode.")
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
    async def stablediffusion():
         print("yeet")


    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        if (message.mentions.has(self.bot.user.id)):
            self.conversational = True
            await message.reply("what do you want", mention_author=True)
        if self.conversational:
             print("yeehaw!")