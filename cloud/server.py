"""Hushyor AI — bulut serveri: Sync API v1 (edge ↔ bulut) + boshqaruv API v1 (panel, foydalanuvchilar, rollar).

Ma'lumotlar: cloud/db.py (SQLAlchemy — SQLite yoki PostgreSQL/TimescaleDB, CLOUD_DB_URL).
Ierarxiya: kompaniya → filial (edge kompyuteri, qurilma tokeni) → kamera. Rollar: owner / manager / guard.

Edge tomoni (app/sync.py) uchun shartnoma o'zgarmagan: docs/SYNC_API.md. Qurilma tokeni bazadagi filialdan
(`POST /api/v1/branches` yaratganda bir marta ko'rsatiladi). Eski usul — CLOUD_EDGE_TOKENS muhit tokeni — faqat
bazada o'z tokeni bo'lmagan qurilmalar uchun ("Mening kompaniyam" da avtomatik filial, ko'pi bilan 20 ta).

Ishga tushirish:
    python -m cloud.server --port 9000
    python -m cloud.server --init-owner egasi@dokon.uz Parol12345 "Kompaniya nomi"   # birinchi egasi
Muhit: CLOUD_DB_URL, CLOUD_DATA_DIR, CLOUD_EDGE_TOKENS (eski usul), CLOUD_ADMIN_TOKEN (barcha kompaniyalar bo'yicha ko'rish),
bildirishnomalar: CLOUD_TELEGRAM_BOT_TOKEN, CLOUD_PUBLIC_URL, CLOUD_SMTP_*, CLOUD_VAPID_SUB (cloud/notify.py).
Panel: cloud/panel_dist (Next.js statik build) — shu server `/` da beradi.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from . import db as cdb
from . import maintenance, notify, service
from .db import CLIPS_DIR, DATA_DIR
from .live import hub

log = logging.getLogger("hushyor.cloud")
VERSION = "2.1.0"

API_VERSION = 1
ROOT = Path(__file__).resolve().parent
CLIP_LIMIT = 60 * 1024 * 1024
JSON_LIMIT = 8 * 1024 * 1024        # bitta JSON so'rov hajmi
ITEM_LIMIT = 200_000                # bitta yozuv (hodisa JSON) hajmi
MAX_ITEMS = 500
RECEIVED_KEEP_DAYS = 7
METRICS_KEEP_DAYS = 400
AUDIT_KEEP_DAYS = 400
UID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}:\d{1,12}")
DEV_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
STATUSES = ("yangi", "tasdiqlangan", "rad_etilgan")
ENV_BRANCH_MAX = 20                 # muhit (CLOUD_EDGE_TOKENS) tokeni bilan o'z-o'zidan yaratiladigan filiallar chegarasi
SESSION_COOKIE = "hc_session"       # panel sessiyasi (HttpOnly, SameSite=Strict)
PANEL_DIR = ROOT / "panel_dist"     # Next.js statik build (cloud/panel → npm run build)
EMAIL_RE = re.compile(r"[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{2,20}")
# Panel sahifalari uchun CSP: tashqi manba yo'q. Skriptlar — faqat o'z fayllari va HTML ichidagi aniq skriptlar
# (sha256 xeshi har sahifa uchun hisoblanadi, `_panel_csp`) — 'unsafe-inline' yo'q: kiritilgan begona skript ishlamaydi
_CSP_REST = ("style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
             "font-src 'self' data:; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; object-src 'none'; "
             "base-uri 'self'; form-action 'self'")
PANEL_CSP = f"default-src 'self'; script-src 'self'; {_CSP_REST}"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
SSE_PER_SESSION = 6                 # bitta brauzer (sessiya): ochiq tablar — eng eskisi yopiladi
SSE_PER_USER = 24                   # bitta xodimning barcha qurilmalari (umumiy qo'riqchi akkaunti — bir nechta planshet)
SSE_TOTAL = 1000


def _env_file_value(*keys: str) -> str:
    for k in keys:
        v = cdb.env(k)
        if v:
            return v
    return ""


def tokens() -> list[tuple[str | None, str]]:
    """Eski usul: muhitdagi tokenlar [(qurilma yoki None, token)] — `qurilma:token` yozuvi tokenni qurilmaga bog'laydi.
    (Token ichida ':' bo'lmasin — token_hex bilan yaratilgan tokenlarda bo'lmaydi.)"""
    # Faqat CLOUD_EDGE_TOKENS: edge'ning EDGE_TOKEN'i (filial tokeni) umumiy bulut tokeniga aylanib qolmasin
    raw = os.environ.get("CLOUD_EDGE_TOKENS") or _env_file_value("CLOUD_EDGE_TOKENS")
    out = []
    for t in raw.split(","):
        t = t.strip()
        if not t:
            continue
        if ":" in t:
            dev, tok = t.split(":", 1)
            out.append((dev.strip() or None, tok.strip()))
        else:
            out.append((None, t))
    return out


def admin_token() -> str:
    return os.environ.get("CLOUD_ADMIN_TOKEN") or _env_file_value("CLOUD_ADMIN_TOKEN")


@asynccontextmanager
async def _lifespan(_app):
    init_db()
    hub.bind(asyncio.get_running_loop())
    if not _state.get("no_notify"):
        if not _state.get("notify_configured"):
            notify.configure()
        notify.start()
    try:
        yield
    finally:
        notify.stop()


def init_db() -> dict:
    info = cdb.init_db()
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    return info


app = FastAPI(title="Hushyor AI Cloud", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)
# tokens/admin: None — birinchi ishlatilganda muhitdan o'qiladi (sinovlar o'rnatadi);
# fail=True / fail_paths — sinovlar uchun: 503 (uzilish taqlidi)
_state: dict = {"tokens": None, "admin": None, "fail": False, "fail_paths": ()}
# no_notify — bildirishnoma oqimlarini ishga tushirmaslik; notify_configured — sinov notify.cfg ni o'zi to'ldirgan


def _tokens() -> list[tuple[str | None, str]]:
    if _state["tokens"] is None:
        _state["tokens"] = tokens()
    return _state["tokens"]


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _fail_now(request: Request) -> bool:
    if _state["fail"]:
        return True
    return any(p in request.url.path for p in _state.get("fail_paths", ()))


def _check_length(request: Request, limit: int) -> None:
    try:
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"so'rov juda katta (> {limit // (1 << 20)} MB)")
    except ValueError:
        raise HTTPException(400, "Content-Length noto'g'ri")


def _ip(request: Request) -> str:
    """Mijoz IP manzili. Ishonchli teskari proksi (CLOUD_TRUSTED_PROXIES, sukut 127.0.0.1) orqasida uvicorn
    X-Forwarded-For dan haqiqiy manzilni qo'yadi — login himoyasi hamma uchun bitta "127.0.0.1" ga aylanmaydi."""
    return request.client.host if request.client else "?"


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _secure_headers(request: Request, resp, path: str) -> None:
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    if _https(request):
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith("/api/"):
        # Shaxsiy ma'lumot (hodisalar, kliplar) brauzer yoki proksi keshida qolmasin (umumiy kompyuter)
        h.setdefault("Cache-Control", "no-store")
        return
    h.setdefault("Content-Security-Policy", PANEL_CSP)
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if path in ("/sw.js",):
        h["Cache-Control"] = "no-cache"
        h["Service-Worker-Allowed"] = "/"


