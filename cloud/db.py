"""Bulut bazasi: SQLAlchemy Core — SQLite (sukut, bitta kompyuter) yoki PostgreSQL (+ TimescaleDB, VPS).

Bitta sxema, bitta sozlama: CLOUD_DB_URL
    sqlite:///cloud/data/cloud.db                         (sukut)
    postgresql+psycopg://user:parol@host:5432/hushyor      (VPS; `pip install "psycopg[binary]"`)

Ierarxiya: kompaniya → filial (uning edge kompyuteri = qurilma) → kamera. Foydalanuvchi kompaniyaga tegishli,
roli: owner (hamma filiallar), manager / guard (user_branches dagi filiallar).
Hodisa, ko'rsatkich va kamera holati filialga bog'lanadi; `device_id` edge tomonidagi identifikator.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import (JSON, BigInteger, Boolean, Column, Float, ForeignKey, Index, Integer, MetaData, String, Table,
                        Text, UniqueConstraint, create_engine, event, inspect, select, text)

ROOT = Path(__file__).resolve().parent


def env(key: str, default: str = "") -> str:
    """Sozlama: avval muhit o'zgaruvchisi, keyin loyiha ildizidagi .env (Notepad BOM'i bilan ham).
    (1.8.0 gacha CLOUD_DB_URL va CLOUD_DATA_DIR faqat muhitdan o'qilardi — .env dagisi e'tiborsiz qolardi.)"""
    v = os.environ.get(key)
    if v is not None:
        return v
    f = ROOT.parent / ".env"
    try:
        lines = f.read_text(encoding="utf-8-sig").splitlines() if f.is_file() else []
    except OSError:
        lines = []
    for raw in lines:
        line = raw.strip()
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        if k.strip() == key:
            return val.strip().strip('"').strip("'")
    return default


DATA_DIR = Path(env("CLOUD_DATA_DIR") or ROOT / "data")
CLIPS_DIR = DATA_DIR / "clips"
ROLES = ("owner", "manager", "guard")
SESSION_TTL = 30 * 86400          # panel sessiyasi (soniya)

meta = MetaData()

companies = Table(
    "companies", meta,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("created_at", Float, nullable=False),
)
users = Table(
    "users", meta,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("email", String(200), nullable=False, unique=True),
    Column("name", String(200), nullable=False, default=""),
    Column("role", String(20), nullable=False),          # owner | manager | guard
    Column("pw_hash", String(300), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", Float, nullable=False),
)
user_branches = Table(                                    # manager / guard qaysi filiallarni ko'radi
    "user_branches", meta,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
)
branches = Table(                                          # filial = do'kon = bitta edge kompyuter (qurilma)
    "branches", meta,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("address", String(300), nullable=False, default=""),
    Column("device_id", String(64), nullable=False, unique=True),
    Column("token_hash", String(64), nullable=False),     # sha256(qurilma tokeni) — token o'zi saqlanmaydi
    Column("created_at", Float, nullable=False),
    Column("last_seen", Float),
    Column("last_ip", String(64)),
    Column("version", String(40)),
    Column("cameras", JSON),                              # hello dagi kameralar (oxirgi holat)
    Column("pending", Integer, default=0),
    Column("clock_skew", Float),
    Column("offline_since", Float),                       # aloqa uzilgan (oxirgi hello) vaqti; None — onlayn
    Column("occupancy", JSON),                            # 1.9.0: hello dagi sanash holati {inside, since, day0, in, out}
    Column("queues", JSON),                               # 2.0.0: hello dagi kassalar holati {at, items: [...]}
    Column("flow_plans", JSON),                           # 2.1.0: hello dagi do'kon rejalari {at, items: [...]}
    Column("readiness", JSON),                            # 2.5.0: hello dagi tayyorlik va pilot holati
)
events = Table(
    "events", meta,
    Column("uid", String(80), primary_key=True),          # <device_id>:<local_id>
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("device_id", String(64), nullable=False),
    Column("local_id", Integer),
    Column("created_at", Float),
    Column("updated_at", Float),
    Column("kind", String(40)),
    Column("score", Float),
    Column("status", String(40)),
    Column("source_name", String(200)),
    Column("clip_file", String(200)),
    Column("thumb_file", String(200)),
    Column("data", JSON),                                 # to'liq hodisa (edge yuborgan ko'rinishda)
    Column("received_at", Float),                         # bulutga birinchi kelgan vaqt (bulut soati)
    Column("clip_clean", String(200)),                    # 3.1.0: chizmalarsiz klip (Telegram'ga shu ketadi)
    Column("loss_sum", BigInteger),                       # 3.1.0: tasdiqlangan o'g'irlikning taxminiy zarari (so'm, qo'lda)
    Index("ix_events_branch_time", "branch_id", "created_at"),
)
metrics_minute = Table(
    "metrics_minute", meta,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("minute", BigInteger, primary_key=True),
    Column("source_id", Integer, primary_key=True),
    Column("data", JSON),
)
visits_minute = Table(       # 1.9.0: sanash yig'indisi — metrics_minute dan ajratilgan (hisobot JSON o'qimasdan, SQL'da)
    "visits_minute", meta,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("minute", BigInteger, primary_key=True),
    Column("source_id", Integer, primary_key=True),
    Column("n_in", Integer, nullable=False, default=0),         # xaridorlar (ish vaqtida, xodimsiz)
    Column("n_out", Integer, nullable=False, default=0),
    Column("n_staff", Integer, nullable=False, default=0),      # xodim o'tishlari (ikki yo'nalish)
    Column("n_off_in", Integer, nullable=False, default=0),     # ish vaqtidan tashqari (xodimsiz)
    Column("n_off_out", Integer, nullable=False, default=0),
)
queue_minute = Table(        # 2.0.0: kassa navbati — edge daqiqalik yig'indisi (har kassa = edge navbat zonasi)
    "queue_minute", meta,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("minute", BigInteger, primary_key=True),
    Column("zone_id", Integer, primary_key=True),             # edge'dagi zona id (filial ichida yagona)
    Column("zone_name", String(200)),
    Column("source_id", Integer),
    Column("t_obs", Float, nullable=False, default=0),        # kuzatilgan soniyalar
    Column("len_sum", Float, nullable=False, default=0),      # uzunlik × soniya
    Column("len_max", Integer, nullable=False, default=0),
    Column("long_sec", Float, nullable=False, default=0),     # navbat ≥ me'yor bo'lgan soniyalar
    Column("busy_sec", Float, nullable=False, default=0),
    Column("cashier_sec", Float, nullable=False, default=0),
    Column("cashier_known", Integer, nullable=False, default=0),
    Column("served", Integer, nullable=False, default=0),
    Column("wait_sum", Float, nullable=False, default=0),
    Column("wait_n", Integer, nullable=False, default=0),
    Column("wait_max", Float, nullable=False, default=0),
)
queue_alerts = Table(        # 2.0.0: navbat qoidalari signallari (edge: 1-4; bulut: 5 — tarmoq, branch_id NULL)
    "queue_alerts", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(120), nullable=False, unique=True),  # <device_id>:<local_id> yoki net:<kompaniya>:<vaqt>
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("branch_id", Integer, ForeignKey("branches.id")),
    Column("t", Float, nullable=False),                       # bulut soatida
    Column("rule", String(8), nullable=False),
    Column("level", String(16)),
    Column("zone_name", String(200)),
    Column("source_name", String(200)),
    Column("title", String(200)),
    Column("text", Text),                                     # oddiy matn (edge HTML'i tozalangan)
    Column("data", JSON),
    Column("received_at", Float),
    Index("ix_qalerts_company_t", "company_id", "t"),
)
flow_hour = Table(           # 2.1.0: oqim va heatmap — edge soatlik yig'indisi (reja bo'yicha, barcha kameralar birga)
    "flow_hour", meta,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("plan_id", Integer, primary_key=True),             # edge'dagi reja id
    Column("hour", BigInteger, primary_key=True),
    Column("grid", String(40)),                               # to'r kaliti (nx×ny@katak) — masshtab o'zgarsa aralashmasin
    Column("visits", Integer, nullable=False, default=0),     # jami bo'lim tashriflari (SQL yig'indisi uchun)
    Column("obs", Float, nullable=False, default=0),          # kuzatilgan kamera-soniyalar
    Column("data", JSON),                                     # {"visits": {bo'lim: n}, "moves": [[a, b, n]], "occ", "pas"}
)
health_log = Table(
    "health_log", meta,
    Column("key", String(120), primary_key=True),         # <device>:<kalit>
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("t", Float),
    Column("source_id", Integer),
    Column("source_name", String(200)),
    Column("state", String(20)),
    Column("reason", String(300)),
)
received = Table(                                          # idempotentlik jurnali (7 kun)
    "received", meta,
    Column("key", String(200), primary_key=True),
    Column("t", Float),
)
sessions = Table(                                          # panel (boshqaruv API) sessiyalari
    "sessions", meta,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", Float),
    Column("expires_at", Float),
)
commands = Table(                                          # bulut → edge buyruqlari (hello javobida ketadi)
    "commands", meta,
    Column("id", Integer, primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("type", String(40), nullable=False),           # review
    Column("payload", JSON),
    Column("created_at", Float),
    Column("delivered_at", Float),
    Column("acked_at", Float),
    Index("ix_commands_branch", "branch_id", "acked_at"),
)

# ----------------------------------------------------------------------------- bildirishnomalar (1.7.0)
notify_targets = Table(                                    # foydalanuvchining yetkazish manzillari
    "notify_targets", meta,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("channel", String(20), nullable=False),        # telegram | email | push
    Column("address", String(600), nullable=False),       # chat_id | email | push endpoint
    Column("data", JSON),                                 # push: {"keys": {"p256dh", "auth"}}; telegram: {"name"}
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", Float),
    Column("last_ok", Float),
    Column("last_error", String(300)),
    UniqueConstraint("user_id", "channel", "address", name="uq_notify_target"),
)
notify_prefs = Table(                                      # nimalar haqida xabar olishi (rol bo'yicha sukut)
    "notify_prefs", meta,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("data", JSON),
)
notify_outbox = Table(                                     # yuborish navbati (qayta urinish, kechikish o'lchovi)
    "notify_outbox", meta,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer),
    Column("target_id", Integer, ForeignKey("notify_targets.id", ondelete="CASCADE")),
    Column("channel", String(20), nullable=False),
    Column("kind", String(20), nullable=False),           # event | clip | review | camera | offline | online | escalation | test
    Column("ref", String(120)),                           # hodisa uid yoki filial id
    Column("payload", JSON),
    Column("status", String(12), nullable=False, default="pending"),   # pending | sent | failed | skipped
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_try", Float, nullable=False, default=0),
    Column("created_at", Float),
    Column("sent_at", Float),
    Column("error", String(300)),
    Index("ix_notify_pending", "status", "next_try"),
)
tg_links = Table(                                          # panel → Telegram bog'lash kodi (bir martalik, 10 daqiqa)
    "tg_links", meta,
    Column("code_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("expires_at", Float),
)
tg_sent = Table(                                           # bulut botining yuborgan xabarlari (tugma, tahrir, klip javobi)
    "tg_sent", meta,
    Column("id", Integer, primary_key=True),               # callback_data dagi qisqa raqam (64 bayt chegarasi)
    Column("uid", String(80), nullable=False),
    Column("target_id", Integer, ForeignKey("notify_targets.id", ondelete="CASCADE")),
    Column("chat_id", String(40)),
    Column("message_id", Integer),
    Column("kind", String(20)),                           # event | clip
    Column("file_id", String(200)),
    Column("created_at", Float),
    Index("ix_tg_sent_uid", "uid"),
)
escalations = Table(                                       # javobsiz hodisa yuqoriga uzatilgan (bir marta)
    "escalations", meta,
    Column("uid", String(80), primary_key=True),
    Column("at", Float),
)
notify_once = Table(                                       # "faqat bir marta" belgisi (masalan, bitta chatga bitta klip)
    "notify_once", meta,
    Column("key", String(200), primary_key=True),
    Column("at", Float),
)
kv = Table(                                                # kichik sozlamalar (masalan, Telegram offset)
    "kv", meta,
    Column("key", String(100), primary_key=True),
    Column("value", Text),
)
audit_log = Table(                                         # kim, qachon, nima qildi (1.8.0): kirish, xodim, token, tekshirish, klip
    "audit_log", meta,
    Column("id", Integer, primary_key=True),
    Column("t", Float, nullable=False),
    Column("company_id", Integer),                        # noma'lum (masalan, mavjud bo'lmagan email bilan kirish) — NULL
    Column("user_id", Integer),
    Column("actor", String(200)),                         # email (o'chirilgan xodim ham ko'rinsin)
    Column("action", String(40), nullable=False),
    Column("target", String(200)),
    Column("ip", String(64)),
    Column("detail", JSON),
    Index("ix_audit_company_t", "company_id", "t"),
)

leads = Table(                                             # 2.6.0: saytdagi so'rovlar (cloud/leads.py) — kompaniyaga bog'liq emas
    "leads", meta,
    Column("id", Integer, primary_key=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float),
    Column("name", String(100), nullable=False),
    Column("phone", String(20), nullable=False),          # faqat raqamlar (998901234567)
    Column("store", String(200)),
    Column("city", String(100)),
    Column("branches", String(20)),
    Column("message", Text),
    Column("source", String(200)),                        # sahifa / utm (qayerdan keldi)
    Column("ip_hash", String(64)),                        # sha256(IP) — IP o'zi saqlanmaydi
    Column("status", String(20), nullable=False, default="yangi"),   # yangi | boglanildi | pilot | rad
    Column("note", Text),
    Column("repeats", Integer, nullable=False, default=0),            # shu telefon 24 soat ichida yana yuborgan
    Column("notified_at", Float),
    Column("notify_tries", Integer, nullable=False, default=0),
    Column("notify_error", String(300)),
    Column("notify_next", Float),                         # keyingi urinish vaqti (yuborilmagan bo'lsa)
    Index("ix_leads_created", "created_at"),
    Index("ix_leads_phone", "phone"),
)

video_tests = Table(                                       # 3.0.0: bulut panelidagi "Video sinovi" (cloud/video_tests.py)
    "video_tests", meta,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("user_id", Integer),
    Column("filename", String(200), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("zone_source_id", Integer),                    # edge kamerasi — uning zonalari videoga qo'llanadi
    Column("zone_source_name", String(200)),
    Column("status", String(20), nullable=False),         # kutilmoqda | yuklanmoqda | navbatda | ishlanmoqda | tayyor | xato | bekor
    Column("progress", Float, nullable=False, default=0),
    Column("job_id", Integer),                            # edge'dagi vazifa (hodisalar data.job_id bilan bog'lanadi)
    Column("events", Integer, nullable=False, default=0),
    Column("duration", Float),
    Column("error", String(300)),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float),
    Column("started_at", Float),
    Column("done_at", Float),
    Column("file_deleted", Boolean, nullable=False, default=False),
    Column("token", String(40)),                          # tasodifiy: edge hisobotini aynan shu sinovga bog'laydi
    Column("zones", JSON),                                # 3.1.0: video kadri ustiga chizilgan zonalar
    Column("metrics", JSON),                              # 3.1.0: navbat, sanash, zonalar bo'yicha natija (edge hisobi)
    Column("notified", Boolean),                          # 3.1.0: yakuniy natija Telegram'ga yuborildi
    Index("ix_video_tests_company", "company_id", "created_at"),
    sqlite_autoincrement=True,                            # o'chirilgan sinov raqami qayta berilmasin (edge eslab qoladi)
)

# Mavjud bazalarga (1.6.0) qo'shiladigan ustunlar: create_all eski jadvalni o'zgartirmaydi
EXTRA_COLUMNS = {
    "branches": {"offline_since": "FLOAT", "occupancy": "JSON", "queues": "JSON", "flow_plans": "JSON",
                 "readiness": "JSON"},
    "events": {"received_at": "FLOAT", "clip_clean": "VARCHAR(200)", "loss_sum": "BIGINT"},
    "video_tests": {"zones": "JSON", "metrics": "JSON", "notified": "BOOLEAN"},
}

_engine = None


def db_url() -> str:
    return env("CLOUD_DB_URL").strip() or f"sqlite:///{(DATA_DIR / 'cloud.db').as_posix()}"


def engine():
    global _engine
    if _engine is None:
        url = db_url()
        if url.startswith("sqlite"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, pool_pre_ping=True)

            @event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _rec):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


def reset_engine() -> None:
    """Sinovlar uchun: CLOUD_DB_URL o'zgarganda yangi engine."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def is_postgres() -> bool:
    return engine().dialect.name == "postgresql"


