"""SQLite bazasi: hodisalar, kameralar, obunachilar, vazifalar, model versiyalari."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DB_PATH, DEFAULT_SETTINGS, SETTINGS_MIGRATIONS, SETTINGS_VERSION

_lock = threading.RLock()
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,           -- webcam | rtsp | file
    uri TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    loop INTEGER DEFAULT 1,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    source_name TEXT,
    job_id INTEGER,
    created_at REAL,
    video_time REAL,
    score REAL,
    level TEXT,
    kind TEXT,
    reasons TEXT,
    breakdown TEXT,
    track_id INTEGER,
    clip_path TEXT,
    thumb_path TEXT,
    crop_path TEXT,
    pose_path TEXT,
    ml_prob REAL,
    status TEXT DEFAULT 'yangi',  -- yangi | tasdiqlangan | rad_etilgan
    reviewed_by TEXT,
    reviewed_at REAL,
    note TEXT,
    telegram_sent INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    name TEXT,
    username TEXT,
    created_at REAL,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tg_messages (
    event_id INTEGER,
    chat_id INTEGER,
    message_id INTEGER,
    kind TEXT,
    PRIMARY KEY (event_id, chat_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    path TEXT,
    status TEXT,                  -- navbatda | ishlanmoqda | tayyor | xato | toxtatildi
    progress REAL DEFAULT 0,
    created_at REAL,
    finished_at REAL,
    events_count INTEGER DEFAULT 0,
    duration REAL,
    frames INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    name TEXT,
    kind TEXT NOT NULL,           -- polka | kassa | eshik | xodim | etiborsiz
    points TEXT NOT NULL,         -- JSON [[x, y], ...], 0..1
    sens TEXT DEFAULT '',         -- '' | yuqori | past
    enabled INTEGER DEFAULT 1,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_zones_source ON zones(source_id);
CREATE TABLE IF NOT EXISTS eval_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    path TEXT,                    -- asl fayl (tahlil shundan)
    play_path TEXT,               -- brauzerda ko'rish uchun (kerak bo'lsa H.264 ga o'tkazilgan)
    play_status TEXT,             -- tayyor | tayyorlanmoqda | xato
    duration REAL,
    fps REAL,
    width INTEGER,
    height INTEGER,
    zone_source_id INTEGER,       -- qaysi kameraning zonalari qo'llanadi
    labels TEXT DEFAULT '[]',     -- [{"start": s, "end": s, "kind": "...", "note": ""}]
    reviewed INTEGER DEFAULT 0,   -- belgilash tugallangan (faqat shular baholanadi)
    note TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL,
    finished_at REAL,
    status TEXT,                  -- ishlanmoqda | tayyor | xato | toxtatildi
    progress REAL DEFAULT 0,
    version TEXT,
    settings TEXT,                -- baholash paytidagi sozlamalar (JSON)
    videos INTEGER,
    hours REAL,
    tp INTEGER, fp INTEGER, fn INTEGER, dup INTEGER,
    precision REAL, recall REAL, f1 REAL,
    fp_per_hour REAL,
    latency_med REAL,
    details TEXT,                 -- har video bo'yicha natija (JSON)
    error TEXT
);
CREATE TABLE IF NOT EXISTS notify_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    channel TEXT,                 -- telegram | email | web | sms
    stage TEXT,                   -- alert (rasm) | clip (video) | full
    chat_id INTEGER,
    t_event REAL,                 -- hodisa aniqlangan devor vaqti
    t_queued REAL,                -- navbatga tushgan vaqt
    t_sent REAL,                  -- API javob qaytargan vaqt
    tg_date REAL,                 -- Telegram xabarga qo'ygan vaqt (soniya)
    ok INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_notify_sent ON notify_log(t_sent);
CREATE INDEX IF NOT EXISTS idx_notify_event ON notify_log(event_id);
CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL,
    path TEXT,
    metrics TEXT,
    n_samples INTEGER,
    n_pos INTEGER,
    data_desc TEXT,
    active INTEGER DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


@contextmanager
def tx():
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


# Keyin qo'shilgan ustunlar (eski bazalarga avtomatik qo'shiladi)
EXTRA_COLUMNS = {
    "events": {
        "susp_start": "REAL",   # shubhali harakat boshlangan vaqt (jonli: unix vaqt, fayl: video soniyasi)
        "susp_end": "REAL",     # signal berilgan vaqt
        "clip_start": "REAL",   # klipning birinchi kadri vaqti (shu bilan klip ichidagi o'rin topiladi)
        "t_detect": "REAL",     # hodisa aniqlangan devor vaqti (kechikish o'lchovi uchun)
        "claimed_by": "TEXT",   # "Ko'ryapman" bosgan qo'riqchi
        "claimed_at": "REAL",
    },
    "subscribers": {
        "kb_cleared": "INTEGER DEFAULT 0",   # eski botdan qolgan pastki tugmalar tozalanganmi
    },
    "jobs": {
        "zone_source_id": "INTEGER",          # yuklangan videoga qaysi kameraning zonalari qo'llanadi
    },
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in EXTRA_COLUMNS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    cur = c.execute("SELECT value FROM settings WHERE key='settings_version'").fetchone()
    ver = int(cur[0]) if cur and str(cur[0]).isdigit() else 1
    is_new = c.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    if not is_new:
        for v in sorted(SETTINGS_MIGRATIONS):
            if v <= ver:
                continue
            for key, (old, new) in SETTINGS_MIGRATIONS[v].items():
                row_ = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if row_ is not None and _same_num(row_[0], old):
                    c.execute("UPDATE settings SET value=? WHERE key=?", (new, key))
    c.execute("INSERT INTO settings(key, value) VALUES('settings_version', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SETTINGS_VERSION),))


def _same_num(a: Any, b: Any) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('connect_code', ?)",
                  (f"{secrets.randbelow(900000) + 100000}",))
        # Ishlanmay qolgan vazifalarni yopish (dastur qayta ishga tushganda)
        c.execute("UPDATE jobs SET status='toxtatildi' WHERE status IN ('navbatda','ishlanmoqda')")


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        cur = conn().execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def row(sql: str, params: Iterable[Any] = ()) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        cur = conn().execute(sql, tuple(params))
        r = cur.fetchone()
        return r[0] if r else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, tuple(params))
        return cur.lastrowid


# ---------- Sozlamalar ----------
_settings_cache: dict[str, str] = {}
_settings_ts = 0.0


def get_settings(force: bool = False) -> dict[str, str]:
    global _settings_cache, _settings_ts
    if force or not _settings_cache or time.time() - _settings_ts > 2:
        data = dict(DEFAULT_SETTINGS)
        for r in rows("SELECT key, value FROM settings"):
            data[r["key"]] = r["value"]
        _settings_cache = data
        _settings_ts = time.time()
    return dict(_settings_cache)


def get_setting(key: str, default: str = "") -> str:
    return get_settings().get(key, default)


def set_settings(values: dict[str, Any]) -> None:
    with tx() as c:
        for k, v in values.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    get_settings(force=True)


def fnum(key: str, default: float) -> float:
    try:
        return float(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def flag(key: str) -> bool:
    return get_setting(key, "0") in ("1", "true", "on", "yes")


# ---------- Hodisalar ----------
def insert_event(data: dict) -> int:
    data = dict(data)
    for k in ("reasons", "breakdown"):
        if k in data and not isinstance(data[k], str):
            data[k] = json.dumps(data[k], ensure_ascii=False)
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    return execute(f"INSERT INTO events({cols}) VALUES({marks})", data.values())


def update_event(event_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE events SET {sets} WHERE id=?", [*fields.values(), event_id])


def decode_event(ev: dict | None) -> dict | None:
    if not ev:
        return ev
    for k in ("reasons", "breakdown"):
        try:
            ev[k] = json.loads(ev.get(k) or "[]")
        except (TypeError, ValueError):
            ev[k] = []
    return ev


def get_event(event_id: int) -> dict | None:
    return decode_event(row("SELECT * FROM events WHERE id=?", (event_id,)))


def review_event(event_id: int, status: str, by: str, note: str | None = None) -> dict | None:
    fields = {"status": status, "reviewed_by": by, "reviewed_at": time.time()}
    if note is not None:
        fields["note"] = note
    update_event(event_id, **fields)
    return get_event(event_id)
