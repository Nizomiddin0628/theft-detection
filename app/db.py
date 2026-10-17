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
-- Bulutga yuborish navbati (store-and-forward): internet bo'lmasa ham yoziladi, yetkazilgach o'chadi
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,           -- event | clip | metrics | health | queue | qalert | flow
    key TEXT NOT NULL,            -- idempotent kalit (serverda takror aniqlanadi), masalan ev:12
    payload TEXT NOT NULL,        -- JSON
    created_at REAL,
    attempts INTEGER DEFAULT 0,   -- vaqtinchalik xatolar soni (ma'lumot uchun)
    next_try REAL DEFAULT 0,      -- shu vaqtdan oldin urinilmaydi (eksponensial kutish)
    sending INTEGER DEFAULT 0,    -- 1: hozir yuborilmoqda (yangilanish shu qatorga qo'shilmaydi)
    failed INTEGER DEFAULT 0,     -- 1: server qabul qilmadi (rad etilgan) — navbatni to'smaydi, panelda ko'rinadi
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_key ON outbox(key);
-- Daqiqalik ko'rsatkichlar (kamera va tizim): hisobotlar va bulut uchun
CREATE TABLE IF NOT EXISTS metrics_minute (
    minute INTEGER NOT NULL,      -- unix vaqt, daqiqa boshiga yaxlitlangan
    source_id INTEGER NOT NULL,   -- 0 — tizim (CPU, RAM, navbat)
    source_name TEXT,
    persons_avg REAL,
    persons_max INTEGER,
    fps_ai REAL,
    active_pct REAL,              -- vaqtning qancha ulushi "faol" rejimda
    up_pct REAL,                  -- kamera ishlagan vaqt ulushi
    alerts INTEGER,
    cpu REAL,
    ram REAL,
    extra TEXT,
    PRIMARY KEY (minute, source_id)
);
-- Odamlarni sanash (1.9.0): har kirish/chiqish chizig'idan o'tish (app/ai/counting.py), 90 kun
CREATE TABLE IF NOT EXISTS counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,              -- o'tish vaqti (unix)
    source_id INTEGER,
    source_name TEXT,
    line_id INTEGER,              -- zones.id (kind='chiziq')
    line_name TEXT,
    dir TEXT NOT NULL,            -- in | out
    track_id INTEGER,
    staff INTEGER DEFAULT 0,      -- xodim (xodim zonasida turgan) — xaridorlar soniga kirmaydi
    offhours INTEGER DEFAULT 0,   -- ish vaqtidan tashqari (sozlama work_hours)
    x REAL, y REAL                -- o'tish nuqtasi (0..1)
);
CREATE INDEX IF NOT EXISTS idx_counts_t ON counts(t);
CREATE INDEX IF NOT EXISTS idx_counts_src_t ON counts(source_id, t);
-- Qo'lda sanash sinovi (aniqlik darvozasi): egasi bosgan "kirdi/chiqdi" vaqtlari, avtomatik sanash bilan solishtiriladi
CREATE TABLE IF NOT EXISTS count_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    source_id INTEGER,            -- NULL — barcha kameralar
    taps TEXT,                    -- JSON [[t, "in"|"out"], ...]
    note TEXT
);
-- Kassa navbati (2.0.0, app/ai/queues.py): har xaridorning navbatda kutgan vaqti, 90 kun
CREATE TABLE IF NOT EXISTS queue_waits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    zone_id INTEGER,              -- zones.id (kind='navbat')
    zone_name TEXT,
    t_in REAL NOT NULL,           -- navbatga kirdi
    t_out REAL NOT NULL,          -- navbatdan chiqdi (to'lab bo'ldi)
    wait REAL NOT NULL,           -- soniya
    track_id INTEGER,
    ids INTEGER DEFAULT 1         -- kuzatuvda necha ID ulangan (to'silish)
);
CREATE INDEX IF NOT EXISTS idx_qwaits_t ON queue_waits(t_out);
CREATE INDEX IF NOT EXISTS idx_qwaits_zone ON queue_waits(zone_id, t_out);
-- Daqiqalik navbat ko'rsatkichlari (har kassa): hisobot, 3-4-qoida va bulut uchun
CREATE TABLE IF NOT EXISTS queue_minute (
    minute INTEGER NOT NULL,
    zone_id INTEGER NOT NULL,
    zone_name TEXT,
    source_id INTEGER,
    source_name TEXT,
    t_obs REAL DEFAULT 0,         -- kuzatilgan soniyalar (kamera ishlagan)
    len_sum REAL DEFAULT 0,       -- navbat uzunligi × soniya (o'rtacha = len_sum / t_obs)
    len_max INTEGER DEFAULT 0,
    long_sec REAL DEFAULT 0,      -- navbat ≥ queue_max_len bo'lgan soniyalar
    busy_sec REAL DEFAULT 0,      -- navbatda ≥ 1 kishi bo'lgan soniyalar (kassa band)
    cashier_sec REAL DEFAULT 0,   -- kassir joyida odam bo'lgan soniyalar
    cashier_known INTEGER DEFAULT 0,   -- kassir joyi chizilganmi
    served INTEGER DEFAULT 0,     -- navbatdan chiqqanlar (≥ 5 s kutgan)
    wait_sum REAL DEFAULT 0,
    wait_n INTEGER DEFAULT 0,
    wait_max REAL DEFAULT 0,
    PRIMARY KEY (minute, zone_id)
);
-- Navbat qoidalari signallari (1-4; 5-qoida — bulutda)
CREATE TABLE IF NOT EXISTS queue_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,
    rule TEXT NOT NULL,           -- R1 .. R4
    level TEXT,
    source_id INTEGER,
    source_name TEXT,
    zone_id INTEGER,
    zone_name TEXT,
    title TEXT,
    text TEXT,                    -- Telegram HTML (nomlar ekranlangan)
    data TEXT,                    -- JSON
    tg_sent INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_qalerts_t ON queue_alerts(t);
-- Navbat aniqligi darvozasi: kadrdagi navbatni egasi sanaydi (manual), tizim o'sha lahzadagi soni (auto)
CREATE TABLE IF NOT EXISTS queue_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,
    source_id INTEGER,
    zone_id INTEGER,
    zone_name TEXT,
    auto INTEGER,                 -- ko'rsatilgan (mediana) son
    raw INTEGER,                  -- shu kadrdagi xom son
    manual INTEGER,               -- egasi sanagan (NULL — hali javob yo'q)
    img TEXT                      -- kadr fayli (data/queue_samples/)
);
-- Navbat aniqligi darvozasi: sekundomer (odam navbatga turdi — chiqdi)
CREATE TABLE IF NOT EXISTS queue_timings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    zone_id INTEGER NOT NULL,
    zone_name TEXT,
    t0 REAL NOT NULL,
    t1 REAL,
    note TEXT
);
-- Oqim va heatmap (2.1.0, app/ai/flow.py): do'kon rejasi, bo'limlar, kamera kalibrlash
CREATE TABLE IF NOT EXISTS floor_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    img TEXT,                     -- data/plans/ dagi rasm fayli
    w_px INTEGER,
    h_px INTEGER,
    m_per_px REAL,                -- masshtab: metr / reja pikseli
    blank INTEGER DEFAULT 0,      -- rasmsiz reja (eni × bo'yi, katakli rasm o'zi yaratilgan)
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS plan_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    name TEXT,
    color TEXT,
    points TEXT,                  -- JSON [[x, y], ...] — reja pikselida (masshtab tuzatilsa o'zi to'g'rilanadi)
    enabled INTEGER DEFAULT 1,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS cam_calib (
    source_id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    pairs TEXT,                   -- JSON [{"img": [u, v] (0..1), "plan": [x, y] (reja pikseli), "check": 0|1}]
    model TEXT,                   -- JSON: app/ai/homography.Calib (metrda) + m_per_px
    stats TEXT,                   -- JSON: qoldiq, LOO, tekshiruv xatosi
    frame_w INTEGER,
    frame_h INTEGER,
    enabled INTEGER DEFAULT 1,
    updated_at REAL
);
-- Bo'lim tashriflari: har tashrif (kirgan vaqti), oldingi bo'lim — yo'nalishlar uchun. 90 kun
CREATE TABLE IF NOT EXISTS section_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,
    plan_id INTEGER,
    section_id INTEGER,
    section_name TEXT,
    source_id INTEGER,
    track_id INTEGER,
    from_id INTEGER               -- shu odamning oldingi bo'limi (3 daqiqa ichida) yoki NULL
);
CREATE INDEX IF NOT EXISTS idx_svisits_t ON section_visits(t);
CREATE INDEX IF NOT EXISTS idx_svisits_plan_t ON section_visits(plan_id, t);
-- Soatlik heatmap (har kamera): siyrak JSON {katak: qiymat}; grid — to'r kaliti (masshtab o'zgarsa aralashmasin)
CREATE TABLE IF NOT EXISTS flow_hour (
    hour INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    grid TEXT,
    obs REAL DEFAULT 0,           -- kamera kuzatgan soniyalar
    occ TEXT,                     -- bandlik: odam × soniya
    pas TEXT,                     -- o'tganlar: odam (soatiga bir marta)
    vec TEXT,                     -- yurish yo'nalishi: [soni, Σdx, Σdy] (2 m katak)
    PRIMARY KEY (hour, plan_id, source_id)
);
-- Darvoza: "nuqtada turish" — odam polda belgilangan joyda turadi, tizim o'lchaydi
CREATE TABLE IF NOT EXISTS flow_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,
    source_id INTEGER,
    plan_id INTEGER,
    x REAL, y REAL,               -- belgilangan joy (m)
    mx REAL, my REAL,             -- tizim o'lchagani (m, mediana)
    err REAL,                     -- m
    n INTEGER,                    -- o'lchovlar soni
    note TEXT
);
-- Darvoza: bo'lim tashriflarini qo'lda sanash
CREATE TABLE IF NOT EXISTS flow_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    plan_id INTEGER,
    sections TEXT,                -- JSON [bo'lim id, ...] — kuzatilayotganlar
    taps TEXT,                    -- JSON [[t, bo'lim id], ...]
    note TEXT
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
        "clip_error": "INTEGER DEFAULT 0",   # 1 — klip yaratib bo'lmadi (Telegram'da "tayyorlanmoqda" qolmasin)
    },
    "subscribers": {
        "kb_cleared": "INTEGER DEFAULT 0",   # eski botdan qolgan pastki tugmalar tozalanganmi
        "queue_alerts": "INTEGER DEFAULT 1",  # 2.0.0: navbat signallari shu chatga (/navbat tugmasi bilan o'chiriladi)
    },
    "jobs": {
        "zone_source_id": "INTEGER",          # yuklangan videoga qaysi kameraning zonalari qo'llanadi
    },
    "eval_runs": {
        "speed": "REAL",                      # tahlil vaqti / video vaqti (model og'irligi)
    },
    "outbox": {
        "failed": "INTEGER DEFAULT 0",        # 1.5.0 ishlab chiqish nusxalari uchun (jadval shu versiyada paydo bo'ldi)
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
        # Qurilma identifikatori (bulutda shu do'kon kompyuterini ajratish uchun) — bir marta yaratiladi
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('device_id', ?)", (secrets.token_hex(6),))
        c.execute("UPDATE outbox SET sending=0 WHERE sending=1")   # dastur to'xtaganda yarim qolgan yuborishlar
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
# Hodisa yozilgach/yangilangach chaqiriladigan funksiyalar: fn(event_id, fields_dict) — bulut navbati shu
# orqali to'ldiriladi (db.py sync'ni import qilmaydi, aylanma import bo'lmasin)
EVENT_HOOKS: list = []


def _notify_hooks(event_id: int, fields: dict) -> None:
    for fn in list(EVENT_HOOKS):
        try:
            fn(event_id, fields)
        except Exception:  # noqa: BLE001 — bulut navbati xatosi hodisa yozilishini to'xtatmasin
            import logging

            logging.getLogger("hushyor.db").exception("Hodisa hook xatosi")


def insert_event(data: dict) -> int:
    data = dict(data)
    for k in ("reasons", "breakdown"):
        if k in data and not isinstance(data[k], str):
            data[k] = json.dumps(data[k], ensure_ascii=False)
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    ev_id = execute(f"INSERT INTO events({cols}) VALUES({marks})", data.values())
    _notify_hooks(ev_id, {"_new": True, **data})
    return ev_id


def update_event(event_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE events SET {sets} WHERE id=?", [*fields.values(), event_id])
    _notify_hooks(event_id, fields)


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
