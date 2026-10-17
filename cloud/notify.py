"""Bulut bildirishnoma xizmati: Telegram v2 (egasi/menejer uchun bulut boti), email (SMTP), brauzer push (Web Push).

Ish tartibi (store-and-forward, edge'dagi kabi):
  * Hodisa, klip, tekshirish natijasi, kamera holati, do'kon aloqasi uzilishi/tiklanishi, javobsiz hodisa
    (eskalatsiya) — avval `notify_outbox` jadvaliga yoziladi (har foydalanuvchi va har manzil uchun alohida qator).
  * Har kanalga alohida yuborish oqimi (Telegram xabarlari, Telegram videolari, email, push — sekin kanal boshqasini
    to'xtatmaydi); hodisa/eskalatsiya/tahrir birinchi. Muvaffaqiyat — `sent`, vaqtinchalik xato — 5 s, 20 s, 1, 5,
    15 daqiqa kutib qayta (6 urinish), doimiy xato (bot bloklangan, push obunasi o'chgan) — `failed`, manzil o'chiriladi.
    Navbatda turganida hodisa tekshirilsa — eskirgan tugmali xabar yuborilmaydi (`skipped`); faol bo'lmagan xodimga ham.
  * Kim nima oladi — foydalanuvchi sozlamasi (rol bo'yicha sukut): qo'riqchi — hodisalar; menejer — hodisalar,
    kamera, aloqa, eskalatsiya; egasi — ball ≥ 75 hodisalar, kamera, aloqa, eskalatsiya.
  * Bulut boti alohida token (CLOUD_TELEGRAM_BOT_TOKEN) — do'kondagi botga xalaqit bermaydi. Ulanish: panelda bir
    martalik havola (t.me/<bot>?start=<kod>, 10 daqiqa). Tugmalar ✅/❌ panel bilan bir xil yo'ldan (service.review).

Muhit: CLOUD_TELEGRAM_BOT_TOKEN, CLOUD_PUBLIC_URL, CLOUD_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS, CLOUD_VAPID_SUB,
CLOUD_TZ_OFFSET (soat, sukut 5 — Toshkent), CLOUD_OFFLINE_MIN (5), CLOUD_ESCALATE_MIN (10; 0 — o'chiq).
"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
import secrets
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass, field
from email.message import EmailMessage

import httpx
from sqlalchemy import func

from . import db as cdb
from .db import CLIPS_DIR, DATA_DIR

log = logging.getLogger("hushyor.cloud.notify")

KIND_UZ = {"pocket": "Cho'ntakka solish", "torso": "Kiyim ichiga yashirish", "bag": "Sumkaga solish",
           "offscreen": "Qo'l kadrdan yashirildi", "exit": "Chiqish tomon ketmoqda",
           # 2.2.0: xavfsizlik va salomatlik (edge: app/ai/safety.py — bulutda numpy/cv2 yo'q, shuning uchun nusxa)
           "fall": "Yiqilish", "lying": "Odam yotibdi", "night": "Tungi kirish", "crowd": "Gavjumlik",
           "blocked": "Chiqish to'silgan", "fire": "Olov (tajriba)", "fight": "Janjal (tajriba)"}
SAFETY_KINDS = ("fall", "lying", "night", "crowd", "blocked", "fire", "fight")
SAFETY_HEAD = {"fall": "🆘 Yiqilish — yordam kerak bo'lishi mumkin", "lying": "🆘 Odam yotibdi — yordam kerak bo'lishi mumkin",
               "night": "🌙 Tungi kirish — ish vaqtidan tashqari odam", "crowd": "👥 Gavjumlik — odam soni me'yordan oshdi",
               "blocked": "🚪 Favqulodda chiqish to'silgan", "fire": "🔥 Olov belgisi (tajriba) — darhol tekshiring",
               "fight": "🥊 Janjal bo'lishi mumkin (tajriba) — darhol tekshiring"}
STATUS_UZ = {"tasdiqlangan": "✅ TASDIQLANDI", "rad_etilgan": "❌ XATO SIGNAL", "yangi": "↩️ Qaror bekor qilindi"}
RETRY = (5, 20, 60, 300, 900)            # soniya: har xatodan keyingi kutish; 6-urinish ham xato bo'lsa — failed
NOTIFY_MAX_AGE = 600                     # bundan eski hodisa (navbatdan kelgan, backfill) uchun xabar yuborilmaydi
STARTUP_GRACE = 300                      # server ishga tushgach shuncha + offline_sec kutiladi (edge qayta ulanib ulgursin)
MAX_TARGETS = 10                         # bitta xodimga bitta kanalda ko'pi bilan manzil
TEST_EVERY = 20.0                        # sinov xabari: bitta xodim uchun shuncha soniyada bir marta
VIDEO_LIMIT = 49_000_000
LINK_TTL = 600
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "notify.windows.com", "web.push.apple.com", "push.apple.com")

DEFAULT_PREFS = {
    "guard": {"event": True, "min_score": 0, "camera": False, "offline": False, "escalation": False,
              "queue": False, "queue_low": False},
    "manager": {"event": True, "min_score": 0, "camera": True, "offline": True, "escalation": True,
                "queue": True, "queue_low": False},
    "owner": {"event": True, "min_score": 75, "camera": True, "offline": True, "escalation": True,
              "queue": True, "queue_low": False},
}
# 2.0.0: queue — navbat qoidalari 1, 2, 4 (va egasiga 5 — tarmoq signali); queue_low — 3-qoida ("kassir kerak emas")
PREF_BOOL = ("event", "camera", "offline", "escalation", "queue", "queue_low")
QUEUE_RULES = {"R1": "Qo'shimcha kassa oching", "R2": "Kassani tekshiring", "R3": "Qo'shimcha kassir kerak emas",
               "R4": "Kassir jadvali", "R5": "Tarmoq signali"}
QUEUE_ICON = {"R1": "🧾", "R2": "⚠️", "R3": "ℹ️", "R4": "📅", "R5": "🌐"}
NET_WINDOW = 15 * 60              # 5-qoida: shu vaqt ichida ≥ 2 filialda 1/2-qoida
NET_COOLDOWN = 30 * 60


@dataclass
class Config:
    tg_token: str = ""
    tg_api: str = "https://api.telegram.org"
    public_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: str = "starttls"           # starttls | ssl | none
    vapid_sub: str = "mailto:admin@hushyor.local"
    tz_offset: float = 5.0
    offline_sec: float = 300.0
    escalate_sec: float = 600.0
    push_allow_any: bool = False         # sinovlar uchun: istalgan push manzili (aks holda faqat ma'lum push xizmatlari)
    edge_tg_token: str = ""              # do'kon boti tokeni (bir xil bo'lsa — bulut boti o'chiriladi)
    extra: dict = field(default_factory=dict)


cfg = Config()
state = {"tg_bot": None, "tg_error": "", "push_error": "", "tg_conflict": False}


def _env(key: str, default: str = "") -> str:
    return cdb.env(key, default)


def configure() -> Config:
    """Muhitdan o'qish (server ishga tushganda). Sinovlar cfg maydonlarini to'g'ridan-to'g'ri o'zgartiradi."""
    global cfg

    def num(key: str, default: float) -> float:
        try:
            return float(_env(key, str(default)) or default)
        except ValueError:
            return default
    cfg = Config(
        tg_token=_env("CLOUD_TELEGRAM_BOT_TOKEN").strip(),
        tg_api=(_env("CLOUD_TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/"),
        public_url=_env("CLOUD_PUBLIC_URL").rstrip("/"),
        smtp_host=_env("CLOUD_SMTP_HOST").strip(), smtp_port=int(num("CLOUD_SMTP_PORT", 587)),
        smtp_user=_env("CLOUD_SMTP_USER"), smtp_password=_env("CLOUD_SMTP_PASSWORD"),
        smtp_from=_env("CLOUD_SMTP_FROM") or _env("CLOUD_SMTP_USER"),
        smtp_tls=(_env("CLOUD_SMTP_TLS") or "starttls").lower(),
        vapid_sub=_env("CLOUD_VAPID_SUB") or "mailto:admin@hushyor.local",
        tz_offset=num("CLOUD_TZ_OFFSET", 5.0), offline_sec=num("CLOUD_OFFLINE_MIN", 5) * 60,
        escalate_sec=num("CLOUD_ESCALATE_MIN", 10) * 60,
        edge_tg_token=_env("TELEGRAM_BOT_TOKEN").strip(),
    )
    state["tg_conflict"] = bool(cfg.tg_token and cfg.tg_token == cfg.edge_tg_token)
    if state["tg_conflict"]:
        log.error("CLOUD_TELEGRAM_BOT_TOKEN do'kon botining tokeni bilan bir xil — bulut boti o'chirildi. "
                  "@BotFather'da alohida bot yarating.")
    return cfg


def channels() -> dict:
    """Qaysi kanallar sozlangan (panel uchun)."""
    return {
        "telegram": {"enabled": bool(cfg.tg_token) and not state["tg_conflict"],
                     "bot": (state["tg_bot"] or {}).get("username"), "error": state["tg_error"]},
        "email": {"enabled": bool(cfg.smtp_host and cfg.smtp_from)},
        "push": {"enabled": vapid_public_key() is not None, "error": state["push_error"]},
    }


# ----------------------------------------------------------------------------- yordamchilar
def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M:%S", time.gmtime(float(ts) + cfg.tz_offset * 3600))


def flow_today_text(b: dict, day0: float) -> str:
    """2.1.0: filialning bugungi eng ko'p tashrif qilingan 3 bo'limi (edge soatlik oqim yig'indisi)."""
    try:
        rows = cdb.flow_rows([b["id"]], day0)
    except Exception:  # noqa: BLE001
        return ""
    plans = {p["id"]: p for p in ((cdb.json_load(b.get("flow_plans")) or {}).get("items") or [])}
    tot: dict = {}
    for r in rows:
        if r["hour"] < day0 - 1:
            continue
        names = {str(s_["id"]): s_["name"] for s_ in (plans.get(r["plan_id"]) or {}).get("sections") or []}
        for k, v in ((cdb.json_load(r["data"]) or {}).get("visits") or {}).items():
            nm = names.get(k, f"#{k}")
            tot[nm] = tot.get(nm, 0) + int(v)
    if not tot:
        return ""
    top = sorted(tot.items(), key=lambda kv: -kv[1])[:3]
    return "bo'limlar: " + ", ".join(f"{esc(k)} {v}" for k, v in top)


def fmt_dur(sec: float) -> str:
    sec = max(0, int(sec))
    if sec >= 3600:
        return f"{sec // 3600} soat {sec % 3600 // 60} daqiqa"
    if sec >= 90:
        return f"{round(sec / 60)} daqiqa"
    return f"{sec} soniya"


def panel_link(path: str) -> str | None:
    """Telegram URL tugmasi faqat ochiq (localhost bo'lmagan) http(s) manzil bilan ishlaydi."""
    u = cfg.public_url
    if not u or not u.startswith(("http://", "https://")) or re.search(r"//(localhost|127\.|0\.0\.0\.0)", u):
        return None
    return f"{u}{path}"


# ----------------------------------------------------------------------------- sozlamalar va manzillar
def get_prefs(user: dict) -> dict:
    base = dict(DEFAULT_PREFS.get(user.get("role"), DEFAULT_PREFS["guard"]))
    stored = cdb.json_load(cdb.scalar("SELECT data FROM notify_prefs WHERE user_id=:u", {"u": user["id"]})) or {}
    if isinstance(stored, dict):
        for k in PREF_BOOL:
            if isinstance(stored.get(k), bool):
                base[k] = stored[k]
        if isinstance(stored.get("min_score"), (int, float)):
            base["min_score"] = max(0, min(100, int(stored["min_score"])))
    return base


def set_prefs(user: dict, data: dict) -> dict:
    cur = get_prefs(user)
    for k in PREF_BOOL:
        if k in data:
            cur[k] = bool(data[k])
    if "min_score" in data:
        try:
            cur["min_score"] = max(0, min(100, int(data["min_score"])))
        except (TypeError, ValueError):
            raise ValueError("min_score 0..100")
    cdb.upsert(cdb.notify_prefs, {"user_id": user["id"], "data": cur}, ["user_id"], ["data"])
    return cur


def targets(user_id: int, enabled_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM notify_targets WHERE user_id=:u"
    if enabled_only:
        sql += " AND enabled=:e"
    return cdb.rows(sql + " ORDER BY id", {"u": user_id, "e": True})


class TooMany(ValueError):
    pass


def add_target(user_id: int, channel: str, address: str, data: dict | None = None) -> int:
    with cdb.engine().begin() as c:
        if channel == "push":
            # Bitta brauzer — bitta xodim: shu brauzer boshqa xodimga ulangan bo'lsa, undan uziladi
            c.execute(cdb.notify_targets.delete().where(cdb.notify_targets.c.channel == "push",
                                                        cdb.notify_targets.c.address == address,
                                                        cdb.notify_targets.c.user_id != user_id))
        r = c.execute(cdb.notify_targets.select().where(
            cdb.notify_targets.c.user_id == user_id, cdb.notify_targets.c.channel == channel,
            cdb.notify_targets.c.address == address)).first()
        if r is not None:
            c.execute(cdb.notify_targets.update().where(cdb.notify_targets.c.id == r.id)
                      .values(enabled=True, data=data, last_error=None))
            return int(r.id)
        if channel == "push":
            # o'chgan (bekor qilingan, kaliti mos bo'lmagan) eski brauzer obunalari joy egallamasin
            c.execute(cdb.notify_targets.delete().where(cdb.notify_targets.c.user_id == user_id,
                                                        cdb.notify_targets.c.channel == "push",
                                                        cdb.notify_targets.c.enabled.is_(False)))
        n = c.execute(cdb.notify_targets.select().with_only_columns(func.count()).where(
            cdb.notify_targets.c.user_id == user_id, cdb.notify_targets.c.channel == channel,
            cdb.notify_targets.c.enabled.is_(True))).scalar() or 0
        if n >= MAX_TARGETS:
            raise TooMany(f"bu kanalda ko'pi bilan {MAX_TARGETS} ta manzil — keraksizini uzing")
        res = c.execute(cdb.notify_targets.insert().values(user_id=user_id, channel=channel, address=address[:600],
                                                           data=data, enabled=True, created_at=time.time()))
        return int(res.inserted_primary_key[0])


def public_target(t: dict) -> dict:
    addr = str(t["address"])
    if t["channel"] == "push":
        m = re.match(r"https?://([^/]+)", addr)
        shown = f"brauzer ({m.group(1) if m else 'push'})"
    elif t["channel"] == "telegram":
        shown = (cdb.json_load(t.get("data")) or {}).get("name") or f"chat {addr[-4:]}"
    else:
        shown = addr
    return {"id": t["id"], "channel": t["channel"], "address": shown, "enabled": bool(t["enabled"]),
            "last_ok": t.get("last_ok"), "last_error": t.get("last_error"), "created_at": t.get("created_at")}


def push_endpoint_ok(endpoint: str) -> bool:
    if cfg.push_allow_any:
        return endpoint.startswith(("https://", "http://"))
    m = re.match(r"https://([A-Za-z0-9.\-]+)(:\d+)?/", endpoint or "")
    if not m:
        return False
    host = m.group(1).lower()
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


# ----------------------------------------------------------------------------- navbatga yozish
_wakeups: list[threading.Event] = []       # har yuborish yo'lagi (Sender) uchun


def _wake() -> None:
    for e in list(_wakeups):
        e.set()


def _channel_ready(ch: str) -> bool:
    if ch == "telegram":
        return bool(cfg.tg_token) and not state["tg_conflict"]
    if ch == "email":
        return bool(cfg.smtp_host and cfg.smtp_from)
    if ch == "push":
        return vapid_public_key() is not None
    return False


def enqueue(user_id: int, target: dict, kind: str, ref: str, payload: dict) -> None:
    status = "pending" if _channel_ready(target["channel"]) else "skipped"
    with cdb.engine().begin() as c:
        c.execute(cdb.notify_outbox.insert().values(user_id=user_id, target_id=target["id"], channel=target["channel"],
                                                    kind=kind, ref=str(ref)[:120], payload=payload, status=status,
                                                    attempts=0, next_try=0, created_at=time.time(),
                                                    error=None if status == "pending" else "kanal sozlanmagan"))
    _wake()


def notify_branch(branch_id: int, kind: str, ref: str, payload: dict, pref: str, score: float | None = None,
                  roles: tuple[str, ...] | None = None, channels_only: tuple[str, ...] | None = None) -> int:
    """Filial bo'yicha qabul qiluvchilarga (sozlamasi ruxsat bersa) har manzilga bitta qator."""
    n = 0
    for u in cdb.branch_recipients(branch_id):
        if roles and u["role"] not in roles:
            continue
        p = get_prefs(u)
        if not p.get(pref):
            continue
        if score is not None and score < p.get("min_score", 0):
            continue
        for t in targets(u["id"]):
            if channels_only and t["channel"] not in channels_only:
                continue
            enqueue(u["id"], t, kind, ref, payload)
            n += 1
    return n


def _event_payload(ev: dict, branch: dict) -> dict:
    d = cdb.json_load(ev.get("data")) or {}
    skew = float(branch.get("clock_skew") or 0)
    return {"uid": ev["uid"], "branch_id": branch["id"], "branch": branch.get("name"), "camera": ev.get("source_name"),
            "kind": ev.get("kind"), "score": float(ev.get("score") or 0), "level": d.get("level"),
            "reasons": [str(r) for r in (d.get("reasons") or [])][:6], "local_id": ev.get("local_id"),
            "t_event": float(ev.get("created_at") or 0) + skew, "status": ev.get("status")}


# ----------------------------------------------------------------------------- hodisa trigerlari
def should_alert(item: dict, branch: dict) -> bool:
    """Yangi hodisa haqida xabar (va panelda ovozli toast) kerakmi: jonli, tekshirilmagan, video tahlili emas."""
    if item.get("job_id") is not None or (item.get("status") or "yangi") != "yangi":
        return False
    try:
        t = float(item.get("created_at") or 0) + float(branch.get("clock_skew") or 0)
    except (TypeError, ValueError):
        return False
    return time.time() - t <= NOTIFY_MAX_AGE


def on_new_event(uid: str) -> int:
    """Edge'dan yangi hodisa keldi. Video tahlili (job_id) va eski (backfill/uzilishdan keyin) hodisalar uchun xabar yo'q."""
    ev = cdb.row("SELECT * FROM events WHERE uid=:u", {"u": uid})
    if not ev:
        return 0
    br = cdb.row("SELECT * FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {"id": ev["branch_id"]}
    d = cdb.json_load(ev.get("data")) or {}
    if d.get("job_id") is not None:
        return 0
    p = _event_payload(ev, br)
    if time.time() - p["t_event"] > NOTIFY_MAX_AGE or (ev.get("status") or "yangi") != "yangi":
        return 0
    # 2.2.0: xavfsizlik signali (yiqilish, tungi kirish...) — egasining min_score chegarasi (75) uni to'smaydi
    return notify_branch(br["id"], "event", uid, p, "event",
                         score=None if p.get("kind") in SAFETY_KINDS else p["score"])


def _clip_for(s: dict) -> bool:
    """Bitta yuborilgan hodisa xabariga (tg_sent) klip — agar shu manzilga hali navbatga qo'yilmagan bo'lsa."""
    t = cdb.row("SELECT * FROM notify_targets WHERE id=:t AND enabled=:e", {"t": s["target_id"], "e": True})
    if not t:
        return False
    # Ikki yo'ldan (klip keldi / xabar yuborildi) bir vaqtda chaqirilsa ham — bitta: birlamchi kalit atomar
    if not cdb.upsert(cdb.notify_once, {"key": f"clip:{t['id']}:{s['uid']}"[:200], "at": time.time()}, ["key"]):
        return False
    enqueue(t["user_id"], t, "clip", s["uid"], {"uid": s["uid"], "reply_to": s["message_id"], "sent_id": s["id"]})
    return True


def on_clip(uid: str) -> int:
    """Klip keldi: bulut botida shu hodisa xabari bor chatlarga video javob sifatida (xabar hali yuborilmagan
    bo'lsa — xabar yuborilgach send_telegram o'zi qo'yadi)."""
    return sum(_clip_for(s) for s in cdb.rows(
        "SELECT * FROM tg_sent WHERE uid=:u AND kind='event' AND message_id IS NOT NULL", {"u": uid}))


def on_status_change(uid: str, status: str, by: str | None) -> int:
    """Hodisa tekshirildi (panel, bulut boti yoki do'kon): bulut botidagi xabarlar tahrirlanadi, tugmalar olinadi."""
    n = 0
    for s in cdb.rows("SELECT * FROM tg_sent WHERE uid=:u AND kind='event' AND message_id IS NOT NULL", {"u": uid}):
        t = cdb.row("SELECT * FROM notify_targets WHERE id=:t AND enabled=:e", {"t": s["target_id"], "e": True})
        if t:
            enqueue(t["user_id"], t, "review", uid, {"uid": uid, "status": status, "by": by, "sent_id": s["id"],
                                                     "message_id": s["message_id"]})
            n += 1
    return n


def plain_text(s: str, limit: int = 1500) -> str:
    """Edge'dan kelgan HTML (Telegram uchun <b>, ekranlangan nomlar) → oddiy matn: bulutda yana ekranlanadi.
    Avval teglar olinadi, keyin ekranlash ochiladi — nomdagi "<" belgisi (&lt;) yo'qolmasin."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(s or "")))[:limit]


def on_queue_alert(branch: dict, alert: dict) -> int:
    """2.0.0: edge navbat qoidasi signali (bulutga yangi kelgan va eskirmagan). 1-2-4 → "queue" sozlamasi bo'yicha
    (qo'riqchiga sukut bo'yicha yo'q), 3 → "queue_low" (sukut bo'yicha hech kimga). Keyin 5-qoida tekshiriladi."""
    rule = str(alert.get("rule") or "")
    p = {"branch_id": branch["id"], "branch": branch.get("name"), "rule": rule, "title": alert.get("title"),
         "zone": alert.get("zone_name"), "text": alert.get("text"), "t_event": time.time()}
    n = notify_branch(branch["id"], "queue", str(alert.get("key") or "")[:120], p,
                      "queue_low" if rule == "R3" else "queue")
    if rule in ("R1", "R2"):
        n += check_network(int(branch["company_id"]))
    return n


def check_network(company_id: int, now: float | None = None) -> int:
    """5-qoida: bir vaqtda (NET_WINDOW) ≥ 2 filialda 1/2-qoida — kompaniya egalariga bitta xabar (NET_COOLDOWN da
    bir marta). Bir vaqtda hamma joyda navbat — ko'pincha umumiy sabab: to'lov terminali / internet / kassa dasturi."""
    now = time.time() if now is None else now
    rows = cdb.rows("SELECT a.branch_id, b.name, MAX(a.t) AS t, COUNT(*) AS n FROM queue_alerts a JOIN branches b "
                    "ON b.id=a.branch_id WHERE a.company_id=:c AND a.rule IN ('R1','R2') AND a.t >= :t "
                    "GROUP BY a.branch_id, b.name ORDER BY a.branch_id", {"c": company_id, "t": now - NET_WINDOW})
    if len(rows) < 2:
        return 0
    last = cdb.scalar("SELECT MAX(t) FROM queue_alerts WHERE company_id=:c AND rule='R5'", {"c": company_id})
    if last and now - float(last) < NET_COOLDOWN:
        return 0
    names = [str(r["name"]) for r in rows]
    text = (f"Oxirgi {NET_WINDOW // 60} daqiqada {len(rows)} ta filialda navbat signali: {', '.join(names)}. "
            "Bir vaqtda hamma joyda navbat bo'lsa — umumiy sabab bo'lishi mumkin: to'lov terminali, internet, kassa "
            "dasturi yoki aksiya. Tekshiring.")
    key = f"net:{company_id}:{int(now)}"
    if not cdb.upsert(cdb.queue_alerts, {"key": key, "company_id": company_id, "branch_id": None, "t": now,
                                         "rule": "R5", "level": "danger", "zone_name": "", "source_name": "",
                                         "title": QUEUE_RULES["R5"], "text": text,
                                         "data": {"branches": [int(r["branch_id"]) for r in rows]},
                                         "received_at": now}, ["key"]):
        return 0
    p = {"branch_id": None, "branch": f"{len(rows)} ta filial", "rule": "R5", "title": QUEUE_RULES["R5"], "zone": "",
         "text": text, "t_event": now}
    n = 0
    for u in cdb.rows("SELECT id, email, name, role, company_id FROM users WHERE company_id=:c AND role='owner' "
                      "AND active=:a ORDER BY id", {"c": company_id, "a": True}):
        if not get_prefs(u).get("queue"):
            continue
        for t in targets(u["id"]):
            enqueue(u["id"], t, "queue", key, p)
            n += 1
    return n


def on_health(branch: dict, item: dict) -> int:
    state_ = str(item.get("state") or "")
    if state_ not in ("down", "up"):
        return 0
    skew = float(branch.get("clock_skew") or 0)
    since = item.get("since")
    since = float(since) + skew if isinstance(since, (int, float)) else None
    p = {"branch_id": branch["id"], "branch": branch.get("name"), "camera": item.get("source_name"),
         "state": state_, "reason": item.get("reason"), "since": since, "t_event": time.time()}
    return notify_branch(branch["id"], "camera", f"{branch['id']}:{item.get('source_id')}", p, "camera")


_inflight: dict[int, int] = {}              # filial → hozir davom etayotgan edge so'rovlari (katta klip yuklash)
_inflight_lock = threading.Lock()


def inflight(branch_id: int, delta: int) -> None:
    with _inflight_lock:
        v = _inflight.get(branch_id, 0) + delta
        if v > 0:
            _inflight[branch_id] = v
        else:
            _inflight.pop(branch_id, None)


def touch(branch: dict, pending: int | None = None) -> int:
    """Edge'dan istalgan so'rov keldi (hello, hodisa, klip...): last_seen yangilanadi; oldin "aloqa uzildi" deb xabar
    qilingan bo'lsa — atomar ravishda "tiklandi" (bir vaqtda ikki so'rov kelsa ham bitta xabar)."""
    now = time.time()
    since = branch.get("offline_since")
    if since:
        n = cdb.execute("UPDATE branches SET offline_since=NULL, last_seen=:n WHERE id=:b AND offline_since IS NOT NULL",
                        {"n": now, "b": branch["id"]})
        if n != 1:
            return 0
        p = {"branch_id": branch["id"], "branch": branch.get("name"), "since": since,
             "duration": now - float(since), "pending": int(pending or 0), "t_event": now}
        from .live import hub

        hub.publish("branch", {"branch_id": branch["id"], "online": True}, branch["id"])
        return notify_branch(branch["id"], "online", str(branch["id"]), p, "offline")
    if now - float(branch.get("last_seen") or 0) > 15:
        cdb.execute("UPDATE branches SET last_seen=:n WHERE id=:b AND (last_seen IS NULL OR last_seen < :n)",
                    {"n": now, "b": branch["id"]})
    return 0


def on_hello(branch_before: dict, pending: int) -> int:
    """Eski nom (1.7.0 dan oldingi chaqiruvlar uchun)."""
    return touch(branch_before, pending)


def monitor() -> dict:
    """Davriy tekshiruv (worker): aloqasi uzilgan do'konlar va javobsiz hodisalar (eskalatsiya)."""
    now = time.time()
    out = {"offline": 0, "escalated": 0}
    # Server o'zi to'xtab turgan bo'lsa, hamma do'kon "uzilgan" bo'lib ko'rinadi — edge qayta ulanguncha kutiladi
    if cfg.offline_sec > 0 and now - _started[0] >= cfg.offline_sec + STARTUP_GRACE:
        for b in cdb.rows("SELECT * FROM branches WHERE last_seen IS NOT NULL AND last_seen < :t AND offline_since IS NULL",
                          {"t": now - cfg.offline_sec}):
            if _inflight.get(b["id"]):
                continue                    # katta klip yuklanmoqda (so'rov hali tugamagan) — aloqa bor
            n = cdb.execute("UPDATE branches SET offline_since=:s WHERE id=:b AND offline_since IS NULL",
                            {"s": b["last_seen"], "b": b["id"]})
            if n:
                p = {"branch_id": b["id"], "branch": b["name"], "since": b["last_seen"], "t_event": now}
                notify_branch(b["id"], "offline", str(b["id"]), p, "offline")
                from .live import hub

                hub.publish("branch", {"branch_id": b["id"], "online": False}, b["id"])
                out["offline"] += 1
    if cfg.escalate_sec > 0:
        evs = cdb.rows("SELECT e.* FROM events e LEFT JOIN escalations x ON x.uid=e.uid WHERE x.uid IS NULL "
                       "AND e.status='yangi' AND e.received_at IS NOT NULL AND e.received_at < :t AND e.received_at > :t0",
                       {"t": now - cfg.escalate_sec, "t0": now - 86400})
        skews = {b["id"]: float(b.get("clock_skew") or 0) for b in cdb.rows("SELECT id, clock_skew FROM branches")}
        for ev in evs:
            d = cdb.json_load(ev.get("data")) or {}
            if d.get("job_id") is not None:
                continue
            # Faqat "jonli" kelgan hodisa (navbatdan/backfill'dan kelgan eski hodisa yuqoriga uzatilmaydi)
            t_event = float(ev.get("created_at") or 0) + skews.get(ev["branch_id"], 0.0)
            if float(ev["received_at"]) - t_event > NOTIFY_MAX_AGE:
                continue
            ins = cdb.upsert(cdb.escalations, {"uid": ev["uid"], "at": now}, ["uid"])
            if not ins:
                continue
            br = cdb.row("SELECT * FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {"id": ev["branch_id"]}
            p = _event_payload(ev, br)
            p["waited"] = now - float(ev["received_at"])
            notify_branch(br["id"], "escalation", ev["uid"], p, "escalation", roles=("manager", "owner"))
            out["escalated"] += 1
    return out


# ----------------------------------------------------------------------------- matnlar
def _event_text(p: dict, kind: str) -> str:
    safe = p.get("kind") in SAFETY_KINDS
    if kind == "escalation":
        head = "⏰ <b>Javobsiz hodisa — {} dan beri tekshirilmagan</b>".format(fmt_dur(p.get("waited", 0)))
    elif safe:
        head = f"<b>{esc(SAFETY_HEAD[p['kind']])}</b>"
    else:
        head = "⚠️ <b>Shubhali harakat — tekshirib ko'ring</b>"
    what = f"🎯 {esc(KIND_UZ.get(p.get('kind'), p.get('kind')))}"
    if not safe:
        what += f" — ball <b>{p.get('score', 0):.0f}</b>" + (f" ({esc(p.get('level'))})" if p.get("level") else "")
    lines = [head, f"🏪 {esc(p.get('branch'))}", f"📷 Kamera: {esc(p.get('camera'))}", f"🕒 {fmt_time(p.get('t_event'))}",
             what]
    if p.get("reasons"):
        lines += ["", "<b>Nima bo'ldi:</b>" if safe else "<b>Sabablar:</b>"] + [f"• {esc(r)}" for r in p["reasons"][:5]]
    if not safe:
        lines += ["", "<i>Bu ayblov emas — videoni ko'rib, o'zingiz qaror qiling.</i>"]
    return "\n".join(lines)[:3900]


def _plain(s: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(s))


def render(kind: str, p: dict) -> tuple[str, str, str]:
    """(sarlavha, matn HTML (Telegram), havola yo'li)."""
    if kind in ("event", "escalation"):
        if kind == "escalation":
            title = f"Javobsiz hodisa: {p.get('branch')}"
        elif p.get("kind") in SAFETY_KINDS:
            title = f"{KIND_UZ[p['kind']]}: {p.get('branch')}, {p.get('camera') or ''}".rstrip(", ")
        else:
            title = f"Shubhali harakat: {p.get('branch')}, ball {p.get('score', 0):.0f}"
        return title, _event_text(p, kind), f"/events/?uid={p.get('uid')}"
    if kind == "camera":
        if p.get("state") == "down":
            return (f"Kamera ishlamayapti: {p.get('camera')}",
                    f"📷 <b>Kamera ishlamayapti:</b> {esc(p.get('camera'))}\n🏪 {esc(p.get('branch'))}\n"
                    f"Sabab: {esc(p.get('reason'))}\nQachondan: {fmt_time(p.get('since'))}", "/branches/")
        return (f"Kamera tiklandi: {p.get('camera')}",
                f"✅ <b>Kamera tiklandi:</b> {esc(p.get('camera'))}\n🏪 {esc(p.get('branch'))}\n{esc(p.get('reason'))}",
                "/branches/")
    if kind == "offline":
        return (f"Do'kon bilan aloqa uzildi: {p.get('branch')}",
                f"📴 <b>Do'kon bilan aloqa uzildi:</b> {esc(p.get('branch'))}\nOxirgi aloqa: {fmt_time(p.get('since'))}\n"
                "Do'kondagi kompyuter va internetni tekshiring. Kameralar ishlashda davom etadi, ma'lumot "
                "do'kondagi kompyuterda saqlanadi.", "/branches/")
    if kind == "online":
        extra = f"\nNavbatda yuborilayotgan yozuvlar: {p['pending']}" if p.get("pending") else ""
        return (f"Aloqa tiklandi: {p.get('branch')}",
                f"✅ <b>Aloqa tiklandi:</b> {esc(p.get('branch'))}\n{fmt_dur(p.get('duration', 0))} uzilgan edi.{extra}",
                "/branches/")
    if kind == "test":
        return ("Hushyor AI: sinov xabari", "🧪 <b>Sinov xabari</b>\nBildirishnomalar shu manzilga yetib kelmoqda ✅",
                "/settings/")
    if kind == "queue":
        rule = str(p.get("rule") or "")
        head = f"{QUEUE_ICON.get(rule, '🧾')} <b>{esc(p.get('title') or QUEUE_RULES.get(rule, 'Navbat'))}</b>"
        where = " · ".join(esc(x) for x in (p.get("branch"), p.get("zone")) if x)
        body = esc(p.get("text") or "")                      # edge matni oddiy matnga aylantirilgan (qabulda)
        return (f"{p.get('title') or 'Navbat'}: {p.get('branch') or ''}".strip(": "),
                f"{head}\n🏪 {where}\n{body}" if where else f"{head}\n{body}", "/queues/")
    return ("Hushyor AI", esc(json.dumps(p, ensure_ascii=False))[:500], "/")


# ----------------------------------------------------------------------------- Telegram
class Retry(Exception):
    def __init__(self, msg: str, delay: float | None = None):
        super().__init__(msg)
        self.delay = delay


class Permanent(Exception):
    pass


class Skip(Exception):
    """Yuborish shart emas (masalan, hodisa navbatda turganida tekshirildi)."""


_tg_client: httpx.Client | None = None


def _tg(method: str, data: dict | None = None, files: dict | None = None, timeout: float = 30.0) -> dict:
    global _tg_client
    if _tg_client is None:
        _tg_client = httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0))
    url = f"{cfg.tg_api}/bot{cfg.tg_token}/{method}"
    try:
        if files:
            r = _tg_client.post(url, data={k: (v if isinstance(v, str) else json.dumps(v)) for k, v in (data or {}).items()},
                                files=files, timeout=timeout)
        else:
            r = _tg_client.post(url, json=data or {}, timeout=timeout)
        res = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise Retry(f"tarmoq: {e.__class__.__name__}")
    if res.get("ok"):
        return res
    desc = str(res.get("description", ""))[:200]
    if r.status_code == 429:
        raise Retry(desc, float((res.get("parameters") or {}).get("retry_after", 5)))
    if r.status_code in (400, 401, 403, 404):
        raise Permanent(f"{r.status_code}: {desc}")
    raise Retry(f"{r.status_code}: {desc}")


def _review_markup(sent_id: int, uid: str) -> dict:
    rows = [[{"text": "✅ Tasdiqlash", "callback_data": f"r:{sent_id}:ok"},
             {"text": "❌ Xato signal", "callback_data": f"r:{sent_id}:no"}]]
    link = panel_link(f"/events/?uid={uid}")
    if link:
        rows.append([{"text": "🔗 Panelda ochish", "url": link}])
    return {"inline_keyboard": rows}


def send_telegram(row: dict, t: dict) -> None:
    kind, p = row["kind"], cdb.json_load(row["payload"]) or {}
    chat = str(t["address"])
    if kind in ("event", "escalation"):
        cur = cdb.row("SELECT status, clip_file FROM events WHERE uid=:u", {"u": p["uid"]})
        if not cur:
            raise Skip("hodisa o'chirilgan")
        if (cur.get("status") or "yangi") != "yangi":
            raise Skip("navbatda turganida tekshirildi")      # eskirgan tugmali xabar yuborilmaydi
        with cdb.engine().begin() as c:
            res = c.execute(cdb.tg_sent.insert().values(uid=p["uid"], target_id=t["id"], chat_id=chat, kind="event",
                                                        created_at=time.time()))
            sent_id = int(res.inserted_primary_key[0])
        _, text_, _ = render(kind, p)
        try:
            res = _tg("sendMessage", {"chat_id": chat, "text": text_, "parse_mode": "HTML",
                                      "disable_web_page_preview": True, "reply_markup": _review_markup(sent_id, p["uid"])})
        except Exception:
            cdb.execute("DELETE FROM tg_sent WHERE id=:i", {"i": sent_id})
            raise
        mid = res["result"]["message_id"]
        cdb.execute("UPDATE tg_sent SET message_id=:m WHERE id=:i", {"m": mid, "i": sent_id})
        after = cdb.row("SELECT status, clip_file, data FROM events WHERE uid=:u", {"u": p["uid"]}) or {}
        # Yuborish paytida (message_id hali yo'q edi) tekshirilgan bo'lsa — tugmalar darhol olinadi
        if (after.get("status") or "yangi") != "yangi":
            d = cdb.json_load(after.get("data")) or {}
            enqueue(t["user_id"], t, "review", p["uid"], {"uid": p["uid"], "status": after["status"],
                                                          "by": d.get("reviewed_by"), "sent_id": sent_id, "message_id": mid})
        # Xabar navbatda turganida klip kelgan bo'lsa — endi javob sifatida
        if after.get("clip_file"):
            _clip_for({"id": sent_id, "uid": p["uid"], "target_id": t["id"], "message_id": mid})
        return
    if kind == "clip":
        ev = cdb.row("SELECT clip_file, branch_id, local_id FROM events WHERE uid=:u", {"u": p["uid"]})
        if not ev or not ev.get("clip_file"):
            raise Permanent("klip yo'q")
        path = CLIPS_DIR / ev["clip_file"]
        br = cdb.row("SELECT name FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {}
        cap = f"🎬 Klip: {esc(br.get('name'))}, hodisa #{ev.get('local_id')}"
        fid = cdb.scalar("SELECT file_id FROM tg_sent WHERE uid=:u AND kind='clip' AND file_id IS NOT NULL LIMIT 1",
                         {"u": p["uid"]})
        if fid:
            res = _tg("sendVideo", {"chat_id": chat, "video": fid, "caption": cap, "parse_mode": "HTML",
                                    "reply_to_message_id": p.get("reply_to")})
        else:
            if not path.is_file() or path.stat().st_size > VIDEO_LIMIT:
                raise Permanent("klip fayli yo'q yoki juda katta")
            with open(path, "rb") as fv:
                res = _tg("sendVideo", {"chat_id": chat, "caption": cap, "parse_mode": "HTML", "supports_streaming": "true",
                                        "reply_to_message_id": str(p.get("reply_to") or "")},
                          files={"video": (path.name, fv, "video/mp4")}, timeout=120)
        v = (res.get("result") or {}).get("video") or {}
        with cdb.engine().begin() as c:
            c.execute(cdb.tg_sent.insert().values(uid=p["uid"], target_id=t["id"], chat_id=chat, kind="clip",
                                                  message_id=(res.get("result") or {}).get("message_id"),
                                                  file_id=v.get("file_id"), created_at=time.time()))
        return
    if kind == "review":
        ev = cdb.row("SELECT * FROM events WHERE uid=:u", {"u": p["uid"]})
        if not ev:
            raise Permanent("hodisa yo'q")
        br = cdb.row("SELECT * FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {"id": ev["branch_id"]}
        _, text_, _ = render("event", _event_payload(ev, br))
        st = p.get("status")
        markup = {"inline_keyboard": []}
        if st in ("tasdiqlangan", "rad_etilgan"):
            text_ += f"\n\n<b>{STATUS_UZ[st]}</b> — {esc(p.get('by'))}, {fmt_time(time.time())}"
        else:
            markup = _review_markup(int(p["sent_id"]), p["uid"])
        try:
            _tg("editMessageText", {"chat_id": chat, "message_id": p["message_id"], "text": text_[:4000],
                                    "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": markup})
        except Permanent as e:
            if "not modified" in str(e):
                return
            raise
        return
    _, text_, path = render(kind, p)
    link = panel_link(path)
    data = {"chat_id": chat, "text": text_[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    if link:
        data["reply_markup"] = {"inline_keyboard": [[{"text": "🔗 Panelda ochish", "url": link}]]}
    _tg("sendMessage", data)


# ----------------------------------------------------------------------------- email
def _still_new(kind: str, p: dict) -> None:
    """Hodisa xabari navbatda turganida tekshirilgan bo'lsa — yuborilmaydi (Skip)."""
    if kind in ("event", "escalation"):
        st = cdb.scalar("SELECT status FROM events WHERE uid=:u", {"u": p.get("uid")})
        if st is None:
            raise Skip("hodisa o'chirilgan")
        if st != "yangi":
            raise Skip("navbatda turganida tekshirildi")


def send_email(row: dict, t: dict) -> None:
    kind, p = row["kind"], cdb.json_load(row["payload"]) or {}
    if kind in ("clip", "review"):
        return                              # email'da klip va tahrir yo'q — havola yetarli
    _still_new(kind, p)
    title, text_, path = render(kind, p)
    link = panel_link(path) or (f"{cfg.public_url}{path}" if cfg.public_url else None)
    msg = EmailMessage()
    msg["Subject"] = " ".join(f"Hushyor AI: {title}".split())[:200]   # CR/LF sarlavhani buzmasin
    msg["From"] = cfg.smtp_from
    msg["To"] = str(t["address"])
    plain = _plain(text_) + (f"\n\nPanelda ochish: {link}" if link else "")
    msg.set_content(plain)
    body_html = text_.replace("\n", "<br>") + (f'<p><a href="{html.escape(link)}">Panelda ochish</a></p>' if link else "")
    msg.add_alternative(f"<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:14px\">{body_html}</div>",
                        subtype="html")
    try:
        if cfg.smtp_tls == "ssl":
            s = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
        with s:
            if cfg.smtp_tls == "starttls":
                s.starttls(context=ssl.create_default_context())
            if cfg.smtp_user:
                s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
    except smtplib.SMTPRecipientsRefused as e:
        raise Permanent(f"manzil rad etildi: {e}")
    except smtplib.SMTPAuthenticationError as e:
        raise Retry(f"SMTP login xato: {e.smtp_code}")
    except (OSError, smtplib.SMTPException) as e:
        raise Retry(f"SMTP: {e.__class__.__name__}: {str(e)[:120]}")


# ----------------------------------------------------------------------------- Web Push
VAPID_FILE = DATA_DIR / "vapid_private.pem"
_vapid_pub: list = []


def vapid_public_key() -> str | None:
    """Brauzer uchun VAPID ochiq kaliti (base64url). Kalit birinchi marta yaratiladi (cloud/data/vapid_private.pem)."""
    if _vapid_pub:
        return _vapid_pub[0]
    try:
        import pywebpush  # noqa: F401
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        state["push_error"] = "pywebpush o'rnatilmagan (pip install -r requirements.txt)"
        return None
    try:
        if VAPID_FILE.exists():
            key = serialization.load_pem_private_key(VAPID_FILE.read_bytes(), password=None)
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            key = ec.generate_private_key(ec.SECP256R1())
            VAPID_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
        raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        _vapid_pub.append(base64.urlsafe_b64encode(raw).rstrip(b"=").decode())
        return _vapid_pub[0]
    except Exception as e:  # noqa: BLE001
        state["push_error"] = f"VAPID kaliti: {e.__class__.__name__}"
        return None


def send_push(row: dict, t: dict) -> None:
    kind, p = row["kind"], cdb.json_load(row["payload"]) or {}
    if kind in ("clip", "review"):
        return
    _still_new(kind, p)
    from pywebpush import WebPushException, webpush

    title, text_, path = render(kind, p)
    sub = {"endpoint": t["address"], "keys": (cdb.json_load(t.get("data")) or {}).get("keys") or {}}
    payload = json.dumps({"title": title, "body": _plain(text_)[:300], "url": path,
                          "tag": f"{kind}:{row.get('ref')}"}, ensure_ascii=False)
    try:
        webpush(sub, payload, vapid_private_key=str(VAPID_FILE), vapid_claims={"sub": cfg.vapid_sub}, ttl=3600,
                timeout=15)
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):
            cdb.execute("UPDATE notify_targets SET enabled=:f, last_error=:e WHERE id=:t",
                        {"f": False, "e": "push obunasi bekor qilingan", "t": t["id"]})
            raise Permanent(f"push obunasi yo'q ({code})")
        if code in (401, 403):
            # Masalan, VAPID kaliti almashgan (cloud/data ko'chirilgan) — obunani panelda qayta yoqish kerak
            cdb.execute("UPDATE notify_targets SET enabled=:f, last_error=:e WHERE id=:t",
                        {"f": False, "e": "push kaliti mos emas — brauzerda qayta yoqing", "t": t["id"]})
            raise Permanent(f"push rad etildi ({code})")
        if code in (400, 413):
            raise Permanent(f"push rad etildi ({code})")
        raise Retry(f"push: {code or e.__class__.__name__}")
    except Exception as e:  # noqa: BLE001 — tarmoq
        raise Retry(f"push: {e.__class__.__name__}")


SENDERS = {"telegram": send_telegram, "email": send_email, "push": send_push}


# ----------------------------------------------------------------------------- worker
# Yuborish yo'laklari: sekin kanal (SMTP, katta video yuklash) boshqalarini to'xtatmasin
LANES = {
    "telegram": "channel='telegram' AND kind <> 'clip'",
    "telegram-clip": "channel='telegram' AND kind = 'clip'",
    "email": "channel='email'",
    "push": "channel='push'",
}
# Ustuvorlik: hodisa, eskalatsiya va tahrir (tugmalar) — birinchi; sinov va boshqalar — keyin
_PRIO = "CASE WHEN kind IN ('event', 'escalation', 'review') THEN 0 ELSE 1 END"


def process(limit: int = 20, lane: str | None = None, stop: threading.Event | None = None) -> int:
    """Navbatdan yuborish. lane — bitta yo'lak (Sender oqimi), None — hammasi. Qaytaradi: nechta qator ishlandi."""
    now = time.time()
    where = f" AND ({LANES[lane]})" if lane else ""
    rows_ = cdb.rows(f"SELECT * FROM notify_outbox WHERE status='pending' AND next_try <= :n{where} "
                     f"ORDER BY {_PRIO}, id LIMIT :l", {"n": now, "l": limit})
    for r in rows_:
        if stop is not None and stop.is_set():
            break                               # oqim to'xtatildi (qayta ishga tushirish) — qolgani yangi oqimga
        t = cdb.row("SELECT t.*, u.active AS user_active FROM notify_targets t JOIN users u ON u.id=t.user_id "
                    "WHERE t.id=:t", {"t": r["target_id"]})
        if not t or not t["enabled"] or not t["user_active"]:
            why = "xodim faol emas" if t and not t["user_active"] else "manzil o'chirilgan"
            cdb.execute("UPDATE notify_outbox SET status='skipped', error=:e WHERE id=:i", {"e": why, "i": r["id"]})
            continue
        try:
            SENDERS[r["channel"]](r, t)
        except Skip as e:
            cdb.execute("UPDATE notify_outbox SET status='skipped', error=:e WHERE id=:i", {"e": str(e)[:300], "i": r["id"]})
            continue
        except Permanent as e:
            cdb.execute("UPDATE notify_outbox SET status='failed', error=:e, attempts=attempts+1 WHERE id=:i",
                        {"e": str(e)[:300], "i": r["id"]})
            cdb.execute("UPDATE notify_targets SET last_error=:e WHERE id=:t", {"e": str(e)[:300], "t": t["id"]})
            if r["channel"] == "telegram" and re.search(r"403|blocked|chat not found|deactivated", str(e), re.I):
                cdb.execute("UPDATE notify_targets SET enabled=:f WHERE id=:t", {"f": False, "t": t["id"]})
            continue
        except Retry as e:
            att = int(r["attempts"]) + 1
            if att > len(RETRY):
                cdb.execute("UPDATE notify_outbox SET status='failed', error=:e, attempts=:a WHERE id=:i",
                            {"e": str(e)[:300], "a": att, "i": r["id"]})
            else:
                delay = max(e.delay or 0, RETRY[att - 1])
                cdb.execute("UPDATE notify_outbox SET attempts=:a, next_try=:n, error=:e WHERE id=:i",
                            {"a": att, "n": time.time() + delay, "e": str(e)[:300], "i": r["id"]})
            cdb.execute("UPDATE notify_targets SET last_error=:e WHERE id=:t", {"e": str(e)[:300], "t": t["id"]})
            continue
        except Exception as e:  # noqa: BLE001 — kutilmagan xato navbatni to'xtatmasin
            log.exception("Bildirishnoma yuborishda xato (%s)", r["channel"])
            cdb.execute("UPDATE notify_outbox SET status='failed', error=:e, attempts=attempts+1 WHERE id=:i",
                        {"e": f"ichki xato: {e.__class__.__name__}", "i": r["id"]})
            continue
        cdb.execute("UPDATE notify_outbox SET status='sent', sent_at=:s, attempts=attempts+1, error=NULL WHERE id=:i",
                    {"s": time.time(), "i": r["id"]})
        cdb.execute("UPDATE notify_targets SET last_ok=:s, last_error=NULL WHERE id=:t", {"s": time.time(), "t": t["id"]})
    return len(rows_)


class Sender(threading.Thread):
    """Bitta yo'lak (LANES) bo'yicha yuboradi."""

    def __init__(self, lane: str):
        super().__init__(daemon=True, name=f"cloud-notify-{lane}")
        self.lane = lane
        self.stop_evt = threading.Event()
        self.kick = threading.Event()

    def stop(self) -> None:
        self.stop_evt.set()
        self.kick.set()

    def run(self) -> None:
        _wakeups.append(self.kick)
        try:
            while not self.stop_evt.is_set():
                try:
                    if process(lane=self.lane, stop=self.stop_evt):
                        continue
                except Exception:
                    log.exception("Bildirishnoma yuborish xatosi (%s)", self.lane)
                self.kick.wait(1.0)
                self.kick.clear()
        finally:
            if self.kick in _wakeups:
                _wakeups.remove(self.kick)


class Worker(threading.Thread):
    """Davriy tekshiruv: aloqasi uzilgan do'konlar, eskalatsiya, eski yozuvlarni tozalash."""
    MONITOR_SEC = 20.0

    def __init__(self):
        super().__init__(daemon=True, name="cloud-notify")
        self.stop_evt = threading.Event()

    def stop(self) -> None:
        self.stop_evt.set()

    def run(self) -> None:
        while not self.stop_evt.is_set():
            try:
                monitor()
                prune()
            except Exception:
                log.exception("Bildirishnoma monitor xatosi")
            try:
                from . import maintenance

                maintenance.tick()          # kliplar muddati, disk, zaxira nusxa (o'zi soat/kunda bir marta)
            except Exception:
                log.exception("Xizmat ko'rsatish (maintenance) xatosi")
            self.stop_evt.wait(self.MONITOR_SEC)


_last_prune = [0.0]


def prune() -> None:
    now = time.time()
    if now - _last_prune[0] < 3600:
        return
    _last_prune[0] = now
    cdb.execute("DELETE FROM notify_outbox WHERE created_at < :t AND status != 'pending'", {"t": now - 30 * 86400})
    cdb.execute("DELETE FROM tg_sent WHERE created_at < :t", {"t": now - 14 * 86400})
    cdb.execute("DELETE FROM tg_links WHERE expires_at < :t", {"t": now})
    cdb.execute("DELETE FROM escalations WHERE at < :t", {"t": now - 7 * 86400})
    cdb.execute("DELETE FROM notify_once WHERE at < :t", {"t": now - 14 * 86400})


def stats(company_id: int, hours: float = 24) -> dict:
    """Kanal bo'yicha: yuborilgan / xato / kutilmoqda, kechikish p50/p95 (navbatga tushgandan yetguncha va
    hodisa aniqlangandan yetguncha — do'kon soati farqi hisobga olingan)."""
    since = time.time() - hours * 3600
    rows_ = cdb.rows("SELECT o.channel, o.kind, o.status, o.created_at, o.sent_at, o.payload FROM notify_outbox o "
                     "JOIN users u ON u.id=o.user_id WHERE u.company_id=:c AND o.created_at >= :s",
                     {"c": company_id, "s": since})
    out: dict = {}

    def pct(v: list[float], q: float) -> float | None:
        if not v:
            return None
        v = sorted(v)
        return round(v[min(len(v) - 1, int(round(q / 100 * (len(v) - 1))))], 2)
    for ch in ("telegram", "email", "push"):
        rs = [r for r in rows_ if r["channel"] == ch]
        sent = [r for r in rs if r["status"] == "sent"]
        queue_lat = [r["sent_at"] - r["created_at"] for r in sent if r["sent_at"]]
        e2e = []
        for r in sent:
            if r["kind"] in ("event", "escalation"):
                p = cdb.json_load(r["payload"]) or {}
                if p.get("t_event") and r["kind"] == "event":
                    e2e.append(r["sent_at"] - float(p["t_event"]))
        out[ch] = {"sent": len(sent), "failed": sum(1 for r in rs if r["status"] == "failed"),
                   "pending": sum(1 for r in rs if r["status"] == "pending"),
                   "skipped": sum(1 for r in rs if r["status"] == "skipped"),
                   "queue_p50": pct(queue_lat, 50), "queue_p95": pct(queue_lat, 95),
                   "event_p50": pct(e2e, 50), "event_p95": pct(e2e, 95)}
    return out


# ----------------------------------------------------------------------------- Telegram boti (bog'lash, tugmalar)
def new_link(user: dict) -> dict:
    code = secrets.token_urlsafe(16)
    cdb.upsert(cdb.tg_links, {"code_hash": cdb.sha256(code), "user_id": user["id"], "expires_at": time.time() + LINK_TTL},
               ["code_hash"], ["user_id", "expires_at"])
    bot = (state["tg_bot"] or {}).get("username")
    return {"code": code, "link": f"https://t.me/{bot}?start={code}" if bot else None, "bot": bot, "expires_in": LINK_TTL}


class TelegramPoller(threading.Thread):
    """Bulut botiga kelgan xabarlar: /start <kod> (bog'lash), /holat, /bugun, tugmalar (✅/❌)."""

    def __init__(self):
        super().__init__(daemon=True, name="cloud-tg")
        self.stop_evt = threading.Event()

    def stop(self) -> None:
        self.stop_evt.set()

    def run(self) -> None:
        backoff = 2.0
        while not self.stop_evt.is_set() and state["tg_bot"] is None:
            try:
                state["tg_bot"] = _tg("getMe", timeout=15)["result"]
                state["tg_error"] = ""
                log.info("Bulut Telegram boti: @%s", state["tg_bot"].get("username"))
            except (Retry, Permanent) as e:
                state["tg_error"] = str(e)[:200]
                if isinstance(e, Permanent):
                    log.error("Bulut Telegram tokeni xato: %s", e)
                    return
                if self.stop_evt.wait(min(backoff, 60)):
                    return
                backoff *= 1.6
        offset = int(cdb.kv_get("tg_offset", "0") or 0)
        while not self.stop_evt.is_set():
            try:
                res = _tg("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                          timeout=35)
                state["tg_error"] = ""
            except Retry as e:
                state["tg_error"] = str(e)[:200]
                self.stop_evt.wait(3)
                continue
            except Permanent as e:
                state["tg_error"] = str(e)[:200]
                if "Conflict" in str(e):
                    state["tg_error"] = "Bu token bilan boshqa dastur ham ishlayapti (Conflict)"
                self.stop_evt.wait(10)
                continue
            for upd in res.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                try:
                    if "message" in upd:
                        self.on_message(upd["message"])
                    elif "callback_query" in upd:
                        self.on_callback(upd["callback_query"])
                except Exception:
                    log.exception("Bulut boti yangilanishida xato")
            cdb.kv_set("tg_offset", str(offset))

    @staticmethod
    def reply(chat_id, text_: str) -> None:
        try:
            _tg("sendMessage", {"chat_id": chat_id, "text": text_, "parse_mode": "HTML", "disable_web_page_preview": True})
        except (Retry, Permanent) as e:
            log.warning("Bulut boti javob bera olmadi: %s", e)

    @staticmethod
    def users_for_chat(chat_id) -> list[dict]:
        return cdb.rows("SELECT u.* FROM notify_targets t JOIN users u ON u.id=t.user_id WHERE t.channel='telegram' "
                        "AND t.address=:a AND t.enabled=:e AND u.active=:e", {"a": str(chat_id), "e": True})

    def on_message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        text_ = (msg.get("text") or "").strip()
        parts = text_.split()
        cmd = parts[0].split("@")[0].lower() if parts else ""
        if cmd == "/start" and len(parts) > 1:
            code = parts[1][:80]
            link = cdb.row("SELECT * FROM tg_links WHERE code_hash=:h AND expires_at > :n",
                           {"h": cdb.sha256(code), "n": time.time()})
            if not link:
                self.reply(chat_id, "❌ Havola noto'g'ri yoki eskirgan. Panelda <b>Sozlamalar → Telegram</b> dan yangisini oling.")
                return
            cdb.execute("DELETE FROM tg_links WHERE code_hash=:h", {"h": cdb.sha256(code)})
            u = cdb.row("SELECT * FROM users WHERE id=:u AND active=:a", {"u": link["user_id"], "a": True})
            if not u:
                self.reply(chat_id, "❌ Foydalanuvchi faol emas.")
                return
            who = " ".join(x for x in ((msg.get("from") or {}).get("first_name"), (msg.get("from") or {}).get("last_name")) if x)
            try:
                add_target(u["id"], "telegram", str(chat_id), {"name": who or msg["chat"].get("title") or "Telegram"})
            except TooMany as e:
                self.reply(chat_id, f"❌ {esc(e)}")
                return
            cdb.audit("telegram_link", u, who or str(chat_id), {"chat": str(chat_id)[-4:]}, ip="telegram")
            comp = cdb.row("SELECT name FROM companies WHERE id=:c", {"c": u["company_id"]}) or {}
            self.reply(chat_id, f"✅ Ulandingiz: <b>{esc(u.get('name') or u['email'])}</b> ({esc(comp.get('name'))}).\n"
                                "Endi filiallaringizdagi hodisalar shu yerga keladi. /holat — do'konlar holati, /bugun — bugungi hodisalar.")
            return
        users = self.users_for_chat(chat_id)
        if not users:
            self.reply(chat_id, "👋 Bu Hushyor AI bulut boti. Ulanish uchun panelda <b>Sozlamalar → Telegram → Ulash</b> ni bosing.")
            return
        u = users[0]
        if cmd == "/holat":
            lines = ["🏪 <b>Do'konlar</b>"]
            for b in cdb.rows("SELECT * FROM branches WHERE company_id=:c ORDER BY id", {"c": u["company_id"]}):
                if b["id"] not in cdb.branch_ids_for(u):
                    continue
                online = b.get("last_seen") and time.time() - b["last_seen"] < 180
                lines.append(f"{'🟢' if online else '🔴'} {esc(b['name'])} — "
                             + ("onlayn" if online else f"aloqa yo'q ({fmt_time(b.get('last_seen'))})"))
            self.reply(chat_id, "\n".join(lines))
        elif cmd == "/bugun":
            day0 = (time.time() + cfg.tz_offset * 3600) // 86400 * 86400 - cfg.tz_offset * 3600
            lines = ["📊 <b>Bugun</b>"]
            for b in cdb.rows("SELECT * FROM branches WHERE company_id=:c ORDER BY id", {"c": u["company_id"]}):
                if b["id"] not in cdb.branch_ids_for(u):
                    continue
                tot = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b AND received_at >= :t",
                                 {"b": b["id"], "t": day0}) or 0
                new = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b AND received_at >= :t AND status='yangi'",
                                 {"b": b["id"], "t": day0}) or 0
                vis = cdb.visits_hourly([b["id"]], day0, off=int(round(cfg.tz_offset * 3600)))
                ain = sum(v["in"] + v["off_in"] for v in vis)
                aout = sum(v["out"] + v["off_out"] for v in vis)
                vtxt = (f"; tashrif: {sum(v['in'] for v in vis)} kirdi, ichkarida "
                        f"{cdb.inside_now(b.get('occupancy'), ain, aout)}") \
                    if vis or cdb.occupancy_fresh(b.get("occupancy")) else ""
                lines.append(f"{esc(b['name'])}: {tot} ta hodisa, tekshirilmagan {new}{vtxt}")
                ftxt = flow_today_text(b, day0)              # 2.1.0: eng ko'p tashrif qilingan bo'limlar
                if ftxt:
                    lines.append("   " + ftxt)
            self.reply(chat_id, "\n".join(lines))
        elif cmd == "/navbat":
            # 2.0.0: filiallar kassalaridagi navbat (edge hello bilan har daqiqada yuboradi)
            lines = ["🧾 <b>Navbat — hozir</b>"]
            icons = {"navbat": "🔴", "kassir_yoq": "🟠", "band": "🟡", "bosh": "🟢", "yopiq": "⚪️"}
            for b in cdb.rows("SELECT * FROM branches WHERE company_id=:c ORDER BY id", {"c": u["company_id"]}):
                if b["id"] not in cdb.branch_ids_for(u):
                    continue
                items = cdb.queues_fresh(b.get("queues"))
                if items is None:
                    if cdb.json_load(b.get("queues")):
                        lines.append(f"🏪 {esc(b['name'])}: ma'lumot eskirgan (aloqa yo'q)")
                    continue
                for x in items:
                    if not x.get("live"):
                        lines.append(f"⚪️ {esc(b['name'])} · {esc(x.get('name'))}: kamera ishlamayapti")
                        continue
                    cash = "" if x.get("cashier") is None else (" · kassir bor" if x.get("cashier") else " · kassir yo'q")
                    lines.append(f"{icons.get(x.get('state'), '⚪️')} {esc(b['name'])} · {esc(x.get('name'))}: "
                                 f"{int(x.get('length') or 0)} kishi" + (f", eng uzoq kutayotgan {fmt_dur(x.get('max_wait') or 0)}"
                                                                        if x.get("length") else "") + cash)
            if len(lines) == 1:
                lines.append("Kassa navbati zonasi chizilgan filial yo'q.")
            self.reply(chat_id, "\n".join(lines))
        else:
            self.reply(chat_id, "Buyruqlar: /holat — do'konlar holati, /bugun — bugungi hodisalar (va bo'limlar), /navbat — "
                                "kassalardagi navbat.\nHodisa xabaridagi ✅/❌ tugmalari bilan tekshiring.")

    def on_callback(self, cb: dict) -> None:
        def answer(t: str) -> None:
            try:
                _tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": t[:180]})
            except (Retry, Permanent):
                pass
        m = re.fullmatch(r"r:(\d{1,12}):(ok|no)", cb.get("data") or "")
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id"))
        if not m:
            answer("")
            return
        s = cdb.row("SELECT * FROM tg_sent WHERE id=:i", {"i": int(m.group(1))})
        t = cdb.row("SELECT * FROM notify_targets WHERE id=:t AND enabled=:e", {"t": s["target_id"], "e": True}) if s else None
        if not s or not t or str(t["address"]) != chat_id:
            answer("Ruxsat yo'q")
            return
        u = cdb.row("SELECT * FROM users WHERE id=:u AND active=:a", {"u": t["user_id"], "a": True})
        if not u:
            answer("Ruxsat yo'q")
            return
        from . import service

        status = "tasdiqlangan" if m.group(2) == "ok" else "rad_etilgan"
        try:
            service.review(u, s["uid"], status, via="Telegram")
        except service.NotFound:
            answer("Hodisa topilmadi yoki ruxsat yo'q")
            return
        answer("✅ Tasdiqlandi" if status == "tasdiqlangan" else "❌ Xato signal deb belgilandi")


_worker: Worker | None = None
_poller: TelegramPoller | None = None
_senders: dict[str, Sender] = {}
_started = [time.time()]


def start() -> None:
    global _worker, _poller
    if _worker is None or not _worker.is_alive() or _worker.stop_evt.is_set():
        _started[0] = time.time()
        _worker = Worker()
        _worker.start()
    for lane in LANES:
        s_ = _senders.get(lane)
        if s_ is None or not s_.is_alive() or s_.stop_evt.is_set():
            _senders[lane] = Sender(lane)
            _senders[lane].start()
    if cfg.tg_token and not state["tg_conflict"] and (_poller is None or not _poller.is_alive()
                                                       or _poller.stop_evt.is_set()):
        _poller = TelegramPoller()
        _poller.start()
    vapid_public_key()


def stop() -> None:
    for t in (_worker, _poller, *_senders.values()):
        if t is not None:
            t.stop()
