"""Tashriflar (3-bosqich, 1.9.0): odamlarni sanash natijalari, CSV va qo'lda sanash sinovi (aniqlik darvozasi).

Qo'lda sanash sinovi: egasi eshik oldida (yoki kamera videosini ko'rib) telefonda **Kirdi +1 / Chiqdi +1** ni
bosadi; tugatgach tizim avtomatik sanash bilan solishtiradi: aniqlik = 1 − |avto − qo'lda| / qo'lda (har yo'nalish va
jami), hamda har bosishni ±4 s ichidagi avtomatik o'tish bilan juftlaydi (yo'qotilgan / ortiqcha). Darvoza:
oxirgi 2 ta kamida 1 soatlik sinovning har birida aniqlik ≥ 95% (mezonlar: app/count_eval.py;
buyruq qatoridan: tests/count_gate.py).
"""
from __future__ import annotations

import csv
import io
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import db
from .ai import counting
from .count_eval import GATE_MIN_SEC, MATCH_TOL, compare, session_ok, sessions_gate

router = APIRouter()
WORK_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def _lines() -> list[dict]:
    return db.rows("SELECT z.id, z.name, z.source_id, s.name AS source_name, z.enabled FROM zones z "
                   "LEFT JOIN sources s ON s.id=z.source_id WHERE z.kind=? ORDER BY z.id", (counting.LINE_KIND,))


def _inside_since() -> float:
    """"Hozir ichkarida" qaysi vaqtdan hisoblanadi: do'kon yopiq paytining o'rtasi (counting.occupancy_start) yoki
    qo'lda "0 qilish" bosilgan vaqt — qaysi biri keyinroq bo'lsa."""
    b = counting.occupancy_start(time.time(), db.get_setting("work_hours", ""))
    try:
        r = float(db.get_setting("occupancy_reset_at", "0") or 0)
    except ValueError:
        r = 0.0
    return r if r >= b else b


def occupancy() -> dict | None:
    """Bulutga hello bilan (1.9.0): hozir ichkarida (qo'lda "0 qilish" hisobga olingan) va bugungi kirdi/chiqdi.
    Sanash chizig'i yo'q bo'lsa — None (maydon yuborilmaydi)."""
    if not db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (counting.LINE_KIND,)):
        return None
    d0, now = counting.day_start(), time.time()
    s = counting.summary(d0, now + 1)
    since = _inside_since()
    occ = counting.summary(since, now + 1)
    return {"inside": occ["inside"], "since": since, "day0": d0, "in": s["in"], "out": s["out"]}


@router.get("/api/counts/today")
def counts_today():
    d0 = counting.day_start()
    now = time.time()
    s = counting.summary(d0, now + 1)
    since = _inside_since()
    occ = counting.summary(since, now + 1)
    y = counting.summary(d0 - 86400, d0)
    last = db.rows("SELECT id, t, source_name, line_name, dir, staff, offhours FROM counts ORDER BY t DESC, id DESC LIMIT 12")
    per_line = db.rows("SELECT line_id, MAX(line_name) AS line_name, MAX(source_name) AS source_name, "
                       "SUM(dir='in' AND staff=0 AND offhours=0) AS cin, SUM(dir='out' AND staff=0 AND offhours=0) AS cout "
                       "FROM counts WHERE t>=? GROUP BY line_id ORDER BY line_id", (d0,))

    def hours(sm: dict, base: float) -> list[int]:
        arr = [0] * 24
        for hrow in sm["hourly"]:
            k = int((hrow["hour"] - base) // 3600)
            if 0 <= k < 24:
                arr[k] = hrow["in"]
        return arr
    return {"lines": _lines(), "hook_ok": counting.hook_ok(), "in": s["in"], "out": s["out"], "inside": occ["inside"], "inside_since": since,
            "staff": s["staff"], "offhours": s["offhours"], "busiest_hour": s["busiest_hour"],
            "hours": hours(s, d0), "hours_yesterday": hours(y, d0 - 86400), "yesterday_in": y["in"],
            "work_hours": db.get_setting("work_hours", ""), "last": last,
            "per_line": [{"line_id": r["line_id"], "line_name": r["line_name"], "source_name": r["source_name"],
                          "in": int(r["cin"] or 0), "out": int(r["cout"] or 0)} for r in per_line]}


@router.get("/api/counts/daily")
def counts_daily(days: int = 14):
    days = max(1, min(int(days), 365))
    return {"days": counting.daily(days)}


@router.post("/api/counts/reset-inside")
def counts_reset_inside():
    """Hozir ichkarida 0 (masalan, yopilishda tekshirildi — hech kim yo'q): bugungi qolgan hisob shu vaqtdan."""
    db.set_settings({"occupancy_reset_at": f"{time.time():.0f}"})
    return {"ok": True}


@router.post("/api/counts/work-hours")
async def counts_work_hours(request: Request):
    try:
        d = await request.json()
    except ValueError:
        d = {}
    v = str((d or {}).get("work_hours", "")).strip().replace(" ", "")
    if v and not WORK_HOURS_RE.match(v):
        raise HTTPException(400, "Ish vaqti ko'rinishi: 09:00-22:00 (yoki bo'sh — har doim)")
    if v and v[:5] == v[6:]:
        raise HTTPException(400, "Ochilish va yopilish vaqti bir xil — kecha-kunduz ishlasa, maydonni bo'sh qoldiring")
    db.set_settings({"work_hours": v})
    return {"ok": True, "work_hours": v}


def _csv_safe(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.get("/api/export/counts.csv")
def counts_csv(days: int = 30):
    days = max(1, min(int(days), 365))
    t0 = counting.day_start() - (days - 1) * 86400
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")   # 3.2.0: Excel (uz/ru Windows) ustunlarni to'g'ri ajratsin
    w.writerow(["vaqt", "kamera", "chiziq", "yo'nalish", "turi"])
    for r in db.rows("SELECT * FROM counts WHERE t>=? ORDER BY t", (t0,)):
        kind = "xodim" if r["staff"] else ("ish vaqtidan tashqari" if r["offhours"] else "xaridor")
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])), _csv_safe(r["source_name"] or ""),
                    _csv_safe(r["line_name"] or ""), "kirdi" if r["dir"] == "in" else "chiqdi", kind])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="tashriflar_{days}kun.csv"'})


