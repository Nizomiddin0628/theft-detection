"""Xavfsizlik sahifasi API (2.2.0, 6-bosqich): signallar, qoidalar sozlamasi, favqulodda chiqish zonalari, SMS va
yiqilish darvozasi (belgilangan videolar + jonli ❌ javoblar). Sahifa: /xavfsizlik (app/web.py)."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import db, safety_eval, sms
from .ai import safety
from .config import APP_VERSION, DATA_DIR
from .logutil import get_logger

log = get_logger("safety_web")
router = APIRouter()
HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
VIDEO_DIR = DATA_DIR / "safety_videos"
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".3gp", ".ts"}
SMS_KEYS = ("sms_enabled", "sms_provider", "sms_phones", "sms_kinds", "sms_max_per_hour")


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise HTTPException(400, "JSON obyekt kerak")
    return d


def _day0(t: float | None = None) -> float:
    lt = time.localtime(t or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _manager():
    from .ai.manager import get_manager

    return get_manager()


def public_settings() -> dict:
    s = db.get_settings()
    out = {k: s.get(k, v) for k, v in safety.DEFAULTS.items()}
    out.update({k: s.get(k, "") for k in SMS_KEYS})
    out["work_hours"] = s.get("work_hours", "")
    return out


# ----------------------------------------------------------------------------- holat
@router.get("/api/safety/summary")
def safety_summary(days: int = 1):
    days = max(1, min(int(days), 90))
    now = time.time()
    t0 = _day0() - (days - 1) * 86400
    marks = ",".join("?" for _ in safety.SAFETY_KINDS)
    evs = db.rows(f"SELECT id, created_at, kind, status, source_name, source_id, thumb_path, reasons, reviewed_by "
                  f"FROM events WHERE job_id IS NULL AND kind IN ({marks}) AND created_at>=? ORDER BY id DESC LIMIT 200",
                  (*safety.SAFETY_KINDS, t0))
    for e in evs:
        e["kind_label"] = safety.KIND_LABELS.get(e["kind"], e["kind"])
        e["thumb_url"] = f"/media/clips/{e['thumb_path']}" if e.get("thumb_path") else None
        try:
            e["reasons"] = json.loads(e.get("reasons") or "[]")[:3]
        except ValueError:
            e["reasons"] = []
    s = db.get_settings()
    sched = safety.night_schedule(s)
    cams = []
    try:
        for w in list(_manager().workers.values()):
            if not w.is_alive():
                continue
            mon = getattr(w.proc, "safety", None)
            info = mon.info() if mon is not None else {}
            cams.append({"id": w.src["id"], "name": w.src["name"], "state": w.state, **info})
    except Exception:  # noqa: BLE001
        pass
    ok, why = sms.configured(s)
    return {"counts": safety.today_counts(_day0()), "events": evs, "days": days, "kinds": safety.KIND_LABELS,
            "settings": public_settings(), "night": {"text": sched["text"], "mode": sched["mode"],
                                                     "now": safety.is_night(now, sched)},
            "store_inside": safety.STORE_CROWD.inside, "cams": cams, "exits": exits_list(),
            "sms": {"configured": ok, "why": why, "phones": [sms.mask(p) for p in sms.phones(s)],
                    "kinds": sms.kinds(s), "sent_hour": sms.sent_last_hour(), "recent": sms.recent(15),
                    "kind_ascii": sms.KIND_ASCII},
            "critical": list(safety.CRITICAL)}


@router.post("/api/safety/settings")
async def safety_settings(request: Request):
    d = await _body(request)
    vals: dict = {}
    for k in safety.FLAGS + ("sms_enabled",):
        if k in d:
            vals[k] = "1" if str(d[k]).lower() in ("1", "true", "on", "yes") else "0"
    for k, (lo, hi) in {**safety.RANGES, "sms_max_per_hour": (1, 60)}.items():
        if k in d and str(d[k]).strip() != "":
            try:
                v = float(d[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k}: son kiriting")
            if not np.isfinite(v):
                raise HTTPException(400, f"{k}: son kiriting")
            v = max(lo, min(hi, v))
            vals[k] = str(round(v, 3)) if k == "safety_blocked_frac" else str(int(round(v)))
    if "safety_fall_sens" in d:
        if not isinstance(d["safety_fall_sens"], str) or d["safety_fall_sens"] not in safety.SENS:
            raise HTTPException(400, "Sezgirlik: normal yoki high")
        vals["safety_fall_sens"] = d["safety_fall_sens"]
    if "safety_night_hours" in d:
        v = str(d["safety_night_hours"] or "").strip().replace(" ", "")
        if v and (not HOURS_RE.match(v) or v[:5] == v[6:]):
            raise HTTPException(400, "Tungi jadval: SS:DD-SS:DD (masalan 22:30-07:30) yoki bo'sh")
        vals["safety_night_hours"] = v
    if "sms_provider" in d:
        if not isinstance(d["sms_provider"], str) or d["sms_provider"] not in sms.PROVIDERS:
            raise HTTPException(400, "SMS shlyuzi: eskiz yoki http")
        vals["sms_provider"] = d["sms_provider"]
    prov = vals.get("sms_provider", db.get_setting("sms_provider", "eskiz"))
    if "sms_phones" in d:
        raw = [p for p in re.split(r"[,;\n]+", str(d["sms_phones"] or "")) if p.strip()]
        good, bad = [], []
        for p in raw:
            n = sms.normalize_phone(p, prov)
            (good if n else bad).append(n or p.strip())
        if bad:
            raise HTTPException(400, "Noto'g'ri raqam: " + ", ".join(bad[:5])
                                + (" (Eskiz: 998XXXXXXXXX)" if prov == "eskiz" else ""))
        if len(good) > 10:
            raise HTTPException(400, "Ko'pi bilan 10 ta raqam")
        vals["sms_phones"] = ", ".join(dict.fromkeys(good))
    if "sms_kinds" in d:
        ks = d["sms_kinds"] if isinstance(d["sms_kinds"], list) else re.split(r"[,\s]+", str(d["sms_kinds"] or ""))
        vals["sms_kinds"] = ",".join(k for k in safety.SAFETY_KINDS if k in ks)
    db.set_settings(vals)
    return {"ok": True, "settings": public_settings()}


# ----------------------------------------------------------------------------- favqulodda chiqishlar
def exits_list() -> list[dict]:
    from .ai import zones as zones_mod

    out = []
    for z in db.rows("SELECT z.*, s.name AS source_name FROM zones z LEFT JOIN sources s ON s.id=z.source_id "
                     "WHERE z.kind=? ORDER BY z.id", (safety.EXIT_KIND,)):
        ref = db.row("SELECT * FROM safety_exits WHERE zone_id=?", (z["id"],))
        stale = False
        if ref:
            try:
                pts = zones_mod.parse_points(z["points"], z["kind"])
                stale = pts is None or not np.allclose(np.asarray(json.loads(ref["points"]), dtype=np.float32),
                                                       np.round(pts, 4), atol=2e-3)
            except (TypeError, ValueError):
                stale = True
        st = safety.EXIT_STATE.get(int(z["id"]), {})
        out.append({"id": z["id"], "name": z["name"], "source_id": z["source_id"], "source_name": z["source_name"],
                    "enabled": int(z["enabled"] or 0), "remembered_at": ref["created_at"] if ref else None,
                    "stale": stale, "frac": st.get("frac"), "why": st.get("why"), "checked_at": st.get("checked_at"),
                    "blocked_since": st.get("blocked_since")})
    return out


@router.get("/api/safety/exits")
def safety_exits():
    return {"exits": exits_list(), "frac": db.get_setting("safety_blocked_frac", "0.35")}


def _live_frame(source_id: int) -> tuple[np.ndarray | None, list | None]:
    """Kameraning oxirgi kadri va undagi odamlar ramkalari (kamera ishlamasa — qisqa ochib kadr, odamlar noma'lum)."""
    mgr = _manager()
    w = mgr.workers.get(source_id)
    if w and w.is_alive():
        with w.frame_lock:
            frame = None if w.latest_frame is None else w.latest_frame.copy()
        mon = getattr(w.proc, "safety", None)
        boxes = list(mon.person_boxes) if mon is not None else None
        if frame is not None:
            return frame, boxes
    data = mgr.raw_snapshot(source_id, fresh=True)
    if not data:
        return None, None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return img, None


@router.post("/api/safety/exits/{zone_id}/remember")
def exit_remember(zone_id: int):
    z = db.row("SELECT * FROM zones WHERE id=? AND kind=?", (zone_id, safety.EXIT_KIND))
    if not z:
        raise HTTPException(404, "Favqulodda chiqish zonasi topilmadi")
    frame, boxes = _live_frame(int(z["source_id"]))
    if frame is None:
        raise HTTPException(400, "Kameradan kadr olinmadi — kamerani yoqing")
    try:
        res = safety.remember_exit(z, frame, boxes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    note = None if boxes is not None else ("Kamera ishlamayotgan edi — kadr qisqa ochib olindi, odam bor-yo'qligi "
                                           "tekshirilmadi: rasmga qarab ishonch hosil qiling")
    return {"ok": True, **res, "note": note, "exits": exits_list()}


@router.post("/api/safety/exits/{zone_id}/forget")
def exit_forget(zone_id: int):
    db.execute("DELETE FROM safety_exits WHERE zone_id=?", (zone_id,))
    safety.exit_ref_path(zone_id).unlink(missing_ok=True)
    safety.forget_state(zone_id)
    return {"ok": True, "exits": exits_list()}


@router.get("/api/safety/exits/{zone_id}/ref.png")
def exit_ref(zone_id: int):
    p = safety.exit_ref_path(zone_id)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})


