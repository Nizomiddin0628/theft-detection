"""Web panel (FastAPI): sahifalar va API."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import cv2
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import db, metrics, sync
from .ai.manager import archive_feedback, delete_event_files, get_manager
from .config import (ADMIN_PASSWORD, ADMIN_USER, ALLOWED_HOSTS, APP_NAME, APP_VERSION, BASE_DIR, CLIPS_DIR,
                     DATA_DIR, DEMO, EVAL_DIR, HOST, MODELS_DIR, PUBLIC_URL, SECRET_KEY, SENSITIVITY_PRESETS,
                     UPLOADS_DIR, env)
from .logutil import MEMORY_LOG, get_logger, setup_logging
from . import evaluation, flow_web, pilot_web, queue_web, safety_web, visits
from .ai import zones as zones_mod
from .ai.safety import SAFETY_KINDS
# 2.2.0: o'g'irlik aniqligi (precision, model javoblari) xavfsizlik signallarisiz hisoblanadi
NOT_SAFETY = "(kind IS NULL OR kind NOT IN (" + ",".join(f"'{k}'" for k in SAFETY_KINDS) + "))"
# 3.2.0: do'kondagi jonli kamera hodisasi (yuklangan video tahlili va "jonli rejim"da sinalgan video emas) — bosh sahifa
# va menyudagi hisoblagichlar faqat shularni sanaydi (demo rejimida namuna videolar kamera o'rnida)
LIVE_EV = ("job_id IS NULL" if DEMO else
           "job_id IS NULL AND (source_id IS NULL OR source_id NOT IN (SELECT id FROM sources WHERE kind='file'))")
from .telegram_bot import KIND_UZ, get_bot, latency_stats, susp_info

log = get_logger("web")
SHUTDOWN = threading.Event()      # server to'xtamoqda — jonli oqimlar (SSE, MJPEG) yopiladi
APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    _startup()
    try:
        yield
    finally:
        _shutdown()


app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(visits.router)        # 1.9.0: odamlarni sanash — /api/counts/*, /api/export/counts.csv
app.include_router(queue_web.router)     # 2.0.0: kassa navbati — /api/queues/*, /api/export/queues.csv
app.include_router(flow_web.router)      # 2.1.0: oqim va heatmap — /api/flow/*, /media/plans/*, /api/export/flow.csv
app.include_router(safety_web.router)    # 2.2.0: xavfsizlik va salomatlik — /api/safety/*
app.include_router(pilot_web.router)     # 2.5.0: pilot — /api/pilot/*, /api/diagnostics.zip

PUBLIC_PATHS = ("/login", "/static", "/favicon.ico", "/healthz")
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".3gp", ".ts"}

NAV = [
    ("dashboard", "/", "Boshqaruv paneli", "grid"),
    ("live", "/live", "Jonli kuzatuv", "eye"),
    ("upload", "/upload", "Video tahlili", "upload"),
    ("events", "/events", "Hodisalar", "alert"),
    ("safety", "/xavfsizlik", "Xavfsizlik", "shield"),
    ("sources", "/sources", "Kameralar", "camera"),
    ("model", "/model", "AI model", "cpu"),
    ("telegram", "/telegram", "Telegram bot", "send"),
    ("reports", "/reports", "Hisobotlar", "chart"),
    ("visits", "/visits", "Tashriflar", "users"),
    ("queues", "/navbat", "Navbat", "cart"),
    ("flow", "/oqim", "Oqim", "map"),
    ("eval", "/eval", "Aniqlik sinovi", "target"),
    ("pilot", "/pilot", "Pilot", "check"),
    ("settings", "/settings", "Sozlamalar", "sliders"),
    ("system", "/system", "Tizim holati", "activity"),
    ("help", "/help", "Yo'riqnoma", "book"),
]

train_state = {"running": False, "logs": [], "result": None, "error": None, "started": 0.0}


# ----------------------------------------------------------------------------- hayot sikli
def _startup() -> None:
    setup_logging()
    db.init_db()
    try:
        from .ml.model import migrate_pointer

        migrate_pointer()                 # 2.3.0: tanlangan model ko'rsatkichi git faylidan data/ ga
    except Exception as e:  # noqa: BLE001
        log.warning("Model ko'rsatkichini ko'chirib bo'lmadi: %s", e)
    # Dastur to'xtaganda chala qolgan baholash va video tayyorlash
    db.execute("UPDATE eval_runs SET status='toxtatildi' WHERE status IN ('ishlanmoqda', 'navbatda')")
    for v in db.rows("SELECT id, path FROM eval_videos WHERE play_status='tayyorlanmoqda'"):
        threading.Thread(target=evaluation.make_playable, args=(v["id"], Path(v["path"])), daemon=True).start()
    if not db.row("SELECT id FROM sources WHERE kind='webcam'"):
        db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                   ("Kompyuter kamerasi", "webcam", "0", 0, 1, time.time()))
    mgr = get_manager()
    bot = get_bot()
    bot.manager = mgr
    mgr.notifier = bot
    from .ai import queues

    queues.set_notifier(bot)                # 2.0.0: navbat qoidalari signallari
    bot.start()
    try:
        _reconcile_telegram(mgr, bot)
    except Exception:  # noqa: BLE001
        log.exception("Oldingi ishga tushishdan qolgan kliplarni tekshirib bo'lmadi")
    # 3.0.0: tahlil paytida to'xtab qolgan bulut video sinovlari qaytadan navbatga (faqat shu yerda — asosiy dasturda)
    try:
        from . import cloud_tests

        cloud_tests.recover()
    except Exception:  # noqa: BLE001
        log.exception("Bulut video sinovlarini tiklab bo'lmadi")
    # Edge agent: bulut navbati (CLOUD_URL berilgan bo'lsa) va daqiqalik ko'rsatkichlar
    sync.install_hooks()
    sync.start_worker(mgr)
    global _metrics
    if _metrics is None or not _metrics.is_alive():
        _metrics = metrics.MetricsSampler(mgr)
        _metrics.start()
    threading.Thread(target=_warmup, daemon=True).start()
    from . import reports

    reports.start(bot)                      # 2.4.0: kunlik/haftalik hisobot (Telegram)
    mgr.autostart()
    if default_password_warning():
        log.warning("Panel standart parol (admin123) bilan tarmoqqa ochiq (HOST=%s). .env da ADMIN_PASSWORD ni "
                    "o'zgartiring yoki HOST=127.0.0.1 qiling", HOST)
    log.info("%s %s ishga tushdi", APP_NAME, APP_VERSION)


def _reconcile_telegram(mgr, bot) -> None:
    """3.2.0: oldingi ishga tushishdan qolganlar: klipi tayyorlanmagan hodisalar — "klip yaratilmadi" (Telegram
    xabaridagi "tayyorlanmoqda" yangilanadi); klipi yozilgan-u, Telegram xabariga qo'shilmay qolganlar — qo'shiladi."""
    for ev_id in mgr.reconcile_pending_clips():
        if db.scalar("SELECT 1 FROM tg_messages WHERE event_id=?", (ev_id,)):
            bot.refresh_event(ev_id)
    rows = db.rows("SELECT DISTINCT e.id FROM events e JOIN tg_messages m ON m.event_id=e.id WHERE e.clip_path IS NOT "
                   "NULL AND m.kind IN ('photo', 'text') AND e.created_at > ?", (time.time() - 6 * 3600,))
    for r in rows:
        bot.notify_clip(int(r["id"]))


def _warmup() -> None:
    try:
        from .ai.engine import Engine
        from .ml.model import BehaviorModel

        eng = Engine.get()
        _ensure_model_set(eng)
        if BehaviorModel.get() is None:
            # Model fayli yo'q yoki boshqa scikit-learn versiyasida saqlangan — shu kompyuterda qayta o'qitamiz
            log.warning("Xulq modeli topilmadi/yuklanmadi — sintetik ma'lumotda avtomatik o'qitilmoqda (~1 daqiqa)")
            from .ml.train import train

            train_state.update(running=True, logs=[], result=None, error=None, started=time.time())
            try:
                res = train(n_synthetic=3000, use_feedback=True, seed=7,
                            log_fn=lambda m: train_state["logs"].append(f"{time.strftime('%H:%M:%S')}  {m}"))
                train_state["result"] = {"metrics": res["metrics"], "path": Path(res["path"]).name}
            finally:
                train_state["running"] = False
    except Exception as e:
        log.error("Modellarni yuklashda xato: %s", e)


def _ensure_model_set(eng) -> None:
    """2.7.0: tanlangan to'plam fayli yo'q (masalan, open-m tanlangan, MoveNet Thunder o'chirilgan yoki buzuq bo'lib
    chetga olingan) — fonda GitHub'dan yuklab olinadi va modellar qayta yuklanadi; shu orada zaxira to'plam ishlaydi
    (Engine). Internet bo'lmasa — jurnalda va AI model sahifasida xato, zaxira bilan ishlash davom etadi."""
    from .ai import model_zoo
    from .ai.engine import Engine

    want = db.get_setting("model_set") or model_zoo.DEFAULT_SET
    if DEMO or os.environ.get("HUSHYOR_MODEL_SET") or want not in model_zoo.MODEL_SETS or eng.model_set == want:
        return                                   # (sinovda to'plam muhit o'zgaruvchisi bilan qotirilgan)
    miss = model_zoo.missing(want)
    if not miss or any("url" not in model_zoo.FILES[k] for k in miss):
        return
    log.warning("%s fayllari yo'q (%s) — fonda yuklab olinmoqda, hozircha %s ishlaydi", want, ", ".join(miss),
                eng.model_set)

    def work():
        model_zoo.ensure_set(want)
        new = Engine.reload(model_set=want)
        log.info("Model to'plami tayyor: %s", new.model_set)
        return new.info()
    try:
        _models_bg("tanlangan to'plam fayli yuklab olinmoqda", work)
    except HTTPException:
        pass                                     # boshqa model vazifasi ketmoqda


_metrics = None


def _shutdown() -> None:
    get_manager().stop_all()
    get_bot().stop()
    try:
        sync.get_worker().stop()
        if _metrics is not None:
            _metrics.stop()
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- yordamchilar
def _host_of(url_or_host: str) -> str:
    return url_or_host.split("://", 1)[-1].split("/", 1)[0].strip().lower()


def _cross_site(request: Request) -> bool:
    """Holatni o'zgartiruvchi so'rov boshqa saytdan kelganmi (CSRF).

    Avval Sec-Fetch-Site (brauzer o'zi hisoblaydi, teskari proksi Host'ni o'zgartirsa ham to'g'ri), u bo'lmasa
    Origin ≈ Host / X-Forwarded-Host / PUBLIC_URL. Ikkalasi ham yo'q so'rov (buyruq qatori, sinov) o'tkaziladi.
    """
    sfs = request.headers.get("sec-fetch-site", "").lower()
    if sfs in ("same-origin", "none"):
        return False
    if sfs == "cross-site":
        return True
    # "same-site" (1.8.0): shu kompyuterning boshqa porti yoki qo'shni subdomen — avtomatik ishonchli emas,
    # Origin tekshiriladi (masalan, shu kompyuterdagi boshqa dastur sahifasi panelga buyruq yubora olmasin)
    origin = request.headers.get("origin")
    if origin == "null" or (sfs == "same-site" and not origin):
        return True
    if origin:
        allowed = {_host_of(request.headers.get("host") or ""), _host_of(request.headers.get("x-forwarded-host") or "")}
        if PUBLIC_URL:   # teskari proksi orqasida Host boshqacha bo'lishi mumkin
            allowed.add(_host_of(PUBLIC_URL))
        if _host_of(origin) not in allowed:
            return True
    return False


SECURITY_HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "same-origin",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()"}
# 1.8.0: sahifalar uchun CSP. Shablonlarda ichki skript va onclick'lar bor — 'unsafe-inline' hozircha qoladi
# (qoldiq xavf: docs/SECURITY.md), lekin tashqi manbadan skript, <object>, <base> almashtirish, boshqa saytga forma
# yuborish va panelni boshqa sayt ichiga joylash taqiqlanadi
EDGE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


_HOSTS_OK: set[str] = set()


