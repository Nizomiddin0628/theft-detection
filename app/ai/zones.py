"""Kamera zonalari — kadrning qismlari va har birining o'z qoidasi.

Zona — kamera rasmi ustiga chizilgan ko'pburchak (nuqtalar 0..1 oralig'ida, rasm o'lchamiga bog'liq emas).

  polka      Mahsulot turgan joy. Qo'l shu yerga kirsa — "mahsulot olinyapti": kamera faol rejimga o'tadi,
             qo'l atrofida buyum qidiriladi; keyin qo'l yashirish zonasiga borsa +10 ball.
  kassa      To'lov joyi. Xaridor bu yerda sumkasiga soladi, pul oladi: "sumkaga solish" va "kadrdan
             yashirish" signallari o'chadi, signal chegarasi +15.
  eshik      Kirish/chiqish. Shubhali odam (signal bo'lgan yoki mahsulotni yashirgan) shu yerga kelsa —
             "Chiqish tomon ketmoqda" signali (qo'riqchi to'xtatib qolishga ulguradi).
  xodim      Xodimlar joyi (peshtaxta orti, ombor eshigi). Shu yerda 2 s turgan odam xodim deb belgilanadi va
             kadrdan chiqquncha unga signal berilmaydi.
  etiborsiz  Televizor, oyna, ko'cha ko'rinadigan deraza: bu yerdagi odamlar va harakat tahlil qilinmaydi.

Odamning joyi oyog'i (ramka pastining o'rtasi) bo'yicha, polka esa qo'l (bilak) bo'yicha aniqlanadi.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import db

ZONE_KINDS = {
    "polka": {"label": "Polka / mahsulot", "color": "#2563eb",
              "hint": "Qo'l shu yerga kirsa — mahsulot olinyapti deb hisoblanadi (buyum qidiriladi, +10 ball)."},
    "kassa": {"label": "Kassa", "color": "#16a34a",
              "hint": "Sumkaga solish va kadrdan yashirish signallari o'chadi, chegara +15."},
    "eshik": {"label": "Kirish / chiqish", "color": "#d97706",
              "hint": "Shubhali odam shu yerga kelsa — 'Chiqish tomon ketmoqda' signali."},
    "xodim": {"label": "Xodimlar joyi", "color": "#7c3aed",
              "hint": "Shu yerda 2 s turgan odam xodim — unga signal berilmaydi."},
    "etiborsiz": {"label": "E'tiborsiz", "color": "#64748b",
                  "hint": "Televizor, oyna, deraza: bu yerdagi odam va harakat tahlil qilinmaydi."},
}
# Zona sezgirligi: signal chegarasiga qo'shiladi (manfiy — sezgirroq)
SENSITIVITY = {"": 0, "yuqori": -15, "past": 15}
SENS_LABELS = {"": "Odatiy", "yuqori": "Yuqori (chegara −15)", "past": "Past (chegara +15)"}
KIND_THRESHOLD = {"kassa": 15}
CACHE_SEC = 3.0


@dataclass
class Zone:
    id: int
    name: str
    kind: str
    pts: np.ndarray            # (N, 2), 0..1
    sens: str = ""
    _poly: dict = field(default_factory=dict, repr=False)

    def poly(self, w: int, h: int) -> np.ndarray:
        key = (w, h)
        p = self._poly.get(key)
        if p is None:
            p = (self.pts * np.array([w, h], dtype=np.float32)).astype(np.float32).reshape(-1, 1, 2)
            self._poly[key] = p
        return p

    def contains(self, pt, w: int, h: int) -> bool:
        x, y = float(pt[0]), float(pt[1])
        if not (np.isfinite(x) and np.isfinite(y)):
            return False
        return cv2.pointPolygonTest(self.poly(w, h), (x, y), False) >= 0

    @property
    def threshold_delta(self) -> int:
        return KIND_THRESHOLD.get(self.kind, 0) + SENSITIVITY.get(self.sens, 0)


class ZoneSet:
    def __init__(self, zones: list[Zone] | None = None):
        self.zones = zones or []
        self._mask: dict = {}

    def __bool__(self) -> bool:
        return bool(self.zones)

    def of(self, kind: str) -> list[Zone]:
        return [z for z in self.zones if z.kind == kind]

    def at(self, kind: str, pt, w: int, h: int) -> Zone | None:
        for z in self.zones:
            if z.kind == kind and z.contains(pt, w, h):
                return z
        return None

    def motion_mask(self, sw: int, sh: int) -> np.ndarray | None:
        """Harakat tekshiruvi uchun niqob (True — hisobga olinadi): e'tiborsiz zonalar chiqarib tashlanadi."""
        ign = self.of("etiborsiz")
        if not ign:
            return None
        key = (sw, sh)
        m = self._mask.get(key)
        if m is None:
            img = np.ones((sh, sw), dtype=np.uint8)
            for z in ign:
                cv2.fillPoly(img, [np.round(z.pts * np.array([sw, sh])).astype(np.int32)], 0)
            m = img.astype(bool)
            self._mask[key] = m
        return m


EMPTY = ZoneSet()
_cache: dict[int, tuple[float, ZoneSet]] = {}
_lock = threading.Lock()


def parse_points(raw) -> np.ndarray | None:
    try:
        pts = np.asarray(json.loads(raw) if isinstance(raw, str) else raw, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3 or not np.isfinite(pts).all():
        return None
    return pts.clip(0.0, 1.0)


def load(source_id: int | None) -> ZoneSet:
    """Manba zonalari (3 s kesh; o'zgartirilganda invalidate())."""
    if not source_id:
        return EMPTY
    now = time.time()
    with _lock:
        hit = _cache.get(int(source_id))
        if hit and now - hit[0] < CACHE_SEC:
            return hit[1]
    zs = []
    for r in db.rows("SELECT * FROM zones WHERE source_id=? AND enabled=1 ORDER BY id", (int(source_id),)):
        pts = parse_points(r["points"])
        if pts is not None and r["kind"] in ZONE_KINDS:
            zs.append(Zone(int(r["id"]), r["name"] or ZONE_KINDS[r["kind"]]["label"], r["kind"], pts,
                           r["sens"] or ""))
    res = ZoneSet(zs)
    with _lock:
        _cache[int(source_id)] = (now, res)
    return res


def invalidate(source_id: int | None = None) -> None:
    with _lock:
        if source_id is None:
            _cache.clear()
        else:
            _cache.pop(int(source_id), None)


def draw(img: np.ndarray, zs: ZoneSet, scale: float = 1.0) -> None:
    """Jonli ko'rinishda zonalarni ingichka chiziq va nom bilan ko'rsatish."""
    h, w = img.shape[:2]
    for z in zs.zones:
        col = ZONE_KINDS[z.kind]["color"].lstrip("#")
        bgr = (int(col[4:6], 16), int(col[2:4], 16), int(col[0:2], 16))
        p = np.round(z.pts * np.array([w, h])).astype(np.int32)
        cv2.polylines(img, [p], True, bgr, 1, cv2.LINE_AA)
        x, y = int(p[:, 0].min()), int(p[:, 1].min())
        label = z.name.encode("ascii", "ignore").decode() or z.kind
        cv2.putText(img, label, (x + 4, max(12, y + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, bgr, 1, cv2.LINE_AA)
