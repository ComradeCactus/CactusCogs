import discord
import re
import random
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.commands import Cog, Context
from redbot.core.data_manager import cog_data_path


listener = getattr(commands.Cog, "listener", None)  # red 3.0 backwards compatibility support

LINK_REGEX: re.Pattern = re.compile(
    r"(http[s]?:\/\/[^\"\']*\.(?:png|jpg|jpeg|gif|mp3|mp4|webp)).*", flags=re.I
)
IMAGE_REGEX: re.Pattern = re.compile(
    r"(?:(?:https?):\/\/)?[\w\/\-?=%.]+\.(?:png|jpg|jpeg|webp)+", flags=re.I
)


if listener is None:
    def listener(name=None):
        return lambda x: x

class ImageResponder(commands.Cog):
    """A cog to welcome people with a fun image! And to replace the jank retrigger rule."""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4204204204206911 , force_registration=True)
        default_guild = {"channel": "",
        "images": [],
        "listencmd": [""],
        "datapath": ""
        }

        self.config.register_guild(**default_guild)

    async def sanitize_filename(self, filename):
        """Sanitize the filename so people can't do the funny."""
        # Split the filename into name and extension
        name, ext = re.match(r'(.+?)(\.[^.]*$|$)', filename).groups()
        # Remove any characters that are not alphanumeric, underscore, hyphen, or dot
        sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        # Reconstruct the filename with the sanitized name and original extension
        sanitized_filename = sanitized_name + ext
        return sanitized_filename

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete, I don't collect user data."""
        print("lol" + str(kwargs))
        return

    async def make_image_list(self, guild):
        """Search the data path and put all the files in a list object"""
        datapath = cog_data_path(self).joinpath(str(guild.id))
        image_list = []

        # Ensure the directory exists
        if datapath.exists() and datapath.is_dir():
            # Iterate over all files in the directory
            for file in datapath.iterdir():
                if file.is_file():
                    image_list.append(file.name)

        return image_list

    @commands.group(aliases=["imgresponder","imgre"])
    @checks.mod_or_permissions(administrator=True)
    async def imageresponder(self, ctx):
        """Change the settings for the image responder"""
        if ctx.invoked_subcommand is None:
            pass

    @imageresponder.command()
    @checks.mod_or_permissions(administrator=True)
    async def initialize(self, ctx: Context):
        """Initialize the image responder cog. Only needs to be run once."""
        guild = ctx.guild
        if await self.config.guild(guild).datapath() == "":
            img_dir = cog_data_path(self).joinpath(str(guild.id))
            print("Initializing image responder for guild %s", guild.id)
            await self.config.guild(guild).datapath.set(str(img_dir))
            img_dir.mkdir(exist_ok=True, parents=True)
            await ctx.send("Image responder initialized.")
        else:
            await ctx.send("Image responder already initialized.")

    @imageresponder.command()
    @checks.is_owner()
    async def deinitialize(self, ctx: Context):
        """Deinitialize the image responder cog. For debugging."""
        guild = ctx.guild
        print("Deinitializing image responder for guild %s", guild.id)
        await self.config.guild(guild).datapath.set("")
        #img_dir.rmdir() -- maybe do this in the future.
        await ctx.send("Image responder deinitialized.")

    @imageresponder.command(aliases=["add"])
    @checks.mod_or_permissions(administrator=True)
    async def addimage(self, ctx: Context):
        """Add an image to the list of images to send"""
        guild = ctx.guild

        # Prompt the user to upload an image file
        await ctx.send("Please upload image files.")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.attachments

        try:
            # Wait for the user to upload a file
            message = await self.bot.wait_for('message', check=check, timeout=60.0)
            
            for attachment in message.attachments:
                # Check if the uploaded file is an image
                if not attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                    await ctx.send(f"Invalid file type: {attachment.filename}. Please upload an image file.")
                    continue
                # Save the uploaded image to the server
                sanitized_filename = await self.sanitize_filename(attachment.filename)
                file_path = str(cog_data_path(self)) + f"/{guild.id}/{sanitized_filename}"
                await attachment.save(file_path)

            images = await self.make_image_list(guild)
            await self.config.guild(guild).images.set(images)
            await ctx.send("Images added.")

        except TimeoutError:
            await ctx.send("You took too long to upload images. Please try again.")
        except Exception as e:
            await ctx.maybe_send_embed(f"An error occurred: {str(e)}")

    @imageresponder.command(aliases=["list"])
    @checks.mod_or_permissions(administrator=True)
    async def listimages(self, ctx: Context):
        """List the images that are currently available"""
        guild = ctx.guild
        images = await self.config.guild(guild).images()
        await ctx.send("Images available: " + ', '.join(images))


    @imageresponder.command(aliases=["remove", "del", "delete"])
    @checks.mod_or_permissions(administrator=True)
    async def removeimage(self, ctx: Context, imagename: str):
        """Remove an image from the list of images."""
        guild = ctx.guild
        getimage = await self.sanitize_filename(imagename)
        imgpath = cog_data_path(self).joinpath(str(guild.id)).joinpath(getimage)
        if imgpath.exists():
            imgpath.unlink()
            images = await self.make_image_list(guild)
            await self.config.guild(guild).images.set(images)
            await ctx.send("Image removed.")
        else:
            await ctx.send("Image not found.")

    @imageresponder.command(aliases=["addt"])
    @checks.mod_or_permissions(administrator=True)
    async def addtrigger(self, ctx: Context, trigger: str):
        """Add a trigger command to listen for"""
        guild = ctx.guild
        triggers = await self.config.guild(guild).listencmd()
        triggers.append(trigger)
        await self.config.guild(guild).listencmd.set(triggers)
        await ctx.send("Trigger added.")
        
    @imageresponder.command(aliases=["triggers"])
    @checks.mod_or_permissions(administrator=True)
    async def listtriggers(self, ctx: Context):
        """List the triggers that are currently available"""
        guild = ctx.guild
        triggers = await self.config.guild(guild).listencmd()
        await ctx.send("Triggers available: " + ', '.join(triggers))
        
    @imageresponder.command(aliases=["delt"])
    @checks.mod_or_permissions(administrator=True)
    async def removetrigger(self, ctx: Context, trigger: str):
        """Remove a trigger command from the list"""
        guild = ctx.guild
        triggers = await self.config.guild(guild).listencmd()
        if trigger in triggers:
            triggers.remove(trigger)
            await self.config.guild(guild).listencmd.set(triggers)
            await ctx.send("Trigger removed.")
        else:
            await ctx.send("Trigger not found")
            
    @imageresponder.command(aliases=["view"])
    @checks.mod_or_permissions(administrator=True)
    async def viewimage(self, ctx: Context, imagename: str):
        """View an image that is currently available"""
        guild = ctx.guild
        getimage = await self.sanitize_filename(imagename)
        imgpath = cog_data_path(self).joinpath(str(guild.id)).joinpath(getimage)
        if imgpath.exists():
            file = discord.File(imgpath)
            await ctx.send(file=file)
        else:
            await ctx.send("Image not found.")

    @listener()
    async def on_message(self, message):
        """Listen for the triggers on messages."""
        channel = message.channel
        guild = message.guild
        if guild is None:
            return
        if message.author.id != self.bot.user.id:
            if message.content in await self.config.guild(guild).listencmd():
                images = await self.config.guild(guild).images()
                if not images:
                    return
                imagefilename = random.choice(images)
                file = discord.File(str(cog_data_path(self)) + f"/{guild.id}/{imagefilename}")
                try:
                    async with channel.typing():
                        await channel.send(
                            file=file
                        )
                except Exception as e:
                    print(e)