def _allowed_hosts() -> set[str]:
    if not _HOSTS_OK:
        hs = {"localhost", "testserver", "testclient"}
        try:
            hn = socket.gethostname().lower()
            hs |= {hn, f"{hn}.local", socket.getfqdn().lower()}
        except OSError:
            pass
        if PUBLIC_URL:
            hs.add(_bare_host(_host_of(PUBLIC_URL)))
        hs |= {_bare_host(h) for h in ALLOWED_HOSTS}       # "panel.example.com:8443" ham to'g'ri tushunilsin
        _HOSTS_OK.update(h for h in hs if h)
    return _HOSTS_OK


_PRIVATE_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def _bare_host(h: str) -> str:
    h = h.strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _host_allowed(request: Request) -> bool:
    """3.2.0: DNS rebinding himoyasi. Begona sayt o'z domenini 127.0.0.1 ga yo'naltirib, do'kon kompyuteridagi
    brauzer orqali panelga "o'zi" bo'lib kira olmasin: panel faqat IP manzil, kompyuter nomi (nuqtasiz yoki .local),
    PUBLIC_URL yoki .env dagi ALLOWED_HOSTS bilan ochiladi."""
    raw = request.headers.get("host") or ""
    if not raw:
        return True
    host = _bare_host(raw)
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # nuqtasiz nom va faqat ichki tarmoqda bo'ladigan qo'shimchalar (router DNS: shop-pc.lan, .home ...) — ularni
    # tashqi sayt o'z domeni qilib ro'yxatdan o'tkaza olmaydi
    if "." not in host or host.endswith(_PRIVATE_SUFFIXES):
        return True
    return host in _allowed_hosts() or "*" in ALLOWED_HOSTS


def _session_fp() -> str:
    """Joriy login/parol izi: .env da parol (yoki login) almashsa, eski sessiyalar darhol yaroqsiz."""
    return hmac.new(SECRET_KEY.encode(), f"{ADMIN_USER}\0{ADMIN_PASSWORD}".encode(), hashlib.sha256).hexdigest()[:20]


SESSION_MAX_AGE = 14 * 24 * 3600
_sid_cache: dict[str, float] = {}          # sid -> oxirgi tekshiruv (bazaga har so'rovda bormaslik uchun)
_sid_lock = threading.Lock()


def _sid_valid(sid: str) -> bool:
    if not sid:
        return False
    now = time.time()
    with _sid_lock:
        t = _sid_cache.get(sid)
        if t is not None and now - t < 60:
            return True
    try:
        row = db.row("SELECT created_at, last_seen FROM web_sessions WHERE sid=?", (sid,))
    except Exception:  # noqa: BLE001 — baza band bo'lsa panel yopilib qolmasin
        return t is not None
    if not row or now - float(row["created_at"] or 0) > SESSION_MAX_AGE:
        with _sid_lock:
            _sid_cache.pop(sid, None)
        return False
    if now - float(row["last_seen"] or 0) > 300:
        try:
            db.execute("UPDATE web_sessions SET last_seen=? WHERE sid=?", (now, sid))
        except Exception:  # noqa: BLE001
            pass
    with _sid_lock:
        _sid_cache[sid] = now
    return True


def _session_ok(request: Request) -> bool:
    s = request.session
    if not s.get("user"):
        return False
    if s.get("fp") != _session_fp() or not _sid_valid(str(s.get("sid") or "")):
        s.clear()                          # eski (3.2.0 dan oldingi), chiqilgan yoki parol almashgan sessiya
        return False
    return True


def _session_start(request: Request, username: str) -> None:
    sid = secrets.token_urlsafe(18)
    now = time.time()
    try:
        db.execute("DELETE FROM web_sessions WHERE created_at < ?", (now - SESSION_MAX_AGE,))
        db.execute("INSERT INTO web_sessions(sid, created_at, last_seen) VALUES(?,?,?)", (sid, now, now))
    except Exception:  # noqa: BLE001
        log.exception("Sessiyani yozib bo'lmadi")
    request.session.clear()                # oldingi sessiyadagi hech narsa yangi kirishga o'tmasin
    request.session.update({"user": username, "sid": sid, "fp": _session_fp()})
    with _sid_lock:
        _sid_cache[sid] = now


def _session_end(request: Request) -> None:
    sid = str(request.session.get("sid") or "")
    if sid:
        try:
            db.execute("DELETE FROM web_sessions WHERE sid=?", (sid,))
        except Exception:  # noqa: BLE001
            log.exception("Sessiyani o'chirib bo'lmadi")
        with _sid_lock:
            _sid_cache.pop(sid, None)
    request.session.clear()


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    if not _host_allowed(request):
        bad = _bare_host(request.headers.get("host") or "")[:100]
        return Response(f"Noto'g'ri manzil (Host: {bad}). Panelni kompyuter IP manzili yoki nomi bilan oching. Domen "
                        f"(tunnel, proksi) orqali ochish kerak bo'lsa — .env ga ALLOWED_HOSTS={bad} (yoki PUBLIC_URL) "
                        "yozib, dasturni qayta ishga tushiring.", status_code=400,
                        media_type="text/plain; charset=utf-8")
    if not path.startswith(PUBLIC_PATHS) and not await run_in_threadpool(_session_ok, request):
        if path.startswith(("/api", "/stream", "/media")):
            return JSONResponse({"detail": "Kirish talab qilinadi"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and _cross_site(request):
        return JSONResponse({"detail": "Boshqa saytdan so'rov rad etildi (CSRF)"}, status_code=403)
    lim = {"/api/models/upload": MODEL_UPLOAD_LIMIT, "/api/upload": 2_000_000_000,
           "/api/eval/videos": 4_000_000_000, "/api/safety/videos": 2_000_000_000}.get(path)
    if lim:
        # Juda katta fayl diskga ham yozilmasin (Starlette multipart'ni avval vaqtinchalik faylga oladi — 3.2.0 dan
        # video yuklashlarda ham: chegaradan katta fayl C: diskdagi vaqtinchalik papkani to'ldirmasin)
        try:
            if int(request.headers.get("content-length") or 0) > lim + (1 << 20):
                return JSONResponse({"detail": f"Fayl juda katta (chegara {lim // (1 << 20)} MB)"}, status_code=413)
        except ValueError:
            pass
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers.setdefault("Content-Security-Policy", EDGE_CSP)
    return resp


# Sessiya middleware'i auth middleware'idan keyin qo'shiladi — shunda u tashqi qavat bo'ladi
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="hushyor_session",
                   max_age=SESSION_MAX_AGE, same_site="lax")


def page(request: Request, name: str, active: str, title: str, **ctx) -> HTMLResponse:
    s = db.get_settings()
    subs = db.scalar("SELECT COUNT(*) FROM subscribers WHERE active=1") or 0
    pending = db.scalar(f"SELECT COUNT(*) FROM events WHERE status='yangi' AND {LIVE_EV}") or 0
    base = {"nav": NAV, "active": active, "title": title, "settings": s, "app_name": APP_NAME,
            "version": APP_VERSION, "user": request.session.get("user"), "subs": subs, "pending": pending,
            "presets": SENSITIVITY_PRESETS, "kind_uz": KIND_UZ, "default_pw_warn": default_password_warning(),
            "demo": DEMO}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def day_start(ts: float | None = None) -> float:
    lt = time.localtime(ts or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def event_public(ev: dict) -> dict:
    ev = dict(ev)
    ev["kind_label"] = KIND_UZ.get(ev.get("kind"), ev.get("kind"))
    ev["safety"] = ev.get("kind") in SAFETY_KINDS          # 2.2.0: xavfsizlik signali (ball yo'q, "ayblov" emas)
    ev["clip_url"] = f"/media/clips/{ev['clip_path']}" if ev.get("clip_path") else None
    ev["thumb_url"] = f"/media/clips/{ev['thumb_path']}" if ev.get("thumb_path") else None
    ev["crop_url"] = f"/media/clips/{ev['crop_path']}" if ev.get("crop_path") else None
    # 3.1.0: chizmalarsiz nusxa (Telegram'ga shu ketadi; panelda "Toza / Belgili")
    ev["clip_clean_url"] = f"/media/clips/{ev['clip_clean']}" if ev.get("clip_clean") else None
    ev["thumb_clean_url"] = f"/media/clips/{ev['thumb_clean']}" if ev.get("thumb_clean") else None
    ev["loss_allowed"] = not ev["safety"] and ev.get("job_id") is None     # 3.1.0: zarar summasi (so'm) yoziladimi
    ev["time_str"] = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ev["created_at"]))
    ev["reviewed_str"] = (time.strftime("%d.%m.%Y %H:%M", time.localtime(ev["reviewed_at"]))
                          if ev.get("reviewed_at") else None)
    ev.update(susp_info(ev))
    for k in ("reasons", "breakdown"):
        if isinstance(ev.get(k), str):
            try:
                ev[k] = json.loads(ev[k])
            except ValueError:
                ev[k] = []
    return ev


async def body_data(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            d = await request.json()
        except ValueError:
            return {}
        if d is None:
            return {}
        if not isinstance(d, dict):
            raise HTTPException(400, "So'rov tanasi JSON obyekt bo'lishi kerak")
        return d
    form = await request.form()
    return {k: v for k, v in form.items()}


# ----------------------------------------------------------------------------- kirish
LOGIN_MAX_FAILS = 5          # shuncha xato paroldan keyin shu IP vaqtincha bloklanadi
LOGIN_BLOCK_SEC = 60.0       # blok vaqti (har keyingi blokda ikki baravar, 15 daqiqagacha)
_login_fails: dict[str, dict] = {}        # ip -> {"n": xatolar, "until": blok tugash vaqti, "blocks": nechinchi blok}
_login_lock = threading.Lock()
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "?"


def _is_local(request: Request) -> bool:
    """So'rov shu kompyuterdan kelganmi. Teskari proksi orqali kelgan so'rov (X-Forwarded-* bor) — tashqi deb
    hisoblanadi, hatto proksi 127.0.0.1 dan ulansa ham (proksiga X-Forwarded-For berish README'da)."""
    if any(h.startswith("x-forwarded-") or h == "forwarded" for h in request.headers.keys()):
        return False
    return _client_ip(request) in LOCAL_HOSTS


def _is_self(request: Request) -> bool:
    """Shu kompyuterning o'zidan kelgan so'rov: loopback yoki manba IP = serverning o'z manzili
    (HOST=192.168.x.x bo'lsa nazoratchi shu manzilga ulanadi; tashqi IP hech qachon serverning o'ziga teng emas)."""
    ip = _client_ip(request)
    if ip in LOCAL_HOSTS:
        return True
    host = _host_of(request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    return bool(host) and ip == host


def login_blocked(ip: str) -> float:
    """Shu IP bloklangan bo'lsa — qolgan soniyalar, aks holda 0."""
    with _login_lock:
        rec = _login_fails.get(ip)
        if not rec:
            return 0.0
        left = rec.get("until", 0.0) - time.time()
        return max(0.0, left)


def _login_failed(ip: str) -> None:
    now = time.time()
    with _login_lock:
        rec = _login_fails.setdefault(ip, {"n": 0, "until": 0.0, "blocks": 0, "t": now})
        rec["n"] += 1
        rec["t"] = now
        if rec["n"] >= LOGIN_MAX_FAILS:
            rec["n"] = 0
            rec["blocks"] += 1
            rec["until"] = now + min(LOGIN_BLOCK_SEC * (2 ** (rec["blocks"] - 1)), 900.0)
            log.warning("Panelga %d marta xato parol (%s) — %.0f s bloklandi", LOGIN_MAX_FAILS, ip,
                        rec["until"] - now)
        if len(_login_fails) > 2000:   # xotira cheksiz o'smasin: 1 soatdan beri tinch yozuvlar o'chiriladi
            for k in [k for k, r in _login_fails.items() if r["until"] < now and now - r.get("t", 0) > 3600]:
                _login_fails.pop(k, None)


def _login_ok(ip: str) -> None:
    with _login_lock:
        _login_fails.pop(ip, None)


def safe_next(nxt: str) -> str:
    """Kirishdan keyingi manzil faqat shu panel ichida (ochiq yo'naltirish yo'q): brauzerlar "/\\evil.com" ni
    "//evil.com" deb tushunadi, shuning uchun teskari chiziq va boshqaruv belgilari ham rad etiladi (1.8.0)."""
    if (not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in nxt)):
        return "/"
    return nxt


def default_password_warning() -> bool:
    """Standart parol o'zgartirilmagan va panel faqat shu kompyuter uchun emas — paneldagi ogohlantirish."""
    return ADMIN_PASSWORD == "admin123" and HOST not in ("127.0.0.1", "localhost", "::1")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    # Standart parol eslatmasi faqat shu kompyuterdan ochilganda ko'rinadi — tarmoqdagi begonaga parol aytilmaydi
    return templates.TemplateResponse(request, "login.html", {
        "app_name": APP_NAME, "next": next, "error": error,
        "default_pw": ADMIN_PASSWORD == "admin123" and _is_local(request),
        "blocked": round(login_blocked(_client_ip(request)))})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    ip = _client_ip(request)
    if login_blocked(ip) > 0:
        return RedirectResponse("/login?error=2", status_code=303)
    # compare_digest: parolni harf-harf solishtirish vaqti bo'yicha topib bo'lmaydi
    good = (hmac.compare_digest(username.encode(), ADMIN_USER.encode())
            & hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()))
    if good and ADMIN_PASSWORD == "admin123" and not _is_local(request):
        # 1.8.0: standart parol (hamma biladi) bilan tarmoqdan kirish yo'q — faqat shu kompyuterning o'zidan.
        # Parol .env da o'zgartirilgach telefon/boshqa kompyuterdan ham kiriladi
        log.warning("Standart parol bilan tarmoqdan kirish rad etildi (%s)", ip)
        return RedirectResponse("/login?error=default", status_code=303)
    if good:
        _login_ok(ip)
        _session_start(request, username)
        return RedirectResponse(safe_next(next), status_code=303)
    _login_failed(ip)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
