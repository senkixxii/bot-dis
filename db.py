import random
from pathlib import Path

import asyncpg

SCHEMA_FILE = Path(__file__).parent / "sql" / "inventory.sql"

DEFAULT_SLOTS = [
    ("left_hand", "มือซ้าย", 1),
    ("right_hand", "มือขวา", 1),
    ("pants_pocket", "กระเป๋ากางเกง", 4),
    ("shoulder_bag", "กระเป๋าสะพาย", 10),
]


class InventoryError(Exception):
    """ข้อผิดพลาดที่แสดงให้ผู้ใช้อ่านได้ (ข้อความภาษาไทย)"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    # statement_cache_size=0 เพื่อให้ใช้ได้กับ pooler แบบ transaction ของ Supabase
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5, statement_cache_size=0)


async def init_schema(pool: asyncpg.Pool):
    """สร้าง/อัปเดตตารางตาม sql/inventory.sql (รันซ้ำได้ ไม่ทับข้อมูลเดิม)"""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


async def ensure_default_slots(conn, guild_id: int):
    """ใส่ช่องเริ่มต้นให้เซิร์ฟเวอร์ที่ยังไม่มีช่องเลย (ทำครั้งเดียว)"""
    if await conn.fetchval("select 1 from slots where guild_id = $1 limit 1", guild_id):
        return
    await conn.executemany(
        "insert into slots (guild_id, key, label, capacity) values ($1, $2, $3, $4) on conflict do nothing",
        [(guild_id, k, label, cap) for k, label, cap in DEFAULT_SLOTS],
    )


async def list_slots(pool, guild_id: int):
    async with pool.acquire() as conn:
        await ensure_default_slots(conn, guild_id)
        return await conn.fetch("select * from slots where guild_id = $1 order by key", guild_id)


async def set_slot(pool, guild_id: int, key: str, label: str, capacity: int):
    async with pool.acquire() as conn:
        await ensure_default_slots(conn, guild_id)
        await conn.execute(
            """insert into slots (guild_id, key, label, capacity) values ($1, $2, $3, $4)
               on conflict (guild_id, key) do update set label = excluded.label, capacity = excluded.capacity""",
            guild_id, key, label, capacity,
        )


async def _remove_slots(conn, guild_id: int, keys: list[str]):
    """ลบช่องตาม keys ถ้าไม่มีใครถืออยู่และไม่มีไอเท็มที่ใส่ได้เฉพาะช่องเหล่านี้"""
    if not keys:
        return
    held = await conn.fetchval(
        "select count(*) from inventory_entries where guild_id = $1 and slot_key = any($2::text[])", guild_id, keys
    )
    if held:
        raise InventoryError(f"มีผู้เล่นถือของในช่องที่จะลบอยู่ {held} ชิ้น ให้ย้ายหรือทิ้งของก่อน")
    stuck = await conn.fetch(
        "select name from items where guild_id = $1 and allowed_slots <@ $2::text[] order by name", guild_id, keys
    )
    if stuck:
        names = ", ".join(r["name"] for r in stuck[:10])
        raise InventoryError(f"ไอเท็มเหล่านี้ใส่ได้เฉพาะช่องที่จะลบ แก้ด้วย /item-edit ก่อน: {names}")
    await conn.execute(
        """update items set allowed_slots = array(select k from unnest(allowed_slots) k where k <> all($2::text[]))
           where guild_id = $1 and allowed_slots && $2::text[]""",
        guild_id, keys,
    )
    await conn.execute("delete from starter_items where guild_id = $1 and slot_key = any($2::text[])", guild_id, keys)
    await conn.execute("delete from slots where guild_id = $1 and key = any($2::text[])", guild_id, keys)


async def delete_slot(pool, guild_id: int, key: str):
    async with pool.acquire() as conn, conn.transaction():
        await ensure_default_slots(conn, guild_id)
        slot = await conn.fetchrow("select * from slots where guild_id = $1 and key = $2", guild_id, key)
        if slot is None:
            raise InventoryError(f"ไม่มีช่อง `{key}`")
        if await conn.fetchval("select count(*) from slots where guild_id = $1", guild_id) <= 1:
            raise InventoryError("ต้องเหลือช่องอย่างน้อย 1 ช่อง")
        await _remove_slots(conn, guild_id, [key])
        return slot


async def reset_slots(pool, guild_id: int):
    """คืนช่องเป็นชุดเริ่มต้น: ลบช่องที่เพิ่มเอง และตั้งชื่อ/ความจุของช่องเริ่มต้นกลับเป็นค่าเดิม"""
    async with pool.acquire() as conn, conn.transaction():
        await ensure_default_slots(conn, guild_id)
        default_keys = [k for k, _, _ in DEFAULT_SLOTS]
        extra = await conn.fetch(
            "select key from slots where guild_id = $1 and key <> all($2::text[])", guild_id, default_keys
        )
        await _remove_slots(conn, guild_id, [r["key"] for r in extra])
        await conn.executemany(
            """insert into slots (guild_id, key, label, capacity) values ($1, $2, $3, $4)
               on conflict (guild_id, key) do update set label = excluded.label, capacity = excluded.capacity""",
            [(guild_id, k, label, cap) for k, label, cap in DEFAULT_SLOTS],
        )
        return len(extra)


async def build_weapon(conn, guild_id: int, w: dict, allowed_slots: list[str] | None = None, self_id: int | None = None) -> dict:
    """ตรวจและแปลงค่าอาวุธเป็นคอลัมน์ของตาราง items
    w: damage, damage_max, ammo_item (ชื่อ), mag_size, ammo_per_attack, attack_slots"""
    damage = w.get("damage")
    if damage is None:
        raise InventoryError("ต้องระบุ damage (ดาเมจต่ำสุด)")
    damage_max = w.get("damage_max")
    damage_max = damage if damage_max is None else damage_max
    if damage_max < damage:
        raise InventoryError("ดาเมจสูงสุดต้องไม่ต่ำกว่าดาเมจต่ำสุด")
    ammo_id = mag_size = per_attack = None
    if w.get("ammo_item"):
        ammo = await get_item(conn, guild_id, w["ammo_item"])
        if ammo["id"] == self_id:
            raise InventoryError("ใช้ไอเท็มตัวเองเป็นกระสุนไม่ได้")
        mag_size = w.get("mag_size")
        if mag_size is None:
            raise InventoryError("เมื่อกำหนดกระสุน ต้องระบุ mag_size (แม็กจุกระสุนกี่นัด)")
        per_attack = w.get("ammo_per_attack") or 1
        if per_attack > mag_size:
            raise InventoryError("ใช้กระสุนต่อการโจมตีมากกว่าความจุแม็กไม่ได้")
        ammo_id = ammo["id"]
    elif w.get("mag_size") is not None or w.get("ammo_per_attack") is not None:
        raise InventoryError("ระบุ mag_size/ammo_per_attack แล้วต้องระบุ ammo_item (ไอเท็มที่เป็นกระสุน) ด้วย")
    attack_slots = list(w.get("attack_slots") or [])
    if allowed_slots is not None and not set(attack_slots) <= set(allowed_slots):
        raise InventoryError("ช่องที่ใช้โจมตีต้องเป็นช่องที่ไอเท็มนี้ใส่ได้")
    return {
        "damage_min": damage, "damage_max": damage_max, "ammo_item_id": ammo_id,
        "mag_size": mag_size, "ammo_per_attack": per_attack, "attack_slots": attack_slots,
    }


async def check_weapon_args(pool, guild_id: int, w: dict, allowed_slots: list[str] | None = None):
    """ตรวจค่าอาวุธล่วงหน้า (ก่อนแสดงเมนูเลือกช่อง) ไม่แก้ฐานข้อมูล"""
    async with pool.acquire() as conn:
        await build_weapon(conn, guild_id, w, allowed_slots)


_NO_WEAPON = {"damage_min": None, "damage_max": None, "ammo_item_id": None, "mag_size": None,
              "ammo_per_attack": None, "attack_slots": []}


async def create_item(pool, guild_id: int, name: str, description: str, allowed_slots: list[str], size: int, created_by: int, weapon: dict | None = None):
    async with pool.acquire() as conn:
        cols = await build_weapon(conn, guild_id, weapon, allowed_slots) if weapon else _NO_WEAPON
        try:
            return await conn.fetchrow(
                """insert into items (guild_id, name, description, allowed_slots, size, created_by,
                       damage_min, damage_max, ammo_item_id, mag_size, ammo_per_attack, attack_slots)
                   values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) returning *""",
                guild_id, name, description, allowed_slots, size, created_by,
                cols["damage_min"], cols["damage_max"], cols["ammo_item_id"], cols["mag_size"],
                cols["ammo_per_attack"], cols["attack_slots"],
            )
        except asyncpg.UniqueViolationError:
            raise InventoryError(f"มีไอเท็มชื่อ **{name}** อยู่แล้ว") from None


async def _apply_weapon(conn, item_id: int, cols: dict):
    row = await conn.fetchrow(
        """update items set damage_min = $2, damage_max = $3, ammo_item_id = $4, mag_size = $5,
               ammo_per_attack = $6, attack_slots = $7 where id = $1 returning *""",
        item_id, cols["damage_min"], cols["damage_max"], cols["ammo_item_id"], cols["mag_size"],
        cols["ammo_per_attack"], cols["attack_slots"],
    )
    # กระสุนที่บรรจุอยู่ต้องไม่เกินแม็กใหม่ (ไม่มีกระสุน = 0)
    await conn.execute(
        "update inventory_entries set loaded = least(loaded, coalesce($2, 0)) where item_id = $1", item_id, cols["mag_size"]
    )
    return row


async def set_weapon(pool, guild_id: int, item_name: str, w: dict):
    async with pool.acquire() as conn, conn.transaction():
        item = await get_item(conn, guild_id, item_name)
        cols = await build_weapon(conn, guild_id, w, item["allowed_slots"], self_id=item["id"])
        return await _apply_weapon(conn, item["id"], cols)


async def clear_weapon(pool, guild_id: int, item_name: str):
    async with pool.acquire() as conn, conn.transaction():
        item = await get_item(conn, guild_id, item_name)
        return await _apply_weapon(conn, item["id"], _NO_WEAPON)


async def get_item(conn, guild_id: int, name: str):
    item = await conn.fetchrow("select * from items where guild_id = $1 and lower(name) = lower($2)", guild_id, name)
    if item is None:
        raise InventoryError(f"ไม่พบไอเท็มชื่อ **{name}**")
    return item


async def find_item(pool, guild_id: int, name: str):
    async with pool.acquire() as conn:
        return await get_item(conn, guild_id, name)


async def list_items(pool, guild_id: int):
    async with pool.acquire() as conn:
        return await conn.fetch(
            """select i.*, a.name as ammo_name from items i left join items a on a.id = i.ammo_item_id
               where i.guild_id = $1 order by i.name""",
            guild_id,
        )


async def update_item(pool, guild_id: int, name: str, new_name=None, description=None, size=None, allowed_slots=None):
    async with pool.acquire() as conn:
        item = await get_item(conn, guild_id, name)
        try:
            return await conn.fetchrow(
                """update items set name = $2, description = $3, size = $4, allowed_slots = $5
                   where id = $1 returning *""",
                item["id"],
                new_name or item["name"],
                item["description"] if description is None else description,
                size or item["size"],
                allowed_slots or item["allowed_slots"],
            )
        except asyncpg.UniqueViolationError:
            raise InventoryError(f"มีไอเท็มชื่อ **{new_name}** อยู่แล้ว") from None


async def delete_item(pool, guild_id: int, name: str) -> int:
    """ลบไอเท็ม คืนจำนวนชิ้นที่ผู้เล่นถืออยู่ซึ่งถูกลบไปด้วย"""
    async with pool.acquire() as conn, conn.transaction():
        item = await get_item(conn, guild_id, name)
        users = await conn.fetch("select name from items where ammo_item_id = $1 order by name", item["id"])
        if users:
            raise InventoryError(
                f"**{item['name']}** เป็นกระสุนของ: {', '.join(r['name'] for r in users)} — แก้หรือลบอาวุธเหล่านั้นก่อน"
            )
        held = await conn.fetchval("select count(*) from inventory_entries where item_id = $1", item["id"])
        await conn.execute("delete from items where id = $1", item["id"])
        return held


async def _lock_owner(conn, guild_id: int, user_id: int):
    # กันผู้เล่นคนเดียวกันกดคำสั่งพร้อมกันจนเกินความจุ
    await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"{guild_id}:{user_id}")


async def _used(conn, guild_id: int, user_id: int, slot_key: str, exclude_entry: int | None = None) -> int:
    return await conn.fetchval(
        """select coalesce(sum(i.size), 0) from inventory_entries e join items i on i.id = e.item_id
           where e.guild_id = $1 and e.user_id = $2 and e.slot_key = $3 and e.id is distinct from $4""",
        guild_id, user_id, slot_key, exclude_entry,
    )


async def _check_fit(conn, guild_id: int, user_id: int, item, slot_key: str, exclude_entry: int | None = None):
    await ensure_default_slots(conn, guild_id)
    slot = await conn.fetchrow("select * from slots where guild_id = $1 and key = $2", guild_id, slot_key)
    if slot is None:
        raise InventoryError(f"ไม่มีช่อง `{slot_key}`")
    if slot_key not in item["allowed_slots"]:
        raise InventoryError(f"**{item['name']}** ใส่ช่อง **{slot['label']}** ไม่ได้")
    used = await _used(conn, guild_id, user_id, slot_key, exclude_entry)
    if used + item["size"] > slot["capacity"]:
        raise InventoryError(
            f"ช่อง **{slot['label']}** เต็ม (ใช้ {used}/{slot['capacity']}, **{item['name']}** ต้องการ {item['size']})"
        )
    return slot


async def candidate_slots(pool, guild_id: int, user_id: int, item) -> list:
    """ช่องที่ไอเท็มใส่ได้และยังมีที่ว่างพอ"""
    slots = [s for s in await list_slots(pool, guild_id) if s["key"] in item["allowed_slots"]]
    result = []
    async with pool.acquire() as conn:
        for slot in slots:
            if await _used(conn, guild_id, user_id, slot["key"]) + item["size"] <= slot["capacity"]:
                result.append(slot)
    return result


async def place(pool, guild_id: int, user_id: int, item_name: str, slot_key: str):
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        item = await get_item(conn, guild_id, item_name)
        slot = await _check_fit(conn, guild_id, user_id, item, slot_key)
        await conn.execute(
            "insert into inventory_entries (guild_id, user_id, item_id, slot_key) values ($1, $2, $3, $4)",
            guild_id, user_id, item["id"], slot_key,
        )
        return item, slot


async def _find_entry(conn, guild_id: int, user_id: int, item_name: str, slot_key: str | None = None):
    entry = await conn.fetchrow(
        """select e.* from inventory_entries e join items i on i.id = e.item_id
           where e.guild_id = $1 and e.user_id = $2 and lower(i.name) = lower($3)
             and ($4::text is null or e.slot_key = $4)
           order by e.id desc limit 1""",
        guild_id, user_id, item_name, slot_key,
    )
    if entry is None:
        raise InventoryError(f"คุณไม่ได้ถือ **{item_name}**" + (f" ในช่อง `{slot_key}`" if slot_key else ""))
    return entry


async def move(pool, guild_id: int, user_id: int, item_name: str, to_slot: str):
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        item = await get_item(conn, guild_id, item_name)
        entries = await conn.fetch(
            "select * from inventory_entries where guild_id = $1 and user_id = $2 and item_id = $3 order by id desc",
            guild_id, user_id, item["id"],
        )
        entries = [e for e in entries if e["slot_key"] != to_slot]
        if not entries:
            raise InventoryError(f"คุณไม่ได้ถือ **{item['name']}** ที่ย้ายไปช่องนี้ได้")
        slot = await _check_fit(conn, guild_id, user_id, item, to_slot)
        await conn.execute("update inventory_entries set slot_key = $2 where id = $1", entries[0]["id"], to_slot)
        return item, slot


async def drop(pool, guild_id: int, user_id: int, item_name: str, slot_key: str | None = None):
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        entry = await _find_entry(conn, guild_id, user_id, item_name, slot_key)
        item = await conn.fetchrow("select * from items where id = $1", entry["item_id"])
        await conn.execute("delete from inventory_entries where id = $1", entry["id"])
        return item


async def inventory(pool, guild_id: int, user_id: int):
    """คืน (slots, entries) ของผู้เล่น"""
    slots = await list_slots(pool, guild_id)
    async with pool.acquire() as conn:
        entries = await conn.fetch(
            """select e.slot_key, e.loaded, i.name, i.size, i.mag_size, i.damage_min, i.damage_max,
                      i.ammo_per_attack, i.ammo_item_id, i.attack_slots
               from inventory_entries e join items i on i.id = e.item_id
               where e.guild_id = $1 and e.user_id = $2 order by e.id""",
            guild_id, user_id,
        )
    return slots, entries


# ---------- ชุดไอเท็มเริ่มต้น ----------
async def add_starter(pool, guild_id: int, item_name: str, slot_key: str):
    async with pool.acquire() as conn, conn.transaction():
        await ensure_default_slots(conn, guild_id)
        item = await get_item(conn, guild_id, item_name)
        slot = await conn.fetchrow("select * from slots where guild_id = $1 and key = $2", guild_id, slot_key)
        if slot is None:
            raise InventoryError(f"ไม่มีช่อง `{slot_key}`")
        if slot_key not in item["allowed_slots"]:
            raise InventoryError(f"**{item['name']}** ใส่ช่อง **{slot['label']}** ไม่ได้")
        used = await conn.fetchval(
            """select coalesce(sum(i.size), 0) from starter_items s join items i on i.id = s.item_id
               where s.guild_id = $1 and s.slot_key = $2""",
            guild_id, slot_key,
        )
        if used + item["size"] > slot["capacity"]:
            raise InventoryError(
                f"ชุดเริ่มต้นในช่อง **{slot['label']}** จะเกินความจุ (ใช้ {used}/{slot['capacity']}, **{item['name']}** ต้องการ {item['size']})"
            )
        await conn.execute(
            "insert into starter_items (guild_id, item_id, slot_key) values ($1, $2, $3)", guild_id, item["id"], slot_key
        )
        return item, slot


async def list_starters(pool, guild_id: int):
    async with pool.acquire() as conn:
        return await conn.fetch(
            """select s.id, s.slot_key, i.name, i.size from starter_items s join items i on i.id = s.item_id
               where s.guild_id = $1 order by s.id""",
            guild_id,
        )


async def remove_starter(pool, guild_id: int, item_name: str, slot_key: str | None = None):
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """select s.id, i.name from starter_items s join items i on i.id = s.item_id
               where s.guild_id = $1 and lower(i.name) = lower($2) and ($3::text is null or s.slot_key = $3)
               order by s.id desc limit 1""",
            guild_id, item_name, slot_key,
        )
        if row is None:
            raise InventoryError(f"ไม่มี **{item_name}** ในชุดเริ่มต้น")
        await conn.execute("delete from starter_items where id = $1", row["id"])
        return row["name"]


async def _grant_starter(conn, guild_id: int, user_id: int, force: bool):
    first_time = await conn.fetchval(
        "insert into starter_granted (guild_id, user_id) values ($1, $2) on conflict do nothing returning true",
        guild_id, user_id,
    )
    if not first_time and not force:
        return [], []
    rows = await conn.fetch(
        """select i.id, i.name, i.allowed_slots, i.size, s.slot_key from starter_items s
           join items i on i.id = s.item_id where s.guild_id = $1 order by s.id""",
        guild_id,
    )
    granted, skipped = [], []
    for r in rows:
        try:
            slot = await _check_fit(conn, guild_id, user_id, r, r["slot_key"])
        except InventoryError as e:
            skipped.append(str(e))
            continue
        await conn.execute(
            "insert into inventory_entries (guild_id, user_id, item_id, slot_key) values ($1, $2, $3, $4)",
            guild_id, user_id, r["id"], r["slot_key"],
        )
        granted.append((r["name"], slot["label"]))
    return granted, skipped


async def grant_starter(pool, guild_id: int, user_id: int, force: bool = False):
    """แจกชุดเริ่มต้นให้ผู้เล่น
    force=False: แจกครั้งเดียวต่อคน ถ้าเคยได้แล้วคืนค่าว่าง
    force=True (แอดมินสั่ง): แจกเสมอ
    คืน (รายการที่ได้ [(ชื่อไอเท็ม, ชื่อช่อง)], ข้อความที่ใส่ไม่ได้ [str])"""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        return await _grant_starter(conn, guild_id, user_id, force)


# ---------- ตัวละคร ----------
async def get_character(pool, guild_id: int, user_id: int):
    async with pool.acquire() as conn:
        return await conn.fetchrow("select * from characters where guild_id = $1 and user_id = $2", guild_id, user_id)


async def _missing_character(conn, guild_id: int, user_id: int) -> InventoryError:
    """ข้อความเมื่อผู้เล่นไม่มีตัวละคร: ถ้าเคยตายจะบอกว่าเสียชีวิตแล้ว"""
    dead = await conn.fetchval(
        "select name from corpses where guild_id = $1 and user_id = $2 order by died_at desc limit 1", guild_id, user_id
    )
    if dead:
        return InventoryError(f"💀 ตัวละครของคุณ (**{dead}**) เสียชีวิตแล้ว ใช้ `/character-create` สร้างตัวละครใหม่")
    return InventoryError("ต้องสร้างตัวละครก่อน ใช้ `/character-create`")


async def require_character(pool, guild_id: int, user_id: int):
    async with pool.acquire() as conn:
        char = await conn.fetchrow("select * from characters where guild_id = $1 and user_id = $2", guild_id, user_id)
        if char is None:
            raise await _missing_character(conn, guild_id, user_id)
        return char


async def create_character(pool, guild_id: int, user_id: int, name: str):
    """สร้างตัวละคร + แจกชุดเริ่มต้น (ครั้งเดียวต่อคน) ใน transaction เดียว"""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        existing = await conn.fetchval(
            "select name from characters where guild_id = $1 and user_id = $2", guild_id, user_id
        )
        if existing:
            raise InventoryError(f"คุณมีตัวละครแล้ว: **{existing}**")
        try:
            hp = await conn.fetchval("select default_hp from guild_settings where guild_id = $1", guild_id) or 100
            await conn.execute(
                "insert into characters (guild_id, user_id, name, hp, max_hp) values ($1, $2, $3, $4, $4)",
                guild_id, user_id, name, hp,
            )
        except asyncpg.UniqueViolationError:
            raise InventoryError(f"มีตัวละครชื่อ **{name}** อยู่แล้ว") from None
        return await _grant_starter(conn, guild_id, user_id, force=False)


async def delete_character(pool, guild_id: int, user_id: int):
    """ลบตัวละคร + ของในกระเป๋าทั้งหมด + สถานะรับชุดเริ่มต้น (สร้างใหม่แล้วจะได้ชุดเริ่มต้นอีกครั้ง)"""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        name = await conn.fetchval(
            "delete from characters where guild_id = $1 and user_id = $2 returning name", guild_id, user_id
        )
        if name is None:
            raise InventoryError("ผู้เล่นคนนี้ยังไม่มีตัวละคร")
        await conn.execute("delete from inventory_entries where guild_id = $1 and user_id = $2", guild_id, user_id)
        await conn.execute("delete from starter_granted where guild_id = $1 and user_id = $2", guild_id, user_id)
        return name


async def get_player_role(pool, guild_id: int) -> int | None:
    async with pool.acquire() as conn:
        return await conn.fetchval("select player_role_id from guild_settings where guild_id = $1", guild_id)


async def set_player_role(pool, guild_id: int, role_id: int | None):
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into guild_settings (guild_id, player_role_id) values ($1, $2)
               on conflict (guild_id) do update set player_role_id = excluded.player_role_id""",
            guild_id, role_id,
        )


