import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="เช็กความหน่วงของบอท")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🏓 Pong! {round(self.bot.latency * 1000)} ms")

    @app_commands.command(name="hello", description="ให้บอททักทาย")
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"สวัสดี {interaction.user.mention} 👋")

    @app_commands.command(name="userinfo", description="ดูข้อมูลสมาชิก")
    @app_commands.describe(member="สมาชิกที่ต้องการดู (ไม่ใส่ = ตัวเอง)")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        embed = discord.Embed(title=str(member), color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=False)
        embed.add_field(name="สร้างบัญชีเมื่อ", value=discord.utils.format_dt(member.created_at, "R"))
        if member.joined_at:
            embed.add_field(name="เข้าเซิร์ฟเมื่อ", value=discord.utils.format_dt(member.joined_at, "R"))
        roles = [r.mention for r in reversed(member.roles) if r != interaction.guild.default_role]
        embed.add_field(name=f"ยศ ({len(roles)})", value=" ".join(roles[:20]) or "-", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="ดูคำสั่งทั้งหมดของบอท")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📖 คำสั่งทั้งหมด", color=discord.Color.blurple())
        for cmd in sorted(self.bot.tree.get_commands(), key=lambda c: c.name):
            embed.add_field(name=f"/{cmd.name}", value=cmd.description, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # คำสั่งแบบ prefix (!ping) ไว้เป็นตัวอย่าง
    @commands.command(name="ping")
    async def ping_prefix(self, ctx: commands.Context):
        await ctx.send(f"🏓 Pong! {round(self.bot.latency * 1000)} ms")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
