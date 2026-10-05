import discord
from discord import app_commands
from discord.ext import commands


# (ชื่อหมวด, คำสั่งในหมวด, เฉพาะแอดมิน) — คำสั่งใหม่ให้เพิ่มชื่อที่นี่
HELP_CATEGORIES = [
    ("🧑 ตัวละคร", ["character-create"], False),
    ("⚔️ ต่อสู้", ["attack", "reload", "hp"], False),
    ("🎒 กระเป๋า", ["status", "pickup", "move", "drop", "item-list", "slot-list", "starter-list"], False),
    ("🎮 ทั่วไป", ["help", "ping", "hello", "userinfo", "choose"], False),
    ("🛡️ แอดมิน: ไอเท็มและช่อง", ["item-create", "item-edit", "item-weapon", "item-delete", "slot-set", "slot-delete", "slot-reset"], True),
    (
        "🛡️ แอดมิน: ผู้เล่นและชุดเริ่มต้น",
        ["give", "character-delete", "player-role", "hp-set", "hp-default", "starter-add", "starter-remove", "starter-give"],
        True,
    ),
]


def format_command(cmd: app_commands.Command) -> str:
    params = " ".join(f"<{p.name}>" if p.required else f"[{p.name}]" for p in cmd.parameters)
    usage = f"/{cmd.name} {params}".strip()
    return f"`{usage}` — {cmd.description.removeprefix('[แอดมิน] ')}"


def add_category(embed: discord.Embed, title: str, lines: list[str]):
    """ใส่หมวดลง embed โดยแบ่งเป็นหลาย field ถ้ายาวเกินขีดจำกัด 1024 ตัวอักษร"""
    chunk: list[str] = []
    for line in lines:
        if sum(len(x) + 1 for x in chunk) + len(line) > 1000:
            embed.add_field(name=title, value="\n".join(chunk), inline=False)
            chunk, title = [], f"{title} (ต่อ)"
        chunk.append(line)
    if chunk:
        embed.add_field(name=title, value="\n".join(chunk), inline=False)


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

    @app_commands.command(name="help", description="ดูคำสั่งทั้งหมดของบอท แบ่งตามหมวด")
    async def help(self, interaction: discord.Interaction):
        is_admin = bool(interaction.permissions.manage_guild)
        commands_by_name = {c.name: c for c in self.bot.tree.get_commands()}
        embed = discord.Embed(title="📖 คำสั่งทั้งหมด", color=discord.Color.blurple())
        listed = set()
        for title, names, admin_only in HELP_CATEGORIES:
            listed.update(names)
            if admin_only and not is_admin:
                continue
            lines = [format_command(commands_by_name[n]) for n in names if n in commands_by_name]
            add_category(embed, title, lines)
        # คำสั่งที่เพิ่มทีหลังแล้วยังไม่ได้จัดหมวด จะไม่หายไป
        others = [format_command(c) for n, c in sorted(commands_by_name.items()) if n not in listed]
        add_category(embed, "📌 อื่นๆ", others)
        embed.set_footer(text="<ค่าที่ต้องใส่>  [ค่าที่ไม่ใส่ก็ได้]")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # คำสั่งแบบ prefix (!ping) ไว้เป็นตัวอย่าง
    @commands.command(name="ping")
    async def ping_prefix(self, ctx: commands.Context):
        await ctx.send(f"🏓 Pong! {round(self.bot.latency * 1000)} ms")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