# ---------- HP / โจมตี ----------
async def set_default_hp(pool, guild_id: int, value: int):
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into guild_settings (guild_id, default_hp) values ($1, $2)
               on conflict (guild_id) do update set default_hp = excluded.default_hp""",
            guild_id, value,
        )


async def _kill(conn, guild_id: int, user_id: int, killed_by: str | None = None) -> dict:
    """ตัวละครตาย: ลบตัวละคร ย้ายของทั้งหมดไปเป็นศพ (user_id = -corpse_id) ล้างสถานะรับชุดเริ่มต้น
    ต้องเรียกใน transaction ที่ล็อกผู้เล่นคนนั้นไว้แล้ว"""
    name = await conn.fetchval(
        "delete from characters where guild_id = $1 and user_id = $2 returning name", guild_id, user_id
    )
    corpse_id = await conn.fetchval(
        "insert into corpses (guild_id, user_id, name, killed_by) values ($1, $2, $3, $4) returning id",
        guild_id, user_id, name, killed_by,
    )
    moved = await conn.fetchval(
        """with m as (update inventory_entries set user_id = $3 where guild_id = $1 and user_id = $2 returning 1)
           select count(*) from m""",
        guild_id, user_id, -corpse_id,
    )
    await conn.execute("delete from starter_granted where guild_id = $1 and user_id = $2", guild_id, user_id)
    return {"corpse_id": corpse_id, "name": name, "items": moved}


async def set_hp(pool, guild_id: int, user_id: int, hp: int, max_hp: int | None = None):
    """ตั้ง HP (และ HP สูงสุดถ้าระบุ) HP จะไม่เกิน HP สูงสุด
    คืน (ตัวละคร, None) หรือ (None, ข้อมูลการตาย) ถ้า HP เหลือ 0 = ตัวละครตาย"""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        char = await conn.fetchrow("select * from characters where guild_id = $1 and user_id = $2", guild_id, user_id)
        if char is None:
            raise InventoryError("ผู้เล่นคนนี้ยังไม่มีตัวละคร")
        new_max = max_hp or char["max_hp"]
        new_hp = min(hp, new_max)
        if new_hp <= 0:
            return None, await _kill(conn, guild_id, user_id)
        row = await conn.fetchrow(
            "update characters set hp = $3, max_hp = $4 where guild_id = $1 and user_id = $2 returning *",
            guild_id, user_id, new_hp, new_max,
        )
        return row, None


async def attack(pool, guild_id: int, attacker_id: int, target_id: int, weapon_name: str) -> dict:
    if attacker_id == target_id:
        raise InventoryError("โจมตีตัวเองไม่ได้")
    async with pool.acquire() as conn, conn.transaction():
        for uid in sorted((attacker_id, target_id)):  # ล็อกเรียงลำดับเดียวกันเสมอ กัน deadlock
            await _lock_owner(conn, guild_id, uid)
        me = await conn.fetchrow("select * from characters where guild_id = $1 and user_id = $2", guild_id, attacker_id)
        if me is None:
            raise await _missing_character(conn, guild_id, attacker_id)
        target = await conn.fetchrow("select * from characters where guild_id = $1 and user_id = $2", guild_id, target_id)
        if target is None:
            raise InventoryError("เป้าหมายยังไม่มีตัวละคร")
        if me["hp"] <= 0:
            raise InventoryError(f"**{me['name']}** ล้มแล้ว (HP 0) โจมตีไม่ได้")
        if target["hp"] <= 0:
            raise InventoryError(f"**{target['name']}** ล้มแล้ว (HP 0)")

        item = await get_item(conn, guild_id, weapon_name)
        if item["damage_min"] is None:
            raise InventoryError(f"**{item['name']}** ใช้โจมตีไม่ได้")
        entries = await conn.fetch(
            "select * from inventory_entries where guild_id = $1 and user_id = $2 and item_id = $3 order by loaded desc, id",
            guild_id, attacker_id, item["id"],
        )
        if not entries:
            raise InventoryError(f"คุณไม่ได้ถือ **{item['name']}**")
        if item["attack_slots"]:
            entries = [e for e in entries if e["slot_key"] in item["attack_slots"]]
            if not entries:
                labels = await conn.fetch(
                    "select label from slots where guild_id = $1 and key = any($2::text[]) order by key",
                    guild_id, item["attack_slots"],
                )
                raise InventoryError(
                    f"ต้องถือ **{item['name']}** ในช่อง {', '.join(r['label'] for r in labels)} ถึงจะโจมตีได้"
                )
        entry, loaded_left = entries[0], None
        if item["ammo_item_id"] is not None:
            need = item["ammo_per_attack"]
            entry = next((e for e in entries if e["loaded"] >= need), None)
            if entry is None:
                best = entries[0]["loaded"]
                raise InventoryError(
                    f"กระสุนไม่พอ (**{item['name']}** มี {best}/{item['mag_size']} ต้องใช้ {need} นัด) ใช้ `/reload`"
                )
            loaded_left = entry["loaded"] - need
            await conn.execute("update inventory_entries set loaded = $2 where id = $1", entry["id"], loaded_left)

        damage = random.randint(item["damage_min"], item["damage_max"])
        new_hp = max(0, target["hp"] - damage)
        died = None
        if new_hp == 0:
            died = await _kill(conn, guild_id, target_id, me["name"])
        else:
            await conn.execute(
                "update characters set hp = $3 where guild_id = $1 and user_id = $2", guild_id, target_id, new_hp
            )
        return {
            "item": item, "attacker": me, "target": target, "damage": damage, "hp": new_hp,
            "max_hp": target["max_hp"], "loaded": loaded_left, "mag_size": item["mag_size"], "died": died,
        }


async def reload(pool, guild_id: int, user_id: int, weapon_name: str) -> dict:
    """เติมแม็กอาวุธจากไอเท็มกระสุนในกระเป๋า (กระสุน 1 ชิ้น = 1 นัด)"""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_owner(conn, guild_id, user_id)
        item = await get_item(conn, guild_id, weapon_name)
        if item["ammo_item_id"] is None:
            raise InventoryError(f"**{item['name']}** ไม่ต้องบรรจุกระสุน")
        entries = await conn.fetch(
            "select * from inventory_entries where guild_id = $1 and user_id = $2 and item_id = $3 order by loaded, id",
            guild_id, user_id, item["id"],
        )
        if not entries:
            raise InventoryError(f"คุณไม่ได้ถือ **{item['name']}**")
        entry = entries[0]
        space = item["mag_size"] - entry["loaded"]
        if space <= 0:
            raise InventoryError(f"แม็กของ **{item['name']}** เต็มแล้ว ({entry['loaded']}/{item['mag_size']})")
        ammo_name = await conn.fetchval("select name from items where id = $1", item["ammo_item_id"])
        rows = await conn.fetch(
            """select id from inventory_entries where guild_id = $1 and user_id = $2 and item_id = $3
               order by id limit $4 for update""",
            guild_id, user_id, item["ammo_item_id"], space,
        )
        if not rows:
            raise InventoryError(f"ไม่มีกระสุน **{ammo_name}** ในกระเป๋า")
        await conn.execute("delete from inventory_entries where id = any($1::bigint[])", [r["id"] for r in rows])
        loaded = entry["loaded"] + len(rows)
        await conn.execute("update inventory_entries set loaded = $2 where id = $1", entry["id"], loaded)
        return {"item": item, "ammo_name": ammo_name, "added": len(rows), "loaded": loaded, "mag_size": item["mag_size"]}


# ---------- ศพ / ลูท ----------
async def list_corpses(pool, guild_id: int, limit: int = 25):
    """ศพที่ยังมีของ (ล่าสุดก่อน)"""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """select c.id, c.name, c.died_at, count(e.id) as items
               from corpses c join inventory_entries e on e.guild_id = c.guild_id and e.user_id = -c.id
               where c.guild_id = $1 group by c.id order by c.died_at desc limit $2""",
            guild_id, limit,
        )


async def get_corpse(pool, guild_id: int, corpse_id: int):
    async with pool.acquire() as conn:
        corpse = await conn.fetchrow("select * from corpses where guild_id = $1 and id = $2", guild_id, corpse_id)
    if corpse is None:
        raise InventoryError("ไม่พบศพนี้")
    return corpse


async def corpse_items(pool, guild_id: int, corpse_id: int):
    await get_corpse(pool, guild_id, corpse_id)
    async with pool.acquire() as conn:
        return await conn.fetch(
            """select e.slot_key, e.loaded, i.name, i.size, i.mag_size
               from inventory_entries e join items i on i.id = e.item_id
               where e.guild_id = $1 and e.user_id = $2 order by e.id""",
            guild_id, -corpse_id,
        )


async def loot(pool, guild_id: int, looter_id: int, corpse_id: int, item_name: str, slot_key: str):
    """เก็บของ 1 ชิ้นจากศพเข้ากระเป๋าผู้ลูท (ใช้กฎช่อง/ความจุเดิม กระสุนที่บรรจุคงอยู่)"""
    async with pool.acquire() as conn, conn.transaction():
        for owner in sorted((looter_id, -corpse_id)):  # ล็อกเรียงลำดับเดียวกันเสมอ กัน deadlock
            await _lock_owner(conn, guild_id, owner)
        corpse = await conn.fetchrow("select * from corpses where guild_id = $1 and id = $2", guild_id, corpse_id)
        if corpse is None:
            raise InventoryError("ไม่พบศพนี้")
        item = await get_item(conn, guild_id, item_name)
        entry = await conn.fetchrow(
            """select id from inventory_entries where guild_id = $1 and user_id = $2 and item_id = $3
               order by loaded desc, id limit 1""",
            guild_id, -corpse_id, item["id"],
        )
        if entry is None:
            raise InventoryError(f"ศพของ **{corpse['name']}** ไม่มี **{item['name']}**")
        slot = await _check_fit(conn, guild_id, looter_id, item, slot_key)
        await conn.execute(
            "update inventory_entries set user_id = $2, slot_key = $3 where id = $1", entry["id"], looter_id, slot_key
        )
        return corpse, item, slot
