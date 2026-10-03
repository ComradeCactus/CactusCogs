import discord
import requests
import datetime
import re
from redbot.core import Config, checks, commands, app_commands
        
class HockeySchedule(commands.Cog):
    """Change the channel description based on the NHL schedule for the selected team. (The Wild)"""

    def __init__(self, bot):
        super().__init__()
        self.config = Config.get_conf(self, identifier=91191191191169420 , force_registration=True)
        default_guild = {"channel": "",
                         "team": "MIN"}

        self.config.register_guild(**default_guild)
        
    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete. This cog does not store user data."""
        return
    
    async def get_next_game(self, team: str):
        """Get the next team game from the NHL API."""
        baseuri = "https://api-web.nhle.com/v1/club-schedule-season/"
        teamuri = baseuri + team + "/now"
        
        try:
            response = requests.get(teamuri)
            data = response.json()
            games = data.get("games", [])
            today = datetime.date.today()

            next_game = None
            for game in games:
                game_date = datetime.datetime.strptime(game["gameDate"], "%Y-%m-%d").date()
                if game_date >= today:
                    next_game = game
                    break

            if next_game:
                return next_game
            else:
                return "No upcoming games found."
        except requests.RequestException as e:
            return f"Error fetching data: {e}"
            

        
    
    @commands.hybrid_group(name="hockeyschedule", description="Manage the hockey schedule description changer.", invoke_without_command=True)
    @checks.mod_or_permissions(manage_channels=True)
    async def hockeyschedule(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.response.send_message("Need subcommand.", ephemeral=True)
            
    @hockeyschedule.command(name="setchannel", description="Choose the channel to change the description of.")
    async def setchannel(self, ctx: commands.Context, chan: discord.TextChannel):
        """Choose the channel to change the description of"""
        guild = ctx.guild
        try:
            await self.config.guild(guild).channel.set(chan.id)
            await ctx.maybe_send_embed("Channel set to " + chan.name)
        except discord.DiscordException as e:
            await ctx.response.send_message("Invalid channel, please try again. " + str(e), ephemeral=True)
            
    @hockeyschedule.command(name="getchannel", description="Get the currently set channel for description changes.")
    async def getchannel(self, ctx: commands.Context):
        """Get the channel the description changer will post to."""
        guild = ctx.guild
        current = await self.config.guild(guild).channel()
        await ctx.maybe_send_embed("Current channel is: <#" + str(current)  + ">")
        
    @hockeyschedule.command(name="setteam", description="Choose the team to get the schedule for.\nShould be in 3-letter short name: MIN, ANA, etc.")
    async def setteam(self, ctx: commands.Context, team: str):
        """Choose the team to get the schedule for. Three letter name: MIN, ANA, etc."""
        guild = ctx.guild
        try:
            await self.config.guild(guild).team.set(team)
            await ctx.maybe_send_embed("Team set to " + team)
        except discord.DiscordException as e:
            await ctx.response.send_message("Invalid team, please try again. " + str(e), ephemeral=True)
            
    @hockeyschedule.command(name="getteam", description="Get the currently set team for the schedule.")
    async def getteam(self, ctx: commands.Context):
        """Get the team the schedule changer will use."""
        guild = ctx.guild
        current = await self.config.guild(guild).team()
        if isinstance(ctx, discord.Interaction):
            await ctx.response.send_message("Current team is: " + current, ephemeral=True)
        else:
            await ctx.send("Current team is: " + current)
        
    @hockeyschedule.command(name="update", description="Manually update the channel description with the next game.")
    async def update(self, ctx: commands.Context):
        """Update the channel description with the next game."""
        guild = ctx.guild
        channel = await self.config.guild(guild).channel()
        team = await self.config.guild(guild).team()
        if channel == "":
            await ctx.response.send_message("Channel not set. Use `[p]hockeyschedule setchannel #channel` to set the channel.", ephemeral=True)
            return
        if team == "":
            await ctx.response.send_message("Team not set. Use `[p]hockeyschedule setteam MIN` to set the team.", ephemeral=True)
            return
        try:
            nextgame = self.get_next_game(team)
            await ctx.response.send_message("Updating channel description with next game: " + nextgame, ephemeral=True)
            #await self.update_channel_description(guild, channel, nextgame)
        except discord.DiscordException as e:
            await ctx.response.send_message("Error updating channel description. " + str(e), ephemeral=True)
            
    @hockeyschedule.command(name="checksched", description="Manually check to see what the API returns.")
    async def checksched(self, ctx: commands.Context):
        """Check the next game from the NHL API."""
        team = await self.config.guild(ctx.guild).team()
        nextgame = await self.get_next_game(team)
        await ctx.response.send_message(nextgame, ephemeral=True)