def _legacy_sqlite() -> str | None:
    """1.5.0 bulut bazasi (jadvallar filialsiz: `devices`, `events` da branch_id yo'q) — sxema boshqacha,
    `create_all` uni o'zgartira olmaydi. Fayl chetga olinadi (cloud.db.v15-<vaqt>), toza baza ochiladi."""
    from sqlalchemy import inspect

    insp = inspect(engine())
    if not insp.has_table("events"):
        return None
    cols = {c["name"] for c in insp.get_columns("events")}
    if "branch_id" in cols:
        return None
    url = db_url()
    path = Path(url.replace("sqlite:///", "", 1))
    reset_engine()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    moved = path.with_name(f"{path.name}.v15-{stamp}")
    path.replace(moved)
    for suf in ("-wal", "-shm"):
        p = path.with_name(path.name + suf)
        if p.exists():
            p.replace(moved.with_name(moved.name + suf))
    return str(moved)


def _add_missing_columns() -> list[str]:
    """Yangi versiyada qo'shilgan ustunlar eski bazaga (ALTER TABLE ADD COLUMN — SQLite va PostgreSQL)."""
    insp = inspect(engine())
    done = []
    with engine().begin() as c:
        for table, cols in EXTRA_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {x["name"] for x in insp.get_columns(table)}
            for col, typ in cols.items():
                if col not in have:
                    c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                    done.append(f"{table}.{col}")
    return done