# ----------------------------------------------------------------------------- SMS
@router.post("/api/safety/sms/test")
def sms_test():
    res = sms.send_test()
    if not res["ok"] and not res.get("results"):
        raise HTTPException(400, res.get("error") or "SMS sozlanmagan")
    return res


# ----------------------------------------------------------------------------- darvoza
class _Runner:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.state = {"running": False, "current": "", "done": 0, "total": 0, "error": None, "finished_at": None}
        self.stop = threading.Event()

    def start(self, ids: list[int]) -> bool:
        if self.thread and self.thread.is_alive():
            return False
        self.stop.clear()
        self.state.update(running=True, current="", done=0, total=len(ids), error=None)
        self.thread = threading.Thread(target=self._run, args=(ids,), daemon=True, name="safety-gate")
        self.thread.start()
        return True

    def _run(self, ids: list[int]) -> None:
        from . import evaluation

        try:
            for vid in ids:
                if self.stop.is_set():
                    break
                v = db.row("SELECT * FROM safety_videos WHERE id=?", (vid,))
                if not v:
                    continue
                self.state["current"] = v["filename"]
                try:
                    r = evaluation.analyze(v["path"], stop_evt=self.stop, hold_last=safety_eval.HOLD_SEC)
                    res = {"safety": r["safety"], "duration": r["duration"], "held": r.get("held"),
                           "seconds": r["seconds"], "model_set": r.get("model_set")}
                    db.execute("UPDATE safety_videos SET result=?, analyzed_at=?, version=?, duration=? WHERE id=?",
                               (json.dumps(res, ensure_ascii=False), time.time(), APP_VERSION,
                                r["duration"] - (r.get("held") or 0), vid))
                except Exception as e:  # noqa: BLE001
                    log.exception("Yiqilish videosi tahlilida xato: %s", v["filename"])
                    self.state["error"] = f"{v['filename']}: {e}"
                self.state["done"] += 1
        finally:
            self.state.update(running=False, current="", finished_at=time.time())


