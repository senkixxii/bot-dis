import logging
import math
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


def attack_slots_view(owner_id: int, slots, allowed_keys, on_done) -> discord.ui.View:
    """เมนูเลือกช่องที่ต้องถืออาวุธเพื่อโจมตี + ปุ่ม "ไม่จำกัดช่อง" (on_done(interaction, keys) keys ว่าง = ไม่จำกัด)"""
    options = [s for s in slots if s["key"] in allowed_keys]
    view = OwnerView(owner_id, SlotSelect(options, on_done, multi=True, placeholder="ช่องที่ต้องถืออาวุธเพื่อโจมตี"))
    button = discord.ui.Button(label="ไม่จำกัดช่อง (ช่องไหนก็โจมตีได้)", style=discord.ButtonStyle.secondary)

    async def free(interaction: discord.Interaction):
        await on_done(interaction, [])

    button.callback = free
    view.add_item(button)
    return view


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


def fmt_starter_note(granted, skipped) -> str | None:
    if not granted and not skipped:
        return None
    lines = []
    if granted:
        lines.append("🎁 ได้รับชุดเริ่มต้น: " + ", ".join(f"{name} → {label}" for name, label in granted))
    lines += [f"⚠️ {msg}" for msg in skipped]
    return "\n".join(lines)


def _pct(hp: int, max_hp: int) -> float:
    return hp / max_hp if max_hp > 0 else 0.0


def hp_bar(hp: int, max_hp: int, width: int = 10) -> str:
    """หลอด HP แบบบล็อกอีโมจิ: เขียว ≥60% เหลือง ≥30% แดง <30% (ยังมีชีวิตอย่างน้อย 1 บล็อก)"""
    pct = _pct(hp, max_hp)
    filled = 0 if hp <= 0 else min(width, max(1, math.ceil(width * pct)))
    color = "🟩" if pct >= 0.6 else "🟨" if pct >= 0.3 else "🟥"
    return color * filled + "⬜" * (width - filled)


def hp_color(hp: int, max_hp: int) -> discord.Color:
    pct = _pct(hp, max_hp)
    if hp <= 0:
        return discord.Color(0x95A5A6)
    return discord.Color(0x2ECC71 if pct >= 0.6 else 0xF1C40F if pct >= 0.3 else 0xE74C3C)


def hp_text(hp: int, max_hp: int) -> str:
    text = f"{hp_bar(hp, max_hp)} **{hp}/{max_hp}**"
    return f"💀 ล้มแล้ว\n{text}" if hp <= 0 else text


def ammo_bar(loaded: int, mag_size: int) -> str:
    width = max(1, min(mag_size, 10))
    filled = 0 if loaded <= 0 else min(width, max(1, math.ceil(width * loaded / mag_size)))
    return "🟦" * filled + "⬜" * (width - filled)


def weapon_status(entry, labels: dict) -> str:
    """สถานะของอาวุธชิ้นหนึ่งในกระเป๋า: พร้อมโจมตีหรือไม่ เพราะอะไร"""
    if entry["attack_slots"] and entry["slot_key"] not in entry["attack_slots"]:
        return "⚠️ ต้องถือใน " + ", ".join(labels.get(k, k) for k in entry["attack_slots"])
    if entry["ammo_item_id"] is not None and entry["loaded"] < entry["ammo_per_attack"]:
        return "🔴 กระสุนไม่พอ ใช้ /reload"
    return "✅ พร้อมโจมตี"