# ----------------------------------------------------------------------------- qo'lda sanash sinovi
def _session(sid: int) -> dict:
    r = db.row("SELECT * FROM count_sessions WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Sinov topilmadi")
    try:
        r["taps"] = json.loads(r.get("taps") or "[]")
    except ValueError:
        r["taps"] = []
    return r


def _result(r: dict) -> dict:
    t1 = r["ended_at"] or time.time()
    src = r.get("source_id")
    sql = "SELECT t, dir FROM counts WHERE t>=? AND t<=?"
    params: list = [r["started_at"] - MATCH_TOL, t1 + MATCH_TOL]
    if src:
        sql += " AND source_id=?"
        params.append(src)
    autos = db.rows(sql, params)
    res = compare(r["taps"], autos)
    dur = t1 - r["started_at"]
    res.update({"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"], "duration": round(dur),
                "source_id": src, "note": r.get("note") or "", "taps": len(r["taps"])})
    res["gate_ok"] = session_ok(res)
    return res


@router.get("/api/counts/sessions")
def sessions_list():
    rows = db.rows("SELECT * FROM count_sessions ORDER BY id DESC LIMIT 50")
    for r in rows:
        try:
            r["taps"] = json.loads(r.get("taps") or "[]")
        except ValueError:
            r["taps"] = []
    res = [_result(r) for r in rows]
    return {"sessions": res, "gate": sessions_gate(res), "tolerance_sec": MATCH_TOL, "min_minutes": GATE_MIN_SEC // 60}


@router.post("/api/counts/sessions")
async def session_start(request: Request):
    try:
        d = await request.json()
    except ValueError:
        d = {}
    src = (d or {}).get("source_id")
    src = int(src) if str(src or "").isdigit() else None
    if db.row("SELECT id FROM count_sessions WHERE ended_at IS NULL"):
        raise HTTPException(409, "Boshqa sinov davom etyapti — avval uni tugating")
    sid = db.execute("INSERT INTO count_sessions(started_at, source_id, taps, note) VALUES(?,?,?,?)",
                     (time.time(), src, "[]", str((d or {}).get("note") or "")[:200]))
    return {"ok": True, "session": _result(_session(sid))}


@router.post("/api/counts/sessions/{sid}/tap")
async def session_tap(request: Request, sid: int):
    try:
        d = await request.json()
    except ValueError:
        d = {}
    direction = (d or {}).get("dir")
    if direction not in ("in", "out"):
        raise HTTPException(400, "dir: in yoki out")
    with db.tx() as c:
        r = c.execute("SELECT taps, ended_at FROM count_sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "Sinov topilmadi")
        if r["ended_at"]:
            raise HTTPException(409, "Sinov tugagan")
        taps = json.loads(r["taps"] or "[]")
        # Telefon soati emas — server vaqti (avtomatik o'tishlar bilan bir soat); tarmoq kechikishi ±4 s ichida
        taps.append([round(time.time(), 2), direction])
        c.execute("UPDATE count_sessions SET taps=? WHERE id=?", (json.dumps(taps), sid))
    return {"ok": True, "n_in": sum(1 for _, x in taps if x == "in"), "n_out": sum(1 for _, x in taps if x == "out")}


@router.post("/api/counts/sessions/{sid}/undo")
def session_undo(sid: int):
    with db.tx() as c:
        r = c.execute("SELECT taps, ended_at FROM count_sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "Sinov topilmadi")
        if r["ended_at"]:
            raise HTTPException(409, "Sinov tugagan")
        taps = json.loads(r["taps"] or "[]")
        if taps:
            taps.pop()
        c.execute("UPDATE count_sessions SET taps=? WHERE id=?", (json.dumps(taps), sid))
    return {"ok": True, "n_in": sum(1 for _, x in taps if x == "in"), "n_out": sum(1 for _, x in taps if x == "out")}


@router.post("/api/counts/sessions/{sid}/stop")
def session_stop(sid: int):
    r = _session(sid)
    if not r["ended_at"]:
        db.execute("UPDATE count_sessions SET ended_at=? WHERE id=?", (time.time(), sid))
    return {"ok": True, "session": _result(_session(sid))}


@router.post("/api/counts/sessions/{sid}/delete")
def session_delete(sid: int):
    _session(sid)
    db.execute("DELETE FROM count_sessions WHERE id=?", (sid,))
    return {"ok": True}
