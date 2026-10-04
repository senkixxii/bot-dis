import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.members = True  # ต้องเปิด "Server Members Intent" ใน Developer Portal ด้วย
intents.message_content = True  # ต้องเปิด "Message Content Intent" ใน Developer Portal ด้วย

EXTENSIONS = ["cogs.general", "cogs.fun", "cogs.welcome"]


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("โหลด %s แล้ว", ext)

        if GUILD_ID:
            # sync เฉพาะเซิร์ฟเวอร์เดียว: คำสั่งขึ้นทันที เหมาะกับตอนพัฒนา
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            # sync ทุกเซิร์ฟเวอร์: อาจใช้เวลาสักพักกว่าคำสั่งจะขึ้น
            synced = await self.tree.sync()
        log.info("sync slash command แล้ว %d คำสั่ง", len(synced))

    async def on_ready(self):
        log.info("ล็อกอินเป็น %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name="/help"))


def main():
    if not TOKEN:
        raise SystemExit("ไม่พบ DISCORD_TOKEN — คัดลอก .env.example เป็น .env แล้วใส่โทเคนก่อน")
    Bot().run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