def kv_get(key: str, default: str = "") -> str:
    v = scalar("SELECT value FROM kv WHERE key=:k", {"k": key})
    return default if v is None else str(v)


def kv_set(key: str, value: str) -> None:
    upsert(kv, {"key": key, "value": str(value)}, ["key"], ["value"])


def init_db() -> dict:
    """Jadvallarni yaratadi; PostgreSQL'da TimescaleDB bo'lsa metrics_minute hypertable qilinadi."""
    info = {"dialect": engine().dialect.name, "timescale": False}
    if not is_postgres():
        legacy = _legacy_sqlite()
        if legacy:
            info["legacy_db"] = legacy
    meta.create_all(engine())
    added = _add_missing_columns()
    if added:
        info["migrated"] = added
    if is_postgres():
        with engine().begin() as c:
            try:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                # Butun son vaqt ustuni (unix daqiqa): hypertable 30 kunlik bo'laklar bilan
                c.execute(text("SELECT create_hypertable('metrics_minute', 'minute', chunk_time_interval => 2592000, "
                               "if_not_exists => TRUE, migrate_data => TRUE)"))
                info["timescale"] = True
            except Exception as e:  # noqa: BLE001 — TimescaleDB o'rnatilmagan: oddiy jadval ham ishlaydi
                info["timescale_error"] = str(e)[:160]
    try:
        nv = _backfill_visits()
        if nv:
            info["visits_backfilled"] = nv
    except Exception as e:  # noqa: BLE001
        info["visits_backfill_error"] = str(e)[:160]
    return info


