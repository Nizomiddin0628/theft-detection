"""Oqim sahifalari API (5-bosqich, 2.1.0): do'kon rejasi, bo'limlar, kamera kalibrlash, heatmap, bo'lim tashriflari,
yo'nalishlar va aniqlik darvozasi (nuqtada turish + qo'lda sanash). Hisob — app/ai/flow.py, gomografiya —
app/ai/homography.py, darvoza mezonlari — app/flow_eval.py (buyruq qatoridan: tests/flow_gate.py).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import db
from . import flow_eval as fe
from .ai import flow
from .ai import homography as hg
from .config import DATA_DIR

router = APIRouter()
PLANS_DIR = DATA_DIR / "plans"
PLAN_UPLOAD_LIMIT = 20 * 1024 * 1024
PLAN_MAX_SIDE = 4000
PLAN_MAX_PIXELS = 50_000_000     # yechishdan oldin sarlavhadan tekshiriladi (kichik fayl ichida ulkan rasm — xotira)
BLANK_PX_PER_M = 40
PLAN_MIN_M, PLAN_MAX_M = 2.0, flow.PLAN_MAX_M
SECTIONS_MAX = 60
PERIODS = {"today": "Bugun", "yesterday": "Kecha", "7": "7 kun", "30": "30 kun"}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _day0(t: float | None = None) -> float:
    lt = time.localtime(t or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def period_range(period: str) -> tuple[float, float, int]:
    """(t0, t1, slot): bugun/kecha — soatlik, 7/30 kun — kunlik."""
    d0 = _day0()
    now = time.time()
    if period == "yesterday":
        y0 = _day0(d0 - 43200)
        return y0, d0, 3600
    if period in ("7", "30"):
        n = int(period)
        return _day0(d0 - (n - 1) * 86400 + 43200), now + 1, 86400
    return d0, now + 1, 3600


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _num(v, name: str, lo: float | None = None, hi: float | None = None) -> float:
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: son kiriting") from None
    if not np.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise HTTPException(400, f"{name}: {lo:g} dan {hi:g} gacha" if lo is not None and hi is not None
                            else f"{name}: noto'g'ri qiymat")
    return x


def image_size(data: bytes) -> tuple[int, int] | None:
    """PNG yoki JPEG sarlavhasidan (eni, bo'yi) — rasmni yechmasdan. Boshqa format — None."""
    import struct

    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            i += 2 + seg
    return None


def _plan_row(pid: int) -> dict:
    r = db.row("SELECT * FROM floor_plans WHERE id=?", (pid,))
    if not r:
        raise HTTPException(404, "Reja topilmadi")
    return r


def _pts(raw, n_min: int, w: float, h: float, name: str) -> list[list[float]]:
    try:
        a = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: nuqtalar noto'g'ri") from None
    if a.ndim != 2 or a.shape[1] != 2 or len(a) < n_min or len(a) > 200 or not np.isfinite(a).all():
        raise HTTPException(400, f"{name}: kamida {n_min} ta nuqta kerak")
    a[:, 0] = a[:, 0].clip(0, w)
    a[:, 1] = a[:, 1].clip(0, h)
    return [[round(float(x), 2), round(float(y), 2)] for x, y in a]


def section_public(s: dict, m_per_px: float) -> dict:
    try:
        pts = json.loads(s.get("points") or "[]")
    except ValueError:
        pts = []
    area = 0.0
    if len(pts) >= 3:
        area = float(abs(cv2.contourArea(np.asarray(pts, dtype=np.float32)))) * m_per_px * m_per_px
    return {"id": s["id"], "plan_id": s["plan_id"], "name": s["name"] or "", "color": s["color"] or flow.PALETTE[0],
            "points": pts, "enabled": int(s.get("enabled") or 0), "area_m2": round(area, 1)}


def plan_public(r: dict) -> dict:
    m = float(r["m_per_px"] or 0)
    secs = db.rows("SELECT * FROM plan_sections WHERE plan_id=? ORDER BY id", (r["id"],))
    cams = db.rows("SELECT c.source_id, c.stats, c.updated_at, c.enabled, s.name AS source_name FROM cam_calib c "
                   "LEFT JOIN sources s ON s.id=c.source_id WHERE c.plan_id=? ORDER BY c.source_id", (r["id"],))
    lay = flow.layout()
    lp = lay.plans.get(int(r["id"]))
    cov = {s.id: s for s in lp.sections} if lp else {}
    out_secs = []
    for s in secs:
        x = section_public(s, m)
        ls = cov.get(int(s["id"]))
        x["coverage"] = round(ls.coverage, 3) if ls else None
        x["owner"] = ls.owner if ls else None
        out_secs.append(x)
    return {"id": r["id"], "name": r["name"] or "", "w_px": r["w_px"], "h_px": r["h_px"], "m_per_px": m,
            "w_m": round((r["w_px"] or 0) * m, 2), "h_m": round((r["h_px"] or 0) * m, 2), "blank": int(r["blank"] or 0),
            "img_url": f"/media/plans/{r['id']}?v={int(float(r['updated_at'] or 0))}", "sections": out_secs,
            "cams": [{"source_id": c["source_id"], "source_name": c["source_name"] or f"#{c['source_id']}",
                      "stats": _json(c["stats"]), "updated_at": c["updated_at"], "enabled": int(c["enabled"] or 0),
                      "active": int(c["source_id"]) in lay.cams} for c in cams]}


def _json(v, default=None):
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


# ============================================================================ reja
@router.get("/api/flow/plans")
def plans_list():
    return {"plans": [plan_public(r) for r in db.rows("SELECT * FROM floor_plans ORDER BY id")],
            "palette": flow.PALETTE}


def _save_image(img: np.ndarray, pid: int, png: bool) -> str:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"plan_{pid}_{int(time.time() * 1000)}.{'png' if png else 'jpg'}"
    ok, buf = cv2.imencode(".png" if png else ".jpg", img, [] if png else [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise HTTPException(500, "Rasmni saqlab bo'lmadi")
    (PLANS_DIR / name).write_bytes(buf.tobytes())
    return name


def blank_image(w_m: float, h_m: float) -> tuple[np.ndarray, float]:
    """Rasmsiz reja: oq fon, har 1 m da och, har 5 m da to'qroq chiziq va metr yozuvlari."""
    ppm = min(BLANK_PX_PER_M, PLAN_MAX_SIDE / max(w_m, h_m))
    W, H = int(round(w_m * ppm)), int(round(h_m * ppm))
    img = np.full((H + 1, W + 1, 3), 255, dtype=np.uint8)
    for i in range(int(w_m) + 1):
        x = int(round(i * ppm))
        cv2.line(img, (x, 0), (x, H), (200, 200, 200) if i % 5 else (150, 150, 150), 1)
        if i % 5 == 0 and i:
            cv2.putText(img, f"{i}", (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (120, 120, 120), 1, cv2.LINE_AA)
    for j in range(int(h_m) + 1):
        y = int(round(j * ppm))
        cv2.line(img, (0, y), (W, y), (200, 200, 200) if j % 5 else (150, 150, 150), 1)
        if j % 5 == 0 and j:
            cv2.putText(img, f"{j}", (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (120, 120, 120), 1, cv2.LINE_AA)
    cv2.rectangle(img, (0, 0), (W, H), (90, 90, 90), 2)
    return img, 1.0 / ppm


@router.post("/api/flow/plans")
async def plan_create(request: Request):
    ctype = request.headers.get("content-type", "")
    now = time.time()
    if "multipart/form-data" in ctype:
        try:
            if int(request.headers.get("content-length") or 0) > PLAN_UPLOAD_LIMIT + 65536:
                raise HTTPException(413, f"Fayl juda katta (chegara {PLAN_UPLOAD_LIMIT >> 20} MB)")
        except ValueError:
            pass
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(400, "Reja rasmini tanlang (PNG yoki JPG)")
        data = await f.read(PLAN_UPLOAD_LIMIT + 1)
        if len(data) > PLAN_UPLOAD_LIMIT:
            raise HTTPException(413, f"Fayl juda katta (chegara {PLAN_UPLOAD_LIMIT >> 20} MB)")
        dims = image_size(data)
        if dims is None:
            raise HTTPException(400, "Rasm o'qilmadi — PNG yoki JPG fayl yuklang (PDF bo'lsa, rasmga aylantiring)")
        if dims[0] * dims[1] > PLAN_MAX_PIXELS:
            raise HTTPException(400, f"Rasm juda katta ({dims[0]}×{dims[1]}) — {PLAN_MAX_PIXELS // 10**6} megapikselgacha "
                                     "kichraytirib yuklang")
        img = await run_in_threadpool(cv2.imdecode, np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            raise HTTPException(400, "Rasm o'qilmadi — PNG yoki JPG fayl yuklang (PDF bo'lsa, rasmga aylantiring)")
        h, w = img.shape[:2]
        if min(w, h) < 50:
            raise HTTPException(400, "Rasm juda kichik")
        k = min(1.0, PLAN_MAX_SIDE / max(w, h))
        if k < 1.0:
            img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
        name = str(form.get("name") or "").strip()[:60] or "Do'kon rejasi"
        # Masshtab keyin rejada 2 nuqta bilan beriladi; boshlang'ich taxmin — rasm eni 20 m
        width_m = form.get("width_m")
        m_per_px = 20.0 / w
        if width_m not in (None, ""):
            m_per_px = _num(width_m, "Reja eni (m)", PLAN_MIN_M, PLAN_MAX_M) / w
        png = data[:8] == b"\x89PNG\r\n\x1a\n"
        pid = db.execute("INSERT INTO floor_plans(name, img, w_px, h_px, m_per_px, blank, created_at, updated_at) "
                         "VALUES(?,?,?,?,?,0,?,?)", (name, "", w, h, m_per_px, now, now))
        fname = await run_in_threadpool(_save_image, img, pid, png)
        db.execute("UPDATE floor_plans SET img=? WHERE id=?", (fname, pid))
    else:
        d = await _body(request)
        w_m = _num(d.get("w_m"), "Eni (m)", PLAN_MIN_M, PLAN_MAX_M)
        h_m = _num(d.get("h_m"), "Bo'yi (m)", PLAN_MIN_M, PLAN_MAX_M)
        img, m_per_px = blank_image(w_m, h_m)
        h, w = img.shape[:2]
        name = str(d.get("name") or "").strip()[:60] or "Do'kon rejasi"
        pid = db.execute("INSERT INTO floor_plans(name, img, w_px, h_px, m_per_px, blank, created_at, updated_at) "
                         "VALUES(?,?,?,?,?,1,?,?)", (name, "", w, h, m_per_px, now, now))
        fname = _save_image(img, pid, True)
        db.execute("UPDATE floor_plans SET img=? WHERE id=?", (fname, pid))
    flow.bump()
    return {"ok": True, "plan": await run_in_threadpool(lambda: plan_public(_plan_row(pid)))}


@router.post("/api/flow/plans/{pid}")
async def plan_update(request: Request, pid: int):
    d = await _body(request)
    # Masshtab o'zgarsa kameralar qayta moslanadi (har biri ~0,1–2 s) — hodisalar oqimi (event loop) to'xtamasin
    return await run_in_threadpool(_plan_update, pid, d)


def _plan_update(pid: int, d: dict) -> dict:
    r = _plan_row(pid)
    upd: dict = {}
    if "name" in d:
        upd["name"] = str(d.get("name") or "").strip()[:60] or r["name"]
    sc = d.get("scale")
    if isinstance(sc, dict):
        p1, p2 = sc.get("p1"), sc.get("p2")
        try:
            dpx = float(np.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1])))
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, "Masshtab: rejada 2 nuqtani belgilang") from None
        meters = _num(sc.get("meters"), "Masofa (m)", 0.2, PLAN_MAX_M)
        if dpx < 10:
            raise HTTPException(400, "Masshtab nuqtalari juda yaqin — uzoqroq ikki nuqta tanlang (masalan, devor uchlari)")
        m_per_px = meters / dpx
        if not (PLAN_MIN_M <= m_per_px * max(r["w_px"], r["h_px"]) <= PLAN_MAX_M):
            raise HTTPException(400, f"Bunday masshtabda reja {m_per_px * max(r['w_px'], r['h_px']):.0f} m chiqadi — "
                                     f"{PLAN_MIN_M:g}–{PLAN_MAX_M:g} m oralig'ida bo'lishi kerak")
        upd["m_per_px"] = m_per_px
    elif d.get("m_per_px") is not None:
        m_per_px = _num(d["m_per_px"], "Masshtab", 1e-4, 10.0)
        if not (PLAN_MIN_M <= m_per_px * max(r["w_px"], r["h_px"]) <= PLAN_MAX_M):
            raise HTTPException(400, f"Reja {PLAN_MIN_M:g}–{PLAN_MAX_M:g} m oralig'ida bo'lishi kerak")
        upd["m_per_px"] = m_per_px
    if not upd:
        return {"ok": True, "plan": plan_public(r)}
    upd["updated_at"] = time.time()
    db.execute("UPDATE floor_plans SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?", (*upd.values(), pid))
    flow.bump()
    flow.layout(force=True)              # qayta moslash shu yerda (threadpool), kamera oqimlarida emas
    return {"ok": True, "plan": plan_public(_plan_row(pid))}


@router.post("/api/flow/plans/{pid}/delete")
def plan_delete(pid: int):
    r = _plan_row(pid)
    db.execute("DELETE FROM plan_sections WHERE plan_id=?", (pid,))
    db.execute("DELETE FROM cam_calib WHERE plan_id=?", (pid,))
    db.execute("DELETE FROM floor_plans WHERE id=?", (pid,))
    if r.get("img"):
        try:
            (PLANS_DIR / r["img"]).unlink(missing_ok=True)
        except OSError:
            pass
    flow.bump()
    return {"ok": True}


@router.get("/media/plans/{pid}")
def plan_image(pid: int):
    r = _plan_row(pid)
    p = PLANS_DIR / str(r.get("img") or "")
    if not r.get("img") or not p.is_file() or p.resolve().parent != PLANS_DIR.resolve():
        raise HTTPException(404, "Reja rasmi topilmadi")
    return FileResponse(p, media_type="image/png" if p.suffix == ".png" else "image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


# ============================================================================ bo'limlar
@router.post("/api/flow/plans/{pid}/sections")
async def section_save(request: Request, pid: int):
    r = _plan_row(pid)
    d = await _body(request)
    pts = _pts(d.get("points"), 3, r["w_px"], r["h_px"], "Bo'lim")
    name = str(d.get("name") or "").strip()[:60]
    color = str(d.get("color") or "")
    if not COLOR_RE.match(color):
        n = db.scalar("SELECT COUNT(*) FROM plan_sections WHERE plan_id=?", (pid,)) or 0
        color = flow.PALETTE[int(n) % len(flow.PALETTE)]
    enabled = 0 if str(d.get("enabled", "1")).strip().lower() in ("0", "false", "off") else 1
    sid = d.get("id")
    if sid not in (None, ""):
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            raise HTTPException(400, "Noto'g'ri bo'lim raqami") from None
        if not db.row("SELECT id FROM plan_sections WHERE id=? AND plan_id=?", (sid, pid)):
            raise HTTPException(404, "Bo'lim topilmadi")
        db.execute("UPDATE plan_sections SET name=?, color=?, points=?, enabled=? WHERE id=?",
                   (name or f"Bo'lim {sid}", color, json.dumps(pts), enabled, sid))
    else:
        if (db.scalar("SELECT COUNT(*) FROM plan_sections WHERE plan_id=?", (pid,)) or 0) >= SECTIONS_MAX:
            raise HTTPException(400, f"Bitta rejada ko'pi bilan {SECTIONS_MAX} ta bo'lim")
        sid = db.execute("INSERT INTO plan_sections(plan_id, name, color, points, enabled, created_at) VALUES(?,?,?,?,?,?)",
                         (pid, name or "Bo'lim", color, json.dumps(pts), enabled, time.time()))
        if not name:
            db.execute("UPDATE plan_sections SET name=? WHERE id=?", (f"Bo'lim {sid}", sid))
    flow.bump()
    s = db.row("SELECT * FROM plan_sections WHERE id=?", (sid,))
    return {"ok": True, "section": section_public(s, float(r["m_per_px"]))}


@router.post("/api/flow/sections/{sid}/delete")
def section_delete(sid: int):
    if not db.row("SELECT id FROM plan_sections WHERE id=?", (sid,)):
        raise HTTPException(404, "Bo'lim topilmadi")
    db.execute("DELETE FROM plan_sections WHERE id=?", (sid,))
    flow.bump()
    return {"ok": True}


# ============================================================================ kamera kalibrlash
def _calib_input(d: dict, r: dict) -> tuple[list[dict], int, int]:
    raw = d.get("pairs")
    if not isinstance(raw, list) or len(raw) > hg.MAX_PAIRS:
        raise HTTPException(400, f"Nuqta juftlari ro'yxati kerak (ko'pi bilan {hg.MAX_PAIRS} ta)")
    pairs = []
    for i, p in enumerate(raw):
        try:
            u, v = float(p["img"][0]), float(p["img"][1])
            x, y = float(p["plan"][0]), float(p["plan"][1])
        except (KeyError, TypeError, ValueError, IndexError):
            raise HTTPException(400, f"#{i + 1} juftlik noto'g'ri") from None
        if not all(np.isfinite([u, v, x, y])) or not (0 <= u <= 1 and 0 <= v <= 1):
            raise HTTPException(400, f"#{i + 1}: kadr nuqtasi kadrdan tashqarida")
        pairs.append({"img": [round(u, 5), round(v, 5)],
                      "plan": [round(min(max(x, 0), r["w_px"]), 2), round(min(max(y, 0), r["h_px"]), 2)],
                      "check": 1 if p.get("check") else 0})
    fw = int(_num(d.get("frame_w") or 1280, "Kadr eni", 16, 10000))
    fh = int(_num(d.get("frame_h") or 720, "Kadr bo'yi", 16, 10000))
    return pairs, fw, fh


def _fit_public(res: dict, plan: dict) -> dict:
    """Moslash natijasi panel uchun: nuqta xatolari, ogohlantirishlar, kadr ustidagi 1 m to'r, reja ustidagi qamrov."""
    m = float(plan["m_per_px"])
    out = {k: v for k, v in res.items() if k != "calib"}
    for p in out["points"]:
        if p.get("pred") is not None:
            p["pred_px"] = [round(p["pred"][0] / m, 1), round(p["pred"][1] / m, 1)]
    if res.get("ok"):
        c = res["calib"]
        out["grid"] = hg.grid_lines(c, plan["w_px"] * m, plan["h_px"] * m, 1.0)
        cov = hg.coverage_polygon(c)
        out["coverage_px"] = [[round(float(x) / m, 1), round(float(y) / m, 1)] for x, y in cov]
        # Qaysi bo'limlar shu kameraga ko'rinadi (reja kataklari bo'yicha, boshqa kameralarsiz)
    return out


@router.get("/api/flow/calib/{source_id}")
def calib_get(source_id: int):
    src = db.row("SELECT id, name FROM sources WHERE id=?", (source_id,))
    if not src:
        raise HTTPException(404, "Kamera topilmadi")
    r = db.row("SELECT * FROM cam_calib WHERE source_id=?", (source_id,))
    plans = [plan_public(p) for p in db.rows("SELECT * FROM floor_plans ORDER BY id")]
    if not r:
        return {"source": src, "calib": None, "plans": plans}
    plan = db.row("SELECT * FROM floor_plans WHERE id=?", (r["plan_id"],))
    out = {"plan_id": r["plan_id"], "pairs": _json(r["pairs"], []), "stats": _json(r["stats"], {}),
           "frame_w": r["frame_w"], "frame_h": r["frame_h"], "updated_at": r["updated_at"], "enabled": r["enabled"]}
    if plan:
        res = hg.fit(flow.pairs_m(out["pairs"], float(plan["m_per_px"])),
                     (r["frame_w"] or 1280) / max(r["frame_h"] or 720, 1), r["frame_w"] or 1280, r["frame_h"] or 720)
        out["fit"] = _fit_public(res, plan)
    return {"source": src, "calib": out, "plans": plans}


@router.post("/api/flow/calib/{source_id}/preview")
async def calib_preview(request: Request, source_id: int):
    d = await _body(request)
    plan = _plan_row(int(_num(d.get("plan_id"), "Reja", 1, 1e9)))
    pairs, fw, fh = _calib_input(d, plan)
    res = await run_in_threadpool(hg.fit, flow.pairs_m(pairs, float(plan["m_per_px"])), fw / fh, fw, fh)
    return _fit_public(res, plan)


@router.post("/api/flow/calib/{source_id}")
async def calib_save(request: Request, source_id: int):
    if not db.row("SELECT id FROM sources WHERE id=?", (source_id,)):
        raise HTTPException(404, "Kamera topilmadi")
    d = await _body(request)
    plan = _plan_row(int(_num(d.get("plan_id"), "Reja", 1, 1e9)))
    pairs, fw, fh = _calib_input(d, plan)
    res = await run_in_threadpool(hg.fit, flow.pairs_m(pairs, float(plan["m_per_px"])), fw / fh, fw, fh)
    if not res.get("ok"):
        raise HTTPException(400, "Kalibrlab bo'lmadi: " + "; ".join(res.get("warnings") or ["nuqtalarni tekshiring"]))
    model = res["calib"].to_json()
    model["m_per_px"] = float(plan["m_per_px"])
    db.execute("INSERT INTO cam_calib(source_id, plan_id, pairs, model, stats, frame_w, frame_h, enabled, updated_at) "
               "VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(source_id) DO UPDATE SET plan_id=excluded.plan_id, "
               "pairs=excluded.pairs, model=excluded.model, stats=excluded.stats, frame_w=excluded.frame_w, "
               "frame_h=excluded.frame_h, enabled=1, updated_at=excluded.updated_at",
               (source_id, plan["id"], json.dumps(pairs), json.dumps(model), json.dumps(flow.stats_of(res)), fw, fh,
                time.time()))
    flow.bump()
    return {"ok": True, "fit": _fit_public(res, plan)}


@router.post("/api/flow/calib/{source_id}/delete")
def calib_delete(source_id: int):
    db.execute("DELETE FROM cam_calib WHERE source_id=?", (source_id,))
    flow.bump()
    return {"ok": True}


# ============================================================================ natijalar
def _plan_or_first(plan_id: int | None) -> dict | None:
    if plan_id:
        return db.row("SELECT * FROM floor_plans WHERE id=?", (plan_id,))
    return db.row("SELECT * FROM floor_plans ORDER BY id LIMIT 1")


@router.get("/api/flow/summary")
def flow_summary(plan_id: int | None = None, period: str = "today"):
    if period not in PERIODS:
        period = "today"
    from .ai import counting

    plan = _plan_or_first(plan_id)
    plans = [{"id": r["id"], "name": r["name"]} for r in db.rows("SELECT id, name FROM floor_plans ORDER BY id")]
    if not plan:
        return {"plan": None, "plans": plans, "period": period, "periods": list(PERIODS.items()),
                "hook_ok": counting.hook_ok()}
    t0, t1, slot = period_range(period)
    s = flow.visits_summary(plan["id"], t0, t1)
    span = t1 - t0
    prev = flow.visits_summary(plan["id"], t0 - span, t0) if period != "today" else \
        flow.visits_summary(plan["id"], t0 - 86400, min(t1, time.time()) - 86400)
    pv = {x["id"]: x["visits"] for x in prev["sections"]}
    for x in s["sections"]:
        x["prev"] = pv.get(x["id"], 0)
    series = flow.visits_series(plan["id"], t0, t1, slot)
    n_slots = 24 if slot == 3600 else max(1, int(round((min(t1, time.time() + 1) - t0) / 86400 + 0.49)))
    return {"plan": plan_public(plan), "plans": plans, "period": period, "periods": list(PERIODS.items()), "t0": t0, "t1": t1,
            "slot": slot, "n_slots": n_slots, "visits": s, "prev_total": prev["total"], "series": series,
            "hook_ok": counting.hook_ok(),
            "live_cams": sorted({p["source_id"] for p in flow.live_people() if p.get("plan_id") == plan["id"]})}


@router.get("/api/flow/heat")
def flow_heat(plan_id: int, period: str = "today", kind: str = "occ"):
    if period not in PERIODS:
        period = "today"
    t0, t1, _ = period_range(period)
    h = flow.heat(plan_id, t0, t1, "pas" if kind == "pas" else "occ")
    if h is None:
        raise HTTPException(404, "Reja topilmadi")
    return h


@router.get("/api/flow/live")
def flow_live(plan_id: int | None = None):
    lay = flow.layout()
    ppl = [p for p in flow.live_people() if plan_id is None or p.get("plan_id") == plan_id]
    out = []
    for p in ppl:
        plan = lay.plans.get(p.get("plan_id"))
        if plan is None:
            continue
        out.append({"source_id": p["source_id"], "tid": p["tid"], "x": p["x"], "y": p["y"],
                    "px": round(p["x"] / plan.m_per_px, 1), "py": round(p["y"] / plan.m_per_px, 1), "est": p["est"]})
    return {"people": out, "t": time.time()}


@router.get("/api/export/flow.csv")
def flow_csv(days: int = 30):
    days = max(1, min(int(days), 90))
    t0 = _day0() - (days - 1) * 86400
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["kun", "reja", "bo'lim", "tashriflar"])
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM floor_plans")}
    rows = db.rows("SELECT plan_id, section_id, MAX(section_name) AS name, CAST((t - ?) / 86400 AS INTEGER) AS d, "
                   "COUNT(*) AS n FROM section_visits WHERE t>=? GROUP BY plan_id, section_id, d ORDER BY d, plan_id, n DESC",
                   (t0, t0))
    from .visits import _csv_safe

    for r in rows:
        w.writerow([time.strftime("%Y-%m-%d", time.localtime(t0 + int(r["d"]) * 86400 + 43200)),
                    _csv_safe(names.get(r["plan_id"], "")), _csv_safe(r["name"] or ""), int(r["n"])])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="bolimlar_{days}kun.csv"'})


# ============================================================================ darvoza
def _gate() -> dict:
    lay = flow.layout()
    cams = []
    for r in db.rows("SELECT c.source_id, c.plan_id, c.updated_at, s.name FROM cam_calib c LEFT JOIN sources s "
                     "ON s.id=c.source_id WHERE c.enabled=1 ORDER BY c.source_id"):
        tests = db.rows("SELECT * FROM flow_points WHERE source_id=? AND t>=? ORDER BY id DESC",
                        (r["source_id"], float(r["updated_at"] or 0)))
        g = fe.point_gate(tests)
        g.update({"source_id": r["source_id"], "source_name": r["name"] or f"#{r['source_id']}",
                  "plan_id": r["plan_id"], "active": int(r["source_id"]) in lay.cams,
                  "calibrated_at": r["updated_at"]})
        cams.append(g)
    sess = [_session_result(x) for x in db.rows("SELECT * FROM flow_sessions ORDER BY id DESC LIMIT 30")]
    sg = fe.sessions_gate(sess)
    pts_ok = bool(cams) and all(c["ok"] for c in cams)
    return {"cams": cams, "points_ok": pts_ok, "sessions": sess, "visits_gate": sg, "ok": pts_ok and sg["ok"],
            "rules": {"point_min": fe.POINT_MIN, "point_spots": fe.POINT_SPOTS, "point_max_err": fe.POINT_MAX_ERR,
                      "visit_acc": fe.VISIT_ACC, "visit_match": fe.VISIT_MATCH, "visit_min_min": fe.VISIT_MIN_SEC // 60,
                      "visit_sessions": fe.VISIT_SESSIONS, "visit_min_manual": fe.VISIT_MIN_MANUAL,
                      "match_tol": fe.MATCH_TOL, "sample_sec": fe.POINT_SAMPLE_SEC},
            "points": db.rows("SELECT p.*, s.name AS source_name FROM flow_points p LEFT JOIN sources s ON "
                              "s.id=p.source_id ORDER BY p.id DESC LIMIT 60")}


@router.get("/api/flow/gate")
def flow_gate():
    return _gate()


@router.post("/api/flow/points")
async def point_test(request: Request):
    """Nuqtada turish: odam belgilangan joyda turadi; POINT_SAMPLE_SEC davomida oyog'i rejada o'lchanadi."""
    d = await _body(request)
    sid = int(_num(d.get("source_id"), "Kamera", 1, 1e9))
    lay = await run_in_threadpool(flow.layout)
    cam = lay.cams.get(sid)
    if cam is None:
        raise HTTPException(400, "Bu kamera rejaga kalibrlanmagan")
    plan = lay.plans[cam.plan_id]
    px, py = _num(d.get("px"), "x", 0, plan.w_px), _num(d.get("py"), "y", 0, plan.h_px)
    x, y = px * plan.m_per_px, py * plan.m_per_px
    samples: dict[int, list] = {}
    t_end = time.time() + fe.POINT_SAMPLE_SEC
    last_t = None
    while time.time() < t_end:
        mon = flow._live.get(sid)
        snap = mon.snapshot if mon is not None else None
        if snap and snap.get("t") != last_t and time.time() - float(snap.get("t") or 0) <= 2.0:
            last_t = snap.get("t")
            for p in snap.get("people") or []:
                samples.setdefault(int(p["tid"]), []).append((float(p["rx"]), float(p["ry"])))
        await asyncio.sleep(0.1)
    if not samples:
        raise HTTPException(409, "Kamerada (rejaga tushadigan joyda) odam topilmadi. Kamera yoqilganmi, odam belgida "
                                 "turibdimi, oyoqlari ko'rinyaptimi?")
    cands = []
    for tid, arr in samples.items():
        if len(arr) < 3:
            continue
        med = np.median(np.asarray(arr), axis=0)
        cands.append((float(np.hypot(med[0] - x, med[1] - y)), tid, med, arr))
    if not cands:
        raise HTTPException(409, "O'lchov juda qisqa (odam 3 s davomida ko'rinmadi) — qayta urinib ko'ring")
    dist, _, med, arr = min(cands, key=lambda c: c[0])
    note = str(d.get("note") or "")[:120]
    pid = db.execute("INSERT INTO flow_points(t, source_id, plan_id, x, y, mx, my, err, n, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), sid, plan.id, round(x, 3), round(y, 3), round(float(med[0]), 3),
                      round(float(med[1]), 3), round(dist, 3), len(arr), note))
    warn = []
    if len(samples) > 1:
        warn.append(f"Kadrda {len(samples)} kishi bor edi — belgiga eng yaqini olindi")
    if dist > 3:
        warn.append("Xato 3 m dan katta: kalibrlashni yoki rejadagi nuqtani tekshiring")
    return {"ok": True, "id": pid, "err": round(dist, 3), "measured": [round(float(med[0]), 3), round(float(med[1]), 3)],
            "measured_px": [round(float(med[0]) / plan.m_per_px, 1), round(float(med[1]) / plan.m_per_px, 1)],
            "n": len(arr), "warnings": warn, "gate": _gate()}


@router.post("/api/flow/points/{pid}/delete")
def point_delete(pid: int):
    db.execute("DELETE FROM flow_points WHERE id=?", (pid,))
    return {"ok": True}


def _session(sid: int) -> dict:
    r = db.row("SELECT * FROM flow_sessions WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Sinov topilmadi")
    return r


def _session_result(r: dict) -> dict:
    taps = _json(r.get("taps"), []) or []
    secs = [int(x) for x in (_json(r.get("sections"), []) or [])]
    t1 = r["ended_at"] or time.time()
    autos = db.rows("SELECT t, section_id FROM section_visits WHERE plan_id=? AND t>=? AND t<=?",
                    (r["plan_id"], r["started_at"] - fe.MATCH_TOL, t1 + fe.MATCH_TOL))
    res = fe.compare(taps, autos, secs)
    names = {s["id"]: s["name"] for s in db.rows("SELECT id, name FROM plan_sections WHERE plan_id=?", (r["plan_id"],))}
    res["sections"] = [dict(v, id=k, name=names.get(k, f"#{k}")) for k, v in res["sections"].items()]
    res.update({"id": r["id"], "plan_id": r["plan_id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
                "duration": round(t1 - r["started_at"]), "section_ids": secs, "taps": len(taps),
                "note": r.get("note") or ""})
    res["gate_ok"] = fe.session_ok(res)
    return res


@router.get("/api/flow/sessions")
def sessions_list():
    return {"sessions": [_session_result(x) for x in db.rows("SELECT * FROM flow_sessions ORDER BY id DESC LIMIT 30")]}


@router.post("/api/flow/sessions")
async def session_start(request: Request):
    d = await _body(request)
    plan = _plan_row(int(_num(d.get("plan_id"), "Reja", 1, 1e9)))
    secs = d.get("sections")
    have = {int(r["id"]) for r in db.rows("SELECT id FROM plan_sections WHERE plan_id=? AND enabled=1", (plan["id"],))}
    try:
        secs = sorted({int(x) for x in (secs or [])})
    except (TypeError, ValueError):
        raise HTTPException(400, "Bo'limlar ro'yxati noto'g'ri") from None
    if not secs or not set(secs) <= have or len(secs) > 6:
        raise HTTPException(400, "Kuzatiladigan 1–6 ta bo'limni tanlang")
    if db.row("SELECT id FROM flow_sessions WHERE ended_at IS NULL"):
        raise HTTPException(409, "Boshqa sinov davom etyapti — avval uni tugating")
    sid = db.execute("INSERT INTO flow_sessions(started_at, plan_id, sections, taps, note) VALUES(?,?,?,?,?)",
                     (time.time(), plan["id"], json.dumps(secs), "[]", str(d.get("note") or "")[:200]))
    return {"ok": True, "session": _session_result(_session(sid))}


def _taps_count(taps: list) -> dict:
    out: dict = {}
    for _, s in taps:
        out[str(s)] = out.get(str(s), 0) + 1
    return out


@router.post("/api/flow/sessions/{sid}/tap")
async def session_tap(request: Request, sid: int):
    d = await _body(request)
    try:
        sec = int(d.get("section_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "section_id kerak") from None
    with db.tx() as c:
        r = c.execute("SELECT taps, sections, ended_at FROM flow_sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "Sinov topilmadi")
        if r["ended_at"]:
            raise HTTPException(409, "Sinov tugagan")
        if sec not in (_json(r["sections"], []) or []):
            raise HTTPException(400, "Bu bo'lim shu sinovda kuzatilmayapti")
        taps = _json(r["taps"], []) or []
        taps.append([round(time.time(), 2), sec])        # server vaqti — avtomatik tashriflar bilan bir soat
        c.execute("UPDATE flow_sessions SET taps=? WHERE id=?", (json.dumps(taps), sid))
    return {"ok": True, "counts": _taps_count(taps), "n": len(taps)}


@router.post("/api/flow/sessions/{sid}/undo")
def session_undo(sid: int):
    with db.tx() as c:
        r = c.execute("SELECT taps, ended_at FROM flow_sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "Sinov topilmadi")
        if r["ended_at"]:
            raise HTTPException(409, "Sinov tugagan")
        taps = _json(r["taps"], []) or []
        if taps:
            taps.pop()
        c.execute("UPDATE flow_sessions SET taps=? WHERE id=?", (json.dumps(taps), sid))
    return {"ok": True, "counts": _taps_count(taps), "n": len(taps)}


@router.post("/api/flow/sessions/{sid}/stop")
def session_stop(sid: int):
    r = _session(sid)
    if not r["ended_at"]:
        db.execute("UPDATE flow_sessions SET ended_at=? WHERE id=?", (time.time(), sid))
    return {"ok": True, "session": _session_result(_session(sid))}


@router.post("/api/flow/sessions/{sid}/delete")
def session_delete(sid: int):
    _session(sid)
    db.execute("DELETE FROM flow_sessions WHERE id=?", (sid,))
    return {"ok": True}
