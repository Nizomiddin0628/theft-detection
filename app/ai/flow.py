"""Oqim va heatmap (5-bosqich, 2.1.0): odamlar do'kon rejasida — bo'lim tashriflari, yo'nalishlar, heatmap.

Sozlash (panel: Oqim → Reja va kameralar):
  * Reja — do'kon chizmasi (rasm) yoki rasmsiz "eni × bo'yi" (metrda, katakli). Masshtab: rejada 2 nuqta va oradagi
    haqiqiy masofa. Rejadagi hamma narsa (bo'limlar, kalibrlash nuqtalari) reja PIKSELIDA saqlanadi — masshtab keyin
    tuzatilsa, hammasi o'zi to'g'rilanadi.
  * Bo'limlar — reja ustidagi ko'pburchaklar ("Sut mahsulotlari", "Ichimliklar").
  * Kamera kalibrlash — kadrdagi pol nuqtasi ↔ rejadagi o'sha joy (≥ 4 juft, yaxshisi 8–12): app/ai/homography.py.

Hisob (har AI qadamida, app/ai/counting.py dagi LineCounter.update orqali — worker.py ga yangi ulanish kerak emas):
  * Odam oyog'i (ramka pastining o'rtasi; oyoqlari polka orqasida ko'rinmasa — skelet bo'yicha taxmin) rejaga
    o'tkaziladi; xodimlar (xodim zonasida 2 s turgan) hisobga olinmaydi. Joy silliqlanadi (TAU_SEC).
  * Bir nechta kamera bir joyni ko'rsa — shu joyni eng aniq ko'radigan (kadrda 1 m ko'proq piksel) kamera "egasi":
    heatmap katagi ham, bo'lim ham faqat egasi tomonidan hisoblanadi (bitta odam ikki marta sanalmaydi).
  * Bo'lim tashrifi — odam bo'limda jami VISIT_MIN_SEC turib, shundan kamida SLOW_SEC sekin (to'xtab, polkaga qarab)
    yurgan bo'lsa yoki bo'limda LONG_SEC dan ko'p bo'lsa sanaladi. To'xtamay o'tib ketgan, chegarada titragan va
    burchakni kesib o'tgan — tashrif emas; bo'limdan EXIT_GRACE gacha chiqib qaytsa — o'sha tashrif. Vaqti — kirgan payt.
    Chiqib REENTRY_SEC ichida qaytsa — yangi tashrif emas. Bo'limda qancha vaqt turgani (dwell) hisoblanmaydi
    (egasi qarori, 14.10.2026).
  * Yo'nalish — shu odamning oldingi bo'limi (MOVE_WINDOW ichida): "Sut → Non". Faqat bitta kamera ichida (kameralar
    orasida odamni tanish — keyingi bosqich).
  * Heatmap (CELL metrli kataklar): "bandlik" — odam × soniya; "o'tganlar" — katakdan o'tgan odamlar (soatiga bir odam
    bir marta); strelkalar — yurish yo'nalishi (tezlik WALK_MIN..WALK_MAX m/s).
  * Kuzatuvchi ID ni almashtirsa (to'silish) — yangi ID yo'qolgan izning taxminiy joyidan STITCH_DIST ichida va
    STITCH_SEC ichida paydo bo'lsa, o'sha odam (bo'lim holati meros, ikki marta sanalmaydi).

Saqlash: section_visits (har tashrif, 90 kun), flow_hour (soatlik heatmap, siyrak JSON; 90 kun). Bulutga — soatlik
yig'indi (bulut "flow" imkoniyatini e'lon qilgan bo'lsa — eski bulutda navbat to'xtamasin). Video tahlili
(persist=False) bazaga yozmaydi — natija xotirada (sinov uchun).
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import db
from ..logutil import get_logger
from . import homography as hg

log = get_logger("flow")

CELL = 0.5                # heatmap katagi (m)
VEC_CELL = 2.0            # strelkalar katagi (m)
VISIT_MIN_SEC = 2.0       # bo'limda jami shuncha turgan bo'lsin ...
SLOW_SPEED = 0.45         # ... va shu tezlikdan sekin (to'xtab, qarab) jami SLOW_SEC — yoki bo'limda LONG_SEC dan ko'p
SLOW_SEC = 1.0            # (katta bo'lim bo'ylab sekin yurish). To'xtamay o'tib ketgan — tashrif emas ("o'tganlar"
LONG_SEC = 8.0            # heatmap'ida ko'rinadi)
SPEED_WIN = 1.0           # tezlik shuncha vaqtlik siljish bo'yicha (oyoq nuqtasi titrashi tezlikni oshirmasin)
EXIT_GRACE = 1.5
REENTRY_SEC = 15.0
MOVE_WINDOW = 180.0
TAU_SEC = 0.35            # joy silliqlash vaqt doimiysi
LOST_TTL = 4.0
STITCH_SEC = 3.0          # yo'qolgan iz shuncha vaqt ichida yangi ID bilan ulanadi (LOST_TTL dan oshmasin)
STITCH_DIST = 1.0         # m — 1 s gacha to'silish; undan uzoqroq to'silishda ruxsat STITCH_GROW m/s ga kengayadi
STITCH_GROW = 0.5
MAX_DT = 2.0
WALK_MIN, WALK_MAX = 0.25, 3.0
VEC_SPAN = 0.5            # yo'nalish shuncha vaqtlik siljish bo'yicha
PLAN_MAX_M = 150.0        # reja tomoni chegarasi (to'r o'lchami cheklansin)
KEEP_DAYS = 90
LIVE_SEC = 10.0
KPT_CONF = 0.35
CLOUD_CELLS = 5000        # bulutga bitta soatda ko'pi bilan shuncha katak (yozuv ≤ ~150 KB)
TRACE_MAX = 200_000       # video tahlilida joylar ro'yxati chegarasi (xotira o'smasin)
LEG_RATIO = 1.7           # oyoqlar ko'rinmasa: son → pol ≈ 1,7 × (yelka → son)

PALETTE = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626", "#65a30d", "#ea580c",
           "#4f46e5", "#0d9488", "#be185d"]


# ============================================================================ joylashuv (reja + kalibrlash)
@dataclass
class Section:
    id: int
    name: str
    color: str
    poly: np.ndarray                 # (N, 2) metr
    area: float = 0.0
    owner: int | None = None         # source_id
    coverage: float = 0.0            # bo'lim kataklarining qancha qismi biror kameraga ko'rinadi
    owner_share: float = 0.0         # egasi ko'radigan qismi
    _p32: np.ndarray | None = field(default=None, repr=False)

    def contains(self, x: float, y: float) -> bool:
        if self._p32 is None:
            self._p32 = self.poly.astype(np.float32).reshape(-1, 1, 2)
        return cv2.pointPolygonTest(self._p32, (float(x), float(y)), False) >= 0


@dataclass
class Plan:
    id: int
    name: str
    m_per_px: float
    w_px: int
    h_px: int
    sections: list = field(default_factory=list)
    owner: np.ndarray | None = None  # (ny, nx) source_id, 0 — hech kim ko'rmaydi

    @property
    def w_m(self) -> float:
        return self.w_px * self.m_per_px

    @property
    def h_m(self) -> float:
        return self.h_px * self.m_per_px

    @property
    def nx(self) -> int:
        return max(1, int(math.ceil(min(self.w_m, PLAN_MAX_M) / CELL)))

    @property
    def ny(self) -> int:
        return max(1, int(math.ceil(min(self.h_m, PLAN_MAX_M) / CELL)))

    @property
    def grid_key(self) -> str:
        return f"{self.nx}x{self.ny}@{CELL:g}"


@dataclass
class Cam:
    source_id: int
    plan_id: int
    calib: hg.Calib
    sections: list = field(default_factory=list)     # shu kamera egasi bo'lgan bo'limlar
    owned: np.ndarray | None = None                  # (ny, nx) bool


@dataclass
class Layout:
    ver: str = ""
    plans: dict = field(default_factory=dict)         # id -> Plan
    cams: dict = field(default_factory=dict)          # source_id -> Cam


_layout = Layout()
_layout_lock = threading.Lock()
_layout_check = [0.0]
CHECK_SEC = 3.0


def bump() -> None:
    """Reja, bo'lim yoki kalibrlash o'zgardi — kameralar 3 s ichida yangi joylashuvni oladi."""
    db.set_settings({"flow_layout_ver": f"{time.time():.6f}"})
    _layout_check[0] = 0.0


def _ver() -> str:
    try:
        return db.get_setting("flow_layout_ver", "") or "0"
    except Exception:  # noqa: BLE001
        return "0"


def plan_from_row(r: dict) -> Plan:
    return Plan(int(r["id"]), r["name"] or f"Reja {r['id']}", float(r["m_per_px"] or 0.01), int(r["w_px"] or 1),
                int(r["h_px"] or 1))


def cell_centers(p: Plan) -> np.ndarray:
    xs = (np.arange(p.nx) + 0.5) * CELL
    ys = (np.arange(p.ny) + 0.5) * CELL
    gx, gy = np.meshgrid(xs, ys)
    return np.c_[gx.ravel(), gy.ravel()]


def calib_for(row: dict, plan: Plan) -> hg.Calib | None:
    """cam_calib qatoridan model: masshtab o'zgargan bo'lsa (model boshqa m_per_px da moslangan) — qayta moslanadi."""
    try:
        model = json.loads(row.get("model") or "null")
    except ValueError:
        model = None
    if isinstance(model, dict) and abs(float(model.get("m_per_px") or 0) - plan.m_per_px) < 1e-12:
        c = hg.Calib.from_json(model)
        if c is not None:
            return c
    res = refit(row, plan)
    return res["calib"] if res and res.get("ok") else None


def pairs_m(pairs: list, m_per_px: float) -> list[dict]:
    out = []
    for p in pairs or []:
        try:
            out.append({"img": [float(p["img"][0]), float(p["img"][1])],
                        "plan": [float(p["plan"][0]) * m_per_px, float(p["plan"][1]) * m_per_px],
                        "check": bool(p.get("check"))})
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out


def refit(row: dict, plan: Plan) -> dict | None:
    try:
        pairs = json.loads(row.get("pairs") or "[]")
    except ValueError:
        return None
    fw, fh = int(row.get("frame_w") or 1280), int(row.get("frame_h") or 720)
    res = hg.fit(pairs_m(pairs, plan.m_per_px), fw / max(fh, 1), fw, fh)
    if res.get("ok"):
        model = res["calib"].to_json()
        model["m_per_px"] = plan.m_per_px
        try:
            # Masshtab o'zgargan — model yangi: darvozaning eski "nuqtada turish" o'lchovlari hisobga olinmaydi
            db.execute("UPDATE cam_calib SET model=?, stats=?, updated_at=? WHERE source_id=?",
                       (json.dumps(model), json.dumps(stats_of(res)), time.time(), row["source_id"]))
        except Exception:  # noqa: BLE001
            log.exception("Kalibrlashni saqlab bo'lmadi")
    return res


def stats_of(res: dict) -> dict:
    return {k: res.get(k) for k in ("rms", "loo_rms", "loo_max", "check_rms", "check_max", "n", "n_check", "k",
                                    "hull_area")}


def build_layout() -> Layout:
    lay = Layout(ver=_ver())
    for r in db.rows("SELECT * FROM floor_plans ORDER BY id"):
        p = plan_from_row(r)
        for s in db.rows("SELECT * FROM plan_sections WHERE plan_id=? AND enabled=1 ORDER BY id", (p.id,)):
            try:
                pts = np.asarray(json.loads(s["points"] or "[]"), dtype=np.float64).reshape(-1, 2) * p.m_per_px
            except (TypeError, ValueError):
                continue
            if len(pts) < 3 or not np.isfinite(pts).all():
                continue
            area = float(abs(cv2.contourArea(pts.astype(np.float32))))
            p.sections.append(Section(int(s["id"]), s["name"] or f"Bo'lim {s['id']}", s["color"] or PALETTE[0], pts,
                                      area))
        lay.plans[p.id] = p
    rows = db.rows("SELECT c.* FROM cam_calib c JOIN sources s ON s.id=c.source_id WHERE c.enabled=1")
    for r in rows:
        p = lay.plans.get(int(r["plan_id"] or 0))
        if p is None:
            continue
        c = calib_for(r, p)
        if c is not None:
            lay.cams[int(r["source_id"])] = Cam(int(r["source_id"]), p.id, c)
    for p in lay.plans.values():
        _assign(p, [c for c in lay.cams.values() if c.plan_id == p.id])
    return lay


def _assign(p: Plan, cams: list[Cam]) -> None:
    """Katak va bo'lim egalari: shu joyni kadrda eng katta (1 m ko'proq piksel) ko'radigan kamera."""
    ny, nx = p.ny, p.nx
    p.owner = np.zeros((ny, nx), dtype=np.int64)
    if not cams:
        for s in p.sections:
            s.owner, s.coverage, s.owner_share = None, 0.0, 0.0
        return
    cc = cell_centers(p)
    best = np.zeros(len(cc))
    for c in sorted(cams, key=lambda c: c.source_id):
        ppm = c.calib.px_per_m(cc)
        uv, ok = c.calib.to_image(cc)
        vis = ok & c.calib.in_valid(uv) & (ppm >= hg.MIN_PX_PER_M)
        better = vis & (ppm > best)
        best[better] = ppm[better]
        p.owner.ravel()[better] = c.source_id
    for c in cams:
        c.owned = p.owner == c.source_id
        c.sections = []
    for s in p.sections:
        m = np.zeros((ny, nx), dtype=np.uint8)
        cv2.fillPoly(m, [np.round(s.poly / CELL - 0.5).astype(np.int32)], 1)
        cells = m.astype(bool)
        if not cells.any():          # juda kichik bo'lim — markazidagi katak
            cx, cy = (s.poly.mean(axis=0) / CELL).astype(int)
            if 0 <= cx < nx and 0 <= cy < ny:
                cells[cy, cx] = True
        own = p.owner[cells]
        tot = max(int(cells.sum()), 1)
        s.coverage = float((own > 0).sum()) / tot
        if (own > 0).any():
            ids, cnt = np.unique(own[own > 0], return_counts=True)
            s.owner = int(ids[np.argmax(cnt)])
            s.owner_share = float(cnt.max()) / tot
            for c in cams:
                if c.source_id == s.owner:
                    c.sections.append(s)
        else:
            s.owner, s.owner_share = None, 0.0