# ----------------------------------------------------------------------------- yordamchilar
def rows(sql: str, params: dict | None = None) -> list[dict]:
    with engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params or {})]


def row(sql: str, params: dict | None = None) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def scalar(sql: str, params: dict | None = None) -> Any:
    with engine().connect() as c:
        return c.execute(text(sql), params or {}).scalar()


def execute(sql: str, params: dict | None = None) -> int:
    with engine().begin() as c:
        return c.execute(text(sql), params or {}).rowcount


def upsert(table: Table, values: dict, conflict: Iterable[str], update_cols: Iterable[str] | None = None,
           where: Any = None, conn=None) -> int:
    """INSERT ... ON CONFLICT (conflict) DO UPDATE (update_cols) — SQLite va PostgreSQL uchun bir xil.
    update_cols=None → ON CONFLICT DO NOTHING. where — faqat shart bajarilsa yangilash (eskiroq yozuv ustiga yozmaslik)."""
    if is_postgres():
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert
    stmt = _insert(table).values(**values)
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=list(conflict),
                                          set_={k: stmt.excluded[k] for k in update_cols}, where=where)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict))
    # RETURNING: yozilgan (qo'shilgan yoki yangilangan) qatorlar soni ikkala bazada ham aniq
    # (PostgreSQL/psycopg ON CONFLICT da rowcount -1 qaytaradi)
    stmt = stmt.returning(*[table.c[k] for k in conflict])
    if conn is not None:
        return len(conn.execute(stmt).fetchall())
    with engine().begin() as c:
        return len(c.execute(stmt).fetchall())


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, h_hex = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(h.hex(), h_hex)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("x" * 16)


