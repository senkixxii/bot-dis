import logging
import os

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = os.getenv("PORT")  # โฮสต์อย่าง Render จะตั้งค่านี้ให้เอง

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.members = True  # ต้องเปิด "Server Members Intent" ใน Developer Portal ด้วย
intents.message_content = True  # ต้องเปิด "Message Content Intent" ใน Developer Portal ด้วย

EXTENSIONS = ["cogs.general", "cogs.fun", "cogs.welcome"]


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None

    async def close(self):
        if self.pool:
            await self.pool.close()
        await super().close()

    async def setup_hook(self):
        if PORT:
            await self.start_web_server(int(PORT))

        extensions = list(EXTENSIONS)
        if DATABASE_URL:
            self.pool = await db.create_pool(DATABASE_URL)
            await db.init_schema(self.pool)
            extensions.append("cogs.inventory")
        else:
            log.warning("ไม่พบ DATABASE_URL — ปิดระบบกระเป๋า (cogs.inventory)")

        for ext in extensions:
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

    async def start_web_server(self, port: int):
        # เว็บเล็ก ๆ ไว้ให้ UptimeRobot เรียกทุก 5 นาที กันโฮสต์ฟรีสั่งหลับ
        async def health(_request):
            return web.Response(text="Bot is running")

        app = web.Application()
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("เปิดเว็บ keep-alive ที่พอร์ต %d", port)

    async def on_ready(self):
        log.info("ล็อกอินเป็น %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name="/help"))


def main():
    if not TOKEN:
        raise SystemExit("ไม่พบ DISCORD_TOKEN — คัดลอก .env.example เป็น .env แล้วใส่โทเคนก่อน")
    Bot().run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
