"""Navbat sahifasi API (4-bosqich, 2.0.0): kassalar holati, bugungi ko'rsatkichlar, qoidalar sozlamasi, signallar,
kassir jadvali tavsiyasi va aniqlik darvozasi (namuna olish + sekundomer). Hisob — app/ai/queues.py, darvoza
mezonlari — app/queue_eval.py (buyruq qatoridan: tests/queue_gate.py).
"""
from __future__ import annotations

import csv
import io
import json
import time

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import db
from . import queue_eval as qe
from .ai import queues
from .config import DATA_DIR

router = APIRouter()
SAMPLES_DIR = DATA_DIR / "queue_samples"
SAMPLES_KEEP = 1000
TIMINGS_MAX_RUNNING = 20
TOGGLES = ("queue_tg", "queue_tg_low", "queue_tg_schedule")


def _day0(t: float | None = None) -> float:
    lt = time.localtime(t or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def zones_info() -> list[dict]:
    """Navbat zonalari: kamera, kassir joyi bilan juftligi (shu kamerada eng yaqin kassir zonasi)."""
    rows = db.rows("SELECT z.id, z.name, z.kind, z.points, z.source_id, z.enabled, s.name AS source_name FROM zones z "
                   "LEFT JOIN sources s ON s.id=z.source_id WHERE z.kind IN (?, ?) ORDER BY z.id",
                   (queues.QUEUE_KIND, queues.CASHIER_KIND))
    out = []
    for r in rows:
        if r["kind"] != queues.QUEUE_KIND:
            continue
        try:
            c = np.asarray(json.loads(r["points"]), dtype=np.float64).mean(axis=0)
        except (TypeError, ValueError):
            c = None
        cash = [k for k in rows if k["kind"] == queues.CASHIER_KIND and k["source_id"] == r["source_id"] and k["enabled"]]
        best = None
        if cash and c is not None:
            def dist(k):
                try:
                    return float(np.hypot(*(np.asarray(json.loads(k["points"]), dtype=np.float64).mean(axis=0) - c)))
                except (TypeError, ValueError):
                    return 1e9
            best = min(cash, key=dist)
        out.append({"id": r["id"], "name": r["name"], "source_id": r["source_id"], "source_name": r["source_name"],
                    "enabled": bool(r["enabled"]), "cashier_zone": best["name"] if best else None})
    return out


def settings_public() -> dict:
    s = db.get_settings()
    out = {k: s.get(k, queues.DEFAULTS[k]) for k in queues.DEFAULTS}
    return out


@router.get("/api/queues/status")
def queues_status():
    d0 = _day0()
    now = time.time()
    live = queues.board()
    zs = zones_info()
    alerts = db.rows("SELECT id, t, rule, level, zone_name, source_name, title, text, tg_sent FROM queue_alerts "
                     "ORDER BY id DESC LIMIT 30")
    hours = [0.0] * 24
    served = [0] * 24
    for h in queues.hourly(d0, now + 60):
        k = int((h["hour"] - d0) // 3600)
        if 0 <= k < 24:
            hours[k] = h["avg_len"]
            served[k] = h["served"]
    from .ai import counting

    return {"live": live, "zones": zs, "today": queues.summary(d0, now + 60), "hours_len": hours,
            "hours_served": served, "alerts": alerts, "settings": settings_public(), "rules": queues.RULES,
            "hook_ok": counting.hook_ok(), "state_uz": queues.STATE_UZ,
            "r3": queues.r3_decision(queues.r3_window(now))}


@router.get("/api/queues/daily")
def queues_daily(days: int = 14):
    days = max(1, min(int(days), 90))
    end = _day0() + 86400
    out = []
    for i in range(days):
        t1 = end - i * 86400
        t0 = _day0(t1 - 43200)
        s = queues.summary(t0, t1)
        n_al = db.scalar("SELECT COUNT(*) FROM queue_alerts WHERE t>=? AND t<? AND rule IN ('R1','R2')", (t0, t1)) or 0
        out.append({"day": t0, "served": s["served"], "avg_wait": s["avg_wait"], "p90_wait": s["p90_wait"],
                    "max_wait": s["max_wait"], "max_len": s["max_len"], "long_min": round(s["long_sec"] / 60, 1),
                    "alerts": int(n_al)})
    return {"days": out}


@router.get("/api/queues/schedule")
def queues_schedule():
    sch = dict(queues.schedule_now(cached=True))
    sch["min_days"] = qe.SCHED_MIN_DAYS
    return sch


@router.post("/api/queues/settings")
async def queues_settings(request: Request):
    d = await _body(request)
    upd = {}
    for k, (lo, hi) in queues.LIMITS.items():
        if k not in d:
            continue
        try:
            v = float(str(d[k]).replace(",", "."))
        except ValueError:
            raise HTTPException(400, f"{k}: son kiriting") from None
        if not (lo <= v <= hi) or v != int(v):
            raise HTTPException(400, f"{k}: {lo} dan {hi} gacha butun son")
        upd[k] = str(int(v))
    for k in TOGGLES:
        if k in d:
            upd[k] = "1" if str(d[k]).lower() in ("1", "true", "on") else "0"
    if upd:
        db.set_settings(upd)
        queues.reload_cfg()
    return {"ok": True, "settings": settings_public()}


@router.post("/api/queues/test-alert")
def queues_test_alert():
    """Navbat signali Telegram'ga yetib borishini sinash (bazaga yozilmaydi)."""
    if queues._notifier is None:
        raise HTTPException(409, "Telegram bot ishlamayapti")
    n = queues._notifier.send_queue_alert("🧪 <b>Navbat signali sinovi</b>\nUzun navbat yoki kassir yo'qligida xabar "
                                          "shu ko'rinishda keladi. O'chirish: /navbat → tugma.")
    return {"ok": True, "sent": n}


@router.get("/api/export/queues.csv")
def queues_csv(days: int = 30):
    days = max(1, min(int(days), 90))
    t0 = _day0() - (days - 1) * 86400
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["navbatga kirdi", "navbatdan chiqdi", "kutish (soniya)", "kassa"])
    for r in db.rows("SELECT * FROM queue_waits WHERE t_out>=? ORDER BY t_out", (t0,)):
        name = r["zone_name"] or ""
        if name[:1] in ("=", "+", "-", "@", "\t", "\r"):
            name = "'" + name
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t_in"])),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t_out"])), round(r["wait"]), name])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="navbat_{days}kun.csv"'})