# ----------------------------------------------------------------------------- kompaniya / foydalanuvchi
def create_company(name: str) -> int:
    with engine().begin() as c:
        r = c.execute(companies.insert().values(name=name[:200], created_at=time.time()))
        return int(r.inserted_primary_key[0])


# Eng ko'p uchraydigan parollar (sizib chiqqan ro'yxatlarning boshi) — bular bilan akkaunt bir necha urinishda ochiladi
COMMON_PASSWORDS = frozenset("""
12345678 123456789 1234567890 12345678910 87654321 11111111 00000000 88888888 66666666 12341234 11223344 1q2w3e4r
1qaz2wsx qwertyui qwerty123 qwerty12 password password1 password123 passw0rd admin123 admin1234 administrator
abcd1234 abc12345 iloveyou welcome1 letmein1 sunshine princess football baseball superman trustno1 changeme
parol123 parol1234 parolim1 dokon123 hushyor1 hushyor123 toshkent uzbekistan qwertyuiop asdfghjk zxcvbnm1
""".split())


VISIT_KEYS = ("in", "out", "staff_in", "staff_out", "off_in", "off_out")
OCC_FRESH_SEC = 600          # edge yuborgan "hozir ichkarida" shuncha vaqt ichida bo'lsa — o'sha ishlatiladi