RUNNER = _Runner()


def _video_public(v: dict) -> dict:
    res = json.loads(v["result"]) if v.get("result") else None
    falls = safety_eval.clean_falls(json.loads(v.get("falls") or "[]"))
    m = safety_eval.match(falls, res["safety"]) if res else None
    return {"id": v["id"], "filename": v["filename"], "duration": v["duration"], "falls": falls,
            "negative": int(v.get("negative") or 0), "note": v.get("note") or "", "analyzed_at": v.get("analyzed_at"),
            "version": v.get("version"), "alerts": [{"t": a["t"], "kind": a["kind"], "kind_label": a.get("kind_label")}
                                                   for a in (res or {}).get("safety", [])],
            "match": m, "held": (res or {}).get("held")}


def live_cams(t0: float | None = None) -> list[dict]:
    """Jonli kameralar: ishlagan daqiqalar (metrics_minute) va yiqilish/yotish hodisalariga javoblar — bir xil oynada."""
    if t0 is None:
        s = db.get_settings()
        t0 = safety_eval.live_window(s.get("safety_since"), s.get("retention_days"), time.time())
    mins = {r["source_id"]: r for r in db.rows(
        "SELECT source_id, MAX(source_name) AS name, COUNT(*) AS minutes FROM metrics_minute WHERE source_id>0 "
        "AND up_pct>=0.5 AND minute>=? GROUP BY source_id", (t0,))}
    marks = ",".join("?" for _ in safety_eval.LIVE_KINDS)
    ev: dict = {}
    for r in db.rows(f"SELECT source_id, status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND source_id IS NOT NULL "
                     f"AND kind IN ({marks}) AND created_at>=? GROUP BY source_id, status", (*safety_eval.LIVE_KINDS, t0)):
        e = ev.setdefault(r["source_id"], {"rejected": 0, "confirmed": 0, "unreviewed": 0})
        e[{"rad_etilgan": "rejected", "tasdiqlangan": "confirmed"}.get(r["status"], "unreviewed")] += r["n"]
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM sources")}
    out = []
    for sid in sorted(set(mins) | set(ev)):
        out.append({"source_id": sid, "name": names.get(sid) or (mins.get(sid) or {}).get("name") or f"#{sid}",
                    "minutes": (mins.get(sid) or {}).get("minutes", 0), **ev.get(sid, {"rejected": 0, "confirmed": 0,
                                                                                   "unreviewed": 0})})
    return out


