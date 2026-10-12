"""Web panel (FastAPI): sahifalar va API."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import platform
import re
import shutil
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
from starlette.middleware.sessions import SessionMiddleware

from . import db
from .ai.manager import delete_event_files, get_manager
from .config import (ADMIN_PASSWORD, ADMIN_USER, APP_NAME, APP_VERSION, BASE_DIR, CLIPS_DIR, DATA_DIR,
                     MODELS_DIR, SECRET_KEY, SENSITIVITY_PRESETS, UPLOADS_DIR)
from .logutil import MEMORY_LOG, get_logger, setup_logging
from .telegram_bot import KIND_UZ, get_bot

log = get_logger("web")
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

PUBLIC_PATHS = ("/login", "/static", "/favicon.ico", "/healthz")
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".3gp", ".ts"}

NAV = [
    ("dashboard", "/", "Boshqaruv paneli", "grid"),
    ("live", "/live", "Jonli kuzatuv", "eye"),
    ("upload", "/upload", "Video tahlili", "upload"),
    ("events", "/events", "Hodisalar", "alert"),
    ("sources", "/sources", "Kameralar", "camera"),
    ("model", "/model", "AI model", "cpu"),
    ("telegram", "/telegram", "Telegram bot", "send"),
    ("reports", "/reports", "Hisobotlar", "chart"),
    ("settings", "/settings", "Sozlamalar", "sliders"),
    ("system", "/system", "Tizim holati", "activity"),
    ("help", "/help", "Yo'riqnoma", "book"),
]

train_state = {"running": False, "logs": [], "result": None, "error": None, "started": 0.0}


# ----------------------------------------------------------------------------- hayot sikli
def _startup() -> None:
    setup_logging()
    db.init_db()
    if not db.row("SELECT id FROM sources WHERE kind='webcam'"):
        db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                   ("Kompyuter kamerasi", "webcam", "0", 0, 1, time.time()))
    mgr = get_manager()
    bot = get_bot()
    bot.manager = mgr
    mgr.notifier = bot
    bot.start()
    threading.Thread(target=_warmup, daemon=True).start()
    mgr.autostart()
    log.info("%s %s ishga tushdi", APP_NAME, APP_VERSION)


def _warmup() -> None:
    try:
        from .ai.engine import Engine
        from .ml.model import BehaviorModel

        Engine.get()
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


def _shutdown() -> None:
    get_manager().stop_all()
    get_bot().stop()


# ----------------------------------------------------------------------------- yordamchilar
@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    if not path.startswith(PUBLIC_PATHS) and not request.session.get("user"):
        if path.startswith(("/api", "/stream", "/media")):
            return JSONResponse({"detail": "Kirish talab qilinadi"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)
    return await call_next(request)


# Sessiya middleware'i auth middleware'idan keyin qo'shiladi — shunda u tashqi qavat bo'ladi
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="hushyor_session",
                   max_age=14 * 24 * 3600, same_site="lax")


def page(request: Request, name: str, active: str, title: str, **ctx) -> HTMLResponse:
    s = db.get_settings()
    subs = db.scalar("SELECT COUNT(*) FROM subscribers WHERE active=1") or 0
    pending = db.scalar("SELECT COUNT(*) FROM events WHERE status='yangi'") or 0
    base = {"nav": NAV, "active": active, "title": title, "settings": s, "app_name": APP_NAME,
            "version": APP_VERSION, "user": request.session.get("user"), "subs": subs, "pending": pending,
            "presets": SENSITIVITY_PRESETS, "kind_uz": KIND_UZ}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def day_start(ts: float | None = None) -> float:
    lt = time.localtime(ts or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def event_public(ev: dict) -> dict:
    ev = dict(ev)
    ev["kind_label"] = KIND_UZ.get(ev.get("kind"), ev.get("kind"))
    ev["clip_url"] = f"/media/clips/{ev['clip_path']}" if ev.get("clip_path") else None
    ev["thumb_url"] = f"/media/clips/{ev['thumb_path']}" if ev.get("thumb_path") else None
    ev["crop_url"] = f"/media/clips/{ev['crop_path']}" if ev.get("crop_path") else None
    ev["time_str"] = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ev["created_at"]))
    ev["reviewed_str"] = (time.strftime("%d.%m.%Y %H:%M", time.localtime(ev["reviewed_at"]))
                          if ev.get("reviewed_at") else None)
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
            return await request.json()
        except ValueError:
            return {}
    form = await request.form()
    return {k: v for k, v in form.items()}


# ----------------------------------------------------------------------------- kirish
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"app_name": APP_NAME, "next": next, "error": error,
                                                              "default_pw": ADMIN_PASSWORD == "admin123"})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        request.session["user"] = username
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        return RedirectResponse(next or "/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": APP_VERSION}


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
    return page(request, "upload.html", "upload", "Video tahlili")


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
    if not re.fullmatch(r"[\w\-.]+", name):
        raise HTTPException(400)
    p = CLIPS_DIR / name
    if not p.exists():
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
    while True:
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
    return StreamingResponse(_mjpeg(lambda: mgr.preview(source_id), request),
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
    today = cnt("SELECT COUNT(*) FROM events WHERE created_at>=?", d0)
    ok = cnt("SELECT COUNT(*) FROM events WHERE created_at>=? AND status='tasdiqlangan'", d0)
    no = cnt("SELECT COUNT(*) FROM events WHERE created_at>=? AND status='rad_etilgan'", d0)
    pending = cnt("SELECT COUNT(*) FROM events WHERE status='yangi'")
    all_ok = cnt("SELECT COUNT(*) FROM events WHERE status='tasdiqlangan'")
    all_no = cnt("SELECT COUNT(*) FROM events WHERE status='rad_etilgan'")
    hours = [0] * 24
    for r in db.rows("SELECT created_at FROM events WHERE created_at>=?", (d0,)):
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
    }


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
                      0 if str(d.get("loop", "1")) in ("0", "false") else 1, time.time()))
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
                     (f"Video (jonli): {name}"[:60], "file", str(path), 0, 1, time.time()))
    get_manager().start_source(sid)
    return sid


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), mode: str = Form("fast")):
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
    jid = get_manager().submit_job(dest, file.filename)
    return {"ok": True, "mode": "fast", "job_id": jid}


download_state: dict[str, dict] = {}


@app.post("/api/upload-url")
async def api_upload_url(request: Request):
    d = await body_data(request)
    url = (d.get("url") or "").strip()
    mode = d.get("mode", "fast")
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Havola http:// yoki https:// bilan boshlanishi kerak")
    if re.search(r"(youtube\.com|youtu\.be|instagram\.com|tiktok\.com|facebook\.com)", url):
        raise HTTPException(400, "YouTube/Instagram/TikTok havolalari to'g'ridan-to'g'ri yuklanmaydi. "
                                 "Videoni kompyuteringizga yuklab olib, fayl sifatida yuboring.")
    key = str(int(time.time() * 1000))
    download_state[key] = {"status": "yuklanmoqda", "bytes": 0, "error": None, "job_id": None}

    def worker():
        name = _safe_name(url.split("?")[0].rstrip("/").split("/")[-1] or "video.mp4")
        if Path(name).suffix.lower() not in VIDEO_EXT:
            name += ".mp4"
        dest = UPLOADS_DIR / name
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
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
            cap = cv2.VideoCapture(str(dest))
            ok = cap.isOpened() and cap.read()[0]
            cap.release()
            if not ok:
                raise ValueError("Yuklangan faylni video sifatida o'qib bo'lmadi")
            if mode == "live":
                download_state[key]["source_id"] = _start_as_live(dest, name)
            else:
                download_state[key]["job_id"] = get_manager().submit_job(dest, name)
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
    get_manager().stop_job(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/delete")
def api_job_delete(job_id: int):
    j = db.row("SELECT * FROM jobs WHERE id=?", (job_id,))
    if j:
        get_manager().stop_job(job_id)
        if j.get("path") and Path(j["path"]).parent == UPLOADS_DIR:
            Path(j["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"ok": True}


# ----------------------------------------------------------------------------- hodisalar
@app.get("/api/events")
def api_events(status: str = "", source: str = "", job: int = 0, days: int = 0, q: str = "",
               page: int = 1, per: int = 24):
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
    rows = db.rows(f"SELECT * FROM events {w} ORDER BY id DESC LIMIT ? OFFSET ?",
                   [*params, per, (max(page, 1) - 1) * per])
    return {"total": total, "page": page, "per": per,
            "items": [event_public(db.decode_event(r)) for r in rows]}


@app.get("/api/events/{event_id}")
def api_event(event_id: int):
    ev = db.get_event(event_id)
    if not ev:
        raise HTTPException(404)
    return event_public(ev)


@app.post("/api/events/{event_id}/review")
async def api_review(request: Request, event_id: int):
    d = await body_data(request)
    status = d.get("status")
    if status not in ("tasdiqlangan", "rad_etilgan", "yangi"):
        raise HTTPException(400, "Noto'g'ri holat")
    note = d.get("note")
    ev = db.review_event(event_id, status, f"Web: {request.session.get('user')}", note)
    if not ev:
        raise HTTPException(404)
    if status == "yangi":
        db.update_event(event_id, reviewed_by=None, reviewed_at=None)
    get_bot().refresh_event(event_id)
    return {"ok": True, "event": event_public(db.get_event(event_id))}


@app.post("/api/events/{event_id}/note")
async def api_note(request: Request, event_id: int):
    d = await body_data(request)
    db.update_event(event_id, note=(d.get("note") or "")[:2000])
    return {"ok": True}


@app.post("/api/events/{event_id}/delete")
def api_event_delete(event_id: int):
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    if ev:
        delete_event_files(ev)
        db.execute("DELETE FROM events WHERE id=?", (event_id,))
        db.execute("DELETE FROM tg_messages WHERE event_id=?", (event_id,))
    return {"ok": True}


@app.post("/api/events/{event_id}/resend")
def api_event_resend(event_id: int):
    bot = get_bot()
    if not bot.token:
        raise HTTPException(400, "Telegram token berilmagan")
    bot.q.put(("event", event_id, None))
    return {"ok": True}


@app.post("/api/events/bulk")
async def api_events_bulk(request: Request):
    d = await body_data(request)
    action = d.get("action")
    if action == "delete_rejected":
        evs = db.rows("SELECT * FROM events WHERE status='rad_etilgan'")
        for ev in evs:
            delete_event_files(ev)
            db.execute("DELETE FROM events WHERE id=?", (ev["id"],))
        return {"ok": True, "count": len(evs)}
    raise HTTPException(400)


@app.get("/api/export/events.csv")
def api_export():
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["ID", "Vaqt", "Kamera", "Turi", "Ball", "Daraja", "Holat", "Kim tekshirdi", "Izoh", "Sabablar", "Klip"])
    for e in db.rows("SELECT * FROM events ORDER BY id DESC"):
        e = db.decode_event(e)
        wr.writerow([e["id"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["created_at"])), e["source_name"],
                     KIND_UZ.get(e["kind"], e["kind"]), round(e["score"] or 0), e["level"], e["status"],
                     e.get("reviewed_by") or "", e.get("note") or "", " | ".join(e["reasons"] or []),
                     e.get("clip_path") or ""])
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
    review_mins.sort()
    return {
        "days": days, "total": len(evs), "ok": ok, "no": no, "pending": len(evs) - ok - no,
        "precision": ok / (ok + no) if ok + no else None,
        "median_review_min": review_mins[len(review_mins) // 2] if review_mins else None,
        "daily": daily, "by_kind": by_kind, "by_source": by_source, "hours": hours,
        "avg_score_ok": (sum(e["score"] for e in evs if e["status"] == "tasdiqlangan") / ok) if ok else None,
        "avg_score_no": (sum(e["score"] for e in evs if e["status"] == "rad_etilgan") / no) if no else None,
    }


# ----------------------------------------------------------------------------- AI model
@app.get("/api/model")
def api_model():
    from .ml.model import BehaviorModel

    versions = db.rows("SELECT * FROM model_versions ORDER BY id DESC")
    active_path = BehaviorModel.active_path()
    for v in versions:
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
    fb_ok = db.scalar("SELECT COUNT(*) FROM events WHERE status='tasdiqlangan'") or 0
    fb_no = db.scalar("SELECT COUNT(*) FROM events WHERE status='rad_etilgan'") or 0
    from .ai.engine import Engine

    try:
        providers = Engine.get().providers
    except Exception as e:
        providers = [f"xato: {e}"]
    return {"versions": versions, "bundled": bundled, "feedback": {"ok": fb_ok, "no": fb_no},
            "train": {k: train_state[k] for k in ("running", "error", "started")},
            "pose_model": "YOLO11n-pose (ONNX)", "det_model": "YOLO11n (ONNX)", "providers": providers,
            "use_ml": db.get_setting("use_ml"), "ml_weight": db.get_setting("ml_weight")}


@app.post("/api/model/train")
async def api_train(request: Request):
    if train_state["running"]:
        raise HTTPException(409, "O'qitish allaqachon ketmoqda")
    d = await body_data(request)
    n_syn = int(d.get("synthetic") or 3000)
    pl = (d.get("poselift") or "").strip() or None
    use_fb = str(d.get("feedback", "1")) in ("1", "on", "true")
    if pl and not Path(pl).exists():
        raise HTTPException(400, "PoseLift papkasi topilmadi")
    train_state.update(running=True, logs=[], result=None, error=None, started=time.time())

    def run():
        from .ml.train import train

        try:
            res = train(n_synthetic=max(0, min(n_syn, 20000)), poselift_dir=pl, use_feedback=use_fb,
                        log_fn=lambda m: train_state["logs"].append(f"{time.strftime('%H:%M:%S')}  {m}"))
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


@app.post("/api/model/{version_id}/activate")
def api_model_activate(version_id: int):
    from .ml.model import BehaviorModel

    v = db.row("SELECT * FROM model_versions WHERE id=?", (version_id,))
    if not v or not (MODELS_DIR / v["path"]).exists():
        raise HTTPException(404, "Model fayli topilmadi")
    db.execute("UPDATE model_versions SET active=0")
    db.execute("UPDATE model_versions SET active=1 WHERE id=?", (version_id,))
    BehaviorModel.set_active(MODELS_DIR / v["path"])
    return {"ok": True}


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
    get_bot().q.put(("event", ev["id"], None))
    return {"ok": True, "event_id": ev["id"]}


# ----------------------------------------------------------------------------- sozlamalar
NUMERIC = {"threshold": (0, 100), "dwell_sec": (0.1, 3), "pre_sec": (1, 20), "post_sec": (1, 20),
           "cooldown_sec": (0, 600), "ai_fps": (1, 30), "infer_size": (320, 1280), "person_conf": (0.1, 0.95),
           "object_conf": (0.1, 0.95), "ml_weight": (0, 60), "telegram_min_score": (0, 100),
           "retention_days": (0, 3650)}
FLAGS = ["use_ml", "signal_pocket", "signal_torso", "signal_bag", "signal_vanish", "signal_looking",
         "signal_offscreen", "telegram_enabled", "telegram_send_video"]


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
            except ValueError:
                raise HTTPException(400, f"{k}: son kiriting")
            vals[k] = max(lo, min(hi, v))
    if d.get("_form") == "full":
        for k in FLAGS:
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
    info["base_dir"] = str(BASE_DIR)
    return info
