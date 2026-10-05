import logging

import discord
from discord import app_commands
from discord.ext import commands


log = logging.getLogger("general")

# (ชื่อหมวด, คำสั่งในหมวด) — คำสั่งใหม่ให้เพิ่มชื่อที่นี่ (ไม่เพิ่มก็ไม่หาย: ตกหมวด "อื่นๆ" ของฝั่งที่ถูกต้อง)
HELP_GENERAL = [
    ("🧑 ตัวละคร", ["character-create"]),
    ("⚔️ ต่อสู้", ["attack", "reload", "hp"]),
    ("🎒 กระเป๋า", ["status", "pickup", "move", "drop", "item-list", "slot-list", "starter-list"]),
    ("🎮 ทั่วไป", ["help", "ping", "hello", "userinfo", "choose"]),
]
HELP_ADMIN = [
    ("🛡️ ไอเท็มและช่อง", ["item-create", "item-edit", "item-weapon", "item-delete", "slot-set", "slot-delete", "slot-reset"]),
    (
        "🛡️ ผู้เล่น ตัวละคร และ HP",
        ["give", "character-delete", "player-role", "hp-set", "hp-default", "starter-add", "starter-remove", "starter-give"],
    ),
    ("🛡️ ทั่วไป", ["admin-help"]),
]


def is_admin_command(cmd: app_commands.Command) -> bool:
    """คำสั่งที่ตั้ง default_permissions (เช่น manage_guild) ถือเป็นคำสั่งแอดมิน"""
    return cmd.default_permissions is not None


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


def build_help(commands_by_name: dict, categories: list, title: str, *, admin: bool) -> discord.Embed:
    """สร้าง embed help ของฝั่งทั่วไป (admin=False) หรือฝั่งแอดมิน (admin=True)
    คำสั่งจะแสดงเฉพาะฝั่งที่ตรงกับสิทธิ์จริงของมัน ต่อให้ถูกใส่ผิดรายการ"""
    embed = discord.Embed(title=title, color=discord.Color.red() if admin else discord.Color.blurple())
    for cat_title, names in categories:
        lines = [
            format_command(commands_by_name[n])
            for n in names
            if n in commands_by_name and is_admin_command(commands_by_name[n]) == admin
        ]
        add_category(embed, cat_title, lines)
    listed = {n for _, names in HELP_GENERAL + HELP_ADMIN for n in names}
    others = [
        format_command(c) for n, c in sorted(commands_by_name.items()) if n not in listed and is_admin_command(c) == admin
    ]
    add_category(embed, "📌 อื่นๆ", others)
    return embed


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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        error = getattr(error, "original", error)
        if isinstance(error, app_commands.MissingPermissions):
            msg = "คำสั่งนี้ใช้ได้เฉพาะแอดมิน (สิทธิ์ Manage Server)"
        else:
            log.exception("คำสั่งผิดพลาด", exc_info=error)
            msg = "เกิดข้อผิดพลาดภายในบอท ลองใหม่อีกครั้ง"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="help", description="ดูคำสั่งทั่วไปของบอท แบ่งตามหมวด")
    async def help(self, interaction: discord.Interaction):
        embed = build_help({c.name: c for c in self.bot.tree.get_commands()}, HELP_GENERAL, "📖 คำสั่งทั่วไป", admin=False)
        legend = "<ค่าที่ต้องใส่>  [ค่าที่ไม่ใส่ก็ได้]"
        if interaction.permissions.manage_guild:
            legend += "  •  แอดมินดูคำสั่งแอดมินด้วย /admin-help"
        embed.set_footer(text=legend)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="admin-help", description="[แอดมิน] ดูคำสั่งสำหรับแอดมิน แบ่งตามหมวด")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_help(self, interaction: discord.Interaction):
        embed = build_help({c.name: c for c in self.bot.tree.get_commands()}, HELP_ADMIN, "🛡️ คำสั่งแอดมิน", admin=True)
        embed.set_footer(text="<ค่าที่ต้องใส่>  [ค่าที่ไม่ใส่ก็ได้]  •  คำสั่งทั่วไปดูด้วย /help")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # คำสั่งแบบ prefix (!ping) ไว้เป็นตัวอย่าง
    @commands.command(name="ping")
    async def ping_prefix(self, ctx: commands.Context):
        await ctx.send(f"🏓 Pong! {round(self.bot.latency * 1000)} ms")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