def layout(force: bool = False) -> Layout:
    """Joriy joylashuv (keshlangan; versiya o'zgarsa — CHECK_SEC ichida qayta quriladi)."""
    global _layout
    now = time.monotonic()
    if not force and now - _layout_check[0] < CHECK_SEC:
        return _layout
    with _layout_lock:
        if not force and now - _layout_check[0] < CHECK_SEC:
            return _layout
        _layout_check[0] = now
        v = _ver()
        if force or v != _layout.ver:
            try:
                _layout = build_layout()
            except Exception:  # noqa: BLE001
                # Vaqtinchalik xato (masalan, "database is locked"): eski joylashuv qoladi, keyingi tekshiruvda qayta
                # quriladi (versiya bo'sh — albatta farq qiladi)
                log.exception("Oqim joylashuvini qurib bo'lmadi — keyinroq qayta uriniladi")
                _layout = dataclasses.replace(_layout, ver="")
        return _layout


def has_calib(source_id: int | None) -> bool:
    return bool(source_id) and int(source_id) in layout().cams


# ============================================================================ oyoq nuqtasi
def foot_point(p) -> tuple[float, float, bool]:
    """(x, y, taxmin) piksel. Odatda ramka pastining o'rtasi. Oyoqlar (tizza va to'piq) ko'rinmasa, son va yelka
    ko'rinsa (polka, peshtaxta to'sgan) — ramka pastki qismi kesilgan: pol ≈ son + LEG_RATIO × (son − yelka)."""
    b = p.box
    x, y = float(b[0] + b[2]) / 2.0, float(b[3])
    k = getattr(p, "kpts", None)
    try:
        if k is not None and len(k) >= 17:
            c = k[:, 2]
            if c[13] < KPT_CONF and c[14] < KPT_CONF and c[15] < KPT_CONF and c[16] < KPT_CONF:
                hips = [i for i in (11, 12) if c[i] >= KPT_CONF]
                sh = [i for i in (5, 6) if c[i] >= KPT_CONF]
                if hips and sh:
                    hy = float(np.mean(k[hips, 1]))
                    torso = hy - float(np.mean(k[sh, 1]))
                    bh = max(float(b[3] - b[1]), 1.0)
                    if torso > 4:
                        est = hy + LEG_RATIO * torso
                        if est > y + 0.1 * bh:
                            return float(np.mean(k[hips, 0])), est, True
    except (IndexError, TypeError, ValueError):
        pass
    return x, y, False


