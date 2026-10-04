# บอทดิสคอร์ด (discord.py)

บอทดิสคอร์ดเริ่มต้นแบบ slash command เขียนด้วย Python + [discord.py](https://discordpy.readthedocs.io/)

## คำสั่งที่มี

| คำสั่ง | ทำอะไร |
|---|---|
| `/help` | ดูคำสั่งทั้งหมด |
| `/ping` | เช็กความหน่วง (มีแบบ `!ping` ด้วย) |
| `/hello` | ให้บอททักทาย |
| `/userinfo [member]` | ดูข้อมูลสมาชิก |
| `/roll [sides]` | ทอยลูกเต๋า |
| `/8ball <question>` | ถามลูกแก้ววิเศษ |
| `/choose <choices>` | ให้บอทช่วยเลือก เช่น `กะเพรา, ข้าวผัด` |

และส่งข้อความต้อนรับเมื่อมีสมาชิกใหม่เข้าเซิร์ฟเวอร์

## ขั้นตอนที่ 1: สร้างบอทใน Discord Developer Portal

1. ไปที่ https://discord.com/developers/applications แล้วกด **New Application** ตั้งชื่อบอท
2. เมนู **Bot** → กด **Reset Token** แล้วคัดลอกโทเคนเก็บไว้ (อย่าให้ใครเห็น!)
3. ในหน้าเดียวกัน เลื่อนลงไปที่ **Privileged Gateway Intents** แล้วเปิด
   - **Server Members Intent** (ใช้กับข้อความต้อนรับ)
   - **Message Content Intent** (ใช้กับคำสั่ง `!`)
4. เมนู **OAuth2 → URL Generator**
   - Scopes: ติ๊ก `bot` และ `applications.commands`
   - Bot Permissions: ติ๊ก `Send Messages`, `Embed Links`, `Read Message History`
   - คัดลอกลิงก์ด้านล่าง เปิดในเบราว์เซอร์ แล้วเลือกเซิร์ฟเวอร์ที่จะเชิญบอทเข้า

## ขั้นตอนที่ 2: รันบอท

ต้องมี Python 3.10 ขึ้นไป

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env           # Windows: copy .env.example .env
# แก้ไฟล์ .env ใส่ DISCORD_TOKEN

python bot.py
```

ถ้าขึ้น `ล็อกอินเป็น ...` แปลว่าบอทออนไลน์แล้ว

> 💡 ใส่ `GUILD_ID` (คลิกขวาที่เซิร์ฟเวอร์ → Copy Server ID, ต้องเปิด Developer Mode ในการตั้งค่า Discord ก่อน)
> เพื่อให้ slash command ขึ้นทันที ถ้าไม่ใส่ คำสั่งอาจใช้เวลาสักพักกว่าจะขึ้น

## เพิ่มคำสั่งใหม่

คำสั่งแยกเป็นหมวดอยู่ในโฟลเดอร์ `cogs/` ตัวอย่างเพิ่มคำสั่งใน `cogs/fun.py`:

```python
@app_commands.command(name="coin", description="โยนเหรียญ")
async def coin(self, interaction: discord.Interaction):
    await interaction.response.send_message(random.choice(["หัว", "ก้อย"]))
```

ถ้าสร้างไฟล์ cog ใหม่ อย่าลืมเพิ่มชื่อใน `EXTENSIONS` ใน `bot.py`

## รันฟรีตลอด 24 ชม. บน Render (ทำบนมือถือได้)

1. สมัคร https://render.com ด้วยบัญชี GitHub
2. กด **New → Blueprint** → เลือก repo นี้ (Render จะอ่าน `render.yaml` ให้เอง)
3. ใส่ `DISCORD_TOKEN` (ส่วน `GUILD_ID`, `WELCOME_CHANNEL_ID` ใส่หรือเว้นไว้ก็ได้) → กด **Apply**
4. รอ deploy เสร็จ จะได้ลิงก์ประมาณ `https://discord-bot-xxxx.onrender.com`
5. สมัคร https://uptimerobot.com → **Add New Monitor** → แบบ HTTP(s) → ใส่ลิงก์จากข้อ 4 → ตั้งให้เช็กทุก 5 นาที

ข้อ 5 สำคัญ: แพ็กเกจฟรีของ Render จะหลับเมื่อไม่มีใครเข้าเว็บ 15 นาที UptimeRobot จะคอยเรียกให้ตื่นตลอด
