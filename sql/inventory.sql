-- ตารางระบบกระเป๋า/เก็บของ (รันซ้ำได้ ไม่ทับข้อมูลเดิม)

create table if not exists slots (
    guild_id bigint not null,
    key      text   not null check (key ~ '^[a-z0-9_]{1,32}$'),
    label    text   not null,
    capacity int    not null check (capacity > 0),
    primary key (guild_id, key)
);

create table if not exists items (
    id            bigserial primary key,
    guild_id      bigint      not null,
    name          text        not null,
    description   text        not null default '',
    allowed_slots text[]      not null check (cardinality(allowed_slots) > 0),
    size          int         not null default 1 check (size > 0),
    created_by    bigint      not null,
    created_at    timestamptz not null default now()
);
create unique index if not exists items_guild_name_uq on items (guild_id, lower(name));

create table if not exists inventory_entries (
    id         bigserial primary key,
    guild_id   bigint      not null,
    user_id    bigint      not null,
    item_id    bigint      not null references items (id) on delete cascade,
    slot_key   text        not null,
    created_at timestamptz not null default now()
);
create index if not exists inventory_entries_owner_idx on inventory_entries (guild_id, user_id);

-- เปิด RLS โดยไม่มี policy = ปิดไม่ให้เข้าผ่าน REST API (บอทต่อ Postgres ตรงด้วยสิทธิ์เจ้าของ)
alter table slots             enable row level security;
alter table items             enable row level security;
alter table inventory_entries enable row level security;
