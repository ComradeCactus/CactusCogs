import asyncio
import discord
import requests
import datetime
from redbot.core import Config, checks, commands
from discord.ext import tasks

class HockeySchedule(commands.Cog):
    """Change the channel description based on the NHL schedule for the selected team. (The Wild)"""

    time_update = datetime.time(hour=4, minute=0, tzinfo=datetime.timezone.utc)

    time_unlock = datetime.time(hour=11, minute=0, tzinfo=datetime.timezone.utc)

    def __init__(self, bot):
        super().__init__()
        self.config = Config.get_conf(self, identifier=91191191191169420, force_registration=True)
        default_guild = {"channel": "", "team": "MIN", "club_utc_offset": "-06:00"}
        self.config.register_guild(**default_guild)
        self.bot = bot
        self.update_all_guilds.start()
        
    def cog_unload(self):
        self.update_all_guilds.cancel()


    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete. This cog does not store user data."""
        return

    async def get_next_game(self, team: str):
        """Get the next team game from the NHL API."""
        baseuri = "https://api-web.nhle.com/v1/club-schedule/"
        teamuri = baseuri + team + "/week/now"
        
        try:
            response = await asyncio.to_thread(requests.get, teamuri, timeout=10)
            data = response.json()
            games = data.get("games", [])
            club_utc_offset = data.get("clubUTCOffset", "-06:00")
            now = datetime.datetime.now(datetime.timezone.utc)

            next_game = None
            for game in games:
                game_datetime = datetime.datetime.strptime(game["startTimeUTC"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                if game_datetime >= now:
                    next_game = game
                    break

            # If no next game is found in the current week, check the next week
            if not next_game:
                next_week_start = now + datetime.timedelta(days=(7 - now.weekday()))
                next_week_uri = baseuri + team + "/week/" + next_week_start.strftime("%Y-%m-%d")
                response = await asyncio.to_thread(requests.get, next_week_uri, timeout=10)
                data = response.json()
                games = data.get("games", [])
                for game in games:
                    game_datetime = datetime.datetime.strptime(game["startTimeUTC"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                    if game_datetime >= now:
                        next_game = game
                        break

            if next_game:
                start_time_utc = datetime.datetime.strptime(next_game['startTimeUTC'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                # Convert to Unix timestamp
                unix_timestamp = int(start_time_utc.timestamp())
                # Get the matchup
                away_team = next_game['awayTeam']['abbrev']
                home_team = next_game['homeTeam']['abbrev']
                return unix_timestamp, away_team, home_team
            else:
                print("No upcoming games found.")
        except requests.RequestException as e:
            print(f"Error fetching data: {e}")
        
    async def update_channel_description(self, ctx, guild: discord.Guild, channel_id: int, next_game: int, away_team: str, home_team: str):
        """Update the channel description with the next game."""
        async def respond(message: str):
            # Scheduled runs have no context to reply to
            if ctx is None:
                return
            if isinstance(ctx, discord.Interaction):
                # Only one initial response is allowed; later messages are follow-ups
                if ctx.response.is_done():
                    await ctx.followup.send(message, ephemeral=True)
                else:
                    await ctx.response.send_message(message, ephemeral=True)
            else:
                await ctx.send(message)

        channel = guild.get_channel(channel_id)
        if not channel:
            await respond("Channel not found.")
            return

        try:
            if channel.permissions_for(guild.me).manage_channels:
                description = f"Next game: {away_team}@{home_team} <t:{next_game}:R> (<t:{next_game}:f>)"
                await channel.edit(topic=description)
                await respond("Channel description updated.")
            else:
                await respond("I don't have permission to manage the channel.")
        except discord.DiscordException as e:
            await respond(f"Error updating channel description: {e}")
        
    @commands.hybrid_group(name="hockeyschedule", description="Manage the hockey schedule description changer.", invoke_without_command=True)
    @checks.mod_or_permissions(manage_channels=True)
    async def hockeyschedule(self, ctx: commands.Context):
        """Base command for the hockey schedule description changer."""
        if ctx.invoked_subcommand is None:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Need subcommand.", ephemeral=True)
            else:
                await ctx.send("Need subcommand.")
            
    @hockeyschedule.command(name="setchannel", description="Choose the channel to change the description of.")
    async def setchannel(self, ctx: commands.Context, chan: discord.TextChannel):
        """Choose the channel to change the description of"""
        guild = ctx.guild
        try:
            await self.config.guild(guild).channel.set(chan.id)
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Channel set to " + chan.name, ephemeral=True)
            else:
                await ctx.send("Channel set to " + chan.name)
        except discord.DiscordException as e:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Invalid channel, please try again. " + str(e), ephemeral=True)
            else:
                await ctx.send("Invalid channel, please try again. " + str(e))
            
    @hockeyschedule.command(name="getchannel", description="Get the currently set channel for description changes.")
    async def getchannel(self, ctx: commands.Context):
        """Get the channel the description changer will post to."""
        guild = ctx.guild
        current = await self.config.guild(guild).channel()
        if isinstance(ctx, discord.Interaction):
            await ctx.response.send_message("Current channel is: <#" + str(current) + ">", ephemeral=True)
        else:
            await ctx.send("Current channel is: <#" + str(current) + ">")
        
    @hockeyschedule.command(name="setteam", description="Choose the team to get the schedule for.\nShould be in 3-letter short name: MIN, ANA, etc.")
    async def setteam(self, ctx: commands.Context, team: str):
        """Choose the team to get the schedule for. Three letter name: MIN, ANA, etc."""
        guild = ctx.guild
        try:
            await self.config.guild(guild).team.set(team)
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Team set to " + team, ephemeral=True)
            else:
                await ctx.send("Team set to " + team)
        except discord.DiscordException as e:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Invalid team, please try again. " + str(e), ephemeral=True)
            else:
                await ctx.send("Invalid team, please try again. " + str(e))
            
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
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Channel not set. Use `[p]hockeyschedule setchannel #channel` to set the channel.", ephemeral=True)
            else:
                await ctx.send("Channel not set. Use `[p]hockeyschedule setchannel #channel` to set the channel.")
            return
        if team == "":
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Team not set. Use `[p]hockeyschedule setteam MIN` to set the team.", ephemeral=True)
            else:
                await ctx.send("Team not set. Use `[p]hockeyschedule setteam MIN` to set the team.")
            return
        try:
            result = await self.get_next_game(team)
            if result is None:
                msg = "Could not find an upcoming game. Try again later."
                if isinstance(ctx, discord.Interaction):
                    await ctx.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            nextgame, away_team, home_team = result
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Updating channel description with next game: " + str(nextgame), ephemeral=True)
            else:
                await ctx.send("Updating channel description with next game...")
            await self.update_channel_description(ctx, guild, channel, nextgame, away_team, home_team)
        except discord.DiscordException as e:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Error updating channel description. " + str(e), ephemeral=True)
            else:
                await ctx.send("Error updating channel description. " + str(e))
            
    @hockeyschedule.command(name="checksched", description="Manually check to see what the API returns.")
    async def checksched(self, ctx: commands.Context):
        """Manually check to see what the API returns."""
        guild = ctx.guild
        team = await self.config.guild(guild).team()
        try:
            nextgame = await self.get_next_game(team)
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Next game: " + str(nextgame), ephemeral=True)
            else:
                await ctx.send("Next game: " + str(nextgame))
        except discord.DiscordException as e:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message("Error checking schedule. " + str(e), ephemeral=True)
            else:
                await ctx.send("Error checking schedule. " + str(e))
                
    @tasks.loop(time=time_update)
    async def update_all_guilds(self):
        """Update the channel description with the next game."""
        print("Starting update task...")
        for guild in self.bot.guilds:
            print("Starting update for guild: " + guild.name)
            channel = await self.config.guild(guild).channel()
            team = await self.config.guild(guild).team()
            if channel == "" or team == "":
                continue
            try:
                result = await self.get_next_game(team)
                if result is None:
                    print(f"No upcoming game found for guild: {guild.name}")
                    continue
                nextgame, away_team, home_team = result
                await self.update_channel_description(None, guild, channel, nextgame, away_team, home_team)
            except discord.DiscordException as e:
                print(f"Error updating channel description: {e}")
                
    @update_all_guilds.error
    async def on_update_error(self, error):
        """Print an error if something goes wrong."""
        print(f"Error in update_task: {error}")