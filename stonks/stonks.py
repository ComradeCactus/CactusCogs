import discord
import requests
import json
import asyncio
from redbot.core import Config, checks, commands, app_commands
import io, base64
from typing import Literal, Optional


class Stonks(commands.Cog):
    """query, find, and gamble on stonks
    using redbot credits."""

    def __init__(self, bot):
        self.config = Config.get_conf(self, identifier=7149203856471)
        default_global = {
            "rapidapikey" : "NOTSET",
            "rapidapihost" : "NOTSET"            
        }

        self.config.register_global(**default_global)
        self.bot = bot

    @commands.hybrid_group(name="stonksetup", description="Manage the connection to Stonks")
    @checks.is_owner()
    async def stonksetup(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Need subcommand", ephemeral=True)

    @stonksetup.command(name="getrapidapi", description="Show the host and key for rapidapi")
    async def getrapidapi(self, ctx: commands.Context):
        host = await self.config.rapidapihost()
        key = await self.config.rapidapikey()
        await  ctx.send(str(host) + " " + str(key), ephemeral=True)

    @stonksetup.command(name="setrapidapi", description="Set the host and key for rapidapi")
    async def setrapidapi(self, ctx, host: str, key: str):
        """Set the rapidapi host and key"""
        await self.config.rapidapihost.set(host)
        await self.config.rapidapikey.set(key)
        await ctx.send("Set host to {} and key to {}".format(host, key), ephemeral=True)

    @commands.hybrid_group(name="stonks", aliases=["stonk"], description="Manage stonks")
    async def stonks(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Need subcommand", ephemeral=True)

    @stonks.command(name="quote", description="Get a quote for a ticker")
    async def quote(self, ctx, ticker: str):
        """Pull a quote from Yahoo Finance"""
        url = "https://mboum-finance.p.rapidapi.com/qu/quote"
        querystring = {"symbol":ticker}
        headers = {
            'X-RapidAPI-Key': await self.config.rapidapikey(),
            'X-RapidAPI-Host': await self.config.rapidapihost()
        }

        try:
            response = await asyncio.to_thread(
                requests.request, "GET", url, headers=headers, params=querystring, timeout=(5, 15)
            )
            response.raise_for_status()
            quote = response.json()
        except requests.exceptions.RequestException as e:
            await ctx.send("Error fetching quote: {}".format(e), ephemeral=True)
            return
        except ValueError:
            await ctx.send("The quote service returned an invalid response.", ephemeral=True)
            return

        if not isinstance(quote, list) or not quote or not isinstance(quote[0], dict):
            await ctx.send("No quote found for `{}`.".format(ticker), ephemeral=True)
            return

        data = quote[0]
        try:
            message = "{} asking price: {} {}".format(
                data["shortName"], data["regularMarketPrice"], data["currency"]
            )
        except KeyError:
            await ctx.send("The quote for `{}` was missing expected fields.".format(ticker), ephemeral=True)
            return
        await ctx.send(message)

    