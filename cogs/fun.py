import random

import discord
from discord import app_commands
from discord.ext import commands


class Fun(commands.Cog):
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