# ============================================================================ yig'indi (soatlik)
class _Acc:
    __slots__ = ("hour", "plan_id", "source_id", "grid", "obs", "occ", "pas", "vec")

    def __init__(self, hour: int, plan_id: int, source_id: int, grid: str):
        self.hour, self.plan_id, self.source_id, self.grid = hour, plan_id, source_id, grid
        self.obs = 0.0
        self.occ: dict[int, float] = {}
        self.pas: dict[int, int] = {}
        self.vec: dict[int, list] = {}

    def merge(self, o: "_Acc") -> None:
        self.obs += o.obs
        for k, v in o.occ.items():
            self.occ[k] = self.occ.get(k, 0.0) + v
        for k, v in o.pas.items():
            self.pas[k] = self.pas.get(k, 0) + v
        for k, v in o.vec.items():
            a = self.vec.setdefault(k, [0, 0.0, 0.0])
            a[0] += v[0]
            a[1] += v[1]
            a[2] += v[2]


_acc_lock = threading.RLock()
_NOLOCK = contextlib.nullcontext()
_acc: dict[tuple, _Acc] = {}
_dirty_hours: set = set()              # (plan_id, soat) — yangi tashrif yozildi (bulutga qayta yuborish)
_live_lock = threading.Lock()
_live: dict[int, "FlowMonitor"] = {}