def gate_data() -> dict:
    vids = db.rows("SELECT * FROM safety_videos ORDER BY id")
    st = db.get_settings()
    t0 = safety_eval.live_window(st.get("safety_since"), st.get("retention_days"), time.time())
    g = safety_eval.gate([{"id": v["id"], "filename": v["filename"], "falls": json.loads(v.get("falls") or "[]"),
                           "negative": v.get("negative"), "result": json.loads(v["result"]) if v.get("result") else None}
                          for v in vids], live_cams(t0))
    g["fp"]["since"] = t0
    g["videos"] = [_video_public(v) for v in reversed(vids)]
    g["runner"] = dict(RUNNER.state)
    g["criteria"] = {"recall": safety_eval.RECALL_MIN, "falls": safety_eval.MIN_FALLS,
                     "fp_week": safety_eval.FP_MAX_WEEK, "days": safety_eval.MIN_DAYS,
                     "win": [safety_eval.WIN_BEFORE, safety_eval.WIN_AFTER], "hold": safety_eval.HOLD_SEC}
    return g


@router.get("/api/safety/gate")
def safety_gate():
    return gate_data()


@router.post("/api/safety/videos")
async def video_upload(file: UploadFile = File(...), falls: str = Form(""), negative: str = Form("0"),
                       note: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Video fayl kerak ({', '.join(sorted(VIDEO_EXT))})")
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^\w.\-]+", "_", Path(file.filename or "video.mp4").name)[:80] or "video.mp4"
    dest = VIDEO_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}_{base}"
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > 2_000_000_000:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Fayl juda katta (2 GB dan oshmasin)")
            f.write(chunk)
    cap = cv2.VideoCapture(str(dest))
    ok = cap.isOpened()
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    if not ok:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Videoni o'qib bo'lmadi (MP4/H.264 ni sinab ko'ring)")
    neg = 1 if str(negative).lower() in ("1", "true", "on") else 0
    fl = [] if neg else safety_eval.clean_falls(falls)
    if not neg and not fl:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Yiqilish soniyalarini kiriting (masalan: 3.5, 12) yoki 'yiqilish yo'q' belgisini qo'ying")
    vid = db.execute("INSERT INTO safety_videos(filename, path, duration, falls, negative, note, created_at) "
                     "VALUES(?,?,?,?,?,?,?)", (file.filename, str(dest), round(n / fps, 2) if fps > 1 else None,
                                               json.dumps(fl), neg, str(note or "")[:300], time.time()))
    return {"ok": True, "video": _video_public(db.row("SELECT * FROM safety_videos WHERE id=?", (vid,)))}


@router.post("/api/safety/videos/{vid}")
async def video_update(request: Request, vid: int):
    v = db.row("SELECT * FROM safety_videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Video topilmadi")
    d = await _body(request)
    upd = {}
    if "negative" in d:
        upd["negative"] = 1 if str(d["negative"]).lower() in ("1", "true", "on") else 0
    if "falls" in d:
        upd["falls"] = json.dumps(safety_eval.clean_falls(d["falls"]))
    if "note" in d:
        upd["note"] = str(d["note"] or "")[:300]
    if upd:
        db.execute(f"UPDATE safety_videos SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), vid))
    return {"ok": True, "video": _video_public(db.row("SELECT * FROM safety_videos WHERE id=?", (vid,)))}


@router.post("/api/safety/videos/{vid}/delete")
def video_delete(vid: int):
    v = db.row("SELECT * FROM safety_videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Video topilmadi")
    if RUNNER.state["running"]:
        raise HTTPException(409, "Tahlil ketmoqda — tugashini kuting")
    try:
        p = Path(v["path"]).resolve()
        if VIDEO_DIR.resolve() in p.parents:
            p.unlink(missing_ok=True)
    except OSError:
        pass
    db.execute("DELETE FROM safety_videos WHERE id=?", (vid,))
    return {"ok": True}


@router.post("/api/safety/gate/run")
async def gate_run(request: Request):
    try:
        d = await request.json()
    except ValueError:
        d = {}
    force = bool((d or {}).get("all"))
    rows = db.rows("SELECT id, analyzed_at, version FROM safety_videos ORDER BY id")
    ids = [r["id"] for r in rows if force or not r["analyzed_at"] or r["version"] != APP_VERSION]
    if not ids:
        return {"ok": True, "started": False, "message": "Hamma videolar tahlil qilingan"}
    if not RUNNER.start(ids):
        raise HTTPException(409, "Tahlil allaqachon ketmoqda")
    return {"ok": True, "started": True, "total": len(ids)}


@router.post("/api/safety/gate/stop")
def gate_stop():
    RUNNER.stop.set()
    return {"ok": True}
