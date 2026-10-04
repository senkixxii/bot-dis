import asyncpg

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


async def create_item(pool, guild_id: int, name: str, description: str, allowed_slots: list[str], size: int, created_by: int):
    async with pool.acquire() as conn:
        try:
            return await conn.fetchrow(
                """insert into items (guild_id, name, description, allowed_slots, size, created_by)
                   values ($1, $2, $3, $4, $5, $6) returning *""",
                guild_id, name, description, allowed_slots, size, created_by,
            )
        except asyncpg.UniqueViolationError:
            raise InventoryError(f"มีไอเท็มชื่อ **{name}** อยู่แล้ว") from None


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
        return await conn.fetch("select * from items where guild_id = $1 order by name", guild_id)


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
            """select e.slot_key, i.name, i.size from inventory_entries e join items i on i.id = e.item_id
               where e.guild_id = $1 and e.user_id = $2 order by e.id""",
            guild_id, user_id,
        )
    return slots, entries