def fmt_weapon(item, ammo_name: str | None = None, labels: dict | None = None) -> str | None:
    """บรรทัดสรุปคุณสมบัติอาวุธ (ไม่ใช่อาวุธ = None)"""
    if item["damage_min"] is None:
        return None
    dmg = str(item["damage_min"]) if item["damage_min"] == item["damage_max"] else f"{item['damage_min']}-{item['damage_max']}"
    parts = [f"⚔️ ดาเมจ {dmg}"]
    if item["ammo_item_id"] is not None:
        parts.append(f"กระสุน: {ammo_name or '?'} (แม็ก {item['mag_size']}, ใช้ {item['ammo_per_attack']} นัด/ครั้ง)")
    if item["attack_slots"]:
        parts.append("ถือที่: " + ", ".join((labels or {}).get(k, k) for k in item["attack_slots"]))
    return " · ".join(parts)


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

    async def corpse_ac(self, interaction: discord.Interaction, current: str):
        rows = await db.list_corpses(self.pool, interaction.guild_id)
        return [
            app_commands.Choice(name=f"{r['name']} (ศพ #{r['id']} · {r['items']} ชิ้น)"[:100], value=str(r["id"]))
            for r in rows
            if current.lower() in r["name"].lower()
        ][:25]

    async def corpse_item_ac(self, interaction: discord.Interaction, current: str):
        try:
            items = await db.corpse_items(self.pool, interaction.guild_id, int(interaction.namespace.corpse))
        except (ValueError, TypeError, db.InventoryError):
            return []
        names = sorted({e["name"] for e in items})
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    # ---------- แอดมิน: จัดการไอเท็ม ----------
    @app_commands.command(name="item-create", description="[แอดมิน] สร้างไอเท็มใหม่ (ใส่ damage = เป็นอาวุธ)")
    @app_commands.describe(
        name="ชื่อไอเท็ม เช่น ปืน",
        size="ขนาด/พื้นที่ที่กินในช่อง (ค่าเริ่มต้น 1)",
        description="คำอธิบาย",
        damage="ดาเมจ (ต่ำสุด) — ไม่ใส่ = โจมตีไม่ได้",
        damage_max="ดาเมจสูงสุด (ไม่ใส่ = เท่าต่ำสุด)",
        ammo_item="ไอเท็มที่ใช้เป็นกระสุน (ไม่ใส่ = ไม่ต้องใช้กระสุน โจมตีได้เลย)",
        mag_size="แม็กจุกระสุนกี่นัด (ต้องใส่เมื่อมี ammo_item)",
        ammo_per_attack="ใช้กระสุนกี่นัดต่อการโจมตี (ค่าเริ่มต้น 1)",
    )
    @app_commands.autocomplete(ammo_item=item_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def item_create(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        size: app_commands.Range[int, 1, 100] = 1,
        description: app_commands.Range[str, 0, 300] = "",
        damage: app_commands.Range[int, 0, 100000] | None = None,
        damage_max: app_commands.Range[int, 0, 100000] | None = None,
        ammo_item: str | None = None,
        mag_size: app_commands.Range[int, 1, 1000] | None = None,
        ammo_per_attack: app_commands.Range[int, 1, 1000] | None = None,
    ):
        weapon = None
        if damage is not None:
            weapon = dict(damage=damage, damage_max=damage_max, ammo_item=ammo_item, mag_size=mag_size, ammo_per_attack=ammo_per_attack)
            await db.check_weapon_args(self.pool, interaction.guild_id, weapon)  # เช็กล่วงหน้า ก่อนให้เลือกช่อง
        elif any(v is not None for v in (damage_max, ammo_item, mag_size, ammo_per_attack)):
            raise db.InventoryError("ต้องใส่ damage ด้วย ถึงจะตั้งเป็นอาวุธได้")
        slots = await db.list_slots(self.pool, interaction.guild_id)
        labels = {s["key"]: s["label"] for s in slots}

        async def finish(i: discord.Interaction, allowed: list[str], attack_slots: list[str] | None):
            try:
                w = {**weapon, "attack_slots": attack_slots} if weapon else None
                item = await db.create_item(self.pool, i.guild_id, name, description, allowed, size, i.user.id, weapon=w)
            except db.InventoryError as e:
                await i.response.edit_message(content=str(e), view=None)
                return
            text = f"✅ สร้าง {fmt_item(item)}\nใส่ได้ที่: {', '.join(labels.get(k, k) for k in allowed)}"
            if weapon:
                text += "\n" + fmt_weapon(item, ammo_item, labels)
            await i.response.edit_message(content=text, view=None)

        async def on_pick(i: discord.Interaction, keys: list[str]):
            if weapon is None:
                await finish(i, keys, None)
                return

            async def on_attack_slots(i2: discord.Interaction, attack_keys: list[str]):
                await finish(i2, keys, attack_keys)

            view = attack_slots_view(i.user.id, slots, keys, on_attack_slots)
            await i.response.edit_message(content=f"**{name}** — ต้องถือในช่องไหนถึงจะโจมตีได้?", view=view)

        view = OwnerView(interaction.user.id, SlotSelect(slots, on_pick, multi=True, placeholder="เลือกช่องที่ไอเท็มนี้ใส่ได้"))
        await interaction.response.send_message(f"สร้าง **{name}** — เลือกช่องที่ใส่ได้:", view=view, ephemeral=True)

    @app_commands.command(name="item-weapon", description="[แอดมิน] ตั้ง/แก้/ลบดาเมจและเงื่อนไขกระสุนของไอเท็ม")
    @app_commands.describe(
        item="ไอเท็มที่จะตั้ง",
        damage="ดาเมจ (ต่ำสุด)",
        damage_max="ดาเมจสูงสุด (ไม่ใส่ = เท่าต่ำสุด)",
        ammo_item="ไอเท็มที่ใช้เป็นกระสุน (ไม่ใส่ = ไม่ต้องใช้กระสุน)",
        mag_size="แม็กจุกระสุนกี่นัด (ต้องใส่เมื่อมี ammo_item)",
        ammo_per_attack="ใช้กระสุนกี่นัดต่อการโจมตี (ค่าเริ่มต้น 1)",
        clear="True = ลบคุณสมบัติอาวุธ (ไอเท็มจะโจมตีไม่ได้)",
    )
    @app_commands.autocomplete(item=item_ac, ammo_item=item_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def item_weapon(
        self,
        interaction: discord.Interaction,
        item: str,
        damage: app_commands.Range[int, 0, 100000] | None = None,
        damage_max: app_commands.Range[int, 0, 100000] | None = None,
        ammo_item: str | None = None,
        mag_size: app_commands.Range[int, 1, 1000] | None = None,
        ammo_per_attack: app_commands.Range[int, 1, 1000] | None = None,
        clear: bool = False,
    ):
        gid = interaction.guild_id
        if clear:
            it = await db.clear_weapon(self.pool, gid, item)
            await interaction.response.send_message(f"✅ **{it['name']}** ไม่ใช่อาวุธแล้ว (กระสุนที่บรรจุอยู่ถูกล้าง)", ephemeral=True)
            return
        if damage is None:
            raise db.InventoryError("ใส่ damage เพื่อตั้งเป็นอาวุธ หรือใส่ clear:True เพื่อลบ")
        target = await db.find_item(self.pool, gid, item)
        weapon = dict(damage=damage, damage_max=damage_max, ammo_item=ammo_item, mag_size=mag_size, ammo_per_attack=ammo_per_attack)
        await db.check_weapon_args(self.pool, gid, weapon, target["allowed_slots"])
        slots = await db.list_slots(self.pool, gid)
        labels = {s["key"]: s["label"] for s in slots}

        async def on_done(i: discord.Interaction, attack_keys: list[str]):
            try:
                row = await db.set_weapon(self.pool, gid, item, {**weapon, "attack_slots": attack_keys})
            except db.InventoryError as e:
                await i.response.edit_message(content=str(e), view=None)
                return
            await i.response.edit_message(content=f"✅ **{row['name']}**\n{fmt_weapon(row, ammo_item, labels)}", view=None)

        view = attack_slots_view(interaction.user.id, slots, target["allowed_slots"], on_done)
        await interaction.response.send_message(f"**{target['name']}** — ต้องถือในช่องไหนถึงจะโจมตีได้?", view=view, ephemeral=True)

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
        labels = {s["key"]: s["label"] for s in await db.list_slots(self.pool, interaction.guild_id)}
        for item in items[:25]:
            allowed = ", ".join(labels.get(k, k) for k in item["allowed_slots"])
            lines = [item["description"] or "-", f"ใส่ได้: {allowed}"]
            weapon = fmt_weapon(item, item["ammo_name"], labels)
            if weapon:
                lines.append(weapon)
            embed.add_field(name=f"{item['name']} (ขนาด {item['size']})", value="\n".join(lines), inline=False)
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

    # ---------- ตัวละคร / ยศ ----------
    async def _role_problem(self, guild: discord.Guild, role: discord.Role) -> str | None:
        """คืนข้อความปัญหาถ้าบอทให้ยศนี้ไม่ได้ ไม่มีปัญหาคืน None"""
        me = guild.me
        if role.is_default() or role.managed:
            return "ยศนี้ให้ผ่านบอทไม่ได้ (เป็น @everyone หรือยศของบอท/ระบบ)"
        if not me.guild_permissions.manage_roles:
            return "บอทไม่มีสิทธิ์ **จัดการยศ** (Manage Roles) — เปิดที่ ตั้งค่าเซิร์ฟเวอร์ → ยศ → ยศของบอท"
        if role >= me.top_role:
            return f"ยศของบอทต้องอยู่ **สูงกว่า** {role.mention} ในลำดับยศ — ลากยศของบอทขึ้นไปไว้เหนือยศนี้"
        return None

    @app_commands.command(name="player-role", description="[แอดมิน] ตั้งยศที่ผู้เล่นจะได้ตอนสร้างตัวละคร (ไม่ใส่ = ปิดการให้ยศ)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def player_role(self, interaction: discord.Interaction, role: discord.Role | None = None):
        if role is None:
            await db.set_player_role(self.pool, interaction.guild_id, None)
            await interaction.response.send_message("ปิดการให้ยศตอนสร้างตัวละครแล้ว", ephemeral=True)
            return
        problem = await self._role_problem(interaction.guild, role)
        await db.set_player_role(self.pool, interaction.guild_id, role.id)
        text = f"✅ ผู้เล่นจะได้ยศ {role.mention} ตอนสร้างตัวละคร"
        if problem:
            text += f"\n⚠️ แต่ตอนนี้ยังให้ไม่ได้: {problem}"
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="character-create", description="สร้างตัวละครของคุณ (ได้ยศผู้เล่นและชุดเริ่มต้น)")
    @app_commands.describe(name="ชื่อตัวละคร (2-32 ตัวอักษร)")
    @app_commands.guild_only()
    async def character_create(self, interaction: discord.Interaction, name: app_commands.Range[str, 2, 32]):
        name = name.strip()
        if len(name) < 2:
            raise db.InventoryError("ชื่อตัวละครสั้นเกินไป")
        granted, skipped = await db.create_character(self.pool, interaction.guild_id, interaction.user.id, name)
        lines = [f"🧑 {interaction.user.mention} สร้างตัวละคร **{name}** แล้ว"]

        role_id = await db.get_player_role(self.pool, interaction.guild_id)
        role = interaction.guild.get_role(role_id) if role_id else None
        if role:
            problem = await self._role_problem(interaction.guild, role)
            if problem is None:
                try:
                    await interaction.user.add_roles(role, reason=f"สร้างตัวละคร {name}")
                    lines.append(f"🏷️ ได้รับยศ {role.mention}")
                except discord.HTTPException:
                    problem = "บอทให้ยศไม่สำเร็จ"
            if problem:
                lines.append(f"⚠️ ยังไม่ได้ยศ: {problem} (แจ้งแอดมิน)")
        note = fmt_starter_note(granted, skipped)
        if note:
            lines.append(note)
        await interaction.response.send_message("\n".join(lines), allowed_mentions=discord.AllowedMentions(roles=False))

    @app_commands.command(name="character-delete", description="[แอดมิน] ลบตัวละครของผู้เล่น (ของในกระเป๋าหายหมด สร้างใหม่แล้วได้ชุดเริ่มต้นอีกครั้ง)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def character_delete(self, interaction: discord.Interaction, member: discord.Member):
        name = await db.delete_character(self.pool, interaction.guild_id, member.id)
        text = f"🗑️ ลบตัวละคร **{name}** ของ {member.mention} แล้ว (รวมของในกระเป๋า)"
        role_id = await db.get_player_role(self.pool, interaction.guild_id)
        role = interaction.guild.get_role(role_id) if role_id else None
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="ลบตัวละคร")
                text += f"\nถอดยศ {role.mention} แล้ว"
            except discord.HTTPException:
                text += f"\n⚠️ ถอดยศ {role.mention} ไม่สำเร็จ (เช็กสิทธิ์/ลำดับยศของบอท)"
        await interaction.response.send_message(text, ephemeral=True)

    # ---------- ชุดไอเท็มเริ่มต้น ----------
    @app_commands.command(name="starter-add", description="[แอดมิน] เพิ่มไอเท็มในชุดเริ่มต้นของผู้เล่นใหม่")
    @app_commands.describe(item="ไอเท็ม", slot="ช่องที่จะใส่ให้")
    @app_commands.autocomplete(item=item_ac, slot=slot_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def starter_add(self, interaction: discord.Interaction, item: str, slot: str):
        it, s = await db.add_starter(self.pool, interaction.guild_id, item, slot)
        await interaction.response.send_message(f"✅ ชุดเริ่มต้น: เพิ่ม **{it['name']}** ใส่ **{s['label']}**", ephemeral=True)

    @app_commands.command(name="starter-remove", description="[แอดมิน] เอาไอเท็มออกจากชุดเริ่มต้น")
    @app_commands.autocomplete(item=item_ac, slot=slot_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def starter_remove(self, interaction: discord.Interaction, item: str, slot: str | None = None):
        name = await db.remove_starter(self.pool, interaction.guild_id, item, slot)
        await interaction.response.send_message(f"🗑️ เอา **{name}** ออกจากชุดเริ่มต้นแล้ว", ephemeral=True)

    @app_commands.command(name="starter-list", description="ดูชุดไอเท็มเริ่มต้นของผู้เล่นใหม่")
    @app_commands.guild_only()
    async def starter_list(self, interaction: discord.Interaction):
        rows = await db.list_starters(self.pool, interaction.guild_id)
        if not rows:
            await interaction.response.send_message("ยังไม่ได้ตั้งชุดเริ่มต้น แอดมินเพิ่มได้ด้วย `/starter-add`", ephemeral=True)
            return
        slots = {s["key"]: s["label"] for s in await db.list_slots(self.pool, interaction.guild_id)}
        lines = [f"• {r['name']} → {slots.get(r['slot_key'], r['slot_key'])}" for r in rows]
        await interaction.response.send_message("🎒 ชุดเริ่มต้น\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="starter-give", description="[แอดมิน] แจกชุดเริ่มต้นให้ผู้เล่น (แจกซ้ำได้ ของอาจซ้ำกับที่มีอยู่)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def starter_give(self, interaction: discord.Interaction, member: discord.Member):
        await db.require_character(self.pool, interaction.guild_id, member.id)
        granted, skipped = await db.grant_starter(self.pool, interaction.guild_id, member.id, force=True)
        if not granted and not skipped:
            await interaction.response.send_message("ยังไม่ได้ตั้งชุดเริ่มต้น เพิ่มด้วย `/starter-add` ก่อน", ephemeral=True)
            return
        text = f"🎁 แจกชุดเริ่มต้นให้ {member.mention}\n" + (fmt_starter_note(granted, skipped) or "")
        await interaction.response.send_message(text)

    @app_commands.command(name="slot-list", description="ดูช่องเก็บของทั้งหมดและความจุ")
    @app_commands.guild_only()
    async def slot_list(self, interaction: discord.Interaction):
        slots = await db.list_slots(self.pool, interaction.guild_id)
        lines = [f"`{s['key']}` — {s['label']} (ความจุ {s['capacity']})" for s in slots]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ---------- เก็บ/แจก ----------
    async def _place_flow(
        self, interaction: discord.Interaction, target: discord.Member, item_name: str, slot: str | None, *, announce: str, place=None
    ):
        """ใส่ไอเท็มเข้ากระเป๋า target — place(key) -> (_, slot) ใช้แทน db.place ได้ (เช่นตอนลูทศพ)"""
        gid = interaction.guild_id
        item = await db.find_item(self.pool, gid, item_name)

        async def put(key: str):
            if place:
                return await place(key)
            return await db.place(self.pool, gid, target.id, item["name"], key)

        async def do_place(i: discord.Interaction, key: str):
            _, s = await put(key)
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
                _, s = await put(keys[0])
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
        await db.require_character(self.pool, interaction.guild_id, interaction.user.id)
        await self._place_flow(interaction, interaction.user, item, slot, announce="🎒 {user} เก็บ **{item}** ใส่ **{slot}**")

    @app_commands.command(name="give", description="[แอดมิน] แจกไอเท็มให้ผู้เล่น")
    @app_commands.autocomplete(item=item_ac, slot=slot_ac)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def give(self, interaction: discord.Interaction, member: discord.Member, item: str, slot: str | None = None):
        await db.require_character(self.pool, interaction.guild_id, member.id)
        await self._place_flow(interaction, member, item, slot, announce="🎁 {user} ได้รับ **{item}** ใส่ **{slot}**")

    @app_commands.command(name="loot", description="เก็บของจากศพเข้ากระเป๋า (ไม่ใส่ item = ดูของบนศพ)")
    @app_commands.describe(corpse="ศพที่เจอ", item="ของที่จะเก็บ (ไม่ใส่ = ดูว่ามีอะไรบ้าง)", slot="ช่องที่จะใส่ (ไม่ใส่ = ให้เลือก)")
    @app_commands.autocomplete(corpse=corpse_ac, item=corpse_item_ac, slot=slot_ac)
    @app_commands.guild_only()
    async def loot(self, interaction: discord.Interaction, corpse: str, item: str | None = None, slot: str | None = None):
        gid = interaction.guild_id
        await db.require_character(self.pool, gid, interaction.user.id)
        try:
            corpse_id = int(corpse)
        except ValueError:
            raise db.InventoryError("เลือกศพจากรายการที่ขึ้นให้") from None
        body = await db.get_corpse(self.pool, gid, corpse_id)
        items = await db.corpse_items(self.pool, gid, corpse_id)
        if item is None:
            if not items:
                await interaction.response.send_message(f"🪦 ศพของ **{body['name']}** ไม่มีของแล้ว", ephemeral=True)
                return
            lines, seen = [], {}
            for e in items:
                seen[e["name"]] = seen.get(e["name"], 0) + 1
            for name, n in sorted(seen.items()):
                mags = [e for e in items if e["name"] == name and e["mag_size"]]
                extra = "  🔫 " + ", ".join(f"{e['loaded']}/{e['mag_size']}" for e in mags) if mags else ""
                lines.append(f"• {name}" + (f" ×{n}" if n > 1 else "") + extra)
            await interaction.response.send_message(f"🪦 **ศพของ {body['name']}**\n" + "\n".join(lines), ephemeral=True)
            return
        if not any(e["name"].lower() == item.lower() for e in items):
            raise db.InventoryError(f"ศพของ **{body['name']}** ไม่มี **{item}**")

        async def place(key: str):
            _, it, s = await db.loot(self.pool, gid, interaction.user.id, corpse_id, item, key)
            return it, s

        name = body["name"].replace("{", "{{").replace("}", "}}")
        await self._place_flow(
            interaction, interaction.user, item, slot, place=place,
            announce="🪦 {user} เก็บ **{item}** จากศพของ **" + name + "** ใส่ **{slot}**",
        )

    async def _remove_player_role(self, guild: discord.Guild | None, member: discord.Member):
        """ถอดยศผู้เล่นตอนตัวละครตาย (ทำได้ก็ทำ ไม่ได้ก็ข้าม ไม่ให้การตายพัง)"""
        if guild is None:
            return
        role_id = await db.get_player_role(self.pool, guild.id)
        role = guild.get_role(role_id) if role_id else None
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="ตัวละครเสียชีวิต")
            except discord.HTTPException:
                log.warning("ถอดยศ %s จาก %s ไม่สำเร็จ", role_id, member.id)

    # ---------- ต่อสู้ ----------
    @app_commands.command(name="attack", description="โจมตีตัวละครของสมาชิกด้วยอาวุธในกระเป๋า")
    @app_commands.describe(weapon="อาวุธที่ใช้", target="ผู้เล่นที่จะโจมตี")
    @app_commands.autocomplete(weapon=held_ac)
    @app_commands.guild_only()
    async def attack(self, interaction: discord.Interaction, weapon: str, target: discord.Member):
        r = await db.attack(self.pool, interaction.guild_id, interaction.user.id, target.id, weapon)
        # ประกาศในห้องโดยไม่บอก HP เป้าหมาย (ผู้เล่นดู HP ตัวเองได้ด้วย /status)
        lines = [
            f"⚔️ **{r['attacker']['name']}** โจมตี {target.mention} (**{r['target']['name']}**) ด้วย **{r['item']['name']}** — ดาเมจ **{r['damage']}**",
        ]
        died = r["died"]
        if died:
            lines.append(f"💀 **{died['name']}** เสียชีวิต! ศพทิ้งของไว้ {died['items']} ชิ้น — ใช้ `/loot` เก็บได้")
        await interaction.response.send_message("\n".join(lines))
        if died:
            await self._remove_player_role(interaction.guild, target)
        if r["loaded"] is not None:  # กระสุนที่เหลือ บอกเฉพาะผู้โจมตี
            await interaction.followup.send(
                f"🔫 **{r['item']['name']}** เหลือกระสุน {ammo_bar(r['loaded'], r['mag_size'])} {r['loaded']}/{r['mag_size']}",
                ephemeral=True,
            )

    @app_commands.command(name="reload", description="เติมกระสุนเข้าอาวุธจากไอเท็มกระสุนในกระเป๋า")
    @app_commands.autocomplete(weapon=held_ac)
    @app_commands.guild_only()
    async def reload(self, interaction: discord.Interaction, weapon: str):
        await db.require_character(self.pool, interaction.guild_id, interaction.user.id)
        r = await db.reload(self.pool, interaction.guild_id, interaction.user.id, weapon)
        await interaction.response.send_message(
            f"🔫 {interaction.user.mention} บรรจุ **{r['item']['name']}** +{r['added']} นัด ({r['loaded']}/{r['mag_size']}) ใช้ {r['ammo_name']} {r['added']} ชิ้น"
        )

    @app_commands.command(name="hp", description="ดู HP ของตัวละครคุณ (แอดมินดูของคนอื่นได้)")
    @app_commands.guild_only()
    async def hp(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        if target.id != interaction.user.id and not interaction.permissions.manage_guild:
            raise app_commands.MissingPermissions(["manage_guild"])
        char = await db.require_character(self.pool, interaction.guild_id, target.id)
        await interaction.response.send_message(f"❤️ **{char['name']}**\n{hp_text(char['hp'], char['max_hp'])}", ephemeral=True)

    @app_commands.command(name="hp-set", description="[แอดมิน] ตั้ง HP ของตัวละคร (0 = ตายถาวร, ใส่ max_hp เพื่อเปลี่ยน HP สูงสุดด้วย)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def hp_set(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: app_commands.Range[int, 0, 1000000],
        max_hp: app_commands.Range[int, 1, 1000000] | None = None,
    ):
        gid = interaction.guild_id
        if hp == 0:  # HP 0 = ตายถาวร (ไม่มีคำสั่งชุบ) จึงต้องยืนยันก่อน
            char = await db.require_character(self.pool, gid, member.id)
            view = ConfirmView(interaction.user.id)
            await interaction.response.send_message(
                f"ตั้ง HP เป็น 0 = ตัวละคร **{char['name']}** ของ {member.mention} จะ **เสียชีวิตถาวร** (ไม่มีคำสั่งชุบ) ยืนยัน?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.confirmed:
                return
            _, died = await db.set_hp(self.pool, gid, member.id, 0, max_hp)
            await interaction.edit_original_response(content="💀 ดำเนินการแล้ว", view=None)
            await interaction.followup.send(
                f"💀 **{died['name']}** เสียชีวิต! ศพทิ้งของไว้ {died['items']} ชิ้น — ใช้ `/loot` เก็บได้"
            )
            await self._remove_player_role(interaction.guild, member)
            return
        char, _ = await db.set_hp(self.pool, gid, member.id, hp, max_hp)
        await interaction.response.send_message(f"❤️ **{char['name']}**\n{hp_text(char['hp'], char['max_hp'])}", ephemeral=True)

    @app_commands.command(name="hp-default", description="[แอดมิน] ตั้ง HP เริ่มต้นของตัวละครที่สร้างใหม่ (ไม่กระทบตัวละครเดิม)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only
    async def hp_default(self, interaction: discord.Interaction, value: app_commands.Range[int, 1, 1000000]):
        await db.set_default_hp(self.pool, interaction.guild_id, value)
        await interaction.response.send_message(f"✅ ตัวละครที่สร้างใหม่จะเริ่มที่ HP {value}", ephemeral=True)

    # ---------- กระเป๋า ----------
    @app_commands.command(name="status", description="ดูข้อมูลตัวละคร: หลอด HP ช่องกระเป๋า และกระสุน (เห็นคนเดียว)")
    @app_commands.describe(member="(แอดมิน) ผู้เล่นที่จะดู — ไม่ใส่ = ตัวคุณเอง")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        if target.id != interaction.user.id and not interaction.permissions.manage_guild:
            raise app_commands.MissingPermissions(["manage_guild"])
        char = await db.require_character(self.pool, interaction.guild_id, target.id)
        slots, entries = await db.inventory(self.pool, interaction.guild_id, target.id)
        labels = {s["key"]: s["label"] for s in slots}

        embed = discord.Embed(title=f"🧑 {char['name']}", color=hp_color(char["hp"], char["max_hp"]))
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="❤️ HP", value=hp_text(char["hp"], char["max_hp"]), inline=False)

        weapons = [e for e in entries if e["damage_min"] is not None]
        if weapons:
            lines = []
            for e in weapons:
                dmg = str(e["damage_min"]) if e["damage_min"] == e["damage_max"] else f"{e['damage_min']}-{e['damage_max']}"
                line = f"**{e['name']}** — {labels.get(e['slot_key'], e['slot_key'])} · ⚔️ {dmg}"
                if e["ammo_item_id"] is not None:
                    line += f"\n{ammo_bar(e['loaded'], e['mag_size'])} {e['loaded']}/{e['mag_size']}"
                lines.append(f"{line}\n{weapon_status(e, labels)}")
            embed.add_field(name="🔫 อาวุธ", value="\n\n".join(lines)[:1024], inline=False)

        for s in slots:
            held = [e for e in entries if e["slot_key"] == s["key"]]
            used = sum(e["size"] for e in held)
            embed.add_field(
                name=f"🎒 {s['label']} ({used}/{s['capacity']})",
                value="\n".join(
                    f"• {e['name']}" + (f"  🔫 {e['loaded']}/{e['mag_size']}" if e["mag_size"] else "") for e in held
                )[:1024]
                or "—",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="move", description="ย้ายไอเท็มไปช่องอื่น")
    @app_commands.autocomplete(item=held_ac, to_slot=slot_ac)
    @app_commands.guild_only()
    async def move(self, interaction: discord.Interaction, item: str, to_slot: str):
        await db.require_character(self.pool, interaction.guild_id, interaction.user.id)
        it, slot = await db.move(self.pool, interaction.guild_id, interaction.user.id, item, to_slot)
        await interaction.response.send_message(f"🔄 ย้าย **{it['name']}** ไป **{slot['label']}**", ephemeral=True)

    @app_commands.command(name="drop", description="ทิ้งไอเท็มจากกระเป๋า")
    @app_commands.autocomplete(item=held_ac, slot=slot_ac)
    @app_commands.guild_only()
    async def drop(self, interaction: discord.Interaction, item: str, slot: str | None = None):
        await db.require_character(self.pool, interaction.guild_id, interaction.user.id)
        it = await db.drop(self.pool, interaction.guild_id, interaction.user.id, item, slot)
        await interaction.response.send_message(f"⬇️ {interaction.user.mention} ทิ้ง **{it['name']}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