def _int(v) -> int:
    try:
        return max(0, int(float(v or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def put_visits(conn, branch_id: int, minute: int, source_id: int, item: dict) -> None:
    """Edge daqiqalik qatori keldi: sanash maydonlari bo'lsa — visits_minute ga (almashtirib), bo'lmasa — o'chiriladi
    (edge qatorni qayta yuborganda sanash yo'qolgan bo'lishi mumkin)."""
    if source_id > 0 and isinstance(item, dict) and any(k in item for k in VISIT_KEYS):
        upsert(visits_minute, {"branch_id": branch_id, "minute": minute, "source_id": source_id,
                               "n_in": _int(item.get("in")), "n_out": _int(item.get("out")),
                               "n_staff": _int(item.get("staff_in")) + _int(item.get("staff_out")),
                               "n_off_in": _int(item.get("off_in")), "n_off_out": _int(item.get("off_out"))},
               ["branch_id", "minute", "source_id"], ["n_in", "n_out", "n_staff", "n_off_in", "n_off_out"], conn=conn)
    else:
        conn.execute(visits_minute.delete().where(visits_minute.c.branch_id == branch_id,
                                                  visits_minute.c.minute == minute,
                                                  visits_minute.c.source_id == source_id))


def visits_hourly(branch_ids: list[int], t0: float, t1: float | None = None, off: int = 0) -> list[dict]:
    """1.9.0 sanash: mahalliy soat bo'yicha yig'indi (SQL'da). off — vaqt mintaqasi siljishi, soniya (yarim soatli
    mintaqalarda ham soat chegarasi to'g'ri). [{"branch_id", "hour", "in", "out", "staff", "off_in", "off_out"}]."""
    if not branch_ids:
        return []
    from sqlalchemy import func

    v = visits_minute.c
    off = int(off)
    hour = ((v.minute + off) // 3600) * 3600 - off
    q = (select(v.branch_id, hour.label("hour"), func.sum(v.n_in).label("n_in"), func.sum(v.n_out).label("n_out"),
                func.sum(v.n_staff).label("n_staff"), func.sum(v.n_off_in).label("n_off_in"),
                func.sum(v.n_off_out).label("n_off_out"))
         .where(v.branch_id.in_([int(b) for b in branch_ids]), v.minute >= int(t0))
         .group_by(v.branch_id, hour))
    if t1 is not None:
        q = q.where(v.minute < int(t1))
    with engine().connect() as c:
        return [{"branch_id": int(r.branch_id), "hour": int(r.hour), "in": int(r.n_in or 0), "out": int(r.n_out or 0),
                 "staff": int(r.n_staff or 0), "off_in": int(r.n_off_in or 0), "off_out": int(r.n_off_out or 0)}
                for r in c.execute(q)]


QUEUE_COLS = ("t_obs", "len_sum", "len_max", "long_sec", "busy_sec", "cashier_sec", "cashier_known", "served",
              "wait_sum", "wait_n", "wait_max")
QUEUE_INT = {"len_max", "cashier_known", "served", "wait_n"}
QUEUE_STATES = ("navbat", "band", "bosh", "yopiq", "kassir_yoq", "nomalum")
QUEUES_FRESH_SEC = 180


def _fnum(v, hi: float = 1e9) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return min(max(f, 0.0), hi) if f == f and f not in (float("inf"), float("-inf")) else 0.0


def put_queue_minute(conn, branch_id: int, item: dict) -> bool:
    """Edge navbat daqiqasi (edge kechikkan qismni o'zida qo'shib, butun qatorni qayta yuboradi — almashtiriladi)."""
    try:
        minute, zid = int(item.get("minute")), int(item.get("zone_id"))
    except (TypeError, ValueError):
        return False
    vals = {"branch_id": branch_id, "minute": minute, "zone_id": zid, "zone_name": str(item.get("zone_name") or "")[:200],
            "source_id": _int(item.get("source_id")) or None}
    for k in QUEUE_COLS:
        v = _fnum(item.get(k), 1e7)
        vals[k] = int(v) if k in QUEUE_INT else round(v, 2)
    # Edge qatorni faqat kattalashtirib qayta yuboradi (kechikkan qism qo'shiladi). Eski nusxa yangisidan keyin yetib
    # kelsa (birinchisi xato bilan kutib qolgan edi) — ustiga yozilmaydi
    upsert(queue_minute, vals, ["branch_id", "minute", "zone_id"], ["zone_name", "source_id", *QUEUE_COLS],
           where=text("excluded.t_obs >= queue_minute.t_obs AND excluded.served >= queue_minute.served "
                      "AND excluded.wait_n >= queue_minute.wait_n"), conn=conn)
    return True


FLOW_GRID_RE = re.compile(r"^\d{1,4}x\d{1,4}@[0-9.]{1,6}$")
FLOW_MAX_CELLS = 400 * 400


def _sparse(d, nmax: int, integer: bool) -> dict:
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in list(d.items())[:20000]:
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= i < nmax:
            x = _fnum(v, 1e9)
            if x > 0:
                out[str(i)] = int(x) if integer else round(x, 1)
    return out


def put_flow_hour(conn, branch_id: int, item: dict) -> bool:
    """Edge soatlik oqim yig'indisi (soat davomida to'liq qayta yuboriladi — almashtiriladi, eskisi yangisini buzmaydi)."""
    try:
        hour, pid = int(item.get("hour")), int(item.get("plan_id"))
        nx, ny = int(item.get("nx")), int(item.get("ny"))
    except (TypeError, ValueError):
        return False
    grid = str(item.get("grid") or "")
    if hour % 3600 or not FLOW_GRID_RE.match(grid) or not (0 < nx * ny <= FLOW_MAX_CELLS):
        return False
    n = nx * ny
    vis = {}
    for k, v in (item.get("visits") or {}).items() if isinstance(item.get("visits"), dict) else []:
        try:
            vis[str(int(k))] = int(min(max(_fnum(v), 0), 1e6))
        except (TypeError, ValueError):
            continue
    moves = []
    for m in (item.get("moves") or [])[:500] if isinstance(item.get("moves"), list) else []:
        try:
            a, b, c = int(m[0]), int(m[1]), int(m[2])
        except (TypeError, ValueError, IndexError):
            continue
        if c > 0:
            moves.append([a, b, min(c, 10**6)])
    data = {"visits": vis, "moves": moves, "occ": _sparse(item.get("occ"), n, False),
            "pas": _sparse(item.get("pas"), n, True), "nx": nx, "ny": ny, "cell": _fnum(item.get("cell"), 10) or 0.5}
    vals = {"branch_id": branch_id, "plan_id": pid, "hour": hour, "grid": grid, "visits": sum(vis.values()),
            "obs": round(_fnum(item.get("obs"), 1e7), 1), "data": data}
    upsert(flow_hour, vals, ["branch_id", "plan_id", "hour"], ["grid", "visits", "obs", "data"],
           # Reja masshtabi o'zgargan bo'lsa (yangi to'r) — almashtiriladi, aks holda faqat kattarog'i (eski nusxa kechikib
           # kelsa yangisini buzmasin)
           where=text("excluded.grid <> flow_hour.grid OR (excluded.visits >= flow_hour.visits "
                      "AND excluded.obs >= flow_hour.obs)"), conn=conn)
    return True


def flow_rows(branch_ids: list[int], t0: float, t1: float | None = None, plan_id: int | None = None) -> list[dict]:
    if not branch_ids:
        return []
    f = flow_hour.c
    cond = [f.branch_id.in_([int(b) for b in branch_ids]), f.hour >= int(t0) - 3599]
    if t1 is not None:
        cond.append(f.hour < int(t1))
    if plan_id is not None:
        cond.append(f.plan_id == int(plan_id))
    with engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(select(flow_hour).where(*cond))]


def flow_with_data(branch_ids: list[int], t0: float) -> set[tuple[int, int]]:
    """Davrda ma'lumoti bor (filial, reja) juftlari — JSON o'qilmaydi."""
    if not branch_ids:
        return set()
    f = flow_hour.c
    sel = (select(f.branch_id, f.plan_id).where(f.branch_id.in_([int(b) for b in branch_ids]), f.hour >= int(t0) - 3599)
           .distinct())
    with engine().connect() as c:
        return {(int(r.branch_id), int(r.plan_id)) for r in c.execute(sel)}


def queue_agg(branch_ids: list[int], t0: float, off: int = 0, slot: int = 86400, t1: float | None = None) -> list[dict]:
    """Navbat yig'indisi SQL'da: filial × mahalliy kun (slot=86400) yoki soat (3600). off — vaqt mintaqasi (soniya).
    t1 (2.4.0) — oraliq oxiri (berilmasa — hozirgacha)."""
    if not branch_ids:
        return []
    from sqlalchemy import func

    q = queue_minute.c
    off = int(off)
    bucket = ((q.minute + off) // slot) * slot - off
    sel = (select(q.branch_id, bucket.label("b"), func.sum(q.t_obs).label("t_obs"), func.sum(q.len_sum).label("len_sum"),
                  func.max(q.len_max).label("len_max"), func.sum(q.long_sec).label("long_sec"),
                  func.sum(q.served).label("served"), func.sum(q.wait_sum).label("wait_sum"),
                  func.sum(q.wait_n).label("wait_n"), func.max(q.wait_max).label("wait_max"))
           .where(q.branch_id.in_([int(b) for b in branch_ids]), q.minute >= int(t0))
           .group_by(q.branch_id, bucket))
    if t1 is not None:
        sel = sel.where(q.minute < int(t1))
    with engine().connect() as c:
        return [{"branch_id": int(r.branch_id), "t": int(r.b), "t_obs": float(r.t_obs or 0),
                 "len_sum": float(r.len_sum or 0), "len_max": int(r.len_max or 0), "long_sec": float(r.long_sec or 0),
                 "served": int(r.served or 0), "wait_sum": float(r.wait_sum or 0), "wait_n": int(r.wait_n or 0),
                 "wait_max": float(r.wait_max or 0)} for r in c.execute(sel)]


def queues_fresh(v) -> list[dict] | None:
    d = json_load(v)
    if not isinstance(d, dict) or time.time() - float(d.get("at") or 0) > QUEUES_FRESH_SEC:
        return None
    return d.get("items") if isinstance(d.get("items"), list) else None


def occupancy_fresh(occupancy) -> bool:
    occ = json_load(occupancy)
    return isinstance(occ, dict) and time.time() - float(occ.get("at") or 0) <= OCC_FRESH_SEC


def inside_now(occupancy, cin: int, cout: int) -> int:
    """Hozir ichkarida: edge hello bilan yuborgani (qo'lda "0 qilish" va do'kon yopiq payti hisobga olingan) — yangi
    bo'lsa; aks holda (eski edge yoki aloqa yo'q) bulutdagi bugungi kirdi − chiqdi (xodimsiz, ish vaqtidan qat'i nazar)."""
    if occupancy_fresh(occupancy):
        return _int(json_load(occupancy).get("inside"))
    return max(0, int(cin) - int(cout))


def _backfill_visits() -> int:
    """Bir marta: 1.9.0 bulutiga yangilanishdan oldin kelgan sanashli qatorlar (metrics_minute JSON) → visits_minute."""
    if kv_get("visits_minute_v1") == "1":
        return 0
    from sqlalchemy import String, cast

    n = 0
    m = metrics_minute.c
    q = select(m.branch_id, m.minute, m.source_id, m.data).where(m.source_id > 0, cast(m.data, String).like('%"in"%'))
    with engine().begin() as c:
        for r in c.execute(q).fetchall():
            d = json_load(r.data) or {}
            if isinstance(d, dict) and "in" in d:
                put_visits(c, int(r.branch_id), int(r.minute), int(r.source_id), d)
                n += 1
    kv_set("visits_minute_v1", "1")
    return n


def password_problem(password: str, email: str = "") -> str | None:
    """Parol siyosati (OWASP ASVS 2.1): 8–200 belgi, keng tarqalgan parol emas, email/login bilan bir xil emas."""
    if len(password) < 8:
        return "parol kamida 8 belgi"
    if len(password) > 200:
        return "parol 200 belgidan oshmasin"
    low = password.lower()
    local = email.strip().lower().split("@")[0]
    if low in COMMON_PASSWORDS or len(set(low)) <= 2:
        return "bu parol juda oddiy (keng tarqalgan) — boshqasini tanlang"
    if email and (low == email.strip().lower() or (len(local) >= 4 and low == local)):
        return "parol email bilan bir xil bo'lmasin"
    return None


def create_user(company_id: int, email: str, password: str, name: str, role: str,
                branch_ids: Iterable[int] = ()) -> int:
    if role not in ROLES:
        raise ValueError("rol noto'g'ri")
    bad = password_problem(password, email)
    if bad:
        raise ValueError(bad)
    email = email.strip().lower()
    with engine().begin() as c:
        r = c.execute(users.insert().values(company_id=company_id, email=email, name=name[:200], role=role,
                                            pw_hash=hash_password(password), active=True, created_at=time.time()))
        uid = int(r.inserted_primary_key[0])
        for b in branch_ids:
            c.execute(user_branches.insert().values(user_id=uid, branch_id=int(b)))
        return uid


def login(email: str, password: str) -> tuple[str, dict] | None:
    """To'g'ri bo'lsa (sessiya tokeni, foydalanuvchi) — token faqat shu yerda ko'rinadi, bazada hash'i."""
    u = row("SELECT * FROM users WHERE email=:e AND active=:a", {"e": email.strip().lower(), "a": True})
    if not u:
        check_password(password, _DUMMY_HASH)     # vaqt bo'yicha email mavjudligini bilib bo'lmasin
        return None
    if not check_password(password, u["pw_hash"]):
        return None
    tok = secrets.token_urlsafe(32)
    with engine().begin() as c:
        c.execute(sessions.insert().values(token_hash=sha256(tok), user_id=u["id"], created_at=time.time(),
                                           expires_at=time.time() + SESSION_TTL))
        c.execute(sessions.delete().where(sessions.c.expires_at < time.time()))
    u.pop("pw_hash", None)
    return tok, u


def user_by_token(tok: str) -> dict | None:
    if not tok:
        return None
    return row("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=:h AND s.expires_at > :now "
               "AND u.active=:a", {"h": sha256(tok), "now": time.time(), "a": True})


def logout(tok: str) -> None:
    execute("DELETE FROM sessions WHERE token_hash=:h", {"h": sha256(tok)})


def set_password(user_id: int, password: str, keep_token: str | None = None) -> int:
    """Yangi parol; foydalanuvchining boshqa barcha sessiyalari yopiladi (keep_token — joriy sessiya qoladi).
    Qaytaradi: yopilgan sessiyalar soni."""
    with engine().begin() as c:
        c.execute(users.update().where(users.c.id == user_id).values(pw_hash=hash_password(password)))
        q = sessions.delete().where(sessions.c.user_id == user_id)
        if keep_token:
            q = q.where(sessions.c.token_hash != sha256(keep_token))
        return c.execute(q).rowcount or 0


def verify_password(user_id: int, password: str) -> bool:
    h = scalar("SELECT pw_hash FROM users WHERE id=:u", {"u": user_id})
    return bool(h) and check_password(password, str(h))


def audit(action: str, user: dict | None = None, target: str = "", detail: dict | None = None, ip: str = "",
          company_id: int | None = None, actor: str | None = None) -> None:
    """Audit jurnaliga yozuv. Hech qachon xato tashlamaydi (asosiy amal jurnal sababli buzilmasin)."""
    try:
        with engine().begin() as c:
            c.execute(audit_log.insert().values(
                t=time.time(), company_id=company_id if company_id is not None else (user or {}).get("company_id"),
                user_id=(user or {}).get("id"), actor=(actor if actor is not None else (user or {}).get("email") or "")[:200],
                action=action[:40], target=str(target or "")[:200], ip=str(ip or "")[:64], detail=detail))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("hushyor.cloud").exception("Audit jurnaliga yozib bo'lmadi (%s)", action)


def branch_ids_for(user: dict) -> list[int]:
    if user["role"] == "owner":
        return [r["id"] for r in rows("SELECT id FROM branches WHERE company_id=:c ORDER BY id", {"c": user["company_id"]})]
    return [r["branch_id"] for r in rows("SELECT ub.branch_id FROM user_branches ub JOIN branches b ON b.id=ub.branch_id "
                                         "WHERE ub.user_id=:u AND b.company_id=:c ORDER BY ub.branch_id",
                                         {"u": user["id"], "c": user["company_id"]})]


def branch_recipients(branch_id: int) -> list[dict]:
    """Shu filial haqida xabar olishi mumkin bo'lgan faol foydalanuvchilar: kompaniya egalari va biriktirilganlar."""
    return rows("SELECT u.id, u.email, u.name, u.role, u.company_id FROM users u JOIN branches b ON b.company_id=u.company_id "
                "WHERE b.id=:b AND u.active=:a AND (u.role='owner' OR EXISTS (SELECT 1 FROM user_branches ub "
                "WHERE ub.user_id=u.id AND ub.branch_id=b.id)) ORDER BY u.id", {"b": branch_id, "a": True})


# ----------------------------------------------------------------------------- filial / qurilma
def create_branch(company_id: int, name: str, device_id: str, address: str = "") -> tuple[int, str]:
    """Filial va uning qurilma tokeni. Token bir marta qaytariladi — edge .env dagi EDGE_TOKEN."""
    tok = secrets.token_hex(16)
    with engine().begin() as c:
        r = c.execute(branches.insert().values(company_id=company_id, name=name[:200], address=address[:300],
                                               device_id=device_id, token_hash=sha256(tok), created_at=time.time()))
        return int(r.inserted_primary_key[0]), tok


def rotate_token(branch_id: int) -> str:
    tok = secrets.token_hex(16)
    execute("UPDATE branches SET token_hash=:h WHERE id=:b", {"h": sha256(tok), "b": branch_id})
    return tok


def branch_by_device(device_id: str, token: str) -> dict | None:
    """Edge autentifikatsiyasi: qurilma identifikatori + token hash mos kelsa — filial."""
    b = row("SELECT * FROM branches WHERE device_id=:d", {"d": device_id})
    if b and hmac.compare_digest(b["token_hash"], sha256(token)):
        return b
    return None


def ensure_env_branch(device_id: str, name: str) -> dict:
    """Eski usul (CLOUD_EDGE_TOKENS muhit tokeni): qurilma uchun filial bo'lmasa, "Standart" kompaniyada yaratiladi."""
    b = row("SELECT * FROM branches WHERE device_id=:d", {"d": device_id})
    if b:
        return b
    comp = row("SELECT id FROM companies ORDER BY id LIMIT 1")
    cid = comp["id"] if comp else create_company("Mening kompaniyam")
    try:
        with engine().begin() as c:
            c.execute(branches.insert().values(company_id=cid, name=(name or device_id)[:200], address="",
                                               device_id=device_id, token_hash="env", created_at=time.time()))
    except Exception:  # noqa: BLE001 — bir vaqtda ikki hello: ikkinchisi bor qatorni oladi
        pass
    return row("SELECT * FROM branches WHERE device_id=:d", {"d": device_id})


def json_load(v) -> Any:
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v
