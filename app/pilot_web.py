"""9-bosqich (2.5.0): Pilot sahifasi API — tayyorlik, darvozalar jadvali, pilot ko'rsatkichlari, baho, PDF, diagnostika."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from . import pilot

router = APIRouter()


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


@router.get("/api/pilot/readiness")
def readiness(force: int = 0):
    return pilot.readiness(force=bool(force))


@router.get("/api/pilot/gates")
def gates():
    return {"gates": pilot.gates()}


@router.get("/api/pilot/summary")
def summary():
    t0, t1 = pilot.since_until()
    return {"since": t0, "until": t1, "gate": pilot.pilot_gate(), "weeks": pilot.weeks(),
            "feedback": pilot.feedback_rows(),
            "criteria": {"days": pilot.PILOT_DAYS, "uptime": pilot.UPTIME_MIN, "cam_up": pilot.CAM_UP_MIN,
                         "fp_day": pilot.FP_MAX_DAY, "unreviewed": pilot.UNREV_MAX, "reports": pilot.REPORT_MIN,
                         "rating": pilot.RATING_MIN, "rating_n": pilot.RATING_N}}


@router.post("/api/pilot/start")
async def start(request: Request):
    d = await _body(request)
    date = str(d.get("date") or "").strip()
    t = None
    if date:
        try:
            t = time.mktime(time.strptime(date, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Sana ko'rinishi: 2026-10-19")
        if t > time.time():
            raise HTTPException(400, "Kelajakdagi sana")
        if t < time.time() - pilot.START_MAX_AGE_DAYS * 86400:
            raise HTTPException(400, "Sana juda eski (ko'pi bilan bir yil oldin)")
    return {"ok": True, "since": pilot.start(t)}


@router.post("/api/pilot/stop")
def stop():
    t0, _ = pilot.since_until()
    if not t0:
        raise HTTPException(400, "Pilot boshlanmagan")
    try:
        return {"ok": True, "until": pilot.stop()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/pilot/feedback")
async def feedback(request: Request):
    d = await _body(request)
    try:
        week = float(d.get("week"))
        rating = int(d["rating"]) if d.get("rating") not in (None, "") else None
    except (TypeError, ValueError, KeyError):
        raise HTTPException(400, "week va rating (1..5)")
    if rating is None and not str(d.get("comment") or "").strip():
        raise HTTPException(400, "Baho yoki izoh kerak")
    if not pilot.valid_week(week):
        raise HTTPException(400, "week — pilot haftalaridan birining boshi (dushanba)")
    try:
        pilot.save_feedback(week, f"panel:{request.session.get('user') or 'admin'}", rating,
                            str(d.get("comment") or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/api/pilot/report.pdf")
async def report_pdf():
    data = await run_in_threadpool(pilot.pdf)
    name = f"pilot_hisoboti_{time.strftime('%Y%m%d')}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/diagnostics.zip")
async def diagnostics_zip():
    from . import diagnostics

    data, name = await run_in_threadpool(diagnostics.build)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})