# ----------------------------------------------------------------------------- aniqlik darvozasi
def _zone(zone_id: int) -> dict:
    z = db.row("SELECT z.*, s.name AS source_name FROM zones z LEFT JOIN sources s ON s.id=z.source_id "
               "WHERE z.id=? AND z.kind=?", (zone_id, queues.QUEUE_KIND))
    if not z:
        raise HTTPException(404, "Navbat zonasi topilmadi")
    return z


def _crop(frame: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Navbat zonasi atrofi va zona chegarasi — egasi telefonda sanashi uchun. Zona oyoqlar bo'yicha, shuning uchun
    tepaga odam bo'yicha (kadr balandligining yarmi) qo'shimcha joy olinadi — odamlar to'liq ko'rinsin."""
    h, w = frame.shape[:2]
    p = np.round(pts * np.array([w, h])).astype(np.int32)
    img = frame.copy()
    over = img.copy()
    cv2.fillPoly(over, [p], (119, 39, 219))
    img = cv2.addWeighted(over, 0.12, img, 0.88, 0)
    cv2.polylines(img, [p], True, (119, 39, 219), 2, cv2.LINE_AA)
    x0, y0 = p.min(axis=0)
    x1, y1 = p.max(axis=0)
    mx = int((x1 - x0) * 0.2) + 20
    x0, x1 = max(0, x0 - mx), min(w, x1 + mx)
    y0, y1 = max(0, y0 - int(h * 0.5)), min(h, y1 + int(h * 0.05))
    img = img[y0:y1, x0:x1]
    if img.shape[1] > 960:
        img = cv2.resize(img, (960, int(img.shape[0] * 960 / img.shape[1])), interpolation=cv2.INTER_AREA)
    return img


def _prune_samples() -> None:
    old = db.rows("SELECT id, img FROM queue_samples WHERE img IS NOT NULL ORDER BY id DESC LIMIT -1 OFFSET ?",
                  (SAMPLES_KEEP,))
    for r in old:
        try:
            (SAMPLES_DIR / r["img"]).unlink(missing_ok=True)
        except OSError:
            pass
    if old:
        db.execute(f"UPDATE queue_samples SET img=NULL WHERE id IN ({','.join('?' * len(old))})", [r["id"] for r in old])


@router.post("/api/queues/samples")
async def sample_new(request: Request):
    """Shu lahzadagi kadr va tizim soni birga saqlanadi; egasi kadrdagi navbatni sanab javob beradi."""
    from .ai.manager import get_manager

    d = await _body(request)
    try:
        zid = int(d.get("zone_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "zone_id kerak") from None
    z = _zone(zid)
    last = db.scalar("SELECT MAX(t) FROM queue_samples WHERE zone_id=?", (zid,))
    if last and time.time() - float(last) < qe.SAMPLE_GAP_SEC:
        raise HTTPException(429, f"Keyingi namuna {qe.SAMPLE_GAP_SEC} s dan keyin (ketma-ket deyarli bir xil kadr bo'lmasin)")
    st = next((x for x in queues.live_status() if x["zone_id"] == zid and x["live"]), None)
    w = get_manager().workers.get(z["source_id"])
    frame = None
    if w is not None and w.is_alive():
        with w.frame_lock:
            frame = None if w.latest_frame is None else w.latest_frame.copy()
    if st is None or frame is None:
        raise HTTPException(409, "Kamera ishlamayapti yoki navbat hali o'lchanmayapti — kamerani yoqing")
    try:
        pts = np.asarray(json.loads(z["points"]), dtype=np.float32).clip(0, 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "Zona nuqtalari noto'g'ri") from None
    img = _crop(frame, pts)
    sid = db.execute("INSERT INTO queue_samples(t, source_id, zone_id, zone_name, auto, raw) VALUES(?,?,?,?,?,?)",
                     (time.time(), z["source_id"], zid, z["name"], int(st["length"]), int(st["raw"])))
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{sid}.jpg"
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        (SAMPLES_DIR / name).write_bytes(buf.tobytes())
        db.execute("UPDATE queue_samples SET img=? WHERE id=?", (name, sid))
    _prune_samples()
    return {"ok": True, "id": sid, "img": f"/api/queues/samples/{sid}.jpg", "zone_name": z["name"]}


@router.get("/api/queues/samples/{sid}.jpg")
def sample_img(sid: int):
    r = db.row("SELECT img FROM queue_samples WHERE id=?", (sid,))
    if not r or not r["img"] or not (SAMPLES_DIR / r["img"]).exists():
        raise HTTPException(404, "Kadr yo'q")
    return FileResponse(SAMPLES_DIR / r["img"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/queues/samples/{sid}/answer")
async def sample_answer(request: Request, sid: int):
    d = await _body(request)
    try:
        n = int(d.get("manual"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Odamlar sonini kiriting") from None
    if not 0 <= n <= 60:
        raise HTTPException(400, "0 dan 60 gacha")
    r = db.row("SELECT * FROM queue_samples WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Namuna topilmadi")
    db.execute("UPDATE queue_samples SET manual=? WHERE id=?", (n, sid))
    return {"ok": True, "auto": r["auto"], "manual": n, "good": abs(int(r["auto"]) - n) <= qe.SAMPLE_TOL}


@router.post("/api/queues/samples/{sid}/delete")
def sample_delete(sid: int):
    r = db.row("SELECT img FROM queue_samples WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Namuna topilmadi")
    if r["img"]:
        try:
            (SAMPLES_DIR / r["img"]).unlink(missing_ok=True)
        except OSError:
            pass
    db.execute("DELETE FROM queue_samples WHERE id=?", (sid,))
    return {"ok": True}


@router.post("/api/queues/timings")
async def timing_start(request: Request):
    d = await _body(request)
    try:
        zid = int(d.get("zone_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "zone_id kerak") from None
    z = _zone(zid)
    if (db.scalar("SELECT COUNT(*) FROM queue_timings WHERE t1 IS NULL") or 0) >= TIMINGS_MAX_RUNNING:
        raise HTTPException(409, f"Bir vaqtda ko'pi bilan {TIMINGS_MAX_RUNNING} ta sekundomer")
    # Telefon soati emas — server vaqti (avtomatik yozuvlar bilan bir soat)
    tid = db.execute("INSERT INTO queue_timings(source_id, zone_id, zone_name, t0, note) VALUES(?,?,?,?,?)",
                     (z["source_id"], zid, z["name"], time.time(), str(d.get("note") or "")[:60]))
    return {"ok": True, "id": tid}


@router.post("/api/queues/timings/{tid}/stop")
def timing_stop(tid: int):
    r = db.row("SELECT * FROM queue_timings WHERE id=?", (tid,))
    if not r:
        raise HTTPException(404, "Sekundomer topilmadi")
    if r["t1"] is None:
        db.execute("UPDATE queue_timings SET t1=? WHERE id=?", (time.time(), tid))
    return {"ok": True}


@router.post("/api/queues/timings/{tid}/delete")
def timing_delete(tid: int):
    db.execute("DELETE FROM queue_timings WHERE id=?", (tid,))
    return {"ok": True}


def gate(days: int = 30) -> dict:
    """Darvoza holati: namunalar (son ±1) va sekundomer (kutish xatosi) — panel va tests/queue_gate.py uchun."""
    since = time.time() - days * 86400
    samples = db.rows("SELECT id, t, zone_id, zone_name, auto, raw, manual, img FROM queue_samples WHERE t>=? "
                      "ORDER BY id DESC", (since,))
    tims = db.rows("SELECT * FROM queue_timings WHERE t0>=? OR t1 IS NULL ORDER BY id DESC", (since,))
    autos = []
    if tims:
        t_lo = min(float(x["t0"]) for x in tims) - 120
        autos = db.rows("SELECT zone_id, t_in, t_out, wait FROM queue_waits WHERE t_out>=?", (t_lo,))
    res = qe.match_timings(tims, autos)
    return {"samples": samples[:200], "samples_gate": qe.samples_gate(samples), "timings": res,
            "timings_gate": qe.timings_gate(res),
            "criteria": {"sample_tol": qe.SAMPLE_TOL, "sample_acc": qe.GATE_SAMPLE_ACC, "sample_min": qe.SAMPLE_MIN,
                         "sample_min_busy": qe.SAMPLE_MIN_BUSY, "wait_err": qe.GATE_WAIT_ERR,
                         "timing_min": qe.TIMING_MIN, "gap_sec": qe.SAMPLE_GAP_SEC}}


@router.get("/api/queues/gate")
def queues_gate():
    return gate()