@app.middleware("http")
async def _guard(request: Request, call_next):
    """Tana o'qilishidan OLDIN: hajm chegarasi, chunked tana rad etiladi, edge yo'llarida token tekshiriladi.
    (FastAPI multipart'ni handler'dan oldin diskka yozadi — shuning uchun tekshiruv shu yerda.)
    Baza so'rovlari threadpool'da — event loop (SSE, boshqa so'rovlar) bloklanmaydi."""
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH"):
        if "transfer-encoding" in request.headers:
            return JSONResponse({"detail": "Content-Length talab qilinadi (chunked tana qabul qilinmaydi)"},
                                status_code=411)
        limit = CLIP_LIMIT + 6 * 1024 * 1024 if path.endswith("/clip") else JSON_LIMIT
        try:
            if int(request.headers.get("content-length") or 0) > limit:
                return JSONResponse({"detail": f"so'rov juda katta (> {limit // (1 << 20)} MB)"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Content-Length noto'g'ri"}, status_code=400)
        if path.startswith(f"/api/v{API_VERSION}/edge/"):
            try:
                b = await run_in_threadpool(_auth_edge, request, limit)
            except HTTPException as e:
                return JSONResponse({"detail": e.detail}, status_code=e.status_code)
            # So'rov davom etayotgan paytda (katta klip yuklanmoqda) do'kon "uzilgan" deb belgilanmaydi;
            # tugagach last_seen yangilanadi
            notify.inflight(b["id"], 1)
            try:
                resp = await call_next(request)
            finally:
                notify.inflight(b["id"], -1)
                try:
                    await run_in_threadpool(notify.touch, {**b, "offline_since": None, "last_seen": 0})
                except Exception:  # noqa: BLE001
                    pass
            _secure_headers(request, resp, path)
            return resp
    resp = await call_next(request)
    _secure_headers(request, resp, path)
    return resp


# ----------------------------------------------------------------------------- edge autentifikatsiyasi
def _auth_edge(request: Request, body_limit: int = JSON_LIMIT) -> dict:
    """Qurilma tokenini tekshiradi: avval bazadagi filial, bo'lmasa muhit tokenlari. Filial qatorini qaytaradi."""
    if _fail_now(request):
        raise HTTPException(503, "server vaqtincha ishlamayapti (sinov)")
    _check_length(request, body_limit)
    given = _bearer(request)
    dev = request.headers.get("x-device-id", "").strip()
    if not DEV_RE.fullmatch(dev):
        raise HTTPException(400, "X-Device-Id noto'g'ri")
    if not given:
        raise HTTPException(401, "token xato")
    b = cdb.branch_by_device(dev, given)
    if b is None:
        existing = cdb.row("SELECT token_hash FROM branches WHERE device_id=:d", {"d": dev})
        if existing is not None and existing["token_hash"] != "env":
            raise HTTPException(401, "token xato")     # bazadagi filial faqat o'z tokeni bilan — muhit tokeni o'tmaydi
        ok = any(hmac.compare_digest(given.encode(), tok.encode()) and (bound is None or bound == dev)
                 for bound, tok in _tokens())
        if not ok:
            raise HTTPException(401, "token xato")
        if existing is None and (cdb.scalar("SELECT COUNT(*) FROM branches WHERE token_hash='env'") or 0) >= ENV_BRANCH_MAX:
            raise HTTPException(403, "muhit tokeni bilan yangi filial yaratish chegarasi — filialni panel orqali yarating")
        b = cdb.ensure_env_branch(dev, "")
    request.state.branch = b
    if not request.url.path.endswith("/edge/hello"):
        try:
            # Navbatni bo'shatayotgan (hello yubormayotgan) edge ham "aloqada" ko'rinsin
            notify.touch(b)
        except Exception:  # noqa: BLE001 — qabul qilishni buzmasin
            pass
    return b


async def _branch(request: Request) -> dict:
    b = getattr(request.state, "branch", None)
    return b if b is not None else await run_in_threadpool(_auth_edge, request)


async def _json(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "JSON noto'g'ri")


def _num(v) -> float:
    try:
        f = float(v or 0)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _items(body) -> list[dict]:
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(400, "items ro'yxati kutilgan")
    if len(items) > MAX_ITEMS:
        raise HTTPException(413, f"bitta so'rovda ko'pi bilan {MAX_ITEMS} ta yozuv")
    return [i for i in items if isinstance(i, dict)]


def _seen(conn, dev: str, kind: str, key: str) -> None:
    cdb.upsert(cdb.received, {"key": f"{dev}:{kind}:{key}"[:200], "t": time.time()}, ["key"], conn=conn)


_last_prune = [0.0]


def _prune(conn) -> None:
    now = time.time()
    if now - _last_prune[0] < 3600:
        return
    _last_prune[0] = now
    conn.execute(cdb.received.delete().where(cdb.received.c.t < now - RECEIVED_KEEP_DAYS * 86400))
    conn.execute(cdb.metrics_minute.delete().where(cdb.metrics_minute.c.minute < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.visits_minute.delete().where(cdb.visits_minute.c.minute < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.queue_minute.delete().where(cdb.queue_minute.c.minute < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.queue_alerts.delete().where(cdb.queue_alerts.c.t < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.flow_hour.delete().where(cdb.flow_hour.c.hour < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.sessions.delete().where(cdb.sessions.c.expires_at < now))
    conn.execute(cdb.commands.delete().where(cdb.commands.c.acked_at.isnot(None), cdb.commands.c.acked_at < now - 7 * 86400))
    conn.execute(cdb.health_log.delete().where(cdb.health_log.c.t < now - METRICS_KEEP_DAYS * 86400))
    conn.execute(cdb.audit_log.delete().where(cdb.audit_log.c.t < now - AUDIT_KEEP_DAYS * 86400))


# ----------------------------------------------------------------------------- Sync API v1 (edge)
@app.post(f"/api/v{API_VERSION}/edge/hello")
async def hello(request: Request):
    b = await _branch(request)
    body = await _json(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    return await run_in_threadpool(_hello, b, body, _ip(request))


def _hello(b: dict, body: dict, ip: str) -> dict:
    now = time.time()
    t = body.get("time")
    skew = round(now - float(t), 2) if isinstance(t, (int, float)) else None
    cams = body.get("cameras") if isinstance(body.get("cameras"), list) else []
    cams = [c for c in cams if isinstance(c, dict)][:64]
    if b.get("offline_since"):
        # Aloqa uzilgan deb xabar qilingan edi — tiklandi (atomar: bir nechta so'rov bo'lsa ham bitta xabar)
        try:
            notify.touch(b, int(_num(body.get("pending"))))
        except Exception:  # noqa: BLE001
            pass
    occ = body.get("occupancy")
    occ = ({k: float(_num(occ.get(k))) for k in ("inside", "since", "day0", "in", "out")}
           if isinstance(occ, dict) else None)
    qs = _hello_queues(body.get("queues"))
    fp = _hello_flow_plans(body.get("flow_plans"))
    with cdb.engine().begin() as c:
        vals = {"last_seen": now, "last_ip": ip[:64],
                "version": str(body.get("version") or "")[:40], "cameras": cams,
                "pending": int(_num(body.get("pending"))), "clock_skew": skew,
                "occupancy": ({**occ, "at": now} if occ else None),
                "queues": ({"at": now, "items": qs} if qs is not None else None)}
        if fp is not None:
            vals["flow_plans"] = {"at": now, "items": fp}
        if b.get("token_hash") == "env" and body.get("name"):
            vals["name"] = str(body.get("name"))[:200]      # muhit tokeni bilan ulangan filial nomi edge'dan
        c.execute(cdb.branches.update().where(cdb.branches.c.id == b["id"]).values(**vals))
        # Yetkazilmagan buyruqlar (bulutdagi tekshirish → edge). delivered_at belgilanadi, ack kelgach yopiladi;
        # ack kelmasa 2 daqiqadan keyin yana yuboriladi (edge takrorni local_id bo'yicha o'zi aniqlaydi)
        cmds = [dict(r._mapping) for r in c.execute(
            cdb.commands.select().where(cdb.commands.c.branch_id == b["id"], cdb.commands.c.acked_at.is_(None))
            .order_by(cdb.commands.c.id).limit(50))]
        cmds = [x for x in cmds if not x["delivered_at"] or now - x["delivered_at"] > 120]
        if cmds:
            c.execute(cdb.commands.update().where(cdb.commands.c.id.in_([x["id"] for x in cmds])).values(delivered_at=now))
        _prune(c)
    return {"ok": True, "server_time": now, "device": {"id": b["device_id"], "branch_id": b["id"], "name": b["name"]},
            "commands": [{"id": x["id"], "type": x["type"], **(cdb.json_load(x["payload"]) or {})} for x in cmds],
            # 2.0.0: edge shu ro'yxatga qarab yangi turdagi yozuvlarni navbatga qo'yadi (eski bulut — yo'q, to'smaydi)
            "features": list(FEATURES)}


FEATURES = ("queues", "flow")


def _hello_flow_plans(raw) -> list[dict] | None:
    """2.1.0: do'kon rejalari (o'lchami va bo'limlari, metrda) — bulut heatmap'ini chizish uchun. Reja rasmi yuborilmaydi."""
    if not isinstance(raw, list):
        return None
    out = []
    for p in raw[:5]:
        if not isinstance(p, dict):
            continue
        secs = []
        for s_ in (p.get("sections") or [])[:60] if isinstance(p.get("sections"), list) else []:
            if not isinstance(s_, dict):
                continue
            pts = []
            for q in (s_.get("pts") or [])[:200] if isinstance(s_.get("pts"), list) else []:
                try:
                    pts.append([round(min(max(float(q[0]), -10), 1000), 2), round(min(max(float(q[1]), -10), 1000), 2)])
                except (TypeError, ValueError, IndexError):
                    continue
            col = str(s_.get("color") or "")
            secs.append({"id": int(_num(s_.get("id"))), "name": str(s_.get("name") or "")[:60],
                         "color": col if re.fullmatch(r"#[0-9a-fA-F]{6}", col) else "#64748b", "pts": pts})
        out.append({"id": int(_num(p.get("id"))), "name": str(p.get("name") or "")[:60],
                    "w_m": round(min(max(_num(p.get("w_m")), 0), 1000), 2), "h_m": round(min(max(_num(p.get("h_m")), 0), 1000), 2),
                    "cell": _num(p.get("cell")) or 0.5, "nx": int(min(max(_num(p.get("nx")), 0), 400)),
                    "ny": int(min(max(_num(p.get("ny")), 0), 400)), "grid": str(p.get("grid") or "")[:40], "sections": secs})
    return out


def _hello_queues(raw) -> list[dict] | None:
    """2.0.0: edge kassalar holati (hello) — faqat kerakli maydonlar, cheklangan uzunlikda."""
    if not isinstance(raw, list):
        return None
    out = []
    for x in raw[:64]:
        if not isinstance(x, dict):
            continue
        st = str(x.get("state") or "")
        cash = x.get("cashier")
        out.append({"zone_id": int(_num(x.get("zone_id"))), "name": str(x.get("name") or "")[:200],
                    "source_name": str(x.get("source_name") or "")[:200],
                    "length": int(min(max(_num(x.get("length")), 0), 999)),
                    "max_wait": int(min(max(_num(x.get("max_wait")), 0), 86400)),
                    "cashier": cash if isinstance(cash, bool) else None,
                    "state": st if st in cdb.QUEUE_STATES else "nomalum", "live": bool(x.get("live"))})
    return out


@app.post(f"/api/v{API_VERSION}/edge/commands/ack")
async def commands_ack(request: Request):
    b = await _branch(request)
    body = await _json(request)
    raw = body.get("ids") if isinstance(body, dict) else None
    ids = [int(i) for i in (raw or []) if isinstance(i, (int, float, str)) and str(i).isdigit()][:200]

    def _ack() -> None:
        with cdb.engine().begin() as c:
            c.execute(cdb.commands.update().where(cdb.commands.c.branch_id == b["id"], cdb.commands.c.id.in_(ids))
                      .values(acked_at=time.time()))
    if ids:
        await run_in_threadpool(_ack)
    return {"ok": True, "acked": len(ids)}


@app.post(f"/api/v{API_VERSION}/edge/events")
async def edge_events(request: Request):
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_events, b, items)


def _edge_events(b: dict, items: list[dict]) -> dict:
    dev = b["device_id"]
    accepted, rejected = [], []
    new_uids: list[tuple[str, dict]] = []
    changed: list[tuple[str, str, str | None]] = []
    now = time.time()
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            uid = str(it.get("uid") or "")
            if not UID_RE.fullmatch(uid) or not uid.startswith(dev + ":"):
                rejected.append({"key": key, "reason": "uid noto'g'ri yoki boshqa qurilmaniki"})
                continue
            if len(json.dumps(it, ensure_ascii=False)) > ITEM_LIMIT:
                rejected.append({"key": key, "reason": f"yozuv juda katta (> {ITEM_LIMIT // 1024} KB)"})
                continue
            try:
                upd = float(it.get("updated_at") or time.time())
            except (TypeError, ValueError):
                rejected.append({"key": key, "reason": "updated_at noto'g'ri"})
                continue
            vals = {"uid": uid, "branch_id": b["id"], "device_id": dev, "local_id": int(_num(it.get("local_id"))),
                    "created_at": _num(it.get("created_at")) or upd, "updated_at": upd,
                    "kind": str(it.get("kind") or "")[:40], "score": _num(it.get("score")),
                    "status": str(it.get("status") or "")[:40], "source_name": str(it.get("source_name") or "")[:200],
                    "data": it, "received_at": now}
            prev = c.execute(cdb.events.select().with_only_columns(cdb.events.c.status, cdb.events.c.updated_at)
                             .where(cdb.events.c.uid == uid)).first()
            # Eskiroq yangilanish ustiga yozilmaydi (updated_at monoton); received_at — faqat birinchi kelganda
            cdb.upsert(cdb.events, vals, ["uid"], ["updated_at", "kind", "score", "status", "source_name", "data"],
                       where=cdb.events.c.updated_at <= upd, conn=c)
            if prev is None:
                new_uids.append((uid, it))
            elif (prev.updated_at or 0) <= upd and (prev.status or "") != vals["status"]:
                changed.append((uid, vals["status"], it.get("reviewed_by")))
            _seen(c, dev, "event", key)
            accepted.append(key)
    # Bildirishnoma va jonli oqim — tranzaksiya yopilgach (SQLite yozish qulfi ushlanmasin)
    for uid, it in new_uids:
        try:
            notify.on_new_event(uid)
        except Exception:  # noqa: BLE001 — xabar xatosi qabul qilishni buzmasin
            pass
        # Panel toast'i uchun qisqa ma'lumot (qo'shimcha so'rovsiz)
        hub.publish("event_new", {"uid": uid, "branch_id": b["id"], "branch_name": b.get("name"),
                                  "score": _num(it.get("score")), "kind": str(it.get("kind") or "")[:40],
                                  "source_name": str(it.get("source_name") or "")[:200],
                                  "status": str(it.get("status") or "")[:40],
                                  "created_at": _num(it.get("created_at")),
                                  # ovozli toast — faqat jonli hodisa (backfill/video tahlili emas)
                                  "alert": notify.should_alert(it, b)}, b["id"])
    for uid, st, by in changed:
        try:
            notify.on_status_change(uid, st, by)
        except Exception:  # noqa: BLE001
            pass
        hub.publish("event_update", {"uid": uid, "status": st, "reviewed_by": by}, b["id"])
    return {"ok": True, "accepted": accepted, "rejected": rejected}


@app.post(f"/api/v{API_VERSION}/edge/events/{{uid}}/clip")
async def edge_clip(request: Request, uid: str, meta: str = Form("{}"), clip: UploadFile = File(...),
                    thumb: UploadFile | None = File(None)):
    b = await _branch(request)
    dev = b["device_id"]
    if not UID_RE.fullmatch(uid) or not uid.startswith(dev + ":"):
        raise HTTPException(400, "uid noto'g'ri")
    thumb_data = await thumb.read(5 * 1024 * 1024 + 1) if thumb is not None else None
    # Fayl nusxalash (60 MB gacha) va baza — threadpool'da: event loop bloklanmaydi
    return await run_in_threadpool(_store_clip, b, uid, clip.file, thumb_data)


def _store_clip(b: dict, uid: str, src, thumb_data: bytes | None) -> dict:
    dev = b["device_id"]
    ev = cdb.row("SELECT clip_file FROM events WHERE uid=:u", {"u": uid})
    if ev is None:
        raise HTTPException(409, "hodisa hali yo'q — avval hodisa yozuvini yuboring")
    if ev["clip_file"]:
        return {"ok": True, "duplicate": True}
    ddir = CLIPS_DIR / dev
    ddir.mkdir(parents=True, exist_ok=True)
    safe = uid.replace(":", "_")
    tmp = ddir / f"{safe}.{threading.get_ident()}.part"     # bir vaqtda ikki yuklash bir-birining faylini buzmasin
    total = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > CLIP_LIMIT:
                    raise HTTPException(413, f"klip juda katta (> {CLIP_LIMIT // (1 << 20)} MB)")
                f.write(chunk)
        if total == 0:
            raise HTTPException(400, "bo'sh fayl")
        final = ddir / f"{safe}.mp4"
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    thumb_name = None
    # Faqat haqiqiy JPEG (FF D8 FF) — rasm o'rnida boshqa narsa saqlanmasin
    if thumb_data and len(thumb_data) <= 5 * 1024 * 1024 and thumb_data[:3] == b"\xff\xd8\xff":
        (ddir / f"{safe}.jpg").write_bytes(thumb_data)
        thumb_name = f"{dev}/{safe}.jpg"
    with cdb.engine().begin() as c:
        c.execute(cdb.events.update().where(cdb.events.c.uid == uid).values(clip_file=f"{dev}/{safe}.mp4",
                                                                              thumb_file=thumb_name))
        _seen(c, dev, "clip", uid)
    try:
        notify.on_clip(uid)
    except Exception:  # noqa: BLE001
        log.exception("Klip bildirishnomasida xato")
    hub.publish("event_update", {"uid": uid, "clip": True}, b["id"])
    return {"ok": True, "bytes": total}


@app.post(f"/api/v{API_VERSION}/edge/metrics")
async def edge_metrics(request: Request):
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_metrics, b, items)


def _edge_metrics(b: dict, items: list[dict]) -> dict:
    accepted, rejected = [], []
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            try:
                minute, sid = int(it.get("minute")), int(it.get("source_id") or 0)
            except (TypeError, ValueError):
                rejected.append({"key": key, "reason": "minute/source_id noto'g'ri"})
                continue
            if len(json.dumps(it, ensure_ascii=False)) > 20000:
                rejected.append({"key": key, "reason": "yozuv juda katta"})
                continue
            cdb.upsert(cdb.metrics_minute, {"branch_id": b["id"], "minute": minute, "source_id": sid, "data": it},
                       ["branch_id", "minute", "source_id"], ["data"], conn=c)
            cdb.put_visits(c, b["id"], minute, sid, it)          # 1.9.0: sanash — alohida jadvalga (tez hisobot)
            _seen(c, b["device_id"], "metrics", key)
            accepted.append(key)
    return {"ok": True, "accepted": accepted, "rejected": rejected}


@app.post(f"/api/v{API_VERSION}/edge/queues")
async def edge_queues(request: Request):
    """2.0.0: kassa navbati daqiqalik yig'indisi (edge app/ai/queues.py)."""
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_queues, b, items)


def _edge_queues(b: dict, items: list[dict]) -> dict:
    accepted, rejected = [], []
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            if len(json.dumps(it, ensure_ascii=False)) > 4000:
                rejected.append({"key": key, "reason": "yozuv juda katta"})
                continue
            if not cdb.put_queue_minute(c, b["id"], it):
                rejected.append({"key": key, "reason": "minute/zone_id noto'g'ri"})
                continue
            _seen(c, b["device_id"], "queue", key)
            accepted.append(key)
    return {"ok": True, "accepted": accepted, "rejected": rejected}


@app.post(f"/api/v{API_VERSION}/edge/flow")
async def edge_flow(request: Request):
    """2.1.0: oqim va heatmap — soatlik yig'indi (edge app/ai/flow.py: hour_item)."""
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_flow, b, items)


def _edge_flow(b: dict, items: list[dict]) -> dict:
    accepted, rejected = [], []
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            if len(json.dumps(it, ensure_ascii=False)) > ITEM_LIMIT:
                rejected.append({"key": key, "reason": "yozuv juda katta"})
                continue
            if not cdb.put_flow_hour(c, b["id"], it):
                rejected.append({"key": key, "reason": "hour/plan_id/grid noto'g'ri"})
                continue
            _seen(c, b["device_id"], "flow", key)
            accepted.append(key)
    return {"ok": True, "accepted": accepted, "rejected": rejected}


@app.post(f"/api/v{API_VERSION}/edge/queue_alerts")
async def edge_queue_alerts(request: Request):
    """2.0.0: navbat qoidasi signali (1-4). Yangi va eskirmagani — bildirishnoma; 1-2 dan keyin 5-qoida (tarmoq)."""
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_queue_alerts, b, items)


def _edge_queue_alerts(b: dict, items: list[dict]) -> dict:
    accepted, rejected, fresh = [], [], []
    now = time.time()
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            rule = str(it.get("rule") or "")
            lid = int(_num(it.get("local_id")))
            if rule not in ("R1", "R2", "R3", "R4") or lid <= 0:
                rejected.append({"key": key, "reason": "qoida yoki local_id noto'g'ri"})
                continue
            if len(json.dumps(it, ensure_ascii=False)) > ITEM_LIMIT:
                rejected.append({"key": key, "reason": "yozuv juda katta"})
                continue
            t_edge = _num(it.get("t")) or now
            t = t_edge + float(b.get("clock_skew") or 0)          # bulut soatiga
            data = it.get("data") if isinstance(it.get("data"), dict) else {}
            row = {"key": f"{b['device_id']}:{lid}"[:120], "company_id": b["company_id"], "branch_id": b["id"],
                   "t": min(t, now + 60), "rule": rule, "level": str(it.get("level") or "")[:16],
                   "zone_name": str(it.get("zone_name") or "")[:200], "source_name": str(it.get("source_name") or "")[:200],
                   "title": notify.plain_text(it.get("title") or notify.QUEUE_RULES.get(rule, ""), 200),
                   "text": notify.plain_text(it.get("text") or ""), "data": data, "received_at": now}
            n = cdb.upsert(cdb.queue_alerts, row, ["key"], conn=c)
            if n and now - row["t"] <= notify.NOTIFY_MAX_AGE:
                fresh.append(row)
            _seen(c, b["device_id"], "qalert", key)
            accepted.append(key)
    for row in fresh:
        try:
            notify.on_queue_alert(b, row)
        except Exception:  # noqa: BLE001 — xabar xatosi qabul qilishni buzmasin
            pass
    return {"ok": True, "accepted": accepted, "rejected": rejected}


@app.post(f"/api/v{API_VERSION}/edge/health")
async def edge_health(request: Request):
    b = await _branch(request)
    items = _items(await _json(request))
    return await run_in_threadpool(_edge_health, b, items)


def _edge_health(b: dict, items: list[dict]) -> dict:
    accepted, rejected = [], []
    fresh: list[dict] = []
    with cdb.engine().begin() as c:
        for it in items:
            key = str(it.get("key") or "")
            if not key:
                rejected.append({"key": key, "reason": "kalit yo'q"})
                continue
            sid = it.get("source_id")
            n = cdb.upsert(cdb.health_log, {"key": f"{b['device_id']}:{key}"[:120], "branch_id": b["id"],
                                        "t": _num(it.get("t")) or time.time(),
                                        "source_id": int(sid) if isinstance(sid, (int, float)) else None,
                                        "source_name": str(it.get("source_name") or "")[:200],
                                        "state": str(it.get("state") or "")[:20],
                                        "reason": str(it.get("reason") or "")[:300]}, ["key"], conn=c)
            t_cloud = (_num(it.get("t")) or time.time()) + float(b.get("clock_skew") or 0)
            if n and time.time() - t_cloud < notify.NOTIFY_MAX_AGE:
                fresh.append(it)            # yangi va eskirmagan holat o'zgarishi — xabar (do'kon soati farqi hisobda)
            _seen(c, b["device_id"], "health", key)
            accepted.append(key)
    for it in fresh:
        try:
            notify.on_health(b, it)
        except Exception:  # noqa: BLE001
            pass
        hub.publish("health", {"branch_id": b["id"], "source_id": it.get("source_id"),
                               "source_name": str(it.get("source_name") or "")[:200],
                               "state": str(it.get("state") or "")[:20]}, b["id"])
    return {"ok": True, "accepted": accepted, "rejected": rejected}


# ----------------------------------------------------------------------------- boshqaruv API v1 (panel)
def _host_of(v: str) -> str:
    return v.split("://", 1)[-1].split("/", 1)[0].strip().lower()


def _cross_site(request: Request) -> bool:
    """Cookie bilan kelgan holat o'zgartiruvchi so'rov boshqa saytdanmi (CSRF)."""
    sfs = request.headers.get("sec-fetch-site", "").lower()
    if sfs in ("same-origin", "none"):
        return False
    if sfs in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    if origin == "null":
        return True                         # sandbox iframe, data: sahifa — o'z panelimiz emas
    if origin:
        allowed = {_host_of(request.headers.get("host") or ""), _host_of(request.headers.get("x-forwarded-host") or "")}
        if notify.cfg.public_url:
            allowed.add(_host_of(notify.cfg.public_url))
        return _host_of(origin) not in allowed
    return False


def _user(request: Request) -> dict:
    """Bearer token (API mijozlari) yoki panel cookie'si. Cookie bilan holat o'zgartirish — faqat o'z saytidan."""
    if _fail_now(request):
        raise HTTPException(503)
    tok = _bearer(request)
    via_cookie = False
    if not tok:
        tok = request.cookies.get(SESSION_COOKIE, "")
        via_cookie = bool(tok)
    u = cdb.user_by_token(tok)
    if not u:
        raise HTTPException(401, "kirish talab qilinadi")
    if via_cookie and request.method not in ("GET", "HEAD", "OPTIONS") and _cross_site(request):
        raise HTTPException(403, "boshqa saytdan so'rov rad etildi (CSRF)")
    return u


def _require(u: dict, *roles: str) -> None:
    if u["role"] not in roles:
        raise HTTPException(403, "bu amal uchun ruxsat yo'q")


def _scope(u: dict, branch_id: int | None = None) -> list[int]:
    ids = cdb.branch_ids_for(u)
    if branch_id is not None:
        if int(branch_id) not in ids:
            raise HTTPException(403, "bu filialga ruxsat yo'q")
        return [int(branch_id)]
    return ids


def _public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "name", "role", "company_id") if k in u}


_login_fails: dict[str, list[float]] = {}
_pw_lock = threading.Lock()
# Blok chegaralari (10 daqiqada): bitta IP + bitta email — 10; bitta IP (ofisda ko'p xodim bitta IP'dan) — 20;
# bitta email ko'p IP'dan — 30 (taqsimlangan tavakkal; past bo'lsa begona odam egasining akkauntini osongina yopadi)
LOGIN_LIMITS = {"emip": 10, "ip": 20, "em": 30}


def _fails(key: str, now: float) -> list[float]:
    f = [t for t in _login_fails.get(key, []) if now - t < 600]
    if f:
        _login_fails[key] = f
    else:
        _login_fails.pop(key, None)
    return f


def _login_sync(email: str, pw: str, ip: str):
    """scrypt (~50 ms) va baza — threadpool'da. Muvaffaqiyat/xato audit jurnaliga yoziladi."""
    res = cdb.login(email, pw)
    if not res:
        known = cdb.row("SELECT id, company_id FROM users WHERE email=:e", {"e": email}) if email else None
        cdb.audit("login_fail", None, email, ip=ip, company_id=(known or {}).get("company_id"), actor=email)
        return None
    tok, u = res
    cdb.audit("login_ok", u, u["email"], ip=ip)
    comp = cdb.row("SELECT id, name FROM companies WHERE id=:c", {"c": u["company_id"]})
    return tok, u, comp


@app.post(f"/api/v{API_VERSION}/auth/login")
async def auth_login(request: Request):
    if _cross_site(request):
        # Boshqa sayt foydalanuvchini o'z akkauntiga kiritib qo'ymasin (login CSRF)
        raise HTTPException(403, "boshqa saytdan so'rov rad etildi (CSRF)")
    body = await _json(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    email, pw = str(body.get("email") or "").strip().lower()[:200], str(body.get("password") or "")[:200]
    ip = _ip(request)
    now = time.time()
    if len(_login_fails) > 5000:            # xotira o'smasin: eskirganlar tozalanadi
        for k in list(_login_fails):
            _fails(k, now)
    keys = {n: k for n, k in {"emip": f"emip:{email}|{ip}", "ip": f"ip:{ip}", "em": f"em:{email}"}.items()
            if n == "ip" or email}
    if any(len(_fails(k, now)) >= LOGIN_LIMITS[n] for n, k in keys.items()):
        raise HTTPException(429, "ko'p xato urinish — 10 daqiqadan keyin")
    # Urinish parol tekshirilishidan OLDIN hisoblanadi (tekshirish va yozish orasida await yo'q — bitta event loop'da
    # atomar): bir vaqtda yuborilgan 60 ta so'rov ham chegaradan oshib parol tanlay olmaydi. Muvaffaqiyatda qaytariladi.
    for k in keys.values():
        _login_fails.setdefault(k, []).append(now)
    res = await run_in_threadpool(_login_sync, email, pw, ip)
    if not res:
        raise HTTPException(401, "email yoki parol noto'g'ri")
    for n, k in keys.items():
        if n != "ip":
            _login_fails.pop(k, None)
        else:
            lst = _login_fails.get(k)
            if lst and now in lst:
                lst.remove(now)          # muvaffaqiyatli kirish IP xatolari qatoriga kirmaydi
    tok, u, comp = res
    out = {"ok": True, "user": _public_user(u), "company": comp}
    if body.get("session") != "cookie":
        out["token"] = tok                  # API mijozlari uchun; panel (session=cookie) tokenni JS'da ko'rmaydi
    resp = JSONResponse(out)
    # Secure — faqat haqiqiy HTTPS so'rovda (teskari proksi X-Forwarded-Proto bilan); http://127.0.0.1 da cookie ishlasin
    resp.set_cookie(SESSION_COOKIE, tok, max_age=cdb.SESSION_TTL, httponly=True, samesite="strict",
                    secure=_https(request), path="/")
    return resp


def _session_token(request: Request) -> str:
    return _bearer(request) or request.cookies.get(SESSION_COOKIE, "")


@app.post(f"/api/v{API_VERSION}/auth/logout")
async def auth_logout(request: Request):
    if _cross_site(request):
        raise HTTPException(403, "boshqa saytdan so'rov rad etildi (CSRF)")
    tok = _session_token(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    ep = body.get("push_endpoint") if isinstance(body, dict) else None

    def _do() -> None:
        u = cdb.user_by_token(tok) if tok else None
        if u and isinstance(ep, str) and ep:
            # Umumiy kompyuter: chiqqan xodimning xabarlari shu brauzerga kelishda davom etmasin
            cdb.execute("DELETE FROM notify_targets WHERE user_id=:u AND channel='push' AND address=:a",
                        {"u": u["id"], "a": ep[:600]})
        if tok:
            cdb.logout(tok)
        if u:
            cdb.audit("logout", u, u["email"], ip=_ip(request))
    await run_in_threadpool(_do)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.post(f"/api/v{API_VERSION}/auth/logout-all")
def auth_logout_all(request: Request):
    """Barcha qurilmalardan chiqish (telefon yo'qolsa): shu foydalanuvchining hamma sessiyalari yopiladi."""
    u = _user(request)
    n = cdb.execute("DELETE FROM sessions WHERE user_id=:u", {"u": u["id"]})
    cdb.audit("logout_all", u, u["email"], {"sessions": n}, ip=_ip(request))
    resp = JSONResponse({"ok": True, "closed": n})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.post(f"/api/v{API_VERSION}/me/password")
async def change_password(request: Request):
    """O'z parolini almashtirish: joriy parol talab qilinadi; boshqa qurilmalardagi sessiyalar yopiladi."""
    body = await _json(request)

    def _do() -> dict:
        u = _user(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON obyekt kutilgan")
        cur, new = str(body.get("current") or "")[:200], str(body.get("new") or "")
        ip = _ip(request)
        k = f"pw:{u['id']}"
        t0 = time.time()
        with _pw_lock:          # tekshirish va hisoblash birga: parallel so'rovlar 5 urinish chegarasidan o'tolmaydi
            if len(_fails(k, t0)) >= 5:
                raise HTTPException(429, "ko'p xato urinish — 10 daqiqadan keyin")
            _login_fails.setdefault(k, []).append(t0)
        if not cdb.verify_password(u["id"], cur):
            cdb.audit("password_fail", u, u["email"], ip=ip)
            raise HTTPException(400, "joriy parol noto'g'ri")
        with _pw_lock:
            lst = _login_fails.get(k)
            if lst and t0 in lst:
                lst.remove(t0)
        bad = cdb.password_problem(new, u["email"]) or ("yangi parol eskisi bilan bir xil" if new == cur else None)
        if bad:
            raise HTTPException(400, bad)
        closed = cdb.set_password(u["id"], new, keep_token=_session_token(request))
        cdb.audit("password_change", u, u["email"], {"closed_sessions": closed}, ip=ip)
        return {"ok": True, "closed_sessions": closed}
    return await run_in_threadpool(_do)


@app.get(f"/api/v{API_VERSION}/auth/status")
def auth_status(request: Request):
    """Panel uchun: sessiya bormi va kim (sessiya bo'lmasa ham 200 — brauzer konsolida 401 xatosi chiqmaydi)."""
    if _fail_now(request):
        raise HTTPException(503)
    u = cdb.user_by_token(_bearer(request) or request.cookies.get(SESSION_COOKIE, ""))
    if not u:
        return {"ok": True, "authenticated": False}
    comp = cdb.row("SELECT id, name FROM companies WHERE id=:c", {"c": u["company_id"]})
    return {"ok": True, "authenticated": True, "user": _public_user(u), "company": comp,
            "branch_ids": cdb.branch_ids_for(u)}


@app.get(f"/api/v{API_VERSION}/me")
def me(request: Request):
    u = _user(request)
    comp = cdb.row("SELECT id, name FROM companies WHERE id=:c", {"c": u["company_id"]})
    return {"ok": True, "user": _public_user(u), "company": comp, "branch_ids": cdb.branch_ids_for(u)}


@app.get(f"/api/v{API_VERSION}/branches")
def list_branches(request: Request):
    u = _user(request)
    ids = set(_scope(u))
    if not ids:
        return {"ok": True, "branches": []}
    day0 = time.time() - 86400
    out = []
    for b in cdb.rows("SELECT id, name, address, device_id, created_at, last_seen, version, cameras, pending, clock_skew, "
                      "occupancy "
                      "FROM branches WHERE company_id=:c ORDER BY id", {"c": u["company_id"]}):
        if b["id"] not in ids:
            continue
        b["cameras"] = cdb.json_load(b.get("cameras")) or []
        b["online"] = bool(b.get("last_seen") and time.time() - b["last_seen"] < 180)
        b["events_24h"] = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b AND created_at >= :t",
                                     {"b": b["id"], "t": day0}) or 0
        b["pending_review"] = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b AND status='yangi'",
                                         {"b": b["id"]}) or 0
        out.append(b)
    vis = cdb.visits_hourly([b["id"] for b in out], _local_day0(), off=_tz_sec())
    for b in out:
        mine = [v for v in vis if v["branch_id"] == b["id"]]
        cin, cout = sum(v["in"] for v in mine), sum(v["out"] for v in mine)
        ain, aout = cin + sum(v["off_in"] for v in mine), cout + sum(v["off_out"] for v in mine)
        # Bugun o'tish bo'lmasa ham edge "ichkarida" ni yuborayotgan bo'lsa (masalan, 00:30, ichkarida 3 kishi) — ko'rsatiladi
        b["visits_today"] = ({"in": cin, "out": cout, "inside": cdb.inside_now(b.get("occupancy"), ain, aout)}
                             if mine or cdb.occupancy_fresh(b.get("occupancy")) else None)
        b.pop("occupancy", None)
    return {"ok": True, "branches": out}


def _tz_sec() -> int:
    return int(round(notify.cfg.tz_offset * 3600))


def _local_day0(t: float | None = None) -> float:
    """Kompaniya mahalliy vaqti (CLOUD_TZ_OFFSET, sukut +5) bo'yicha kun boshi."""
    off = notify.cfg.tz_offset * 3600
    return (int((t or time.time()) + off) // 86400) * 86400 - off


@app.get(f"/api/v{API_VERSION}/visits")
def visits(request: Request, days: int = 14, branch_id: int | None = None):
    """1.9.0 Tashriflar (sanash chizig'i): filial va kun bo'yicha kirdi/chiqdi, bugun soatlar bo'yicha, hozir ichkarida."""
    u = _user(request)
    ids = _scope(u, branch_id)
    days = max(1, min(int(days), 90))
    today = _local_day0()
    t0 = today - (days - 1) * 86400
    brows = {r["id"]: r for r in cdb.rows("SELECT id, name, occupancy FROM branches WHERE company_id=:c",
                                          {"c": u["company_id"]})}
    names = {k: r["name"] for k, r in brows.items()}
    rows = cdb.visits_hourly(ids, t0, off=_tz_sec())
    per_day: dict[tuple[int, int], dict] = {}
    hourly: dict[int, list[int]] = {}
    today_all: dict[int, list[int]] = {}                       # xodimsiz, ish vaqtidan qat'i nazar (ichkarida)
    for v in rows:
        di = int((v["hour"] - t0) // 86400)
        a = per_day.setdefault((v["branch_id"], di), {"in": 0, "out": 0, "staff": 0})
        a["in"] += v["in"]
        a["out"] += v["out"]
        a["staff"] += v["staff"]
        if v["hour"] >= today:
            hourly.setdefault(v["branch_id"], [0] * 24)[min(23, int((v["hour"] - today) // 3600))] += v["in"]
            ta = today_all.setdefault(v["branch_id"], [0, 0])
            ta[0] += v["in"] + v["off_in"]
            ta[1] += v["out"] + v["off_out"]
    with_data = sorted({b for b, _ in per_day})
    out_days = []
    for di in range(days):
        row = {"day": t0 + di * 86400, "in": 0, "out": 0, "branches": {}}
        for b in with_data:
            a = per_day.get((b, di))
            if a:
                row["branches"][str(b)] = a
                row["in"] += a["in"]
                row["out"] += a["out"]
        out_days.append(row)
    branches = []
    for b in with_data:
        a = per_day.get((b, days - 1)) or {"in": 0, "out": 0}
        h = hourly.get(b, [0] * 24)
        ta = today_all.get(b, [0, 0])
        branches.append({"id": b, "name": names.get(b, str(b)), "today_in": a["in"], "today_out": a["out"],
                         "inside": cdb.inside_now((brows.get(b) or {}).get("occupancy"), ta[0], ta[1]), "hourly": h,
                         "busiest_hour": (h.index(max(h)) if max(h) > 0 else None),
                         "total_in": sum((per_day.get((b, i)) or {}).get("in", 0) for i in range(days))})
    return {"ok": True, "days": out_days, "branches": branches, "tz_offset": notify.cfg.tz_offset,
            "day0": today}


@app.get(f"/api/v{API_VERSION}/queues")
def queues(request: Request, days: int = 14, branch_id: int | None = None):
    """2.0.0 Navbat: filiallar kassalari hozir (hello), bugungi ko'rsatkichlar, soatlar, kunlar, signallar."""
    u = _user(request)
    ids = _scope(u, branch_id)
    days = max(1, min(int(days), 90))
    today = _local_day0()
    t0 = today - (days - 1) * 86400
    brows = {r["id"]: r for r in cdb.rows("SELECT id, name, queues FROM branches WHERE company_id=:c",
                                          {"c": u["company_id"]}) if r["id"] in ids}
    # Yig'indi SQL'da (filial × kun, bugun — filial × soat): 14 kunlik daqiqalar Python'ga o'qilmaydi
    per_day: dict[tuple[int, int], dict] = {}
    today_b: dict[int, dict] = {}
    hours_len: dict[int, list] = {}
    for r in cdb.queue_agg(ids, t0, _tz_sec(), 86400):
        di = int(round((r["t"] - t0) / 86400))
        if 0 <= di < days:
            per_day[(r["branch_id"], di)] = r
            if di == days - 1:
                today_b[r["branch_id"]] = r
    for r in cdb.queue_agg(ids, today, _tz_sec(), 3600):
        h = int((r["t"] - today) // 3600)
        if 0 <= h < 24:
            hl = hours_len.setdefault(r["branch_id"], [[0.0, 0.0, 0] for _ in range(24)])
            hl[h] = [r["len_sum"], r["t_obs"], r["served"]]
    branches = []
    for bid, b in sorted(brows.items()):
        live = cdb.queues_fresh(b.get("queues"))
        stored = cdb.json_load(b.get("queues"))
        has_any = live is not None or stored or any(k[0] == bid for k in per_day)
        if not has_any:
            continue
        a = per_day.get((bid, days - 1)) or {"served": 0, "wait_sum": 0.0, "wait_n": 0, "wait_max": 0.0, "len_max": 0,
                                              "long_sec": 0.0}
        tb = today_b.get(bid) or {"t_obs": 0.0, "len_sum": 0.0}
        hl = hours_len.get(bid) or [[0.0, 0.0, 0] for _ in range(24)]
        # "aloqa yo'q" — edge kassalar holatini yuborgan edi, endi kelmayapti (navbat zonasi o'chirilgan filial emas)
        branches.append({"id": bid, "name": b["name"], "live": live, "stale": live is None and bool(stored),
                         "today": {"served": a["served"], "avg_wait": round(a["wait_sum"] / a["wait_n"], 1) if a["wait_n"] else None,
                                   "max_wait": round(a["wait_max"], 1), "max_len": a["len_max"],
                                   "long_min": round(a["long_sec"] / 60, 1),
                                   "avg_len": round(tb["len_sum"] / tb["t_obs"], 2) if tb["t_obs"] else None},
                         "hours_len": [round(x[0] / x[1], 2) if x[1] else 0 for x in hl],
                         "hours_served": [x[2] for x in hl]})
    out_days = []
    for di in range(days):
        row = {"day": t0 + di * 86400, "served": 0, "wait_sum": 0.0, "wait_n": 0, "wait_max": 0.0, "len_max": 0,
               "long_min": 0.0, "branches": {}}
        for b in branches:
            a = per_day.get((b["id"], di))
            if a:
                row["branches"][str(b["id"])] = {"served": a["served"], "max_len": a["len_max"],
                                                 "avg_wait": round(a["wait_sum"] / a["wait_n"], 1) if a["wait_n"] else None}
                row["served"] += a["served"]
                row["wait_sum"] += a["wait_sum"]
                row["wait_n"] += a["wait_n"]
                row["wait_max"] = max(row["wait_max"], a["wait_max"])
                row["len_max"] = max(row["len_max"], a["len_max"])
                row["long_min"] += a["long_sec"] / 60
        row["avg_wait"] = round(row.pop("wait_sum") / row["wait_n"], 1) if row["wait_n"] else None
        row.pop("wait_n")
        row["long_min"] = round(row["long_min"], 1)
        out_days.append(row)
    # Filial signali — faqat ruxsat etilgan filiallarniki (SQL'da: LIMIT dan oldin); tarmoq signali (5) — faqat egaga
    qa = cdb.queue_alerts.c
    cond = qa.branch_id.in_([int(x) for x in ids]) if ids else qa.branch_id.in_([-1])
    if u["role"] == "owner" and branch_id is None:
        cond = cond | qa.branch_id.is_(None)
    sel = (select(qa.id, qa.branch_id, cdb.branches.c.name.label("branch_name"), qa.t, qa.rule, qa.level, qa.zone_name,
                  qa.title, qa.text)
           .select_from(cdb.queue_alerts.outerjoin(cdb.branches, cdb.branches.c.id == qa.branch_id))
           .where(qa.company_id == u["company_id"], qa.t >= t0, cond).order_by(qa.t.desc()).limit(50))
    with cdb.engine().connect() as conn:
        al = [dict(r._mapping) for r in conn.execute(sel)]
    return {"ok": True, "branches": branches, "days": out_days, "alerts": al, "rules": notify.QUEUE_RULES,
            "tz_offset": notify.cfg.tz_offset, "day0": today}


@app.get(f"/api/v{API_VERSION}/flow")
def flow(request: Request, days: int = 7, branch_id: int | None = None, plan_id: int | None = None):
    """2.1.0 Oqim: filial rejasi (bo'limlar), bo'lim tashriflari (davr va bugun soatlar bo'yicha), yo'nalishlar va
    heatmap (bandlik va o'tganlar; davr bo'yicha yig'indi, faqat joriy to'r kalitidagi soatlar)."""
    u = _user(request)
    _scope(u, branch_id)                         # begona filial — 403
    ids = _scope(u, None)                        # tanlash ro'yxati — foydalanuvchining barcha filiallari
    days = max(1, min(int(days), 90))
    today = _local_day0()
    t0 = today - (days - 1) * 86400
    brows = [r for r in cdb.rows("SELECT id, name, flow_plans FROM branches WHERE company_id=:c ORDER BY id",
                                 {"c": u["company_id"]}) if r["id"] in ids]
    avail = []
    for r in brows:
        fp = cdb.json_load(r.get("flow_plans")) or {}
        for p in fp.get("items") or []:
            avail.append({"branch_id": r["id"], "branch_name": r["name"], "plan_id": p["id"], "plan_name": p["name"]})
    with_data = cdb.flow_with_data([r["id"] for r in brows], t0)
    avail = [a for a in avail if (a["branch_id"], a["plan_id"]) in with_data] + \
            [a for a in avail if (a["branch_id"], a["plan_id"]) not in with_data]
    if not avail:
        return {"ok": True, "plans": [], "plan": None, "days": days, "tz_offset": notify.cfg.tz_offset, "day0": today}
    sel = next((a for a in avail if (branch_id is None or a["branch_id"] == branch_id) and
                (plan_id is None or a["plan_id"] == plan_id)), avail[0])
    br = next(r for r in brows if r["id"] == sel["branch_id"])
    plan = next(p for p in (cdb.json_load(br.get("flow_plans")) or {}).get("items") or [] if p["id"] == sel["plan_id"])
    mine = cdb.flow_rows([sel["branch_id"]], t0, plan_id=sel["plan_id"])     # JSON faqat tanlangan reja uchun
    n = int(plan.get("nx") or 0) * int(plan.get("ny") or 0)
    occ = [0.0] * n                   # bulut serverida numpy yo'q (requirements-cloud.txt) — oddiy ro'yxat
    pas = [0.0] * n
    visits: dict[str, int] = {}
    today_h = [dict() for _ in range(24)]
    per_day = [0] * days
    moves: dict[tuple, int] = {}
    obs = 0.0
    for x in mine:
        d = cdb.json_load(x["data"]) or {}
        for k, v in (d.get("visits") or {}).items():
            visits[k] = visits.get(k, 0) + int(v)
            hi = int((x["hour"] - today) // 3600)
            if 0 <= hi < 24:
                today_h[hi][k] = today_h[hi].get(k, 0) + int(v)
        di = int((x["hour"] - t0) // 86400)
        if 0 <= di < days:
            per_day[di] += int(x["visits"] or 0)
        for a, b_, c in d.get("moves") or []:
            moves[(a, b_)] = moves.get((a, b_), 0) + int(c)
        if x["grid"] == plan.get("grid") and n:
            obs += float(x["obs"] or 0)
            for k, v in (d.get("occ") or {}).items():
                i = int(k)
                if 0 <= i < n:
                    occ[i] += float(v)
            for k, v in (d.get("pas") or {}).items():
                i = int(k)
                if 0 <= i < n:
                    pas[i] += float(v)

    def enc(g: list) -> dict:
        mx = max(g) if g else 0.0
        q = bytes(min(255, max(0, int(round(v / mx * 255)))) for v in g) if mx > 0 else bytes(len(g))
        return {"max": round(mx, 2), "total": round(sum(g), 1), "data": base64.b64encode(q).decode()}
    names = {str(s_["id"]): s_["name"] for s_ in plan.get("sections") or []}
    tot = sum(visits.values())
    secs = [{"id": int(k), "name": names.get(k, f"#{k}"), "visits": v, "share": round(v / tot, 4) if tot else 0}
            for k, v in sorted(visits.items(), key=lambda kv: -kv[1])]
    mv = [{"from": a, "to": b_, "n": c, "from_name": names.get(str(a), f"#{a}"), "to_name": names.get(str(b_), f"#{b_}")}
          for (a, b_), c in sorted(moves.items(), key=lambda kv: -kv[1])[:20]]
    return {"ok": True, "plans": avail, "plan": {**plan, "branch_id": sel["branch_id"], "branch_name": sel["branch_name"]},
            "days": days, "day0": today, "t0": t0, "tz_offset": notify.cfg.tz_offset, "total": tot, "sections": secs,
            "moves": mv, "today_hours": today_h, "per_day": per_day, "obs": round(obs, 1),
            "heat": {"occ": enc(occ), "pas": enc(pas), "nx": plan.get("nx"), "ny": plan.get("ny"),
                     "cell": plan.get("cell")}}


@app.post(f"/api/v{API_VERSION}/branches")
async def create_branch(request: Request):
    """Yangi filial (do'kon). Javobdagi device_token faqat bir marta ko'rinadi — edge .env dagi EDGE_TOKEN."""
    body = await _json(request)
    return await run_in_threadpool(_create_branch, request, body)


def _create_branch(request: Request, body) -> dict:
    u = _user(request)
    _require(u, "owner")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    name = str(body.get("name") or "").strip()
    dev = str(body.get("device_id") or "").strip()
    if not name:
        raise HTTPException(400, "filial nomi kerak")
    if not DEV_RE.fullmatch(dev):
        raise HTTPException(400, "device_id noto'g'ri (edge paneli: Tizim holati → Bulut → Qurilma)")
    try:
        bid, tok = cdb.create_branch(u["company_id"], name, dev, str(body.get("address") or ""))
    except IntegrityError:
        raise HTTPException(409, "bu qurilma identifikatori bilan filial yaratib bo'lmadi (band)")
    cdb.audit("branch_create", u, f"{name} ({dev})", {"branch_id": bid}, ip=_ip(request))
    return {"ok": True, "branch": {"id": bid, "name": name, "device_id": dev}, "device_token": tok}


@app.post(f"/api/v{API_VERSION}/branches/{{branch_id}}/token")
def rotate_branch_token(request: Request, branch_id: int):
    u = _user(request)
    _require(u, "owner")
    _scope(u, branch_id)
    tok = cdb.rotate_token(branch_id)
    name = cdb.scalar("SELECT name FROM branches WHERE id=:b", {"b": branch_id})
    cdb.audit("token_rotate", u, str(name or branch_id), {"branch_id": branch_id}, ip=_ip(request))
    return {"ok": True, "device_token": tok}


@app.get(f"/api/v{API_VERSION}/branches/{{branch_id}}/cameras")
def branch_cameras(request: Request, branch_id: int):
    u = _user(request)
    _scope(u, branch_id)
    b = cdb.row("SELECT cameras, last_seen FROM branches WHERE id=:b", {"b": branch_id})
    cams = cdb.json_load(b["cameras"]) if b else []
    health = cdb.rows("SELECT * FROM health_log WHERE branch_id=:b ORDER BY t DESC LIMIT 50", {"b": branch_id})
    return {"ok": True, "cameras": cams or [], "health": health}


@app.get(f"/api/v{API_VERSION}/users")
def list_users(request: Request):
    u = _user(request)
    _require(u, "owner")
    out = cdb.rows("SELECT id, email, name, role, active, created_at FROM users WHERE company_id=:c ORDER BY id",
                   {"c": u["company_id"]})
    for x in out:
        x["branch_ids"] = [r["branch_id"] for r in cdb.rows("SELECT branch_id FROM user_branches WHERE user_id=:u",
                                                           {"u": x["id"]})]
    return {"ok": True, "users": out}


@app.post(f"/api/v{API_VERSION}/users")
async def create_user(request: Request):
    body = await _json(request)
    return await run_in_threadpool(_create_user, request, body)


def _branch_list(u: dict, raw) -> list[int]:
    bids = sorted({int(x) for x in (raw or []) if str(x).isdigit()}) if isinstance(raw, list) else []
    allowed = set(cdb.branch_ids_for(u))
    if any(b not in allowed for b in bids):
        raise HTTPException(400, "filial boshqa kompaniyaniki")
    return bids


def _create_user(request: Request, body) -> dict:
    u = _user(request)
    _require(u, "owner")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    role = str(body.get("role") or "guard")
    if role not in cdb.ROLES:
        raise HTTPException(400, "rol: owner, manager yoki guard")
    bids = _branch_list(u, body.get("branch_ids"))
    if role != "owner" and not bids:
        raise HTTPException(400, "menejer/qo'riqchi uchun kamida bitta filial")
    email = str(body.get("email") or "").strip().lower()
    if not EMAIL_RE.fullmatch(email) or len(email) > 200:
        raise HTTPException(400, "email noto'g'ri")
    try:
        uid = cdb.create_user(u["company_id"], email, str(body.get("password") or ""), str(body.get("name") or ""),
                              role, bids if role != "owner" else [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except IntegrityError:
        raise HTTPException(409, "bu email bilan foydalanuvchi yaratib bo'lmadi (band)")
    cdb.audit("user_create", u, email, {"user_id": uid, "role": role, "branch_ids": bids}, ip=_ip(request))
    return {"ok": True, "user": {"id": uid, "email": email, "role": role, "branch_ids": bids if role != "owner" else []}}


def _company_user(u: dict, user_id: int) -> dict:
    t = cdb.row("SELECT id, email, name, role, active FROM users WHERE id=:u AND company_id=:c",
                {"u": user_id, "c": u["company_id"]})
    if not t:
        raise HTTPException(404, "xodim topilmadi")
    return t


@app.post(f"/api/v{API_VERSION}/users/{{user_id}}/deactivate")
def deactivate_user(request: Request, user_id: int):
    u = _user(request)
    _require(u, "owner")
    if user_id == u["id"]:
        raise HTTPException(400, "o'zingizni o'chirib bo'lmaydi")
    n = cdb.execute("UPDATE users SET active=:f WHERE id=:u AND company_id=:c", {"f": False, "u": user_id, "c": u["company_id"]})
    if n:
        cdb.execute("DELETE FROM sessions WHERE user_id=:u", {"u": user_id})
        cdb.execute("UPDATE notify_targets SET enabled=:f, last_error=:e WHERE user_id=:u",
                    {"f": False, "e": "xodim o'chirilgan", "u": user_id})
        cdb.audit("user_deactivate", u, str(cdb.scalar("SELECT email FROM users WHERE id=:u", {"u": user_id})),
                  {"user_id": user_id}, ip=_ip(request))
    return {"ok": True, "updated": n}


@app.post(f"/api/v{API_VERSION}/users/{{user_id}}/activate")
def activate_user(request: Request, user_id: int):
    """O'chirilgan xodimni qaytarish (bildirishnoma manzillari o'zi yoqilmaydi — xodim qayta ulaydi)."""
    u = _user(request)
    _require(u, "owner")
    t = _company_user(u, user_id)
    n = cdb.execute("UPDATE users SET active=:t WHERE id=:u AND company_id=:c", {"t": True, "u": user_id, "c": u["company_id"]})
    cdb.audit("user_activate", u, t["email"], {"user_id": user_id}, ip=_ip(request))
    return {"ok": True, "updated": n}


@app.post(f"/api/v{API_VERSION}/users/{{user_id}}/update")
async def update_user(request: Request, user_id: int):
    """Ism, rol, filiallar (egasi). O'z rolini o'zgartirib bo'lmaydi — kompaniya egasiz qolmasin."""
    body = await _json(request)

    def _do() -> dict:
        u = _user(request)
        _require(u, "owner")
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON obyekt kutilgan")
        t = _company_user(u, user_id)
        role = str(body.get("role") or t["role"])
        if role not in cdb.ROLES:
            raise HTTPException(400, "rol: owner, manager yoki guard")
        if user_id == u["id"] and role != t["role"]:
            raise HTTPException(400, "o'z rolingizni o'zgartirib bo'lmaydi")
        bids = _branch_list(u, body.get("branch_ids")) if "branch_ids" in body else [
            r["branch_id"] for r in cdb.rows("SELECT branch_id FROM user_branches WHERE user_id=:u", {"u": user_id})]
        if role != "owner" and not bids:
            raise HTTPException(400, "menejer/qo'riqchi uchun kamida bitta filial")
        name = str(body.get("name") if body.get("name") is not None else t["name"] or "").strip()[:200]
        with cdb.engine().begin() as c:
            c.execute(cdb.users.update().where(cdb.users.c.id == user_id).values(role=role, name=name))
            c.execute(cdb.user_branches.delete().where(cdb.user_branches.c.user_id == user_id))
            for b in (bids if role != "owner" else []):
                c.execute(cdb.user_branches.insert().values(user_id=user_id, branch_id=b))
        cdb.audit("user_update", u, t["email"], {"user_id": user_id, "role": role, "branch_ids": bids,
                                                 "role_before": t["role"]}, ip=_ip(request))
        return {"ok": True, "user": {"id": user_id, "email": t["email"], "name": name, "role": role,
                                     "branch_ids": bids if role != "owner" else []}}
    return await run_in_threadpool(_do)


@app.post(f"/api/v{API_VERSION}/users/{{user_id}}/password")
async def reset_user_password(request: Request, user_id: int):
    """Egasi xodimga yangi parol beradi (xodim parolni unutganda); xodimning hamma sessiyalari yopiladi."""
    body = await _json(request)

    def _do() -> dict:
        u = _user(request)
        _require(u, "owner")
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON obyekt kutilgan")
        t = _company_user(u, user_id)
        if user_id == u["id"]:
            raise HTTPException(400, "o'z parolingizni Sozlamalar → Parol orqali almashtiring")
        new = str(body.get("password") or "")
        bad = cdb.password_problem(new, t["email"])
        if bad:
            raise HTTPException(400, bad)
        closed = cdb.set_password(user_id, new)
        cdb.audit("password_reset", u, t["email"], {"user_id": user_id, "closed_sessions": closed}, ip=_ip(request))
        return {"ok": True, "closed_sessions": closed}
    return await run_in_threadpool(_do)


AUDIT_ACTIONS = ("login_ok", "login_fail", "logout", "logout_all", "password_change", "password_fail", "password_reset",
                 "user_create", "user_update", "user_deactivate", "user_activate", "branch_create", "token_rotate",
                 "review", "clip_view", "telegram_link")


@app.get(f"/api/v{API_VERSION}/audit")
def audit_list(request: Request, limit: int = 200, before_id: int | None = None, action: str | None = None,
               user_id: int | None = None):
    """Kompaniya audit jurnali (faqat egasi): kim, qachon, qayerdan, nima qildi."""
    u = _user(request)
    _require(u, "owner")
    limit = max(1, min(limit, 500))
    where, params = ["company_id=:c"], {"c": u["company_id"], "lim": limit}
    if before_id is not None:
        where.append("id < :b")
        params["b"] = before_id
    if action:
        if action not in AUDIT_ACTIONS:
            raise HTTPException(400, "amal noto'g'ri")
        where.append("action=:a")
        params["a"] = action
    if user_id is not None:
        where.append("user_id=:uid")
        params["uid"] = user_id
    rows_ = cdb.rows(f"SELECT id, t, user_id, actor, action, target, ip, detail FROM audit_log WHERE {' AND '.join(where)} "
                     "ORDER BY id DESC LIMIT :lim", params)
    for r in rows_:
        r["detail"] = cdb.json_load(r.get("detail"))
    return {"ok": True, "items": rows_, "actions": list(AUDIT_ACTIONS)}


@app.get(f"/api/v{API_VERSION}/system")
def system_status(request: Request):
    """Server holati (egasi): versiya, baza, disk, kliplar, oxirgi zaxira, kanallar, jonli ulanishlar."""
    u = _user(request)
    _require(u, "owner")
    st = maintenance.status()
    st.pop("backup_dir", None)          # server fayl yo'li panelga chiqmasin
    return {"ok": True, "version": VERSION, "sse": hub.count(), "channels": notify.channels(), **st}


@app.get(f"/api/v{API_VERSION}/events")
def list_events(request: Request, branch_id: int | None = None, status: str | None = None, since: float | None = None,
                until: float | None = None, limit: int = 100):
    u = _user(request)
    ids = _scope(u, branch_id)
    if not ids:
        return {"ok": True, "events": []}
    limit = max(1, min(limit, 500))
    where = ["branch_id IN (" + ",".join(f":b{i}" for i in range(len(ids))) + ")"]
    params: dict = {f"b{i}": v for i, v in enumerate(ids)}
    if status:
        if status not in STATUSES:
            raise HTTPException(400, "holat noto'g'ri")
        where.append("status=:s")
        params["s"] = status
    if since is not None:
        where.append("created_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("created_at <= :until")
        params["until"] = until
    params["lim"] = limit
    rows_ = cdb.rows("SELECT e.uid, e.branch_id, b.name AS branch_name, e.device_id, e.local_id, e.created_at, e.updated_at, "
                     "e.received_at, e.kind, e.score, e.status, e.source_name, e.clip_file, e.thumb_file, e.data "
                     f"FROM events e JOIN branches b ON b.id=e.branch_id WHERE {' AND '.join('e.' + w for w in where)} "
                     "ORDER BY e.created_at DESC LIMIT :lim", params)
    for r in rows_:
        d = cdb.json_load(r.pop("data", None)) or {}
        r["level"] = d.get("level")
        r["reasons"] = [str(x) for x in (d.get("reasons") or [])][:3]
        r["reviewed_by"] = d.get("reviewed_by")
        r["video"] = d.get("job_id") is not None
    return {"ok": True, "events": rows_}


@app.get(f"/api/v{API_VERSION}/events/{{uid}}")
def get_event(request: Request, uid: str):
    u = _user(request)
    ev = cdb.row("SELECT e.*, b.name AS branch_name FROM events e JOIN branches b ON b.id=e.branch_id WHERE e.uid=:u",
                 {"u": uid})
    if not ev or ev["branch_id"] not in cdb.branch_ids_for(u):
        raise HTTPException(404, "hodisa topilmadi")
    ev["data"] = cdb.json_load(ev.get("data"))
    return {"ok": True, "event": ev}


@app.post(f"/api/v{API_VERSION}/events/{{uid}}/review")
async def review_event(request: Request, uid: str):
    """Bulutda tekshirish (✅/❌). Bazada yangilanadi va edge'ga buyruq ketadi — do'kondagi Telegram xabari ham
    yangilanadi (edge keyingi hello'da oladi, bajaradi, ack qiladi)."""
    body = await _json(request)

    def _do() -> dict:
        u = _user(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON obyekt kutilgan")
        status = str(body.get("status") or "")
        if status not in STATUSES:
            raise HTTPException(400, "holat: yangi, tasdiqlangan yoki rad_etilgan")
        note = body.get("note")
        note = str(note)[:2000] if note is not None else None
        try:
            res = service.review(u, uid, status, note, via="Bulut", ip=_ip(request))
        except service.NotFound:
            raise HTTPException(404, "hodisa topilmadi")
        return {"ok": True, "event": res}
    return await run_in_threadpool(_do)


@app.get(f"/api/v{API_VERSION}/branches/{{branch_id}}/metrics")
def branch_metrics(request: Request, branch_id: int, hours: float = 24, source_id: int | None = None):
    """Soatlik yig'indi: har kamera uchun odamlar (o'rtacha/eng ko'p), AI tezligi, faol ulushi, ishlagan vaqt,
    signallar; tizim (source_id=0): CPU, RAM. JSON ichidan o'qiladi (SQLite/PostgreSQL bir xil)."""
    u = _user(request)
    _scope(u, branch_id)
    hours = max(0.1, min(hours, 24 * 90))
    since = time.time() - hours * 3600
    params: dict = {"b": branch_id, "s": since}
    sql = "SELECT minute, source_id, data FROM metrics_minute WHERE branch_id=:b AND minute >= :s"
    if source_id is not None:
        sql += " AND source_id=:sid"
        params["sid"] = source_id
    acc: dict[tuple[int, int], dict] = {}
    for r in cdb.rows(sql + " ORDER BY minute", params):
        d = cdb.json_load(r["data"]) or {}
        k = (int(r["minute"]) // 3600 * 3600, int(r["source_id"]))
        a = acc.setdefault(k, {"hour": k[0], "source_id": k[1], "source_name": d.get("source_name"), "n": 0,
                               "persons_avg": 0.0, "persons_max": 0, "fps_ai": 0.0, "active_pct": 0.0, "up_pct": 0.0,
                               "alerts": 0, "cpu": 0.0, "ram": 0.0, "n_sys": 0, "in": 0, "out": 0})
        a["in"] += int(_num(d.get("in")))           # 1.9.0: sanash chizig'i (xaridorlar)
        a["out"] += int(_num(d.get("out")))
        if k[1] > 0 and d.get("up_pct") is None:
            continue    # 1.9.0: faqat sanash yig'indisi (edge daqiqa o'rtasida to'xtagan) — o'rtachalarni buzmasin
        a["n"] += 1
        for f in ("persons_avg", "fps_ai", "active_pct", "up_pct"):
            a[f] += _num(d.get(f))
        a["persons_max"] = max(a["persons_max"], int(_num(d.get("persons_max"))))
        a["alerts"] += int(_num(d.get("alerts")))
        if d.get("cpu") is not None:
            a["n_sys"] += 1
            a["cpu"] += _num(d.get("cpu"))
            a["ram"] += _num(d.get("ram"))
    out = []
    for a in acc.values():
        n, ns = a.pop("n"), a.pop("n_sys")
        for f in ("persons_avg", "fps_ai", "active_pct", "up_pct"):
            a[f] = round(a[f] / n, 3) if n else None
        a["cpu"] = round(a["cpu"] / ns, 1) if ns else None
        a["ram"] = round(a["ram"] / ns, 1) if ns else None
        out.append(a)
    return {"ok": True, "hours": hours, "rows": sorted(out, key=lambda x: (x["hour"], x["source_id"]))}


@app.get(f"/api/v{API_VERSION}/clips/{{device_id}}/{{name}}")
def get_clip(request: Request, device_id: str, name: str):
    u = _user(request)
    if not DEV_RE.fullmatch(device_id) or not re.fullmatch(r"[A-Za-z0-9_\-]+\.(mp4|jpg)", name):
        raise HTTPException(400)
    b = cdb.row("SELECT id FROM branches WHERE device_id=:d", {"d": device_id})
    if not b:
        raise HTTPException(404)
    _scope(u, b["id"])
    p = CLIPS_DIR / device_id / name
    if not p.is_file():
        raise HTTPException(404)
    if name.endswith(".mp4"):
        _audit_clip(request, u, f"{device_id}/{name}")
    return FileResponse(p, media_type="video/mp4" if name.endswith(".mp4") else "image/jpeg")


_clip_seen: dict[tuple[int, str], float] = {}
_clip_seen_lock = threading.Lock()


def _audit_clip(request: Request, u: dict, clip: str) -> None:
    """Kim qaysi klipni ko'rdi — jurnalga (video Range so'rovlari ko'p: bitta xodim+klip 10 daqiqada bir yozuv)."""
    now = time.time()
    with _clip_seen_lock:
        if now - _clip_seen.get((u["id"], clip), 0) < 600:
            return
        _clip_seen[(u["id"], clip)] = now
        if len(_clip_seen) > 20000:
            for k in [k for k, v in _clip_seen.items() if now - v > 600]:
                _clip_seen.pop(k, None)
    cdb.audit("clip_view", u, clip, ip=_ip(request))


# ----------------------------------------------------------------------------- bildirishnomalar (o'zi uchun)
@app.get(f"/api/v{API_VERSION}/me/notifications")
def my_notifications(request: Request):
    u = _user(request)
    return {"ok": True, "prefs": notify.get_prefs(u), "defaults": notify.DEFAULT_PREFS.get(u["role"]),
            "targets": [notify.public_target(t) for t in notify.targets(u["id"], enabled_only=False)],
            "channels": notify.channels(), "email": u["email"]}


@app.put(f"/api/v{API_VERSION}/me/notifications")
async def set_my_notifications(request: Request):
    body = await _json(request)

    def _do() -> dict:
        u = _user(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON obyekt kutilgan")
        try:
            prefs = notify.set_prefs(u, body.get("prefs") if isinstance(body.get("prefs"), dict) else body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "prefs": prefs}
    return await run_in_threadpool(_do)


@app.post(f"/api/v{API_VERSION}/me/telegram/link")
def telegram_link(request: Request):
    """Bulut botiga ulanish havolasi (bir martalik, 10 daqiqa): t.me/<bot>?start=<kod>."""
    u = _user(request)
    if not notify.channels()["telegram"]["enabled"]:
        raise HTTPException(400, "Bulut Telegram boti sozlanmagan (CLOUD_TELEGRAM_BOT_TOKEN)")
    return {"ok": True, **notify.new_link(u)}


@app.post(f"/api/v{API_VERSION}/me/email")
async def my_email(request: Request):
    """Email bildirishnomasi o'z login manziliga (yoqish/o'chirish)."""
    body = await _json(request)
    return await run_in_threadpool(_my_email, request, body)


def _my_email(request: Request, body) -> dict:
    u = _user(request)
    enabled = bool(body.get("enabled", True)) if isinstance(body, dict) else True
    if not EMAIL_RE.fullmatch(u["email"]):
        raise HTTPException(400, "login email manzili noto'g'ri")
    if enabled:
        try:
            tid = notify.add_target(u["id"], "email", u["email"], None)
        except notify.TooMany as e:
            raise HTTPException(400, str(e))
    else:
        cdb.execute("UPDATE notify_targets SET enabled=:f WHERE user_id=:u AND channel='email'", {"f": False, "u": u["id"]})
        tid = None
    return {"ok": True, "target_id": tid, "enabled": enabled}


@app.get(f"/api/v{API_VERSION}/push/key")
def push_key(request: Request):
    _user(request)
    key = notify.vapid_public_key()
    if not key:
        raise HTTPException(404, notify.state.get("push_error") or "push o'chirilgan")
    return {"ok": True, "public_key": key}


@app.post(f"/api/v{API_VERSION}/me/push")
async def my_push(request: Request):
    """Brauzer push obunasi (PushSubscription.toJSON())."""
    body = await _json(request)
    return await run_in_threadpool(_my_push, request, body)


def _my_push(request: Request, body) -> dict:
    u = _user(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON obyekt kutilgan")
    endpoint = str(body.get("endpoint") or "")
    keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
    if len(endpoint) > 600 or not notify.push_endpoint_ok(endpoint):
        raise HTTPException(400, "push manzili qabul qilinmadi")
    if not (isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str)
            and len(keys["p256dh"]) < 200 and len(keys["auth"]) < 100):
        raise HTTPException(400, "push kalitlari noto'g'ri")
    try:
        tid = notify.add_target(u["id"], "push", endpoint, {"keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                                                             "ua": request.headers.get("user-agent", "")[:120]})
    except notify.TooMany as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "target_id": tid}


@app.post(f"/api/v{API_VERSION}/me/targets/{{target_id}}/delete")
def delete_target(request: Request, target_id: int):
    u = _user(request)
    n = cdb.execute("DELETE FROM notify_targets WHERE id=:t AND user_id=:u", {"t": target_id, "u": u["id"]})
    if not n:
        raise HTTPException(404, "manzil topilmadi")
    return {"ok": True}


_test_sent: dict[int, float] = {}


@app.post(f"/api/v{API_VERSION}/me/notifications/test")
def test_notification(request: Request):
    u = _user(request)
    now = time.time()
    if now - _test_sent.get(u["id"], 0.0) < notify.TEST_EVERY:
        raise HTTPException(429, f"sinov xabari {int(notify.TEST_EVERY)} soniyada bir marta")
    _test_sent[u["id"]] = now
    if len(_test_sent) > 5000:
        for k in [k for k, v in _test_sent.items() if now - v > notify.TEST_EVERY]:
            _test_sent.pop(k, None)
    ts = notify.targets(u["id"])
    if not ts:
        raise HTTPException(400, "Hali manzil yo'q: Telegram, email yoki brauzer push'ni ulang")
    for t in ts:
        notify.enqueue(u["id"], t, "test", f"user:{u['id']}", {"t_event": time.time()})
    return {"ok": True, "count": len(ts)}


@app.get(f"/api/v{API_VERSION}/notify/stats")
def notify_stats(request: Request, hours: float = 24):
    u = _user(request)
    _require(u, "owner", "manager")
    return {"ok": True, "hours": hours, "channels": notify.stats(u["company_id"], max(0.1, min(hours, 24 * 30)))}


# ----------------------------------------------------------------------------- jonli oqim (SSE)
@app.get(f"/api/v{API_VERSION}/stream")
async def stream(request: Request):
    """Panel uchun: yangi hodisa, holat o'zgarishi, filial onlayn/offlayn. Har 15 s ping, har 60 s ruxsat qayta tekshiriladi.
    Bitta xodimda ko'pi bilan SSE_PER_USER ta ochiq oqim (eng eskisi yopiladi), serverda — SSE_TOTAL."""
    u = await run_in_threadpool(_user, request)
    tok = _session_token(request)
    scope = set(await run_in_threadpool(cdb.branch_ids_for, u))
    if hub.count() >= SSE_TOTAL:
        raise HTTPException(503, "jonli ulanishlar soni chegarada — keyinroq")

    async def gen():
        q = hub.subscribe(u["id"], scope, per_user=SSE_PER_USER, session=cdb.sha256(tok)[:16] if tok else "",
                          per_session=SSE_PER_SESSION)
        try:
            yield "retry: 3000\n\n"
            last = time.time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    if msg is None:         # shu xodim yangi oqim ochdi, bu eng eskisi — yopiladi
                        # Brauzer o'zi qayta ulanmasin (aks holda tablar bir-birini navbat bilan uzib turadi):
                        # panel bu belgini olib, tab qayta ko'ringanda (visibilitychange) ulanadi
                        yield "event: evicted\ndata: {}\n\n"
                        break
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                if time.time() - last > 60:
                    last = time.time()
                    uu = await run_in_threadpool(cdb.user_by_token, tok)
                    if not uu:
                        yield "event: logout\ndata: {}\n\n"
                        break
                    hub.update_scope(q, set(await run_in_threadpool(cdb.branch_ids_for, uu)))
        finally:
            hub.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------------- superadmin (barcha kompaniyalar)
def _auth_admin(request: Request) -> None:
    if _fail_now(request):
        raise HTTPException(503)
    if _state["admin"] is None:
        _state["admin"] = admin_token()
    adm = _state["admin"]
    if not adm:
        raise HTTPException(403, "CLOUD_ADMIN_TOKEN berilmagan — admin API o'chirilgan")
    given = _bearer(request)
    if not given or not hmac.compare_digest(given.encode(), adm.encode()):
        raise HTTPException(401, "token xato")


@app.get(f"/api/v{API_VERSION}/admin/summary")
def admin_summary(request: Request):
    _auth_admin(request)
    devs = cdb.rows("SELECT b.*, c.name AS company FROM branches b JOIN companies c ON c.id=b.company_id "
                    "ORDER BY b.last_seen DESC")
    for d in devs:
        d.pop("token_hash", None)
        d["branch_id"] = d["id"]
        d["id"] = d["device_id"]           # eski ko'rinish: id = qurilma identifikatori
        d["cameras"] = cdb.json_load(d.get("cameras")) or []
        d["events"] = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b", {"b": d["branch_id"]}) or 0
        d["clips"] = cdb.scalar("SELECT COUNT(*) FROM events WHERE branch_id=:b AND clip_file IS NOT NULL",
                                {"b": d["branch_id"]}) or 0
        d["metrics"] = cdb.scalar("SELECT COUNT(*) FROM metrics_minute WHERE branch_id=:b", {"b": d["branch_id"]}) or 0
        d["health"] = cdb.scalar("SELECT COUNT(*) FROM health_log WHERE branch_id=:b", {"b": d["branch_id"]}) or 0
    return {"ok": True, "devices": devs, "server_time": time.time(), "db": cdb.engine().dialect.name}


@app.get(f"/api/v{API_VERSION}/admin/events")
def admin_events(request: Request, device_id: str | None = None, limit: int = 100):
    _auth_admin(request)
    limit = max(1, min(limit, 1000))
    params: dict = {"lim": limit}
    sql = "SELECT uid, device_id, local_id, created_at, updated_at, kind, score, status, source_name, clip_file, thumb_file FROM events"
    if device_id:
        sql += " WHERE device_id=:d"
        params["d"] = device_id
    return {"ok": True, "events": cdb.rows(sql + " ORDER BY created_at DESC LIMIT :lim", params)}


@app.get(f"/api/v{API_VERSION}/admin/clips/{{device_id}}/{{name}}")
def admin_clip(request: Request, device_id: str, name: str):
    _auth_admin(request)
    if not DEV_RE.fullmatch(device_id) or not re.fullmatch(r"[A-Za-z0-9_\-]+\.(mp4|jpg)", name):
        raise HTTPException(400)
    p = CLIPS_DIR / device_id / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp4" if name.endswith(".mp4") else "image/jpeg")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "hushyor-cloud", "api": API_VERSION, "db": cdb.engine().dialect.name}


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Mijozga ichki tafsilot (stack, SQL) chiqmaydi; jurnalga — to'liq
    log.error("Kutilmagan xato: %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"ok": False, "detail": f"server xatosi: {exc.__class__.__name__}"}, status_code=500)


_SCRIPT_RE = re.compile(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_csp_cache: dict[str, tuple[tuple[int, int], str]] = {}


def _panel_csp(file: str) -> str:
    """HTML sahifadagi ichki skriptlarning sha256 xeshlari bilan CSP (fayl o'zgarmaguncha keshda)."""
    try:
        st = os.stat(file)
    except OSError:
        return PANEL_CSP
    key = (st.st_mtime_ns, st.st_size)
    hit = _csp_cache.get(file)
    if hit and hit[0] == key:
        return hit[1]
    with open(file, "rb") as f:
        body = f.read()
    hashes = sorted({"'sha256-" + base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode() + "'"
                     for m in _SCRIPT_RE.finditer(body)})
    csp = f"default-src 'self'; script-src 'self' {' '.join(hashes)}; {_CSP_REST}".replace("  ", " ")
    _csp_cache[file] = (key, csp)
    return csp


class _PanelFiles(StaticFiles):
    """Panelning statik sahifalari: /events/ → events/index.html; topilmasa 404.html. HTML — xeshli CSP bilan
    (304 Not Modified javobida ham: brauzer keshdagi sahifani shu javob sarlavhalari bilan ishlatadi)."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".html"):
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["Content-Security-Policy"] = _panel_csp(str(full_path))
        return resp

    async def get_response(self, path, scope):
        # Starlette 404.html ni file_response'siz beradi — unga ham shu sahifaning xeshli CSP'si (aks holda ichki
        # skriptlar bloklanib, "topilmadi" sahifasi ishlamaydi)
        resp = await super().get_response(path, scope)
        p = str(getattr(resp, "path", "") or "")
        if resp.status_code == 404 and p.endswith(".html"):
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["Content-Security-Policy"] = _panel_csp(p)
        return resp


if PANEL_DIR.is_dir():
    app.mount("/", _PanelFiles(directory=str(PANEL_DIR), html=True), name="panel")
else:
    @app.get("/")
    def _no_panel():
        return JSONResponse({"ok": True, "detail": "Panel build qilinmagan: cloud/panel da `npm ci && npm run build`"})


def bootstrap_owner(email: str, password: str, company: str) -> dict:
    """Birinchi kompaniya va egasi (buyruq qatoridan)."""
    if cdb.row("SELECT id FROM users WHERE email=:e", {"e": email.strip().lower()}):
        raise ValueError("bu email allaqachon bor")
    cid = cdb.create_company(company)
    uid = cdb.create_user(cid, email, password, "Egasi", "owner")
    return {"company_id": cid, "user_id": uid}


def setup_logging() -> Path:
    """Jurnal: cloud/data/logs/cloud.log (5 MB × 5, aylanma) + konsol. 72 soatlik sinov va VPS'da xatolarni ko'rish uchun."""
    logs = DATA_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "cloud.log"
    root = logging.getLogger("hushyor")
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)
        root.setLevel(logging.INFO)
    return path


def _ask_password(prompt: str = "Parol: ") -> str:
    import getpass

    p1 = getpass.getpass(prompt)
    p2 = getpass.getpass("Yana bir marta: ")
    if p1 != p2:
        raise ValueError("parollar mos emas")
    return p1


def security_warnings(host: str) -> list[str]:
    """Ishga tushishdagi xavfsizlik eslatmalari (OWASP A05 — noto'g'ri sozlama)."""
    out = []
    if _state["tokens"]:
        out.append("CLOUD_EDGE_TOKENS (eski umumiy token) yoqilgan — har do'konga panelda filial tokeni bering va "
                   "bu qatorni .env dan o'chiring")
    adm = _state["admin"] or ""
    if adm and len(adm) < 32:
        out.append("CLOUD_ADMIN_TOKEN qisqa (< 32 belgi): python -c \"import secrets; print(secrets.token_hex(32))\"")
    pub = notify.cfg.public_url
    if pub.startswith("http://") and not re.search(r"//(localhost|127\.|10\.|192\.168\.)", pub):
        out.append(f"CLOUD_PUBLIC_URL={pub} — internetda HTTPS'siz: parol va sessiya ochiq ketadi (nginx + certbot)")
    if host not in ("127.0.0.1", "localhost", "::1") and not pub:
        out.append(f"Server {host} da tarmoqqa ochiq. VPS'da --host 127.0.0.1 va oldida nginx (HTTPS) ishlating "
                   "(docs/DEPLOY.md)")
    return out


def main() -> None:
    import sys

    import uvicorn

    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe (Windows avtomatik start, oynasiz): chiqishlar faylga
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        con = open(DATA_DIR / "logs" / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or con
        sys.stderr = sys.stderr or con

    ap = argparse.ArgumentParser(description="Hushyor AI bulut serveri")
    ap.add_argument("--host", default=cdb.env("CLOUD_HOST", "0.0.0.0") or "0.0.0.0")
    ap.add_argument("--port", type=int, default=int(cdb.env("CLOUD_PORT", "9000") or 9000))
    ap.add_argument("--init-owner", nargs=3, metavar=("EMAIL", "PAROL", "KOMPANIYA"),
                    help="birinchi kompaniya va egasini yaratish, keyin chiqish (PAROL o'rniga - : so'rab oladi)")
    ap.add_argument("--set-password", metavar="EMAIL",
                    help="foydalanuvchiga yangi parol (egasi parolni unutsa); parol so'rab olinadi")
    a = ap.parse_args()
    info = init_db()
    if a.init_owner:
        email, pw, comp = a.init_owner
        try:
            if pw == "-":
                pw = _ask_password()
            r = bootstrap_owner(email, pw, comp)
            print(f"Kompaniya #{r['company_id']} va egasi #{r['user_id']} yaratildi ({email})")
        except ValueError as e:
            print("Xato:", e)
        return
    if a.set_password:
        email = a.set_password.strip().lower()
        u = cdb.row("SELECT id, company_id, email FROM users WHERE email=:e", {"e": email})
        if not u:
            print("Xato: bunday foydalanuvchi yo'q")
            return
        try:
            pw = os.environ.get("HUSHYOR_NEW_PASSWORD") or _ask_password("Yangi parol: ")
            bad = cdb.password_problem(pw, email)
            if bad:
                raise ValueError(bad)
        except ValueError as e:
            print("Xato:", e)
            return
        n = cdb.set_password(u["id"], pw)
        cdb.execute("UPDATE users SET active=:t WHERE id=:u", {"t": True, "u": u["id"]})
        cdb.audit("password_reset", None, email, {"via": "cli", "closed_sessions": n}, ip="cli",
                  company_id=u["company_id"], actor="server (buyruq qatori)")
        print(f"Parol almashtirildi: {email} (yopilgan sessiyalar: {n})")
        return
    _state["tokens"], _state["admin"] = tokens(), admin_token()
    log_path = setup_logging()
    notify.configure()
    _state["notify_configured"] = True
    print(f"Hushyor AI bulut {VERSION}: http://{a.host}:{a.port}  (baza: {info['dialect']}"
          f"{', TimescaleDB' if info.get('timescale') else ''}; fayllar: {DATA_DIR}; jurnal: {log_path})")
    if info.get("timescale_error"):
        print("Eslatma: TimescaleDB ulanmadi (oddiy jadval ishlatiladi):", info["timescale_error"][:120])
    if info.get("legacy_db"):
        print("DIQQAT: eski (1.5.0) bulut bazasi topildi va chetga olindi:", info["legacy_db"])
    if not cdb.row("SELECT id FROM users LIMIT 1"):
        print("Eslatma: foydalanuvchi yo'q — birinchi egasini yarating: python -m cloud.server --init-owner EMAIL - \"Kompaniya\"")
    if not _state["admin"]:
        print("Eslatma: CLOUD_ADMIN_TOKEN berilmagan — superadmin API o'chirilgan")
    for w in security_warnings(a.host):
        print("XAVFSIZLIK:", w)
        log.warning("Xavfsizlik eslatmasi: %s", w)
    # Teskari proksi (nginx) ortida: X-Forwarded-For/Proto faqat ishonchli manzildan qabul qilinadi
    trusted = cdb.env("CLOUD_TRUSTED_PROXIES") or "127.0.0.1"
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", access_log=False, proxy_headers=True,
                forwarded_allow_ips=trusted, server_header=False, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
