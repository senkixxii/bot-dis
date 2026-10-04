import os

import discord
from discord.ext import commands


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        channel_id = os.getenv("WELCOME_CHANNEL_ID")
        self.channel_id = int(channel_id) if channel_id else None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self.bot.get_channel(self.channel_id) if self.channel_id else member.guild.system_channel
        if channel is None:
            return
        embed = discord.Embed(
            description=f"ยินดีต้อนรับ {member.mention} สู่ **{member.guild.name}** 🎉\nคุณเป็นสมาชิกคนที่ {member.guild.member_count}",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
