import random

import discord
from discord import app_commands
from discord.ext import commands

EIGHT_BALL = ["แน่นอน", "ใช่เลย", "น่าจะใช่", "ยังไม่แน่ใจ ลองถามใหม่", "ไม่น่าใช่", "ไม่มีทาง", "อย่าหวังเลย"]


class Fun(commands.Cog):
    @app_commands.command(name="roll", description="ทอยลูกเต๋า")
    @app_commands.describe(sides="จำนวนหน้าลูกเต๋า (ค่าเริ่มต้น 6)")
    async def roll(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
        await interaction.response.send_message(f"🎲 ได้ **{random.randint(1, sides)}** (1-{sides})")

    @app_commands.command(name="8ball", description="ถามลูกแก้ววิเศษ")
    @app_commands.describe(question="คำถามของคุณ")
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(f"❓ {question}\n🎱 {random.choice(EIGHT_BALL)}")

    @app_commands.command(name="choose", description="ให้บอทช่วยเลือก (คั่นตัวเลือกด้วย ,)")
    @app_commands.describe(choices="เช่น ข้าวมันไก่, กะเพรา, ก๋วยเตี๋ยว")
    async def choose(self, interaction: discord.Interaction, choices: str):
        options = [c.strip() for c in choices.split(",") if c.strip()]
        if len(options) < 2:
            await interaction.response.send_message("ใส่อย่างน้อย 2 ตัวเลือก คั่นด้วย `,`", ephemeral=True)
            return
        await interaction.response.send_message(f"🤔 เลือก **{random.choice(options)}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun())