def _hour(t: float) -> int:
    return int(t // 3600) * 3600


@dataclass
class _Sec:
    t_enter: float
    acc: float = 0.0
    slow: float = 0.0
    last_in: float = 0.0
    counted: bool = False


@dataclass
class _T:
    tid: int
    x: float
    y: float
    t: float
    first_t: float
    vx: float = 0.0
    vy: float = 0.0
    anchor: tuple = (0.0, 0.0, 0.0)              # (x, y, t) — yo'nalish uchun
    secs: dict = field(default_factory=dict)     # bo'lim -> _Sec (hozir ichida / yaqinda chiqqan)
    left: dict = field(default_factory=dict)     # bo'lim -> chiqqan vaqti (qayta kirish)
    last_visit: tuple | None = None              # (bo'lim, vaqt) — yo'nalish uchun
    cells: set = field(default_factory=set)      # (soat, katak) — "o'tganlar" soatiga bir marta
    seen_t: float = 0.0                          # oxirgi marta kadrda ko'ringan (rejada bo'lmasa ham)
    hist: list = field(default_factory=list)     # [(t, x, y)] — oxirgi SPEED_WIN+ soniya (tezlik uchun)
    speed: float | None = None                   # m/s, SPEED_WIN bo'yicha (None — hali noma'lum)


@dataclass
class Visit:
    t: float
    plan_id: int
    section_id: int
    section_name: str
    source_id: int | None
    track_id: int
    from_id: int | None = None


class FlowMonitor:
    """Bitta kamera uchun. update() har AI qadamida chaqiriladi (LineCounter.update ichidan)."""

    def __init__(self, source_id: int | None, source_name: str = "", persist: bool = True):
        self.source_id = source_id
        self.source_name = source_name
        self.persist = persist and bool(source_id)
        self.collect = not self.persist
        self.tracks: dict[int, _T] = {}
        self.last_t: float | None = None
        self.visits: list[Visit] = []          # persist=False: hamma tashriflar
        self.local: dict[tuple, _Acc] = {}     # persist=False: heatmap
        self.snapshot: dict = {"t": 0.0, "people": []}
        self.ver = ""
        self.cam: Cam | None = None
        self.plan: Plan | None = None
        self.calib_override: tuple | None = None   # sinov: (Plan, Cam) — bazasiz
        self.trace: deque = deque(maxlen=TRACE_MAX)   # persist=False: (t, iz, x, y) — video tahlili va sinov
        self.cpu_sec = 0.0
        self.n_updates = 0

    # ------------------------------------------------------------------
    def update(self, tracked: list, t: float, w: int, h: int, zoneset=None, status: dict | None = None,
               persist: bool | None = None) -> list[Visit]:
        c0 = time.perf_counter()
        try:
            return self._update(tracked, t, w, h, status, persist)
        finally:
            self.cpu_sec += time.perf_counter() - c0
            self.n_updates += 1

    def _update(self, tracked: list, t: float, w: int, h: int, status: dict | None, persist: bool | None) -> list[Visit]:
        if persist is not None:
            self.persist = bool(persist) and bool(self.source_id)
        if self.calib_override is not None:
            plan, cam = self.calib_override
        else:
            lay = layout()
            cam = lay.cams.get(int(self.source_id or 0))
            plan = lay.plans.get(cam.plan_id) if cam else None
            if lay.ver != self.ver:
                self.ver = lay.ver
                if self.cam is not None and cam is not None and (cam.plan_id != self.cam.plan_id):
                    self.tracks.clear()
        if cam is None or plan is None:
            self.tracks.clear()
            self.cam = self.plan = None
            self.snapshot = {"t": time.time(), "people": []}
            return []
        self.cam, self.plan = cam, plan
        status = status or {}
        dt_frame = 0.0 if self.last_t is None else min(max(t - self.last_t, 0.0), MAX_DT)
        self.last_t = t
        heat_jobs: list = []
        # 1) Oyoq nuqtalari → reja
        cand = []
        for p in tracked:
            tid = int(getattr(p, "track_id", -1))
            if tid < 0:
                continue
            if (status.get(tid) or {}).get("staff"):
                self.tracks.pop(tid, None)
                continue
            fx, fy, est = foot_point(p)
            cand.append((tid, fx / max(w, 1), fy / max(h, 1), est))
        pos = {}
        if cand:
            xy, ok = cam.calib.to_plan(np.array([[u, v] for _, u, v, _ in cand], dtype=np.float64))
            for (tid, u, v, est), (x, y), good in zip(cand, xy, ok):
                if good and -1.0 <= x <= plan.w_m + 1.0 and -1.0 <= y <= plan.h_m + 1.0:
                    pos[tid] = (float(x), float(y), u, v, est)
        seen_ids = {c[0] for c in cand}
        for tid in seen_ids:
            if tid in self.tracks:
                self.tracks[tid].seen_t = t
        # 2) Yangi ID — yo'qolgan izning davomi bo'lishi mumkin (to'silish, ID almashishi)
        fresh = [tid for tid in pos if tid not in self.tracks]
        if fresh:
            self._stitch(fresh, pos, t, seen_ids)
        out: list[Visit] = []
        people = []
        for tid, (x, y, u, v, est) in pos.items():
            tr = self.tracks.get(tid)
            if tr is None:
                tr = _T(tid, x, y, t, t, anchor=(x, y, t), seen_t=t)
                self.tracks[tid] = tr
                dt = 0.0
            else:
                dt = min(max(t - tr.t, 0.0), MAX_DT)
                a = 1.0 - math.exp(-dt / TAU_SEC) if dt > 0 else 1.0
                nx_, ny_ = tr.x + (x - tr.x) * a, tr.y + (y - tr.y) * a
                if dt > 0:
                    tr.vx, tr.vy = (nx_ - tr.x) / dt, (ny_ - tr.y) / dt
                tr.x, tr.y = nx_, ny_
                tr.t = t
            tr.seen_t = t
            tr.hist.append((t, tr.x, tr.y))
            while len(tr.hist) > 2 and t - tr.hist[1][0] >= SPEED_WIN:
                tr.hist.pop(0)
            h0 = tr.hist[0]
            tr.speed = math.hypot(tr.x - h0[1], tr.y - h0[2]) / (t - h0[0]) if t - h0[0] >= 0.6 * SPEED_WIN else None
            if self.collect:
                self.trace.append((t, tid, tr.x, tr.y))
            heat_jobs.append((tr, dt))
            out.extend(self._sections(plan, cam, tr, dt, t))
            people.append({"tid": tid, "x": round(tr.x, 3), "y": round(tr.y, 3), "rx": round(x, 3), "ry": round(y, 3),
                           "u": round(u, 4), "v": round(v, 4), "est": bool(est)})
        # Heatmap yig'indisi: jonli kamerada qulf ostida (flush() shu paytda yig'indini bazaga olib ketishi mumkin —
        # yozuv yo yangi yig'indiga tushadi, yo eskisi bilan birga yoziladi, yo'qolmaydi)
        with (_acc_lock if self.persist else _NOLOCK):
            acc = self._acc(t, plan, cam)
            acc.obs += dt_frame
            for tr, dt in heat_jobs:
                self._heat(acc, plan, cam, tr, dt, t)
        # 3) Ko'rinmagan izlar: bo'limdan chiqish muddati va eskirish
        for tid in [k for k in self.tracks if k not in pos]:
            tr = self.tracks[tid]
            for sid in [s for s, st in tr.secs.items() if t - st.last_in > EXIT_GRACE]:
                st = tr.secs.pop(sid)
                if st.counted:
                    tr.left[sid] = st.last_in
            if t - max(tr.t, tr.seen_t) > LOST_TTL:
                self.tracks.pop(tid, None)
        self.snapshot = {"t": time.time(), "plan_id": plan.id, "people": people}
        if self.persist:
            with _live_lock:
                _live[int(self.source_id)] = self
        if out:
            self._emit(out)
        return out

    # ------------------------------------------------------------------
    def _acc(self, t: float, plan: Plan, cam: Cam) -> _Acc:
        key = (_hour(t), plan.id, int(self.source_id or 0))
        if not self.persist:
            a = self.local.get(key)
            if a is None or a.grid != plan.grid_key:
                a = self.local[key] = _Acc(*key, plan.grid_key)
            return a
        with _acc_lock:
            a = _acc.get(key)
            if a is None or a.grid != plan.grid_key:
                a = _acc[key] = _Acc(*key, plan.grid_key)
            return a

    def _stitch(self, fresh: list[int], pos: dict, t: float, seen_ids: set) -> None:
        pairs = []
        for otid, tr in self.tracks.items():
            if otid in seen_ids or not (0 <= t - tr.t <= STITCH_SEC):
                continue
            gap = t - tr.t
            dt = min(gap, 1.0)
            px, py = tr.x + tr.vx * dt, tr.y + tr.vy * dt
            lim = STITCH_DIST + STITCH_GROW * max(0.0, gap - 1.0)
            for tid in fresh:
                x, y = pos[tid][0], pos[tid][1]
                d = math.hypot(x - px, y - py)
                if d <= lim:
                    pairs.append((d, tid, otid))
        used_new, used_old = set(), set()
        for d, tid, otid in sorted(pairs):
            if tid in used_new or otid in used_old:
                continue
            used_new.add(tid)
            used_old.add(otid)
            tr = self.tracks.pop(otid)
            tr.tid = tid
            self.tracks[tid] = tr

    def _heat(self, acc: _Acc, plan: Plan, cam: Cam, tr: _T, dt: float, t: float) -> None:
        cx, cy = int(tr.x // CELL), int(tr.y // CELL)
        if not (0 <= cx < plan.nx and 0 <= cy < plan.ny):
            return
        if cam.owned is not None and not cam.owned[cy, cx]:
            return                                     # bu joyni boshqa kamera aniqroq ko'radi
        cell = cy * plan.nx + cx
        if dt > 0:
            acc.occ[cell] = acc.occ.get(cell, 0.0) + dt
        key = (acc.hour, cell)
        if key not in tr.cells:
            tr.cells.add(key)
            acc.pas[cell] = acc.pas.get(cell, 0) + 1
            if len(tr.cells) > 4000:
                tr.cells = {k for k in tr.cells if k[0] == acc.hour}
        ax, ay, at = tr.anchor
        span = t - at
        if span >= VEC_SPAN:
            dx, dy = tr.x - ax, tr.y - ay
            sp = math.hypot(dx, dy) / span
            if WALK_MIN <= sp <= WALK_MAX and span <= 3 * VEC_SPAN:
                vc = int(tr.y // VEC_CELL) * 100000 + int(tr.x // VEC_CELL)
                a = acc.vec.setdefault(vc, [0, 0.0, 0.0])
                a[0] += 1
                a[1] += dx / (sp * span)
                a[2] += dy / (sp * span)
            tr.anchor = (tr.x, tr.y, t)

    def _sections(self, plan: Plan, cam: Cam, tr: _T, dt: float, t: float) -> list[Visit]:
        out = []
        for s in cam.sections:
            inside = s.contains(tr.x, tr.y)
            st = tr.secs.get(s.id)
            if inside:
                if st is None:
                    st = tr.secs[s.id] = _Sec(t_enter=t, last_in=t)
                else:
                    step = min(dt, max(t - st.last_in, 0.0))
                    st.acc += step
                    if tr.speed is not None and tr.speed < SLOW_SPEED:
                        st.slow += step
                    st.last_in = t
                if not st.counted and st.acc >= VISIT_MIN_SEC and (st.slow >= SLOW_SEC or st.acc >= LONG_SEC):
                    st.counted = True
                    lt = tr.left.get(s.id)
                    if lt is not None and st.t_enter - lt < REENTRY_SEC:
                        continue                       # bir lahza chiqib qaytdi — o'sha tashrif
                    frm = None
                    if tr.last_visit is not None and tr.last_visit[0] != s.id:
                        prev = tr.last_visit[0]
                        pt = max(tr.left.get(prev, 0.0), tr.last_visit[1])
                        if st.t_enter - pt <= MOVE_WINDOW:
                            frm = prev
                    tr.last_visit = (s.id, t)
                    out.append(Visit(st.t_enter, plan.id, s.id, s.name, self.source_id, tr.tid, frm))
            elif st is not None and t - st.last_in > EXIT_GRACE:
                tr.secs.pop(s.id, None)
                if st.counted:
                    tr.left[s.id] = st.last_in
                    if tr.last_visit and tr.last_visit[0] == s.id:
                        tr.last_visit = (s.id, st.last_in)
        return out

    def _emit(self, visits: list[Visit]) -> None:
        if not self.persist:
            if self.collect:
                self.visits.extend(visits)
            return
        try:
            with db.tx() as c:
                for v in visits:
                    c.execute("INSERT INTO section_visits(t, plan_id, section_id, section_name, source_id, track_id, "
                              "from_id) VALUES(?,?,?,?,?,?,?)", (v.t, v.plan_id, v.section_id, v.section_name,
                                                                 v.source_id, v.track_id, v.from_id))
            with _acc_lock:
                for v in visits:
                    _dirty_hours.add((v.plan_id, _hour(v.t)))
        except Exception:  # noqa: BLE001
            log.exception("Bo'lim tashriflarini yozib bo'lmadi")


# ============================================================================ jonli holat
def live_people(source_id: int | None = None) -> list[dict]:
    """Jonli kameralardagi odamlar rejada (LIVE_SEC dan eski holat — kamera ishlamayapti)."""
    now = time.time()
    out = []
    with _live_lock:
        mons = list(_live.items())
    for sid, m in mons:
        if source_id is not None and sid != int(source_id):
            continue
        snap = m.snapshot
        if now - float(snap.get("t") or 0) > LIVE_SEC:
            continue
        for p in snap.get("people") or []:
            out.append(dict(p, source_id=sid, plan_id=snap.get("plan_id")))
    return out


# ============================================================================ saqlash (har daqiqa)
def _merge_json(old: str | None, add: dict, kind: str) -> str:
    try:
        d = json.loads(old) if old else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    for k, v in add.items():
        ks = str(k)
        if kind == "vec":
            a = d.get(ks) or [0, 0.0, 0.0]
            d[ks] = [int(a[0]) + int(v[0]), round(float(a[1]) + v[1], 2), round(float(a[2]) + v[2], 2)]
        elif kind == "pas":
            d[ks] = int(d.get(ks) or 0) + int(v)
        else:
            d[ks] = round(float(d.get(ks) or 0.0) + v, 2)
    return json.dumps(d, separators=(",", ":"))


def flush() -> tuple[set, set]:
    """Xotiradagi soatlik yig'indilar bazaga (mavjud qatorga qo'shiladi). Qaytaradi: (o'zgargan (reja, soat) lar,
    yangi tashrif yozilgan (reja, soat) lar). Yozib bo'lmasa — yig'indi xotiraga qaytariladi (keyingi daqiqada)."""
    with _acc_lock:
        accs = list(_acc.values())
        _acc.clear()
        dirty = set(_dirty_hours)
        _dirty_hours.clear()
    if not accs and not dirty:
        return set(), set()
    touched = set(dirty)
    try:
        with db.tx() as c:
            for a in accs:
                r = c.execute("SELECT grid, obs, occ, pas, vec FROM flow_hour WHERE hour=? AND plan_id=? AND source_id=?",
                              (a.hour, a.plan_id, a.source_id)).fetchone()
                if r is not None and r["grid"] != a.grid:
                    r = None                           # reja masshtabi o'zgargan — eski to'r almashtiriladi
                c.execute("INSERT INTO flow_hour(hour, plan_id, source_id, grid, obs, occ, pas, vec) "
                          "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(hour, plan_id, source_id) DO UPDATE SET grid=excluded.grid, "
                          "obs=excluded.obs, occ=excluded.occ, pas=excluded.pas, vec=excluded.vec",
                          (a.hour, a.plan_id, a.source_id, a.grid, round((r["obs"] if r else 0.0) + a.obs, 2),
                           _merge_json(r["occ"] if r else None, a.occ, "occ"),
                           _merge_json(r["pas"] if r else None, a.pas, "pas"),
                           _merge_json(r["vec"] if r else None, a.vec, "vec")))
                touched.add((a.plan_id, a.hour))
    except Exception:
        with _acc_lock:                                # hech narsa yo'qolmasin: keyingi daqiqada qayta yoziladi
            for a in accs:
                key = (a.hour, a.plan_id, a.source_id)
                cur = _acc.get(key)
                if cur is None:
                    _acc[key] = a
                elif cur.grid == a.grid:
                    cur.merge(a)
            _dirty_hours.update(dirty)
        raise
    with _heat_lock:
        _heat_cache.clear()                            # yangi ma'lumot — heatmap keshi eskirdi
    return touched, dirty


CLOUD_EVERY_SEC = 600     # joriy soat bulutga shuncha vaqtda bir marta (yangi tashrif bo'lsa — darhol)
BACKFILL_DAYS = 7         # bulut "flow" ni birinchi e'lon qilganda oxirgi shuncha kun yuboriladi


def on_minute(minute: int) -> None:
    """MetricsSampler har daqiqada: yig'indini yozish, bulut navbati, tozalash."""
    try:
        touched, dirty = flush()
    except Exception:  # noqa: BLE001
        log.exception("Oqim yig'indisini yozib bo'lmadi (keyingi daqiqada qayta uriniladi)")
        return
    try:
        from .. import sync

        if sync.cloud_supports("flow"):
            cur_h = _hour(minute + 59)
            for plan_id, hour in sorted(touched):
                # Yopilgan soat — oxirgi marta; joriy soat — yangi tashrif bo'lsa yoki 10 daqiqada bir (heatmap)
                if hour < cur_h or (plan_id, hour) in dirty or minute % CLOUD_EVERY_SEC < 60:
                    item = hour_item(plan_id, hour)
                    if item is not None:
                        sync.enqueue_flow_hour(item)
            backfill()
    except Exception:  # noqa: BLE001
        log.exception("Oqim yig'indisini bulut navbatiga qo'yib bo'lmadi")
    try:
        prune()
    except Exception:  # noqa: BLE001
        log.exception("Oqim jadvallarini tozalab bo'lmadi")


def backfill(days: int = BACKFILL_DAYS) -> int:
    """Bulut "flow" ni birinchi marta e'lon qilganda: oxirgi kunlar soatlari (bir marta)."""
    if db.get_setting("flow_backfill_done", "") == "1":
        return 0
    from .. import sync

    t0 = time.time() - days * 86400
    hours = {(int(r["plan_id"]), int(r["hour"])) for r in db.rows(
        "SELECT DISTINCT plan_id, hour FROM flow_hour WHERE hour >= ?", (t0,))}
    hours |= {(int(r["plan_id"]), int(r["h"])) for r in db.rows(
        "SELECT DISTINCT plan_id, CAST(t / 3600 AS INTEGER) * 3600 AS h FROM section_visits WHERE t >= ?", (t0,))}
    n = 0
    for plan_id, hour in sorted(hours):
        item = hour_item(plan_id, hour)
        if item is not None:
            sync.enqueue_flow_hour(item)
            n += 1
    db.set_settings({"flow_backfill_done": "1"})
    if n:
        log.info("Bulutga oqimning oxirgi %d kunlik %d soati navbatga qo'yildi", days, n)
    return n


def prune() -> None:
    now = time.time()
    last = float(db.get_setting("flow_prune_at", "0") or 0)
    if now - last < 3600:
        return
    db.set_settings({"flow_prune_at": f"{now:.0f}"})
    db.execute("DELETE FROM section_visits WHERE t < ?", (now - KEEP_DAYS * 86400,))
    db.execute("DELETE FROM flow_hour WHERE hour < ?", (now - KEEP_DAYS * 86400,))


# ============================================================================ hisobot
def _sparse_sum(rows: list[dict], col: str, n: int) -> np.ndarray:
    g = np.zeros(n, dtype=np.float64)
    for r in rows:
        try:
            d = json.loads(r[col] or "{}")
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            try:
                i = int(k)
                if 0 <= i < n:
                    g[i] += float(v[0] if isinstance(v, list) else v)
            except (TypeError, ValueError, IndexError):
                continue
    return g


_heat_cache: dict = {}
_heat_lock = threading.Lock()
HEAT_CACHE_SEC = 60.0


def heat(plan_id: int, t0: float, t1: float, kind: str = "occ") -> dict | None:
    """Heatmap (1 daqiqa kesh: 30 kunlik yig'indi har so'rovda qayta hisoblanmasin)."""
    key = (int(plan_id), int(t0), int(t1 // 60), kind, layout().ver)
    now = time.monotonic()
    with _heat_lock:
        hit = _heat_cache.get(key)
        if hit and now - hit[0] < HEAT_CACHE_SEC:
            return hit[1]
    res = _heat(plan_id, t0, t1, kind)
    with _heat_lock:
        if len(_heat_cache) > 32:
            _heat_cache.clear()
        _heat_cache[key] = (now, res)
    return res


def _heat(plan_id: int, t0: float, t1: float, kind: str = "occ") -> dict | None:
    """Heatmap: kataklar (ny × nx) bo'yicha yig'indi. kind: occ — odam × soniya / m², pas — o'tgan odamlar."""
    lay = layout()
    p = lay.plans.get(int(plan_id))
    if p is None:
        return None
    rows = db.rows("SELECT * FROM flow_hour WHERE plan_id=? AND hour>=? AND hour<? AND grid=?",
                   (p.id, _hour(t0), t1, p.grid_key))
    n = p.nx * p.ny
    g = _sparse_sum(rows, "pas" if kind == "pas" else "occ", n).reshape(p.ny, p.nx)
    obs = sum(float(r["obs"] or 0) for r in rows)
    raw_max = float(g.max()) if g.size else 0.0
    sm = cv2.GaussianBlur(g.astype(np.float32), (0, 0), 0.8) if raw_max > 0 else g.astype(np.float32)
    if kind != "pas":
        sm = sm / (CELL * CELL)                        # odam × soniya / m²
    mx = float(sm.max()) if sm.size else 0.0
    q = np.zeros(sm.shape, dtype=np.uint8) if mx <= 0 else np.clip(np.round(sm / mx * 255), 0, 255).astype(np.uint8)
    # Strelkalar (VEC_CELL m): n ≥ 3 bo'lgan kataklar, o'rtacha yo'nalish
    vec: dict = {}
    for r in rows:
        try:
            d = json.loads(r["vec"] or "{}")
        except ValueError:
            continue
        for k, v in (d.items() if isinstance(d, dict) else []):
            a = vec.setdefault(k, [0, 0.0, 0.0])
            a[0] += int(v[0])
            a[1] += float(v[1])
            a[2] += float(v[2])
    arrows = []
    for k, (cnt, sx, sy) in vec.items():
        if cnt < 3:
            continue
        try:
            ki = int(k)
        except ValueError:
            continue
        gy, gx = divmod(ki, 100000)
        L = math.hypot(sx, sy)
        if L <= 0:
            continue
        arrows.append({"x": round((gx + 0.5) * VEC_CELL, 2), "y": round((gy + 0.5) * VEC_CELL, 2),
                       "dx": round(sx / L, 3), "dy": round(sy / L, 3), "n": cnt,
                       "c": round(L / cnt, 3)})          # c — yo'nalishlar mosligi (1 — hamma bir tomonga)
    import base64

    return {"plan_id": p.id, "nx": p.nx, "ny": p.ny, "cell": CELL, "kind": kind, "max": round(mx, 3),
            "total": round(float(g.sum()), 2), "obs": round(obs, 1), "hours": len({r["hour"] for r in rows}),
            "data": base64.b64encode(q.tobytes()).decode(), "arrows": arrows,
            "unit": "o'tgan odam" if kind == "pas" else "odam·s/m²"}


def visits_summary(plan_id: int, t0: float, t1: float) -> dict:
    lay = layout()
    p = lay.plans.get(int(plan_id))
    secs = {s.id: s for s in p.sections} if p else {}
    rows = db.rows("SELECT section_id, MAX(section_name) AS name, COUNT(*) AS n FROM section_visits WHERE plan_id=? "
                   "AND t>=? AND t<? GROUP BY section_id", (plan_id, t0, t1))
    by = {int(r["section_id"]): r for r in rows}
    tot = sum(int(r["n"]) for r in rows)
    out = []
    for sid in list(secs) + [k for k in by if k not in secs]:
        s = secs.get(sid)
        r = by.get(sid)
        n = int(r["n"]) if r else 0
        out.append({"id": sid, "name": s.name if s else (r["name"] if r else str(sid)), "color": s.color if s else "#94a3b8",
                    "visits": n, "share": round(n / tot, 4) if tot else 0.0, "active": s is not None,
                    "coverage": round(s.coverage, 3) if s else None, "owner": s.owner if s else None,
                    "area": round(s.area, 1) if s else None})
    out.sort(key=lambda x: (-x["visits"], x["name"]))
    moves = db.rows("SELECT from_id, section_id AS to_id, COUNT(*) AS n FROM section_visits WHERE plan_id=? AND t>=? "
                    "AND t<? AND from_id IS NOT NULL GROUP BY from_id, section_id ORDER BY n DESC LIMIT 30",
                    (plan_id, t0, t1))
    names = {x["id"]: x["name"] for x in out}
    return {"total": tot, "sections": out,
            "moves": [{"from": int(m["from_id"]), "to": int(m["to_id"]), "n": int(m["n"]),
                       "from_name": names.get(int(m["from_id"]), str(m["from_id"])),
                       "to_name": names.get(int(m["to_id"]), str(m["to_id"]))} for m in moves]}


def visits_series(plan_id: int, t0: float, t1: float, slot: int) -> list[dict]:
    """Bo'limlar bo'yicha vaqt qatori (slot — 3600 yoki 86400; mahalliy vaqtda — chaqiruvchi t0 ni yarim tundan beradi)."""
    rows = db.rows("SELECT CAST((t - ?) / ? AS INTEGER) AS b, section_id, COUNT(*) AS n FROM section_visits "
                   "WHERE plan_id=? AND t>=? AND t<? GROUP BY b, section_id ORDER BY b", (t0, slot, plan_id, t0, t1))
    out: dict[int, dict] = {}
    for r in rows:
        b = int(r["b"])
        out.setdefault(b, {"t": t0 + b * slot, "by": {}})["by"][int(r["section_id"])] = int(r["n"])
    return [out[k] for k in sorted(out)]


def hour_item(plan_id: int, hour: int) -> dict | None:
    """Bulutga ketadigan soatlik yig'indi: bo'lim tashriflari, o'tishlar, heatmap (barcha kameralar birga)."""
    lay = layout()
    p = lay.plans.get(int(plan_id))
    if p is None:
        return None
    rows = db.rows("SELECT * FROM flow_hour WHERE plan_id=? AND hour=? AND grid=?", (p.id, hour, p.grid_key))
    n = p.nx * p.ny
    occ = _sparse_sum(rows, "occ", n)
    pas = _sparse_sum(rows, "pas", n)
    vis = db.rows("SELECT section_id, COUNT(*) AS n FROM section_visits WHERE plan_id=? AND t>=? AND t<? "
                  "GROUP BY section_id", (p.id, hour, hour + 3600))
    mv = db.rows("SELECT from_id, section_id, COUNT(*) AS n FROM section_visits WHERE plan_id=? AND t>=? AND t<? "
                 "AND from_id IS NOT NULL GROUP BY from_id, section_id", (p.id, hour, hour + 3600))
    if not rows and not vis:
        return None
    return {"plan_id": p.id, "hour": hour, "grid": p.grid_key, "nx": p.nx, "ny": p.ny, "cell": CELL,
            "obs": round(sum(float(r["obs"] or 0) for r in rows), 1),
            "visits": {str(int(r["section_id"])): int(r["n"]) for r in vis},
            "moves": [[int(r["from_id"]), int(r["section_id"]), int(r["n"])] for r in mv],
            "occ": {str(int(i)): round(float(occ[i]), 1) for i in _top(occ, 0.05)},
            "pas": {str(int(i)): int(pas[i]) for i in _top(pas, 0.5)}}


def _top(g: np.ndarray, lo: float) -> np.ndarray:
    """Bulutga ketadigan kataklar: qiymati lo dan katta, ko'pi bilan CLOUD_CELLS ta eng kattasi (yozuv hajmi cheklangan)."""
    idx = np.nonzero(g > lo)[0]
    if len(idx) > CLOUD_CELLS:
        idx = idx[np.argsort(g[idx])[-CLOUD_CELLS:]]
    return np.sort(idx)


def plan_public(p: Plan) -> dict:
    """Bulutga (hello) va panelga: reja o'lchami va bo'limlar (metrda)."""
    return {"id": p.id, "name": p.name, "w_m": round(p.w_m, 2), "h_m": round(p.h_m, 2), "cell": CELL,
            "nx": p.nx, "ny": p.ny, "grid": p.grid_key,
            "sections": [{"id": s.id, "name": s.name, "color": s.color,
                          "pts": [[round(float(x), 2), round(float(y), 2)] for x, y in s.poly]} for s in p.sections]}


def has_plans() -> bool:
    return bool(layout().plans)


def status_text(day0: float | None = None) -> str:
    """Telegram /oqim: bugungi bo'lim tashriflari va eng ko'p yo'nalishlar."""
    import html

    lay = layout()
    if not lay.plans:
        return ("🗺 <b>Oqim</b>\n\nDo'kon rejasi hali kiritilmagan. Panelda <b>Oqim → Reja va kameralar</b> bo'limida reja "
                "yuklang, bo'limlarni chizing va kamerani kalibrlang.")
    if day0 is None:
        lt = time.localtime()
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    lines = ["🗺 <b>Bugun bo'limlar</b>"]
    for p in lay.plans.values():
        s = visits_summary(p.id, day0, time.time() + 1)
        if len(lay.plans) > 1:
            lines.append(f"\n<b>{html.escape(p.name)}</b>")
        cams = [c for c in lay.cams.values() if c.plan_id == p.id]
        if not cams:
            lines.append("Kamera kalibrlanmagan.")
            continue
        if not s["total"]:
            lines.append("Hali tashrif yo'q.")
            continue
        for x in s["sections"][:8]:
            lines.append(f"• {html.escape(x['name'])}: <b>{x['visits']}</b> ({round(x['share'] * 100)}%)")
        if s["moves"]:
            lines.append("\n↪️ <b>Ko'p yo'nalishlar</b>")
            for m in s["moves"][:3]:
                lines.append(f"• {html.escape(m['from_name'])} → {html.escape(m['to_name'])}: {m['n']}")
    return "\n".join(lines)