@app.post("/logout")
def logout(request: Request):
    _session_end(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz")
async def healthz():
    # async: sinxron ishchi oqimlar band bo'lsa ham (video yuklash, kamera sinovi) nazoratchiga darhol javob
    return {"ok": True, "version": APP_VERSION}


CTL_TOKEN = env("HUSHYOR_CTL_TOKEN")      # nazoratchi (run.py --supervise) bergan maxfiy kalit
SERVER_REF: list = []                     # uvicorn Server (run.py qo'yadi) — toza to'xtatish uchun


@app.post("/healthz/shutdown")
async def healthz_shutdown(request: Request):
    """Nazoratchidan toza to'xtatish buyrug'i (faqat shu kompyuterdan, faqat to'g'ri kalit bilan).

    Windows'da oynasiz bola jarayonga Ctrl+C yuborib bo'lmaydi; shu yo'l bilan kameralar va kliplar
    to'g'ri yopiladi (TerminateProcess o'rniga)."""
    if not CTL_TOKEN or not _is_self(request):
        raise HTTPException(404)
    if not hmac.compare_digest(request.headers.get("x-hushyor-token", "").encode("utf-8", "replace"),
                               CTL_TOKEN.encode()):
        raise HTTPException(403)
    SHUTDOWN.set()
    for srv in SERVER_REF:
        srv.should_exit = True
    log.info("Nazoratchidan to'xtatish buyrug'i olindi")
    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    return FileResponse(APP_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


# ----------------------------------------------------------------------------- sahifalar
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return page(request, "dashboard.html", "dashboard", "Boshqaruv paneli")


@app.get("/live", response_class=HTMLResponse)
def live(request: Request):
    return page(request, "live.html", "live", "Jonli kuzatuv")


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    zs = db.rows("SELECT s.id, s.name, COUNT(z.id) AS n FROM sources s JOIN zones z ON z.source_id=s.id "
                 "WHERE z.enabled=1 GROUP BY s.id ORDER BY s.id")
    return page(request, "upload.html", "upload", "Video tahlili", zone_sources=zs)


@app.get("/events", response_class=HTMLResponse)
def events_page(request: Request):
    srcs = db.rows("SELECT DISTINCT source_name FROM events WHERE source_name IS NOT NULL ORDER BY source_name")
    return page(request, "events.html", "events", "Hodisalar", sources=[s["source_name"] for s in srcs])


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(request: Request, event_id: int):
    ev = db.get_event(event_id)
    if not ev:
        raise HTTPException(404, "Hodisa topilmadi")
    prev_id = db.scalar("SELECT id FROM events WHERE id<? ORDER BY id DESC LIMIT 1", (event_id,))
    next_id = db.scalar("SELECT id FROM events WHERE id>? ORDER BY id ASC LIMIT 1", (event_id,))
    return page(request, "event_detail.html", "events", f"Hodisa #{event_id}", ev=event_public(ev),
                prev_id=prev_id, next_id=next_id)


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    return page(request, "sources.html", "sources", "Kameralar")


@app.get("/model", response_class=HTMLResponse)
def model_page(request: Request):
    from .ml.features import feature_names

    return page(request, "model.html", "model", "AI model", n_features=len(feature_names()))


@app.get("/telegram", response_class=HTMLResponse)
def telegram_page(request: Request):
    return page(request, "telegram.html", "telegram", "Telegram bot")


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    return page(request, "reports.html", "reports", "Hisobotlar")


@app.get("/visits", response_class=HTMLResponse)
def visits_page(request: Request):
    srcs = db.rows("SELECT id, name FROM sources ORDER BY id")
    return page(request, "visits.html", "visits", "Tashriflar", sources=srcs)


@app.get("/navbat", response_class=HTMLResponse)
def queues_page(request: Request):
    return page(request, "navbat.html", "queues", "Navbat")


@app.get("/oqim", response_class=HTMLResponse)
def flow_page(request: Request):
    return page(request, "oqim.html", "flow", "Oqim va heatmap")


@app.get("/oqim/sozlash", response_class=HTMLResponse)
def flow_setup_page(request: Request):
    srcs = db.rows("SELECT id, name, kind FROM sources ORDER BY id")
    return page(request, "reja.html", "flow", "Reja va kameralar", sources=srcs)


@app.get("/oqim/kamera/{source_id}", response_class=HTMLResponse)
def flow_calib_page(request: Request, source_id: int):
    src = db.row("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        raise HTTPException(404, "Manba topilmadi")
    return page(request, "kalibr.html", "flow", f"Kalibrlash — {src['name']}", src=src)


@app.get("/xavfsizlik", response_class=HTMLResponse)
def safety_page(request: Request):
    return page(request, "xavfsizlik.html", "safety", "Xavfsizlik va salomatlik")


@app.get("/pilot", response_class=HTMLResponse)
def pilot_page(request: Request):
    return page(request, "pilot.html", "pilot", "Pilot: tayyorlik va darvozalar")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    return page(request, "settings.html", "settings", "Sozlamalar", saved=saved)


@app.get("/system", response_class=HTMLResponse)
def system_page(request: Request):
    return page(request, "system.html", "system", "Tizim holati")


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return page(request, "help.html", "help", "Yo'riqnoma")


# ----------------------------------------------------------------------------- media va oqimlar
@app.get("/media/clips/{name}")
def media(name: str):
    # Faqat fayl nomi: "." bilan boshlanmaydi (".." papka emas), kengaytmasi klip yoki rasm
    if not re.fullmatch(r"[\w\-][\w\-.]*\.(mp4|jpg|jpeg|png)", name):
        raise HTTPException(400)
    p = CLIPS_DIR / name
    if not p.is_file():
        raise HTTPException(404)
    mt = "video/mp4" if p.suffix == ".mp4" else "image/jpeg"
    return FileResponse(p, media_type=mt)


def _placeholder(text: str) -> bytes:
    import numpy as np

    img = np.full((360, 640, 3), (38, 32, 28), dtype=np.uint8)
    cv2.putText(img, text, (30, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


async def _mjpeg(get_frame, request: Request):
    boundary = b"--frame\r\n"
    last = None
    idle = 0
    while not SHUTDOWN.is_set():
        if await request.is_disconnected():
            break
        frame = get_frame()
        if frame and frame is not last:
            last = frame
            idle = 0
            yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + \
                b"\r\n\r\n" + frame + b"\r\n"
        else:
            idle += 1
            if idle > 600:  # ~40 s kadr bo'lmasa
                break
        await asyncio.sleep(0.066)


@app.get("/stream/{source_id}")
async def stream(request: Request, source_id: int):
    mgr = get_manager()
    return StreamingResponse(_mjpeg(lambda: mgr.preview(source_id, viewing=True), request),
                             media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@app.get("/stream/job/{job_id}")
async def stream_job(request: Request, job_id: int):
    mgr = get_manager()
    return StreamingResponse(_mjpeg(lambda: mgr.job_preview(job_id), request),
                             media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/sources/{source_id}/snapshot.jpg")
def snapshot(source_id: int):
    data = get_manager().preview(source_id)
    if not data:
        data = _placeholder("Kamera ishlamayapti")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ----------------------------------------------------------------------------- umumiy API
@app.get("/api/summary")
def api_summary():
    d0 = day_start()
    cnt = lambda sql, *p: db.scalar(sql, p) or 0  # noqa: E731
    today = cnt(f"SELECT COUNT(*) FROM events WHERE created_at>=? AND {LIVE_EV}", d0)
    ok = cnt(f"SELECT COUNT(*) FROM events WHERE created_at>=? AND status='tasdiqlangan' AND {LIVE_EV}", d0)
    no = cnt(f"SELECT COUNT(*) FROM events WHERE created_at>=? AND status='rad_etilgan' AND {LIVE_EV}", d0)
    pending = cnt(f"SELECT COUNT(*) FROM events WHERE status='yangi' AND {LIVE_EV}")
    all_ok = cnt(f"SELECT COUNT(*) FROM events WHERE status='tasdiqlangan' AND {NOT_SAFETY} AND {LIVE_EV}")
    all_no = cnt(f"SELECT COUNT(*) FROM events WHERE status='rad_etilgan' AND {NOT_SAFETY} AND {LIVE_EV}")
    hours = [0] * 24
    for r in db.rows(f"SELECT created_at FROM events WHERE created_at>=? AND {LIVE_EV}", (d0,)):
        hours[time.localtime(r["created_at"]).tm_hour] += 1
    latest = [event_public(db.decode_event(e)) for e in db.rows("SELECT * FROM events ORDER BY id DESC LIMIT 8")]
    return {
        "today": today, "today_ok": ok, "today_no": no, "pending": pending,
        "precision": (all_ok / (all_ok + all_no)) if (all_ok + all_no) else None,
        "reviewed": all_ok + all_no, "hours": hours, "latest": latest,
        "sources": get_manager().source_info(), "telegram": get_bot().info(),
        "subs": cnt("SELECT COUNT(*) FROM subscribers WHERE active=1"),
        "sensitivity": db.get_setting("sensitivity"), "last_id": latest[0]["id"] if latest else 0,
        "jobs_running": cnt("SELECT COUNT(*) FROM jobs WHERE status IN ('navbatda','ishlanmoqda')"),
        "visits": _visits_brief(d0),
        "queues": _queues_brief(d0),
        "losses": _losses_brief(d0),
    }


def _losses_brief(d0: float) -> dict | None:
    """3.1.0: tasdiqlangan o'g'irliklar bo'yicha qo'lda kiritilgan zarar (bugun, shu oy, jami). Hali birorta
    o'g'irlik tasdiqlanmagan bo'lsa — kartalar ko'rsatilmaydi."""
    from . import losses

    lt = time.localtime(d0)
    m0 = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    tot = losses.totals()
    if not tot["confirmed"]:
        return None
    month = losses.totals(m0)
    last30 = losses.totals(time.time() - 30 * 86400)          # summasi yozilmaganlar — oxirgi 30 kun (bulutdagi kabi)
    return {"today": losses.totals(d0)["sum"], "month": month["sum"], "total": tot["sum"],
            "missing": last30["confirmed"] - last30["with_sum"], "month_n": month["with_sum"]}


def _queues_brief(d0: float) -> dict | None:
    """2.0.0: navbat zonasi bo'lsa — hozirgi eng uzun navbat, kassir yo'q kassalar, bugun o'rtacha kutish."""
    from .ai import queues

    if not queues.has_queues():
        return None
    live = [x for x in queues.live_status() if x["live"]]
    s = queues.summary(d0, time.time() + 60)
    worst = max(live, key=lambda x: (x["length"], x["max_wait"]), default=None)
    return {"n": len(live), "max_len": worst["length"] if worst else 0, "max_name": worst["name"] if worst else "",
            "no_cashier": sum(1 for x in live if x["state"] == "kassir_yoq"), "served": s["served"],
            "avg_wait": s["avg_wait"], "long": sum(1 for x in live if x["state"] == "navbat")}


def _visits_brief(d0: float) -> dict | None:
    """1.9.0: sanash chizig'i bor bo'lsa — bugun kirdi/chiqdi va hozir ichkarida (boshqaruv paneli kartasi;
    Tashriflar sahifasi va bulut bilan bir xil hisob — visits.occupancy)."""
    occ = visits.occupancy()
    if occ is None:
        return None
    from .ai import counting

    s = counting.summary(d0, time.time() + 1)
    return {"in": s["in"], "out": s["out"], "inside": occ["inside"], "staff": s["staff"]}


# ----------------------------------------------------------------------------- manbalar
@app.get("/api/sources")
def api_sources():
    return get_manager().source_info()


@app.post("/api/sources")
async def api_add_source(request: Request):
    d = await body_data(request)
    name = (d.get("name") or "").strip()
    kind = d.get("kind", "webcam")
    uri = (d.get("uri") or "").strip()
    if kind not in ("webcam", "rtsp", "file"):
        raise HTTPException(400, "Noto'g'ri tur")
    if kind == "webcam":
        uri = uri or "0"
        if not uri.isdigit():
            raise HTTPException(400, "Veb-kamera raqami 0, 1, 2 ... bo'lishi kerak")
    elif kind == "rtsp" and not re.match(r"^(rtsp|rtsps|http|https|rtmp)://", uri):
        raise HTTPException(400, "Manzil rtsp:// yoki http:// bilan boshlanishi kerak")
    elif kind == "file" and not Path(uri).exists():
        raise HTTPException(400, "Fayl topilmadi")
    if not name:
        name = {"webcam": f"Veb-kamera {uri}", "rtsp": "IP kamera", "file": Path(uri).name}[kind]
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     (name, kind, uri, 1 if str(d.get("enabled", "0")) in ("1", "on", "true") else 0,
                      1 if str(d.get("loop", "0")) in ("1", "on", "true") else 0, time.time()))
    if str(d.get("start", "0")) in ("1", "on", "true"):
        get_manager().start_source(sid)
    return {"ok": True, "id": sid}


@app.post("/api/sources/{source_id}/start")
def api_start(source_id: int):
    ok, msg = get_manager().start_source(source_id)
    return {"ok": ok, "message": msg}


@app.post("/api/sources/{source_id}/stop")
def api_stop(source_id: int):
    get_manager().stop_source(source_id)
    return {"ok": True}


@app.post("/api/sources/{source_id}/delete")
def api_delete_source(source_id: int):
    get_manager().stop_source(source_id)
    db.execute("DELETE FROM sources WHERE id=?", (source_id,))
    db.execute("DELETE FROM zones WHERE source_id=?", (source_id,))
    zones_mod.invalidate(source_id)
    return {"ok": True}


# ----------------------------------------------------------------------------- zonalar
def _zone_public(r: dict) -> dict:
    pts = zones_mod.parse_points(r["points"], r["kind"])
    return {"id": r["id"], "source_id": r["source_id"], "name": r["name"] or "", "kind": r["kind"],
            "points": [] if pts is None else [[round(float(x), 4), round(float(y), 4)] for x, y in pts],
            "sens": r["sens"] or "", "enabled": int(r["enabled"] or 0)}


@app.get("/sources/{source_id}/zones", response_class=HTMLResponse)
def zones_page(request: Request, source_id: int):
    src = db.row("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        raise HTTPException(404, "Manba topilmadi")
    return page(request, "zones.html", "sources", f"Zonalar — {src['name']}", src=src,
                zone_kinds=zones_mod.ZONE_KINDS, sens_labels=zones_mod.SENS_LABELS)


# ----------------------------------------------------------------------------- aniqlik sinovi
@app.get("/eval", response_class=HTMLResponse)
def eval_page(request: Request):
    zs = db.rows("SELECT s.id, s.name, COUNT(z.id) AS n FROM sources s LEFT JOIN zones z ON z.source_id=s.id "
                 "AND z.enabled=1 GROUP BY s.id ORDER BY s.id")
    from .ai import model_zoo

    return page(request, "eval.html", "eval", "Aniqlik sinovi", zone_sources=zs, label_kinds=evaluation.LABEL_KINDS,
                tol=(evaluation.TOL_BEFORE, evaluation.TOL_AFTER), model_sets=model_zoo.status())


def _eval_video_public(v: dict) -> dict:
    return {"id": v["id"], "filename": v["filename"], "duration": v["duration"], "fps": v["fps"],
            "width": v["width"], "height": v["height"], "zone_source_id": v["zone_source_id"],
            "labels": evaluation.clean_labels(v["labels"]), "reviewed": int(v["reviewed"] or 0),
            "note": v["note"] or "", "play_status": v["play_status"], "created_at": v["created_at"]}


@app.get("/api/eval/videos")
def api_eval_videos():
    return [_eval_video_public(v) for v in db.rows("SELECT * FROM eval_videos ORDER BY id DESC")]


@app.post("/api/eval/videos")
async def api_eval_upload(file: UploadFile = File(...), zone_source: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Video fayl kerak ({', '.join(sorted(VIDEO_EXT))})")
    dest = EVAL_DIR / _safe_name(file.filename)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 4_000_000_000:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Fayl juda katta (4 GB dan oshmasin)")
            f.write(chunk)
    info = evaluation.probe(dest)
    if not info.get("ok"):
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Videoni o'qib bo'lmadi. MP4 (H.264) formatini sinab ko'ring.")
    conv = evaluation.needs_transcode(dest, info)
    vid = db.execute("INSERT INTO eval_videos(filename, path, play_path, play_status, duration, fps, width, height, "
                     "zone_source_id, labels, reviewed, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (file.filename, str(dest), None if conv else str(dest), "tayyorlanmoqda" if conv else "tayyor",
                      info["duration"], info["fps"], info["width"], info["height"],
                      int(zone_source) if zone_source.isdigit() else None, "[]", 0, time.time()))
    if conv:
        threading.Thread(target=evaluation.make_playable, args=(vid, dest), daemon=True).start()
    return {"ok": True, "video": _eval_video_public(db.row("SELECT * FROM eval_videos WHERE id=?", (vid,)))}


@app.post("/api/eval/videos/{vid}")
async def api_eval_video_update(request: Request, vid: int):
    v = db.row("SELECT * FROM eval_videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Video topilmadi")
    d = await body_data(request)
    upd = {}
    if "labels" in d:
        upd["labels"] = json.dumps(evaluation.clean_labels(d["labels"]), ensure_ascii=False)
    if "reviewed" in d:
        upd["reviewed"] = 1 if str(d["reviewed"]) in ("1", "true", "on", "True") else 0
    if "zone_source_id" in d:
        z = str(d["zone_source_id"] or "")
        upd["zone_source_id"] = int(z) if z.isdigit() else None
    if "note" in d:
        upd["note"] = str(d["note"] or "")[:500]
    if upd:
        db.execute(f"UPDATE eval_videos SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), vid))
    return {"ok": True, "video": _eval_video_public(db.row("SELECT * FROM eval_videos WHERE id=?", (vid,)))}


@app.post("/api/eval/videos/{vid}/delete")
def api_eval_video_delete(vid: int):
    v = db.row("SELECT * FROM eval_videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Video topilmadi")
    for p in {v["path"], v["play_path"]}:
        if p:
            Path(p).unlink(missing_ok=True)
    db.execute("DELETE FROM eval_videos WHERE id=?", (vid,))
    return {"ok": True}


@app.get("/media/eval/{vid}")
def media_eval(vid: int):
    v = db.row("SELECT play_path, play_status FROM eval_videos WHERE id=?", (vid,))
    if not v or not v["play_path"] or not Path(v["play_path"]).exists():
        raise HTTPException(404, "Video hali tayyor emas")
    return FileResponse(v["play_path"], media_type="video/mp4")


@app.post("/api/eval/run")
async def api_eval_run(request: Request):
    d = await body_data(request)
    ids = d.get("video_ids") or None
    sets = d.get("model_sets") or None
    if isinstance(sets, str):
        sets = [x for x in sets.split(",") if x]
    if sets is not None and (not isinstance(sets, list) or not all(isinstance(x, str) for x in sets)):
        raise HTTPException(400, "model_sets — nomlar ro'yxati bo'lishi kerak")
    if ids is not None and (not isinstance(ids, list) or not all(str(x).isdigit() for x in ids)):
        raise HTTPException(400, "video_ids — raqamlar ro'yxati bo'lishi kerak")
    try:
        run_ids = evaluation.get_runner().start(ids, sets)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "run_id": run_ids[0], "run_ids": run_ids}


@app.post("/api/eval/stop")
def api_eval_stop():
    evaluation.get_runner().stop()
    return {"ok": True}


def theft_live_cams(t0: float | None = None, t1: float | None = None) -> tuple[list[dict], float]:
    """2.3.0: jonli kameralar bo'yicha o'g'irlik signallari va ishlagan daqiqalar (bir oynada). t0 / t1 (2.5.0, pilot) —
    oyna boshi va oxiri (berilmasa — saqlash muddati bo'yicha, hozirgacha)."""
    from . import theft_eval
    from .config import FEEDBACK_DIR

    if t0 is None:
        t0 = theft_eval.window(db.get_setting("retention_days", "30"), time.time())
    end = 1e18 if t1 is None else float(t1)
    mins = {r["source_id"]: r for r in db.rows(
        "SELECT source_id, MAX(source_name) AS name, COUNT(*) AS minutes FROM metrics_minute WHERE source_id>0 "
        "AND up_pct>=0.5 AND minute>=? AND minute<? GROUP BY source_id", (t0, end))}
    ev: dict = {}
    for r in db.rows(f"SELECT source_id, status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND source_id IS NOT NULL "
                     f"AND {NOT_SAFETY} AND created_at>=? AND created_at<? GROUP BY source_id, status", (t0, end)):
        e = ev.setdefault(r["source_id"], {"rejected": 0, "confirmed": 0, "unreviewed": 0})
        e[{"rad_etilgan": "rejected", "tasdiqlangan": "confirmed"}.get(r["status"], "unreviewed")] += r["n"]
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM sources")}
    live_ids = {r["id"] for r in db.rows("SELECT id FROM events")}
    theft_eval.add_archived(ev, FEEDBACK_DIR, t0, live_ids, {v: k for k, v in names.items()}, SAFETY_KINDS, t1)
    cams = [{"source_id": sid, "name": names.get(sid) or (mins.get(sid) or {}).get("name") or f"#{sid}",
             "minutes": (mins.get(sid) or {}).get("minutes", 0),
             **ev.get(sid, {"rejected": 0, "confirmed": 0, "unreviewed": 0})} for sid in sorted(set(mins) | set(ev))]
    return cams, t0


@app.get("/api/eval/live_gate")
def api_eval_live_gate():
    from . import theft_eval

    cams, t0 = theft_live_cams()
    return {**theft_eval.gate(cams), "since": t0}


@app.get("/api/eval/status")
def api_eval_status():
    r = evaluation.get_runner()
    return {**r.state, "running": r.busy()}


@app.get("/api/eval/runs")
def api_eval_runs():
    cols = ("id, created_at, finished_at, status, progress, version, settings, videos, hours, tp, fp, fn, dup, "
            "precision, recall, f1, fp_per_hour, latency_med, error, speed")
    out = []
    for r in db.rows(f"SELECT {cols} FROM eval_runs ORDER BY id DESC LIMIT 50"):
        r["settings"] = json.loads(r["settings"] or "{}")
        out.append(r)
    return out


@app.get("/api/eval/runs/{run_id}")
def api_eval_run_detail(run_id: int):
    r = db.row("SELECT * FROM eval_runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "Topilmadi")
    r["settings"] = json.loads(r["settings"] or "{}")
    r["details"] = json.loads(r["details"] or "[]")
    return r


@app.get("/api/sources/{source_id}/frame.jpg")
def api_source_frame(source_id: int, fresh: int = 1):
    data = get_manager().raw_snapshot(source_id, fresh=bool(fresh))
    if not data:
        data = _placeholder("Kadr olinmadi - kamerani yoqing")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/sources/{source_id}/zones")
def api_zones(source_id: int):
    return [_zone_public(r) for r in db.rows("SELECT * FROM zones WHERE source_id=? ORDER BY id", (source_id,))]


@app.post("/api/sources/{source_id}/zones")
async def api_zone_save(request: Request, source_id: int):
    if not db.row("SELECT id FROM sources WHERE id=?", (source_id,)):
        raise HTTPException(404, "Manba topilmadi")
    d = await body_data(request)
    kind = d.get("kind", "")
    if not isinstance(kind, str) or kind not in zones_mod.ZONE_KINDS:
        raise HTTPException(400, "Noto'g'ri zona turi")
    pts = zones_mod.parse_points(d.get("points"), kind)
    if pts is None:
        raise HTTPException(400, "Sanash chizig'i — aynan 2 nuqta (bir-biridan uzoqroq)" if kind in zones_mod.LINE_KINDS
                            else "Zona kamida 3 nuqtadan iborat bo'lishi kerak")
    sens = d.get("sens", "") or ""
    if not isinstance(sens, str) or sens not in zones_mod.SENSITIVITY:
        raise HTTPException(400, "Noto'g'ri sezgirlik")
    name = str(d.get("name") or "").strip()[:60] or zones_mod.ZONE_KINDS[kind]["label"]
    enabled = 0 if str(d.get("enabled", "1")).strip().lower() in ("0", "false", "off", "no") else 1
    points = json.dumps([[round(float(x), 4), round(float(y), 4)] for x, y in pts])
    zid = d.get("id")
    if zid is not None and zid != "":
        try:
            zid = int(zid)
        except (TypeError, ValueError):
            raise HTTPException(400, "Noto'g'ri zona raqami")
    if zid:
        if not db.row("SELECT id FROM zones WHERE id=? AND source_id=?", (int(zid), source_id)):
            raise HTTPException(404, "Zona topilmadi")
        db.execute("UPDATE zones SET name=?, kind=?, points=?, sens=?, enabled=? WHERE id=?",
                   (name, kind, points, sens, enabled, int(zid)))
    else:
        zid = db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) "
                         "VALUES(?,?,?,?,?,?,?)", (source_id, name, kind, points, sens, enabled, time.time()))
    zones_mod.invalidate(source_id)
    return {"ok": True, "zone": _zone_public(db.row("SELECT * FROM zones WHERE id=?", (int(zid),)))}


@app.post("/api/zones/{zone_id}/delete")
def api_zone_delete(zone_id: int):
    z = db.row("SELECT source_id FROM zones WHERE id=?", (zone_id,))
    if not z:
        raise HTTPException(404, "Zona topilmadi")
    db.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    zones_mod.invalidate(z["source_id"])
    return {"ok": True}


@app.post("/api/sources/{source_id}/autostart")
async def api_autostart(request: Request, source_id: int):
    d = await body_data(request)
    db.execute("UPDATE sources SET enabled=? WHERE id=?",
               (1 if str(d.get("enabled")) in ("1", "true", "on") else 0, source_id))
    return {"ok": True}


@app.post("/api/webcam/quick")
def api_webcam_quick():
    """Kompyuter kamerasini bir tugma bilan yoqish."""
    s = db.row("SELECT id FROM sources WHERE kind='webcam' ORDER BY id LIMIT 1")
    sid = s["id"] if s else db.execute(
        "INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
        ("Kompyuter kamerasi", "webcam", "0", 0, 1, time.time()))
    ok, msg = get_manager().start_source(sid)
    return {"ok": ok, "message": msg, "id": sid}


@app.get("/api/webcam/probe")
def api_webcam_probe():
    from .ai.worker import open_capture

    busy = {w.src["uri"] for w in get_manager().workers.values() if w.is_alive() and w.src["kind"] == "webcam"}
    found = []
    for i in range(4):
        if str(i) in busy:
            found.append({"index": i, "ok": True, "note": "ishlatilmoqda"})
            continue
        cap = open_capture("webcam", str(i))
        ok = cap.isOpened() and cap.read()[0]
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if ok else 0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if ok else 0
        cap.release()
        if ok:
            found.append({"index": i, "ok": True, "note": f"{w}x{h}"})
    return {"cameras": found}


# ----------------------------------------------------------------------------- yuklash va vazifalar
def _safe_name(name: str) -> str:
    base = re.sub(r"[^\w.\-]+", "_", Path(name).name)[:80] or "video.mp4"
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{base}"


def _start_as_live(path: Path, name: str) -> int:
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     (f"Video (jonli): {name}"[:60], "file", str(path), 0, 0, time.time()))
    get_manager().start_source(sid)
    return sid


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), mode: str = Form("fast"), zone_source: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Video fayl kerak ({', '.join(sorted(VIDEO_EXT))})")
    dest = UPLOADS_DIR / _safe_name(file.filename)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 2_000_000_000:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Fayl juda katta (2 GB dan oshmasin)")
            f.write(chunk)
    cap = cv2.VideoCapture(str(dest))
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    if not ok:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Videoni o'qib bo'lmadi. MP4 (H.264) formatini sinab ko'ring.")
    if mode == "live":
        sid = _start_as_live(dest, file.filename)
        return {"ok": True, "mode": "live", "source_id": sid}
    zsid = int(zone_source) if zone_source.isdigit() else None
    jid = get_manager().submit_job(dest, file.filename, zone_source_id=zsid)
    return {"ok": True, "mode": "fast", "job_id": jid}


download_state: dict[str, dict] = {}


def _ip_public(ip) -> bool:
    import ipaddress

    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in ipaddress.ip_network("64:ff9b::/96"):          # NAT64: ichidagi IPv4 manzil tekshiriladi
            ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast


def resolve_public(url: str) -> tuple[bool, str, str]:
    """Havola internetdagi ochiq manzilmi (SSRF himoyasi, 1.8.0): lokal tarmoq (kameralar, router), shu kompyuter
    va bulut metadata manzillariga (169.254.x) server nomidan so'rov yuborilmaydi. Qaytaradi: (ok, sabab, IP) —
    so'rov aynan shu tekshirilgan IP'ga yuboriladi (DNS ikkinchi marta boshqa manzil bera olmaydi)."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    try:
        u = urlsplit(url)
        port = u.port
        host = u.hostname
    except ValueError:
        return False, "havola noto'g'ri", ""
    if u.scheme not in ("http", "https") or not host:
        return False, "havola http:// yoki https:// bo'lishi kerak", ""
    try:
        infos = socket.getaddrinfo(host, port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        return False, "manzil topilmadi", ""
    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not ips or not all(_ip_public(ip) for ip in ips):
        return False, "lokal tarmoq yoki shu kompyuter manziliga yuklab bo'lmaydi", ""
    return True, "", str(ips[0])


def public_url_ok(url: str) -> tuple[bool, str]:
    ok_, why, _ip = resolve_public(url)
    return ok_, why


def open_public(url: str, timeout: float = 60.0, max_redirects: int = 5):
    """GET (oqim): har qadamda manzil tekshiriladi va ulanish aynan tekshirilgan IP'ga (Host va TLS SNI — asl nom,
    sertifikat ham asl nom bo'yicha). Yo'naltirishlar qo'lda. Qaytaradi: (client, response) — ikkalasi ham yopilsin."""
    cur = url
    for _ in range(max_redirects + 1):
        good, why, ip = resolve_public(cur)
        if not good:
            raise ValueError(f"Havola rad etildi: {why}")
        u = httpx.URL(cur)
        client = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)
        try:
            req = client.build_request("GET", u.copy_with(host=ip), headers={"Host": u.netloc.decode("ascii")},
                                       extensions={"sni_hostname": u.host} if u.scheme == "https" else {})
            r = client.send(req, stream=True)
        except Exception:
            client.close()
            raise
        if not r.is_redirect:
            return client, r
        loc = r.headers.get("location", "")
        r.close()
        client.close()
        cur = str(u.join(loc))
    raise ValueError("Juda ko'p yo'naltirish")


@app.post("/api/upload-url")
async def api_upload_url(request: Request):
    d = await body_data(request)
    url = str(d.get("url") or "").strip()
    mode = str(d.get("mode") or "fast")
    zsrc = str(d.get("zone_source") or "")
    zsid = int(zsrc) if zsrc.isdigit() else None
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Havola http:// yoki https:// bilan boshlanishi kerak")
    if re.search(r"(youtube\.com|youtu\.be|instagram\.com|tiktok\.com|facebook\.com)", url):
        raise HTTPException(400, "YouTube/Instagram/TikTok havolalari to'g'ridan-to'g'ri yuklanmaydi. "
                                 "Videoni kompyuteringizga yuklab olib, fayl sifatida yuboring.")
    good, why = await run_in_threadpool(public_url_ok, url)
    if not good:
        raise HTTPException(400, f"Havola qabul qilinmadi: {why}")
    key = str(int(time.time() * 1000))
    download_state[key] = {"status": "yuklanmoqda", "bytes": 0, "error": None, "job_id": None}

    def worker():
        name = _safe_name(url.split("?")[0].rstrip("/").split("/")[-1] or "video.mp4")
        if Path(name).suffix.lower() not in VIDEO_EXT:
            name += ".mp4"
        dest = UPLOADS_DIR / name
        try:
            # Har qadam (yo'naltirishlar ham) tekshiriladi va tekshirilgan IP'ga ulanadi (SSRF, DNS rebinding)
            client, r = open_public(url)
            try:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype:
                    raise ValueError("Havola video fayl emas, veb-sahifa. To'g'ridan-to'g'ri .mp4 havolasi kerak.")
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(1024 * 256):
                        f.write(chunk)
                        download_state[key]["bytes"] += len(chunk)
                        if download_state[key]["bytes"] > 2_000_000_000:
                            raise ValueError("Fayl juda katta")
            finally:
                r.close()
                client.close()
            cap = cv2.VideoCapture(str(dest))
            ok = cap.isOpened() and cap.read()[0]
            cap.release()
            if not ok:
                raise ValueError("Yuklangan faylni video sifatida o'qib bo'lmadi")
            if mode == "live":
                download_state[key]["source_id"] = _start_as_live(dest, name)
            else:
                download_state[key]["job_id"] = get_manager().submit_job(dest, name, zone_source_id=zsid)
            download_state[key]["status"] = "tayyor"
        except Exception as e:
            dest.unlink(missing_ok=True)
            download_state[key]["status"] = "xato"
            download_state[key]["error"] = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "key": key}


@app.get("/api/upload-url/{key}")
def api_upload_url_status(key: str):
    return download_state.get(key, {"status": "noma'lum"})


@app.get("/api/jobs")
def api_jobs():
    jobs = db.rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 50")
    for j in jobs:
        j["created_str"] = time.strftime("%d.%m %H:%M", time.localtime(j["created_at"]))
        j["running"] = j["id"] in get_manager().jobs
    return jobs


@app.post("/api/jobs/{job_id}/stop")
def api_job_stop(job_id: int):
    from . import cloud_tests

    if not cloud_tests.stop_local(job_id):      # 3.0.0: bulutdan yuklab olinayotgan sinov
        get_manager().stop_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/delete")
def api_job_delete(job_id: int):
    j = db.row("SELECT * FROM jobs WHERE id=?", (job_id,))
    if j:
        from . import cloud_tests

        if not cloud_tests.stop_local(job_id):
            get_manager().stop_job(job_id)
        # 3.0.0: bitta videoni bir nechta vazifa ishlatishi mumkin (bulut sinovi qayta tahlili) — oxirgisi bilan o'chadi
        shared = db.scalar("SELECT COUNT(*) FROM jobs WHERE path=? AND id != ?", (j.get("path"), job_id)) or 0
        if j.get("path") and Path(j["path"]).parent == UPLOADS_DIR and not shared:
            Path(j["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"ok": True}


# ----------------------------------------------------------------------------- hodisalar
@app.get("/api/events")
def api_events(status: str = "", source: str = "", job: int = 0, days: int = 0, q: str = "",
               page: int = 1, per: int = 24, before: int = 0):
    where, params = [], []
    if status in ("yangi", "tasdiqlangan", "rad_etilgan"):
        where.append("status=?")
        params.append(status)
    if source:
        where.append("source_name=?")
        params.append(source)
    if job:
        where.append("job_id=?")
        params.append(job)
    if days:
        where.append("created_at>=?")
        params.append(time.time() - days * 86400)
    if q.strip().lstrip("#").isdigit():
        where.append("id=?")
        params.append(int(q.strip().lstrip("#")))
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar(f"SELECT COUNT(*) FROM events {w}", params) or 0
    per = max(1, min(per, 100))
    page = max(1, min(page, 100_000))
    if before > 0:
        # 3.2.0: "Yana ko'rsatish" — oxirgi ko'rilgan hodisadan eskilari (OFFSET emas: orada tekshirilgan yoki yangi
        # kelgan hodisalar tufayli ba'zilari tushib qolmaydi, takrorlanmaydi)
        ww = ("WHERE " + " AND ".join([*where, "id<?"]))
        rows = db.rows(f"SELECT * FROM events {ww} ORDER BY id DESC LIMIT ?", [*params, min(before, 2 ** 62), per + 1])
    else:
        rows = db.rows(f"SELECT * FROM events {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                       [*params, per + 1, (page - 1) * per])
    more = len(rows) > per
    rows = rows[:per]
    return {"total": total, "page": page, "per": per, "next_before": rows[-1]["id"] if more and rows else None,
            "items": [event_public(db.decode_event(r)) for r in rows]}


@app.get("/api/events/{event_id}")
def api_event(event_id: int):
    ev = db.get_event(event_id)
    if not ev:
        raise HTTPException(404)
    return event_public(ev)


@app.get("/api/events/{event_id}/evidence.zip")
def api_event_evidence(event_id: int):
    """2.3.0: dalil paketi — klip, rasmlar, PDF hisobot, SHA-256 (app/evidence.py)."""
    from . import evidence

    try:
        data, name = evidence.build(event_id)
    except KeyError:
        raise HTTPException(404, "Hodisa topilmadi")
    log.info("Dalil paketi yuklab olindi: hodisa #%d", event_id)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.post("/api/events/{event_id}/review")
async def api_review(request: Request, event_id: int):
    d = await body_data(request)
    status = d.get("status")
    if status not in ("tasdiqlangan", "rad_etilgan", "yangi"):
        raise HTTPException(400, "Noto'g'ri holat")
    note = d.get("note")
    note = str(note)[:2000] if note is not None else None     # JSON'da son/obyekt kelsa ham 500 bo'lmasin
    amount = None
    if d.get("loss_sum") not in (None, ""):
        from . import losses

        try:
            amount = losses.parse_sum(d.get("loss_sum"))
        except ValueError as e:
            raise HTTPException(400, f"Summa: {e}")
    ev = db.review_event(event_id, status, f"Web: {request.session.get('user')}", note)
    if not ev:
        raise HTTPException(404)
    if status == "yangi":
        db.update_event(event_id, reviewed_by=None, reviewed_at=None)
    if amount is not None and status == "tasdiqlangan":
        from . import losses

        losses.set_loss(event_id, amount, f"Web: {request.session.get('user')}")
    get_bot().refresh_event(event_id)
    get_manager().emit("update", event_id)
    return {"ok": True, "event": event_public(db.get_event(event_id))}


@app.post("/api/events/{event_id}/loss")
async def api_loss(request: Request, event_id: int):
    """3.1.0: tasdiqlangan o'g'irlik signaliga taxminiy zarar (so'm). Bo'sh yoki 0 — summa olib tashlanadi."""
    from . import losses

    d = await body_data(request)
    ev = db.get_event(event_id)
    if not ev:
        raise HTTPException(404, "Hodisa topilmadi")
    if not losses.allowed(ev) or ev.get("job_id") is not None:
        raise HTTPException(400, "Summa faqat do'kondagi o'g'irlik signaliga yoziladi")
    if ev.get("status") != "tasdiqlangan":
        raise HTTPException(409, "Avval signalni tasdiqlang")
    try:
        amount = losses.parse_sum(d.get("loss_sum"))
    except ValueError as e:
        raise HTTPException(400, f"Summa: {e}")
    losses.set_loss(event_id, amount, f"Web: {request.session.get('user')}")
    get_bot().refresh_event(event_id)
    get_manager().emit("update", event_id)
    return {"ok": True, "event": event_public(db.get_event(event_id))}


@app.post("/api/events/{event_id}/note")
async def api_note(request: Request, event_id: int):
    d = await body_data(request)
    db.update_event(event_id, note=str(d.get("note") or "")[:2000])
    return {"ok": True}


@app.post("/api/events/{event_id}/delete")
def api_event_delete(event_id: int):
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    if ev:
        archive_feedback(ev)
        sync.forget_event(event_id)
        delete_event_files(ev)
        db.execute("DELETE FROM events WHERE id=?", (event_id,))
        db.execute("DELETE FROM tg_messages WHERE event_id=?", (event_id,))
    return {"ok": True}


@app.post("/api/events/{event_id}/resend")
def api_event_resend(event_id: int):
    bot = get_bot()
    if not bot.token:
        raise HTTPException(400, "Telegram token berilmagan")
    bot.resend_event(event_id)
    return {"ok": True}


@app.post("/api/events/bulk")
async def api_events_bulk(request: Request):
    d = await body_data(request)
    action = d.get("action")
    if action == "delete_rejected":
        evs = db.rows("SELECT * FROM events WHERE status='rad_etilgan'")
        for ev in evs:
            archive_feedback(ev)
            sync.forget_event(ev["id"])
            delete_event_files(ev)
            db.execute("DELETE FROM events WHERE id=?", (ev["id"],))
            db.execute("DELETE FROM tg_messages WHERE event_id=?", (ev["id"],))
        return {"ok": True, "count": len(evs)}
    raise HTTPException(400)


def csv_safe(v):
    """Excel'da formula sifatida bajarilmasin: =, +, -, @ bilan boshlanadigan matn oldiga ' qo'yiladi."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@app.get("/api/export/events.csv")
def api_export():
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";")   # 3.2.0: Excel (uz/ru Windows) ustunlarni to'g'ri ajratsin
    wr.writerow(["ID", "Vaqt", "Shubhali lahza", "Klipdagi o'rni (s)", "Kamera", "Turi", "Ball", "Daraja", "Holat",
                 "Kim tekshirdi", "Izoh", "Sabablar", "Klip", "Taxminiy zarar (so'm)"])
    for e in db.rows("SELECT * FROM events ORDER BY id DESC"):
        e = db.decode_event(e)
        si = susp_info(e)
        wr.writerow([csv_safe(v) for v in (
            e["id"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["created_at"])),
            si["susp_label"] or "", si["susp_offset"] if si["susp_offset"] is not None else "", e["source_name"],
            KIND_UZ.get(e["kind"], e["kind"]), round(e["score"] or 0), e["level"], e["status"],
            e.get("reviewed_by") or "", e.get("note") or "", " | ".join(e["reasons"] or []),
            e.get("clip_path") or "", e.get("loss_sum") or "")])
    data = "﻿" + buf.getvalue()
    return Response(data.encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=hodisalar_{time.strftime('%Y%m%d')}.csv"})


# ----------------------------------------------------------------------------- hisobotlar
@app.get("/api/reports")
def api_reports(days: int = 14):
    days = max(1, min(days, 365))
    start = day_start() - (days - 1) * 86400
    evs = [db.decode_event(e) for e in db.rows("SELECT * FROM events WHERE created_at>=?", (start,))]
    daily = []
    for i in range(days):
        d0 = start + i * 86400
        d1 = d0 + 86400
        de = [e for e in evs if d0 <= e["created_at"] < d1]
        daily.append({"day": time.strftime("%d.%m", time.localtime(d0)),
                      "yangi": sum(e["status"] == "yangi" for e in de),
                      "tasdiqlangan": sum(e["status"] == "tasdiqlangan" for e in de),
                      "rad_etilgan": sum(e["status"] == "rad_etilgan" for e in de)})
    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    hours = [0] * 24
    review_mins = []
    for e in evs:
        by_kind[KIND_UZ.get(e["kind"], e["kind"])] = by_kind.get(KIND_UZ.get(e["kind"], e["kind"]), 0) + 1
        by_source[e["source_name"] or "—"] = by_source.get(e["source_name"] or "—", 0) + 1
        hours[time.localtime(e["created_at"]).tm_hour] += 1
        if e.get("reviewed_at"):
            review_mins.append((e["reviewed_at"] - e["created_at"]) / 60)
    ok = sum(e["status"] == "tasdiqlangan" for e in evs)
    no = sum(e["status"] == "rad_etilgan" for e in evs)
    th = [e for e in evs if e.get("kind") not in SAFETY_KINDS]          # o'g'irlik aniqligi — xavfsizliksiz
    t_ok = [e for e in th if e["status"] == "tasdiqlangan"]
    t_no = [e for e in th if e["status"] == "rad_etilgan"]
    review_mins.sort()
    return {
        "days": days, "total": len(evs), "ok": ok, "no": no, "pending": len(evs) - ok - no,
        "precision": len(t_ok) / (len(t_ok) + len(t_no)) if t_ok or t_no else None,
        "median_review_min": review_mins[len(review_mins) // 2] if review_mins else None,
        "daily": daily, "by_kind": by_kind, "by_source": by_source, "hours": hours,
        "avg_score_ok": (sum(e["score"] for e in t_ok) / len(t_ok)) if t_ok else None,
        "avg_score_no": (sum(e["score"] for e in t_no) / len(t_no)) if t_no else None,
    }


# ----------------------------------------------------------------------------- 2.4.0: kunlik/haftalik hisobot
REPORT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _report_brief(r: dict) -> dict:
    try:
        d = json.loads(r.get("data") or "{}")
    except ValueError:
        d = {}
    return {"id": r["id"], "kind": r["kind"], "t0": r["t0"], "t1": r["t1"], "label": d.get("label"),
            "created_at": r["created_at"], "due_at": r["due_at"], "sent_at": r["sent_at"], "sent_n": r["sent_n"],
            "error": r["error"], "recs": len(d.get("recs") or []),
            "visitors": (d.get("visitors") or {}).get("in"), "theft": (d.get("theft") or {}).get("total")}


@app.get("/api/reports/list")
def api_reports_list(limit: int = 30):
    rows = db.rows("SELECT * FROM reports ORDER BY t0 DESC, kind LIMIT ?", (max(1, min(limit, 200)),))
    return {"reports": [_report_brief(r) for r in rows]}


@app.get("/api/reports/settings")
def api_reports_settings():
    from . import reports

    s = db.get_settings()
    subs = db.rows("SELECT chat_id, name, username, COALESCE(reports, 1) AS reports FROM subscribers WHERE active=1")
    return {"daily": s.get("report_daily", "1") == "1", "weekly": s.get("report_weekly", "1") == "1",
            "time": s.get("report_time", "09:00"), "since": float(s.get("report_since", 0) or 0),
            "subscribers": subs, "bot": bool(get_bot().token), "next": {k: reports.period(k) for k in reports.KINDS}}


@app.post("/api/reports/settings")
async def api_reports_settings_save(request: Request):
    d = await body_data(request)
    vals = {}
    for k in ("daily", "weekly"):
        if k in d:
            vals[f"report_{k}"] = "1" if str(d[k]) in ("1", "true", "on", "True") else "0"
    if "time" in d:
        t = str(d["time"]).strip()
        if not REPORT_TIME_RE.match(t):
            raise HTTPException(400, "Vaqt ko'rinishi: 09:00")
        vals["report_time"] = t
    if vals:
        db.set_settings(vals)
    subs_in = d.get("subscribers") or []
    for sub in subs_in if isinstance(subs_in, list) else []:
        try:
            db.execute("UPDATE subscribers SET reports=? WHERE chat_id=?", (int(bool(sub.get("reports"))),
                                                                            int(sub["chat_id"])))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return {"ok": True}


@app.get("/api/reports/gate")
def api_reports_gate():
    from . import report_eval

    return report_eval.gate(lambda sql, p=(): db.rows(sql, p), float(db.get_setting("report_since", "0") or 0))


@app.post("/api/reports/generate")
async def api_reports_generate(request: Request):
    """Hisobotni hozir yaratish (ko'rish uchun): kind daily|weekly, date (YYYY-MM-DD, ixtiyoriy — shu kun / shu kun
    tushgan hafta; bo'sh — oxirgi tugagan davr)."""
    from . import reports

    d = await body_data(request)
    kind = d.get("kind", "daily")
    if kind not in reports.KINDS:
        raise HTTPException(400, "kind: daily yoki weekly")
    date = str(d.get("date") or "").strip()
    if date:
        try:
            t = time.mktime(time.strptime(date, "%Y-%m-%d")) + 43200
        except ValueError:
            raise HTTPException(400, "Sana ko'rinishi: 2026-10-17")
        t0 = reports.day_start(t) if kind == "daily" else reports.week_start(t)
        t1 = reports.day_start(t0 + (36 if kind == "daily" else 7 * 24 + 12) * 3600)
        if t0 >= time.time():
            raise HTTPException(400, "Kelajakdagi davr")
    else:
        t0, t1 = reports.period(kind)
    rid = await run_in_threadpool(reports.generate, kind, t0, t1)
    return {"ok": True, "id": rid}


@app.get("/api/reports/{report_id}")
def api_report_get(report_id: int):
    from . import reports

    r = reports.get(report_id)
    if not r:
        raise HTTPException(404, "Hisobot topilmadi")
    return r


@app.get("/api/reports/{report_id}/pdf")
def api_report_pdf(report_id: int):
    from . import reports

    r = reports.get(report_id)
    if not r:
        raise HTTPException(404, "Hisobot topilmadi")
    name = f"hisobot_{r['kind']}_{time.strftime('%Y%m%d', time.localtime(r['t0']))}.pdf"
    return Response(reports.pdf(r["data"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/reports/{report_id}/send")
async def api_report_send(report_id: int):
    from . import reports

    bot = get_bot()
    if not bot.token:
        raise HTTPException(400, "Telegram bot ulanmagan")
    if not reports.get(report_id):
        raise HTTPException(404, "Hisobot topilmadi")
    # Hammaga (qayta) yuborish; har chat natijasi yoziladi — rejalashtiruvchi yetgan chatlarga takror yubormaydi
    n = await run_in_threadpool(lambda: reports.send(report_id, bot, mark=True, only_missing=False))
    return {"ok": n > 0, "sent": n}


@app.post("/api/reports/{report_id}/feedback")
async def api_report_feedback(report_id: int, request: Request):
    from . import reports

    d = await body_data(request)
    ref = str(d.get("key") or "").strip()[:120] or None       # tavsiya kaliti (2.4.0 panel) yoki idx
    if ref is None:
        try:
            ref = int(d.get("idx"))
        except (TypeError, ValueError):
            raise HTTPException(400, "key yoki idx")
    rec = reports.feedback(report_id, ref, str(d.get("useful")) in ("1", "true", "True"),
                           f"panel:{request.session.get('user') or 'admin'}")
    if rec is None:
        raise HTTPException(404, "Tavsiya topilmadi")
    return {"ok": True}


# ----------------------------------------------------------------------------- AI model
@app.get("/api/model")
def api_model():
    from .ml.model import BehaviorModel

    versions = db.rows("SELECT * FROM model_versions ORDER BY id DESC")
    active_path = BehaviorModel.active_path()
    ch_path = BehaviorModel.challenger_path()
    for v in versions:
        try:
            v["oof_n"] = len(json.loads(v.pop("oof", None) or "{}"))
        except ValueError:
            v["oof_n"] = 0
        v["is_challenger"] = ch_path is not None and v["path"] == ch_path.name
        v["metrics"] = json.loads(v["metrics"] or "{}")
        v["data_desc"] = json.loads(v["data_desc"] or "{}")
        v["created_str"] = time.strftime("%d.%m.%Y %H:%M", time.localtime(v["created_at"]))
        v["is_active"] = active_path is not None and v["path"] == active_path.name
        v["exists"] = (MODELS_DIR / v["path"]).exists()
    bundled = None
    if active_path and not any(v["is_active"] for v in versions):
        try:
            import joblib

            b = joblib.load(active_path)
            bundled = {"path": active_path.name, "metrics": b.get("metrics", {}), "data": b.get("data", {}),
                       "version": b.get("version")}
        except Exception:
            bundled = {"path": active_path.name}
    fb_ok = db.scalar(f"SELECT COUNT(*) FROM events WHERE status='tasdiqlangan' AND {NOT_SAFETY}") or 0
    fb_no = db.scalar(f"SELECT COUNT(*) FROM events WHERE status='rad_etilgan' AND {NOT_SAFETY}") or 0
    from .ai.engine import Engine

    try:
        providers = Engine.get().providers
    except Exception as e:
        providers = [f"xato: {e}"]
    return {"versions": versions, "bundled": bundled, "feedback": {"ok": fb_ok, "no": fb_no},
            "train": {k: train_state[k] for k in ("running", "error", "started")},
            "providers": providers,
            "use_ml": db.get_setting("use_ml"), "ml_weight": db.get_setting("ml_weight")}


# ----------------------------------------------------------------------------- ko'rish modellari
models_state: dict = {"busy": False, "task": "", "error": None, "done": None}
_models_lock = threading.Lock()
MODEL_UPLOAD_LIMIT = 300 * 1024 * 1024    # qo'lda yuklanadigan model fayli (zip/onnx) uchun chegara


def _models_claim(task: str) -> None:
    """Bir vaqtda bitta model vazifasi (tekshirish va band qilish — bitta qulf ichida, ikki tez bosish o'tmaydi)."""
    with _models_lock:
        if models_state["busy"]:
            raise HTTPException(409, "Boshqa model vazifasi ketmoqda — biroz kuting")
        models_state.update(busy=True, task=task, error=None, done=None)


def _models_bg(task: str, fn) -> None:
    _models_claim(task)

    def run():
        try:
            models_state["done"] = fn()
        except Exception as e:  # noqa: BLE001
            log.exception("Model vazifasi xatosi (%s)", task)
            models_state["error"] = str(e)
        finally:
            models_state["busy"] = False
    threading.Thread(target=run, daemon=True, name="models").start()


def _model_key_from_name(filename: str) -> str | None:
    """Fayl nomidan model kalitini aniqlash: rtmpose-t_...zip, rtmpose_t.onnx, yolox_nano.onnx, yolox-tiny.onnx ..."""
    n = Path(filename or "").name.lower().replace("_", "-")
    for k in ("rtmpose-t", "rtmpose-s", "yolox-nano", "yolox-tiny"):
        if k in n:
            return k
    for k in ("thunder", "lightning"):              # 2.7.0: movenet_singlepose_thunder_4.onnx, movenet_thunder.onnx
        if "movenet" in n and k in n:
            return f"movenet-{k}"
    return None


@app.get("/api/models")
def api_models():
    from .ai import model_zoo, runtime
    from .ai.engine import Engine

    try:
        active = Engine.get().info()
    except Exception as e:  # noqa: BLE001
        active = {"error": str(e)}
    s = db.get_settings()
    bench = dict(active.get("runtime_info") or {})
    return {"active": active, "sets": model_zoo.status(), "model_set": s.get("model_set", model_zoo.DEFAULT_SET),
            "ai_runtime": s.get("ai_runtime", "auto"), "openvino": runtime.openvino_available(),
            "bench": bench, "state": models_state}


@app.post("/api/models/activate")
async def api_models_activate(request: Request):
    """Model to'plami va muhitni tanlash; modellar fonda qayta yuklanadi (kameralar to'xtamaydi)."""
    from .ai import model_zoo, runtime
    from .ai.engine import Engine

    d = await body_data(request)
    ms = d.get("model_set") or db.get_setting("model_set") or model_zoo.DEFAULT_SET
    rt = d.get("ai_runtime") or db.get_setting("ai_runtime") or "auto"
    if not isinstance(ms, str) or ms not in model_zoo.MODEL_SETS:
        raise HTTPException(400, "Noto'g'ri model to'plami")
    if not isinstance(rt, str) or rt not in ("auto", *runtime.RUNTIMES):
        raise HTTPException(400, "Noto'g'ri muhit")
    if model_zoo.missing_required(ms):        # buyum modeli ixtiyoriy (yo'q bo'lsa skelet va qoidalar ishlaydi)
        raise HTTPException(400, "Model fayllari yo'q: " + ", ".join(model_zoo.missing_required(ms)) +
                            " — avval yuklab oling")
    if rt == "openvino" and not runtime.openvino_available():
        raise HTTPException(400, "OpenVINO o'rnatilmagan: pip install openvino")

    def work():
        # Sozlama faqat yangi modellar muvaffaqiyatli yuklangach yoziladi (xato bo'lsa — eski holat qoladi)
        eng = Engine.reload(model_set=ms, runtime_pref=rt)
        if eng.model_set != ms:
            raise RuntimeError(f"{ms} yuklanmadi — {eng.model_set} ishlatilmoqda (jurnalga qarang)")
        db.set_settings({"model_set": ms, "ai_runtime": rt})
        return eng.info()
    _models_bg("yuklanmoqda", work)
    return {"ok": True}


@app.post("/api/models/benchmark")
def api_models_benchmark():
    """Shu kompyuterda ONNX Runtime va OpenVINO ni qayta o'lchash (auto rejim natijasi yangilanadi)."""
    from .ai.engine import Engine

    def work():
        db.set_settings({"runtime_auto": ""})
        return Engine.reload().info()
    _models_bg("o'lchanmoqda", work)
    return {"ok": True}


def _no_demo(what: str) -> None:
    """Demo rejimida models/ papkasiga (haqiqiy dastur bilan umumiy) yozadigan amallar o'chiq (2.6.0)."""
    if DEMO:
        raise HTTPException(400, f"Demo rejimida {what} o'chirilgan")


@app.post("/api/models/download")
async def api_models_download(request: Request):
    from .ai import model_zoo

    _no_demo("model yuklab olish")
    d = await body_data(request)
    ms = d.get("model_set", model_zoo.OPEN_SET)
    if not isinstance(ms, str) or ms not in model_zoo.MODEL_SETS:
        raise HTTPException(400, "Noto'g'ri model to'plami")
    _models_bg("yuklab olinmoqda", lambda: model_zoo.ensure_set(ms))
    return {"ok": True}


@app.post("/api/models/upload")
def api_models_upload(file: UploadFile = File(...)):
    """Internetdan yuklab bo'lmasa: rasmiy saytdan olingan zip/onnx faylni qo'lda yuklash."""
    from .ai import model_zoo

    _no_demo("model yuklash")
    name = (file.filename or "").lower()
    key = _model_key_from_name(name)
    if key not in model_zoo.FILES:
        raise HTTPException(400, "Fayl nomidan model aniqlanmadi (movenet_thunder, movenet_lightning, yolox_nano, "
                                 "yolox_tiny, rtmpose-t, rtmpose-s)")
    _models_claim("qo'lda yuklanmoqda")
    tmp = UPLOADS_DIR / f"_model_{int(time.time())}_{Path(name).name}"
    dst = model_zoo.path(key)
    bak = dst.with_suffix(".bak")
    try:
        total = 0
        with open(tmp, "wb") as f:             # sinxron: og'ir ish server oqimini bloklamaydi
            while True:
                chunk = file.file.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MODEL_UPLOAD_LIMIT:      # hajm chegarasi: 8 GB xotirali kompyuter to'lib qolmasin
                    raise HTTPException(413, f"Fayl juda katta (chegara {MODEL_UPLOAD_LIMIT // (1 << 20)} MB)")
                f.write(chunk)
        try:
            if dst.exists():
                dst.replace(bak)
            # ensure: tekshiradi (ochiladi, kirish shakli to'g'ri) va faqat keyin models/ ga ko'chiradi
            model_zoo.ensure(key, local_zip=tmp)
            bak.unlink(missing_ok=True)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            if bak.exists():
                bak.replace(dst)          # eski fayl qaytariladi
            raise HTTPException(400, f"Model fayli qabul qilinmadi: {e}")
    finally:
        tmp.unlink(missing_ok=True)
        models_state["busy"] = False
    return {"ok": True, "model": key}


@app.post("/api/model/train")
async def api_train(request: Request):
    _no_demo("model o'qitish")
    if train_state["running"]:
        raise HTTPException(409, "O'qitish allaqachon ketmoqda")
    d = await body_data(request)
    try:
        n_syn = int(d.get("synthetic") or 3000)
    except (TypeError, ValueError):
        raise HTTPException(400, "synthetic: son kiriting")
    pl = (d.get("poselift") or "").strip() or None
    use_fb = str(d.get("feedback", "1")) in ("1", "on", "true")
    # 2.3.0: panel sukut bo'yicha challenger (soya) model o'qitadi — champion darhol almashmaydi
    as_ch = str(d.get("challenger", "0")) in ("1", "on", "true")
    if pl and not Path(pl).exists():
        raise HTTPException(400, "PoseLift papkasi topilmadi")
    train_state.update(running=True, logs=[], result=None, error=None, started=time.time())

    def run():
        from .ml.train import train

        try:
            res = train(n_synthetic=max(0, min(n_syn, 20000)), poselift_dir=pl, use_feedback=use_fb,
                        log_fn=lambda m: train_state["logs"].append(f"{time.strftime('%H:%M:%S')}  {m}"),
                        challenger=as_ch, activate=not as_ch)
            train_state["result"] = {"metrics": res["metrics"], "path": Path(res["path"]).name}
        except Exception as e:
            train_state["error"] = str(e)
            train_state["logs"].append(f"XATO: {e}")
            log.exception("O'qitish xatosi")
        finally:
            train_state["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/model/train/status")
def api_train_status():
    return train_state


@app.get("/api/model/champion")
def api_model_champion():
    """2.3.0: champion / challenger solishtiruvi (app/ml/champion.py)."""
    from .ml import champion

    return champion.compare()


@app.post("/api/model/challenger/promote")
def api_challenger_promote():
    from .ml import champion

    if train_state["running"]:
        raise HTTPException(409, "O'qitish ketmoqda — tugashini kuting")
    try:
        return {"ok": True, **champion.promote()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/model/challenger/drop")
def api_challenger_drop():
    from .ml import champion

    champion.drop()
    return {"ok": True}


@app.post("/api/model/{version_id}/activate")
def api_model_activate(version_id: int):
    from .ml.model import BehaviorModel

    v = db.row("SELECT * FROM model_versions WHERE id=?", (version_id,))
    if not v or not (MODELS_DIR / v["path"]).exists():
        raise HTTPException(404, "Model fayli topilmadi")
    prev = BehaviorModel.active_path()
    db.execute("UPDATE model_versions SET active=0")
    db.execute("UPDATE model_versions SET active=1 WHERE id=?", (version_id,))
    BehaviorModel.set_active(MODELS_DIR / v["path"])
    if prev is not None and prev.name == v["path"]:
        return {"ok": True}                       # champion o'zgarmadi — challenger soyasi davom etadi
    ch = BehaviorModel.challenger_path()
    if ch is not None and ch.name == v["path"]:
        BehaviorModel.set_challenger(None)        # challenger qo'lda faollashtirildi — endi u champion
        db.execute("UPDATE model_versions SET role='champion', promoted_at=? WHERE id=?", (time.time(), version_id))
    elif ch is not None:
        from .ml import champion

        champion.drop("eskirgan")                 # champion almashdi — soya solishtiruvi eski champion bilan edi
    return {"ok": True}


# ----------------------------------------------------------------------------- signal tezligi
@app.get("/api/latency")
def api_latency(hours: float = 24):
    """Hodisa aniqlangandan xabar yetkazilguncha bo'lgan vaqt (soniya), kanal va bosqich bo'yicha."""
    hours = max(0.1, min(hours, 24 * 30))
    recent = db.rows("SELECT n.*, e.kind, e.source_name FROM notify_log n LEFT JOIN events e ON e.id = n.event_id "
                     "ORDER BY n.id DESC LIMIT 30")
    for r in recent:
        r["latency"] = round(r["t_sent"] - r["t_event"], 2) if r.get("t_event") and r.get("t_sent") else None
        r["time_str"] = time.strftime("%H:%M:%S", time.localtime(r["t_sent"])) if r.get("t_sent") else ""
    return {"hours": hours, "stats": latency_stats(hours), "recent": recent, "target_sec": 5}


@app.get("/api/sse")
async def api_events_stream(request: Request):
    """Web panel uchun jonli oqim (SSE): yangi hodisa, klip tayyor, holat o'zgardi — darhol."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)

    def put(data: str) -> None:
        if not q.full():
            q.put_nowait(data)

    def listener(kind: str, ev: dict) -> None:
        try:
            data = json.dumps({"kind": kind, "event": event_public(ev)}, ensure_ascii=False, default=str)
            loop.call_soon_threadsafe(put, data)
        except Exception:
            pass

    mgr = get_manager()
    mgr.listeners.append(listener)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            idle = 0.0
            while not SHUTDOWN.is_set():
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=1.0)
                    idle = 0.0
                    yield f"event: ev\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": ping\n\n"
        finally:
            if listener in mgr.listeners:
                mgr.listeners.remove(listener)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------------- Telegram
@app.get("/api/telegram")
def api_telegram():
    subs = db.rows("SELECT * FROM subscribers ORDER BY created_at DESC")
    for s in subs:
        s["created_str"] = time.strftime("%d.%m.%Y %H:%M", time.localtime(s["created_at"]))
    info = get_bot().info()
    return {"bot": info, "code": db.get_setting("connect_code"), "subscribers": subs,
            "link": f"https://t.me/{info['username']}" if info.get("username") else None,
            "deep_link": (f"https://t.me/{info['username']}?start={db.get_setting('connect_code')}"
                          if info.get("username") else None)}


@app.post("/api/telegram/test")
def api_telegram_test():
    n = get_bot().send_test()
    if not n:
        raise HTTPException(400, "Hali obunachi yo'q. Avval botga ulanish kodini yuboring.")
    return {"ok": True, "count": n}


@app.post("/api/telegram/new-code")
def api_telegram_code():
    import secrets

    code = f"{secrets.randbelow(900000) + 100000}"
    db.set_settings({"connect_code": code})
    return {"ok": True, "code": code}


@app.post("/api/telegram/subscribers/{chat_id}/delete")
def api_sub_delete(chat_id: int):
    db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
    return {"ok": True}


@app.post("/api/telegram/demo-alert")
def api_demo_alert():
    """So'nggi hodisani Telegramga qayta yuborish (sinov uchun)."""
    ev = db.row("SELECT id FROM events ORDER BY id DESC LIMIT 1")
    if not ev:
        raise HTTPException(400, "Hali hodisa yo'q")
    get_bot().resend_event(ev["id"])
    return {"ok": True, "event_id": ev["id"]}


# ----------------------------------------------------------------------------- sozlamalar
NUMERIC = {"threshold": (0, 100), "dwell_sec": (0.1, 3), "pre_sec": (1, 30), "post_sec": (1, 30),
           "cooldown_sec": (0, 600), "ai_fps": (1, 30), "calm_fps": (1, 30), "infer_size": (320, 1280),
           "person_conf": (0.1, 0.95),
           "object_conf": (0.1, 0.95), "ml_weight": (0, 60), "telegram_min_score": (0, 100),
           "retention_days": (0, 3650), "camera_alert_sec": (15, 3600)}
FLAGS = ["use_ml", "signal_pocket", "signal_torso", "signal_bag", "signal_vanish", "signal_looking",
         "signal_offscreen", "telegram_enabled", "telegram_send_video", "personal_items", "camera_alerts",
         "show_objects", "signal_exit"]
# 3.2.0: Sozlamalar sahifasidagi formada BOR bayroqlar. Forma yuborilganda faqat shular "belgilanmagan = o'chiq" deb
# olinadi. (Avval FLAGS ning hammasi olinardi: Telegram sahifasidagi "Telegramga yuborish", "Video klip" va "Kamera
# uzilsa xabar" Sozlamalarni saqlash bilan jimgina o'chib qolardi.)
SETTINGS_FORM_FLAGS = ["signal_pocket", "signal_torso", "signal_bag", "signal_vanish", "signal_looking",
                       "signal_offscreen", "signal_exit", "personal_items", "use_ml", "show_objects"]


@app.get("/api/settings")
def api_get_settings():
    return db.get_settings(force=True)


@app.post("/api/settings")
async def api_save_settings(request: Request):
    d = await body_data(request)
    vals: dict = {}
    if "sensitivity" in d and d["sensitivity"] in SENSITIVITY_PRESETS:
        vals["sensitivity"] = d["sensitivity"]
        if d.get("apply_preset", "1") in ("1", "true", "on"):
            p = SENSITIVITY_PRESETS[d["sensitivity"]]
            vals["threshold"] = p["threshold"]
            vals["dwell_sec"] = p["dwell"]
    for k, (lo, hi) in NUMERIC.items():
        if k in d and str(d[k]).strip() != "" and k not in vals:
            try:
                v = float(d[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k}: son kiriting")
            if v != v or v in (float("inf"), float("-inf")):
                raise HTTPException(400, f"{k}: son kiriting")
            vals[k] = max(lo, min(hi, v))
    if d.get("_form") == "full":
        for k in SETTINGS_FORM_FLAGS:
            vals[k] = "1" if str(d.get(k, "0")) in ("1", "on", "true") else "0"
    else:
        for k in FLAGS:
            if k in d:
                vals[k] = "1" if str(d[k]) in ("1", "on", "true") else "0"
    if "store_name" in d:
        vals["store_name"] = str(d["store_name"])[:80]
    db.set_settings(vals)
    if request.headers.get("accept", "").startswith("text/html") or d.get("_form") == "full":
        return RedirectResponse("/settings?saved=1", status_code=303)
    return {"ok": True, "settings": db.get_settings(force=True)}


# ----------------------------------------------------------------------------- tizim
@app.get("/api/system")
def api_system():
    info = {"python": sys.version.split()[0], "os": f"{platform.system()} {platform.release()}",
            "opencv": cv2.__version__, "app": APP_VERSION}
    try:
        import onnxruntime as ort

        info["onnxruntime"] = ort.__version__
        info["providers"] = ort.get_available_providers()
    except Exception:
        pass
    try:
        import psutil

        info["cpu"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        info["ram"] = vm.percent
        info["ram_total_gb"] = round(vm.total / 1e9, 1)
        info["cpu_count"] = psutil.cpu_count()
    except Exception:
        pass
    clips = list(CLIPS_DIR.glob("*"))
    info["clips_mb"] = round(sum(p.stat().st_size for p in clips) / 1e6, 1)
    info["uploads_mb"] = round(sum(p.stat().st_size for p in UPLOADS_DIR.glob("*")) / 1e6, 1)
    du = shutil.disk_usage(DATA_DIR)
    info["disk_free_gb"] = round(du.free / 1e9, 1)
    from .ai.clipper import ffmpeg_exe

    info["ffmpeg"] = bool(ffmpeg_exe())
    info["events_total"] = db.scalar("SELECT COUNT(*) FROM events") or 0
    info["sources"] = get_manager().source_info()
    info["telegram"] = get_bot().info()
    info["logs"] = list(MEMORY_LOG)[-150:]
    info["camera_health"] = [{"source_id": sid, **h} for sid, h in get_manager().health.items()]
    try:
        from .ai.engine import ai_threads

        info["ai_threads"] = ai_threads()
    except Exception:
        pass
    info["base_dir"] = str(BASE_DIR)
    try:
        info["sync"] = sync.get_worker().status()
    except Exception:  # noqa: BLE001
        info["sync"] = {"configured": False}
    return info


# ----------------------------------------------------------------------------- bulut (edge agent)
@app.get("/api/sync/status")
def api_sync_status():
    return sync.get_worker().status()


@app.post("/api/sync/test")
def api_sync_test():
    """Bulut bilan ulanishni hozir tekshirish (hello)."""
    w = sync.get_worker()
    if not sync.configured():
        raise HTTPException(400, ".env da CLOUD_URL va EDGE_TOKEN berilmagan")
    ok = w.hello()
    if ok:
        w.auth_failed = False
        w.state = "ishlayapti"
        db.execute("UPDATE outbox SET next_try=0 WHERE sending=0 AND failed=0 AND next_try > 0")
        w.kick()
    return {"ok": ok, "error": None if ok else w.last_error, "status": w.status()}


@app.post("/api/sync/retry")
def api_sync_retry():
    """Rad etilgan (xato deb belgilangan) qatorlarni qayta navbatga qo'yish."""
    n = db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=1") or 0
    db.execute("UPDATE outbox SET failed=0, attempts=0, next_try=0, last_error=NULL WHERE failed=1")
    sync.get_worker().kick()
    return {"ok": True, "count": n}


@app.get("/api/metrics")
def api_metrics(hours: float = 24, source_id: int | None = None):
    """Daqiqalik ko'rsatkichlar; 2 kundan uzun oraliq soatlik o'rtachaga yig'iladi (javob katta bo'lmasin)."""
    hours = max(0.1, min(hours, 24 * 90))
    rows = metrics.recent_hourly(hours, source_id) if hours > 48 else metrics.recent(hours, source_id)
    return {"rows": rows[-20000:], "step": "hour" if hours > 48 else "minute"}
