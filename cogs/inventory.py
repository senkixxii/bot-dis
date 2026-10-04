import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import db

log = logging.getLogger("inventory")
SLOT_KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
admin_only = app_commands.checks.has_permissions(manage_guild=True)


class SlotSelect(discord.ui.Select):
    """เมนูเลือกช่อง ใช้ได้ทั้งตอนเลือกเงื่อนไขไอเท็ม และตอนเลือกช่องเก็บของ"""

    def __init__(self, slots, on_pick, *, multi: bool, placeholder: str):
        options = [
            discord.SelectOption(label=s["label"][:100], value=s["key"], description=f"ความจุ {s['capacity']}")
            for s in slots[:25]
        ]
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=len(options) if multi else 1)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction):
        await self.on_pick(interaction, list(self.values))


class OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, select: discord.ui.Select):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id


class ConfirmView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="ยืนยัน", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.confirmed = True
        await interaction.response.edit_message(content="กำลังดำเนินการ...", view=None)
        self.stop()

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="ยกเลิกแล้ว", view=None)
        self.stop()


def fmt_item(item) -> str:
    return f"**{item['name']}** (ขนาด {item['size']}) — {item['description'] or 'ไม่มีคำอธิบาย'}"


class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def pool(self):
        return self.bot.pool

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        error = getattr(error, "original", error)
        if isinstance(error, app_commands.MissingPermissions):
            msg = "คำสั่งนี้ใช้ได้เฉพาะแอดมิน (สิทธิ์ Manage Server)"
        elif isinstance(error, db.InventoryError):
            msg = str(error)
        else:
            log.exception("คำสั่ง inventory ผิดพลาด", exc_info=error)
            msg = "เกิดข้อผิดพลาดภายในบอท ลองใหม่อีกครั้ง"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ---------- autocomplete ----------
    async def item_ac(self, interaction: discord.Interaction, current: str):
        items = await db.list_items(self.pool, interaction.guild_id)
        return [app_commands.Choice(name=i["name"], value=i["name"]) for i in items if current.lower() in i["name"].lower()][:25]

    async def slot_ac(self, interaction: discord.Interaction, current: str):
        slots = await db.list_slots(self.pool, interaction.guild_id)
        return [
            app_commands.Choice(name=f"{s['label']} ({s['key']})", value=s["key"])
            for s in slots
            if current.lower() in s["key"] or current in s["label"]
        ][:25]

    async def held_ac(self, interaction: discord.Interaction, current: str):
        _, entries = await db.inventory(self.pool, interaction.guild_id, interaction.user.id)
        names = sorted({e["name"] for e in entries})
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    # ---------- แอดมิน: จัดการไอเท็ม ----------
    @app_commands.command(name="item-create", description="[แอดมิน] สร้างไอเท็มใหม่ แล้วเลือกช่องที่ใส่ได้")
    @app_commands.describe(name="ชื่อไอเท็ม เช่น ปืน", size="ขนาด/พื้นที่ที่กินในช่อง (ค่าเริ่มต้น 1)", description="คำอธิบาย")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def item_create(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        size: app_commands.Range[int, 1, 100] = 1,
        description: app_commands.Range[str, 0, 300] = "",
    ):
        slots = await db.list_slots(self.pool, interaction.guild_id)

        async def on_pick(i: discord.Interaction, keys: list[str]):
            try:
                item = await db.create_item(self.pool, i.guild_id, name, description, keys, size, i.user.id)
            except db.InventoryError as e:
                await i.response.edit_message(content=str(e), view=None)
                return
            labels = ", ".join(s["label"] for s in slots if s["key"] in keys)
            await i.response.edit_message(content=f"✅ สร้าง {fmt_item(item)}\nใส่ได้ที่: {labels}", view=None)

        view = OwnerView(interaction.user.id, SlotSelect(slots, on_pick, multi=True, placeholder="เลือกช่องที่ไอเท็มนี้ใส่ได้"))
        await interaction.response.send_message(f"สร้าง **{name}** — เลือกช่องที่ใส่ได้:", view=view, ephemeral=True)

    @app_commands.command(name="item-edit", description="[แอดมิน] แก้ไอเท็ม (ใส่เฉพาะช่องที่อยากเปลี่ยน)")
    @app_commands.describe(name="ไอเท็มที่จะแก้", new_name="ชื่อใหม่", description="คำอธิบายใหม่", size="ขนาดใหม่", edit_slots="True = เลือกช่องที่ใส่ได้ใหม่")
    @app_commands.autocomplete(name=item_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def item_edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: app_commands.Range[str, 1, 60] | None = None,
        description: app_commands.Range[str, 0, 300] | None = None,
        size: app_commands.Range[int, 1, 100] | None = None,
        edit_slots: bool = False,
    ):
        await db.find_item(self.pool, interaction.guild_id, name)  # เช็กว่ามีจริง

        async def apply(keys: list[str] | None) -> str:
            item = await db.update_item(self.pool, interaction.guild_id, name, new_name, description, size, keys)
            return (
                f"✅ แก้แล้ว: {fmt_item(item)}\nใส่ได้ที่: {', '.join(item['allowed_slots'])}\n"
                "(ของที่ผู้เล่นถืออยู่ไม่ถูกย้าย)"
            )

        if not edit_slots:
            await interaction.response.send_message(await apply(None), ephemeral=True)
            return

        slots = await db.list_slots(self.pool, interaction.guild_id)

        async def on_pick(i: discord.Interaction, keys: list[str]):
            try:
                text = await apply(keys)
            except db.InventoryError as e:
                text = str(e)
            await i.response.edit_message(content=text, view=None)

        view = OwnerView(interaction.user.id, SlotSelect(slots, on_pick, multi=True, placeholder="เลือกช่องที่ใส่ได้ใหม่"))
        await interaction.response.send_message(f"แก้ **{name}** — เลือกช่องที่ใส่ได้:", view=view, ephemeral=True)

    @app_commands.command(name="item-delete", description="[แอดมิน] ลบไอเท็ม (ของที่ผู้เล่นถืออยู่จะหายด้วย)")
    @app_commands.autocomplete(name=item_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def item_delete(self, interaction: discord.Interaction, name: str):
        held = await db.delete_item(self.pool, interaction.guild_id, name)
        await interaction.response.send_message(f"🗑️ ลบ **{name}** แล้ว (ลบจากกระเป๋าผู้เล่น {held} ชิ้น)", ephemeral=True)

    @app_commands.command(name="item-list", description="ดูรายการไอเท็มทั้งหมดในแคตาล็อก")
    @app_commands.guild_only()
    async def item_list(self, interaction: discord.Interaction):
        items = await db.list_items(self.pool, interaction.guild_id)
        if not items:
            await interaction.response.send_message("ยังไม่มีไอเท็ม แอดมินสร้างได้ด้วย `/item-create`", ephemeral=True)
            return
        embed = discord.Embed(title="📦 ไอเท็มทั้งหมด", color=discord.Color.blurple())
        for item in items[:25]:
            embed.add_field(
                name=f"{item['name']} (ขนาด {item['size']})",
                value=f"{item['description'] or '-'}\nใส่ได้: {', '.join(item['allowed_slots'])}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- แอดมิน: จัดการช่อง ----------
    @app_commands.command(name="slot-set", description="[แอดมิน] เพิ่ม/แก้ช่องเก็บของและความจุ")
    @app_commands.describe(key="รหัสช่อง ภาษาอังกฤษพิมพ์เล็ก/ตัวเลข/_ เช่น backpack", label="ชื่อที่แสดง เช่น เป้หลัง", capacity="ความจุ")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def slot_set(
        self,
        interaction: discord.Interaction,
        key: str,
        label: app_commands.Range[str, 1, 50],
        capacity: app_commands.Range[int, 1, 1000],
    ):
        key = key.lower()
        if not SLOT_KEY_RE.match(key):
            await interaction.response.send_message("รหัสช่องใช้ได้เฉพาะ a-z 0-9 _ (ไม่เกิน 32 ตัว)", ephemeral=True)
            return
        await db.set_slot(self.pool, interaction.guild_id, key, label, capacity)
        await interaction.response.send_message(f"✅ ช่อง `{key}` = **{label}** ความจุ {capacity}", ephemeral=True)

    @app_commands.command(name="slot-delete", description="[แอดมิน] ลบช่องเก็บของ (ต้องไม่มีใครถือของในช่องนั้น)")
    @app_commands.autocomplete(key=slot_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def slot_delete(self, interaction: discord.Interaction, key: str):
        slot = await db.delete_slot(self.pool, interaction.guild_id, key.lower())
        await interaction.response.send_message(f"🗑️ ลบช่อง **{slot['label']}** (`{slot['key']}`) แล้ว", ephemeral=True)

    @app_commands.command(name="slot-reset", description="[แอดมิน] คืนช่องเก็บของเป็นชุดเริ่มต้น (มือซ้าย/ขวา กระเป๋ากางเกง กระเป๋าสะพาย)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def slot_reset(self, interaction: discord.Interaction):
        view = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            "คืนช่องเป็นชุดเริ่มต้น? ช่องที่แอดมินเพิ่มเองจะถูกลบ และชื่อ/ความจุของช่องเริ่มต้นจะกลับเป็นค่าเดิม",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return
        try:
            removed = await db.reset_slots(self.pool, interaction.guild_id)
            text = f"✅ คืนช่องเป็นชุดเริ่มต้นแล้ว (ลบช่องที่เพิ่มเอง {removed} ช่อง)"
        except db.InventoryError as e:
            text = str(e)
        await interaction.edit_original_response(content=text, view=None)

    @app_commands.command(name="slot-list", description="ดูช่องเก็บของทั้งหมดและความจุ")
    @app_commands.guild_only()
    async def slot_list(self, interaction: discord.Interaction):
        slots = await db.list_slots(self.pool, interaction.guild_id)
        lines = [f"`{s['key']}` — {s['label']} (ความจุ {s['capacity']})" for s in slots]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ---------- เก็บ/แจก ----------
    async def _place_flow(self, interaction: discord.Interaction, target: discord.Member, item_name: str, slot: str | None, *, announce: str):
        gid = interaction.guild_id
        item = await db.find_item(self.pool, gid, item_name)

        async def do_place(i: discord.Interaction, key: str):
            _, s = await db.place(self.pool, gid, target.id, item["name"], key)
            text = announce.format(item=item["name"], slot=s["label"], user=target.mention)
            if i.response.is_done():
                await i.followup.send(text)
            else:
                await i.response.send_message(text)

        if slot:
            await do_place(interaction, slot)
            return

        candidates = await db.candidate_slots(self.pool, gid, target.id, item)
        if not candidates:
            raise db.InventoryError(f"ไม่มีช่องที่ใส่ **{item['name']}** ได้ หรือช่องที่ใส่ได้เต็มหมดแล้ว")
        if len(candidates) == 1:
            await do_place(interaction, candidates[0]["key"])
            return

        async def on_pick(i: discord.Interaction, keys: list[str]):
            try:
                _, s = await db.place(self.pool, gid, target.id, item["name"], keys[0])
            except db.InventoryError as e:
                await i.response.edit_message(content=str(e), view=None)
                return
            await i.response.edit_message(content="✅", view=None)
            await i.followup.send(announce.format(item=item["name"], slot=s["label"], user=target.mention))

        view = OwnerView(interaction.user.id, SlotSelect(candidates, on_pick, multi=False, placeholder="เลือกช่องที่จะใส่"))
        await interaction.response.send_message(f"จะใส่ **{item['name']}** ช่องไหน?", view=view, ephemeral=True)

    @app_commands.command(name="pickup", description="เก็บไอเท็มที่เห็นเข้ากระเป๋า")
    @app_commands.describe(item="ชื่อไอเท็ม", slot="ช่องที่จะใส่ (ไม่ใส่ = ให้เลือก)")
    @app_commands.autocomplete(item=item_ac, slot=slot_ac)
    @app_commands.guild_only()
    async def pickup(self, interaction: discord.Interaction, item: str, slot: str | None = None):
        await self._place_flow(interaction, interaction.user, item, slot, announce="🎒 {user} เก็บ **{item}** ใส่ **{slot}**")

    @app_commands.command(name="give", description="[แอดมิน] แจกไอเท็มให้ผู้เล่น")
    @app_commands.autocomplete(item=item_ac, slot=slot_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def give(self, interaction: discord.Interaction, member: discord.Member, item: str, slot: str | None = None):
        await self._place_flow(interaction, member, item, slot, announce="🎁 {user} ได้รับ **{item}** ใส่ **{slot}**")

    # ---------- กระเป๋า ----------
    @app_commands.command(name="inventory", description="ดูกระเป๋าของคุณ (แอดมินดูของคนอื่นได้)")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        if target.id != interaction.user.id and not interaction.permissions.manage_guild:
            raise app_commands.MissingPermissions(["manage_guild"])
        slots, entries = await db.inventory(self.pool, interaction.guild_id, target.id)
        embed = discord.Embed(title=f"🎒 กระเป๋าของ {target.display_name}", color=discord.Color.gold())
        for s in slots:
            held = [e for e in entries if e["slot_key"] == s["key"]]
            used = sum(e["size"] for e in held)
            embed.add_field(
                name=f"{s['label']} ({used}/{s['capacity']})",
                value="\n".join(f"• {e['name']}" for e in held) or "—",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=target.id == interaction.user.id)

    @app_commands.command(name="move", description="ย้ายไอเท็มไปช่องอื่น")
    @app_commands.autocomplete(item=held_ac, to_slot=slot_ac)
    @app_commands.guild_only()
    async def move(self, interaction: discord.Interaction, item: str, to_slot: str):
        it, slot = await db.move(self.pool, interaction.guild_id, interaction.user.id, item, to_slot)
        await interaction.response.send_message(f"🔄 ย้าย **{it['name']}** ไป **{slot['label']}**", ephemeral=True)

    @app_commands.command(name="drop", description="ทิ้งไอเท็มจากกระเป๋า")
    @app_commands.autocomplete(item=held_ac, slot=slot_ac)
    @app_commands.guild_only()
    async def drop(self, interaction: discord.Interaction, item: str, slot: str | None = None):
        it = await db.drop(self.pool, interaction.guild_id, interaction.user.id, item, slot)
        await interaction.response.send_message(f"⬇️ {interaction.user.mention} ทิ้ง **{it['name']}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
