from .casino import Casino

__red_end_user_data_statement__ = (
    "This cog stores Discord IDs, lotto ticket numbers and drawing dates, "
    "and casino data as needed for operation."
)


async def setup(bot):
    await bot.add_cog(Casino(bot))
