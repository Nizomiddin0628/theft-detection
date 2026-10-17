"""Kamera kadri ↔ do'kon rejasi (5-bosqich, 2.1.0): pol tekisligi gomografiyasi va linza egriligi.

Kalibrlash: kadrda pol sathidagi kamida 4 nuqta (plitka burchagi, polka oyog'i, eshik ostonasi burchagi) va rejadagi
o'sha joy (metrda). Odam oyog'i (ramka pastining o'rtasi) shu model bilan rejaga o'tkaziladi.

Model: kadr nuqtasi (0..1) → markazlashgan koordinata q (yarim diagonal = 1, tomonlar nisbati saqlanadi) → linza
egriligini to'g'rilash q / (1 − k1·r² − k2·r⁴) (bo'linma modeli: "bochka" ni bitta-ikkita parametr bilan yaxshi
ifodalaydi) → gomografiya H → rejadagi (x, y) metrda (x — o'ngga, y — pastga, reja rasmining chap yuqori burchagidan).

  * Egrilik faqat yetarli nuqtada baholanadi (k1 — ≥ MIN_K1_POINTS, k2 — ≥ MIN_K2_POINTS): har variant uchun "bittasini
    chiqarib" (leave-one-out, LOO) xato hisoblanadi, eng yaxshisidan K_TOL dan ko'p yomon bo'lmagan eng "oddiy" (kichik
    egrilik) model olinadi; egriliksiz modelga nisbatan foyda K_GAIN dan kam bo'lsa — egrilik yo'q. Kam nuqtada
    egrilikni baholash — "egri chiziqni 4 nuqtaga moslash" (o'z nuqtalarida 0 xato, boshqa joyda katta xato).
  * Xatolar (metrda): "qoldiq" — nuqta moslashda ishtirok etgan (4 nuqtada doim 0 — aldaydi); "LOO" — shu nuqtasiz
    moslangan model bilan (≥ MIN_LOO_POINTS nuqtada; halol baho); "tekshiruv" — moslashda ishtirok etmagan tekshiruv
    nuqtalari (sizning bosish xatoingiz ham kiradi). Darvoza esa jonli: odam belgilangan nuqtada turadi, tizim o'lchaydi
    (app/flow_eval.py).
  * Yaroqli soha: kalibrlash nuqtalari qamrovi (qavariq qobiq) VALID_GROW marta kengaytirilgan va kadrda 1 m kamida
    MIN_PX_PER_M piksel bo'lgan joy. Undan tashqarida model ekstrapolyatsiya qiladi (ufqqa yaqin joyda xato o'nlab
    metr) — u yerdagi odamlar rejaga tushirilmaydi.

Hamma narsa toza numpy/OpenCV — baza yo'q (app/ai/flow.py va panel shu funksiyalardan foydalanadi).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

MIN_POINTS = 4
MIN_LOO_POINTS = 6        # LOO baho shundan boshlab (5 nuqtada har "4 tasi" aniq o'tadi — baho beqaror)
MIN_K1_POINTS = 6
MIN_K2_POINTS = 10
K1_GRID = np.linspace(-0.2, 1.2, 29)     # k1 > 0 — "bochka" (keng burchakli CCTV linzasi), k1 < 0 — "yostiq"
K2_GRID = np.linspace(-0.6, 1.5, 15)
K_GAIN = 0.15             # egrilik faqat LOO xatosini shuncha (15%) kamaytirsa olinadi
K_TOL = 0.10              # LOO eng yaxshisidan shuncha (10%) yomonroq bo'lsa ham "oddiyroq" model afzal
VALID_GROW = 1.25         # yaroqli soha: kalibrlash nuqtalari qobig'i shuncha marta kengaytirilgan
MIN_SCALE = 0.2           # egrilik maxraji bundan kichik (kuchli "bochka", juda chet) — yaroqsiz
OUTLIER_M = 1.0           # LOO xatosi bundan katta nuqta — "ehtimol noto'g'ri bosilgan"
MIN_PX_PER_M = 18.0       # kadrda 1 m shundan kam piksel bo'lgan joy (ufqqa yaqin, juda uzoq) — rejaga tushirilmaydi:
                          # u yerda oyoq nuqtasidagi 2–3 piksel xato metrlab xato beradi
MAX_PAIRS = 40


def _scale(aspect: float) -> float:
    return math.hypot(aspect / 2.0, 0.5)


def center(uv, aspect: float) -> np.ndarray:
    """Kadr nuqtasi (0..1) → markazlashgan q (yarim diagonal 1, tomonlar nisbati to'g'ri)."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    s = _scale(aspect)
    q = np.empty_like(uv)
    q[:, 0] = (uv[:, 0] - 0.5) * aspect / s
    q[:, 1] = (uv[:, 1] - 0.5) / s
    return q


def uncenter(q, aspect: float) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64).reshape(-1, 2)
    s = _scale(aspect)
    uv = np.empty_like(q)
    uv[:, 0] = q[:, 0] * s / aspect + 0.5
    uv[:, 1] = q[:, 1] * s + 0.5
    return uv


def undistort(q, k: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """q_u = q / (1 − k1·r² − k2·r⁴). Natija: (to'g'rilangan q, maxraj) — maxraj MIN_SCALE dan kichik joy yaroqsiz."""
    q = np.asarray(q, dtype=np.float64).reshape(-1, 2)
    r2 = (q * q).sum(axis=1)
    d = 1.0 - k[0] * r2 - k[1] * r2 * r2
    ds = np.where(np.abs(d) < 1e-9, 1e-9, d)
    return q / ds[:, None], d


def distort(qu, k: tuple[float, float]) -> np.ndarray:
    """undistort ning teskarisi: radius bo'yicha r / (1 − k1·r² − k2·r⁴) = r_u. k2 = 0 — yopiq formula, aks holda
    Nyuton usuli (yechim topilmasa — NaN)."""
    qu = np.asarray(qu, dtype=np.float64).reshape(-1, 2)
    k1, k2 = float(k[0]), float(k[1])
    if not k1 and not k2:
        return qu.copy()
    ru = np.hypot(qu[:, 0], qu[:, 1])
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        if not k2:
            disc = 1.0 + 4.0 * k1 * ru * ru
            r = np.where(ru > 1e-12, (np.sqrt(disc) - 1.0) / (2.0 * k1 * np.where(ru > 1e-12, ru, 1.0)), 0.0)
            bad = disc < 0
        else:
            # g(r) = r − r_u·(1 − k1 r² − k2 r⁴) = 0;  g'(r) = 1 + r_u·(2 k1 r + 4 k2 r³)
            r = ru / (1.0 + max(k1, 0.0) * ru * ru)
            r = np.where(np.isfinite(r) & (r > 0), r, ru)
            for _ in range(30):
                g = r - ru * (1 - k1 * r * r - k2 * r ** 4)
                dg = 1 + ru * (2 * k1 * r + 4 * k2 * r ** 3)
                dg = np.where(np.abs(dg) < 1e-9, 1e-9, dg)
                r = np.clip(r - g / dg, 0.0, 3.0)
            bad = np.abs(r - ru * (1 - k1 * r * r - k2 * r ** 4)) > 1e-7 * np.maximum(1.0, ru)
        s = np.where(ru > 1e-12, r / np.where(ru > 1e-12, ru, 1.0), 1.0)
    q = qu * s[:, None]
    q[bad | ~np.isfinite(s)] = np.nan
    return q


def apply_h(H: np.ndarray, pts) -> tuple[np.ndarray, np.ndarray]:
    """(nuqtalar, w) — w ning ishorasi ufqning qaysi tomonida ekanini bildiradi."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    hp = np.c_[p, np.ones(len(p))] @ np.asarray(H, dtype=np.float64).T
    w = hp[:, 2]
    ws = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return hp[:, :2] / ws[:, None], w


def _fit_h(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    if len(src) < MIN_POINTS or not np.isfinite(src).all():
        return None
    try:
        H, _ = cv2.findHomography(src.astype(np.float64), dst.astype(np.float64), 0)
    except cv2.error:
        return None
    if H is None or not np.isfinite(H).all() or abs(H[2, 2]) < 1e-12:
        return None
    return H / H[2, 2]


def degenerate(pts) -> bool:
    """Hech bir to'rtligi "yaxshi" emas (uchtasi bir chiziqda yoki hammasi bir joyda) — gomografiya aniqlanmaydi."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if len(pts) < MIN_POINTS:
        return True
    c = pts - pts.mean(axis=0)
    sv = np.linalg.svd(c, compute_uv=False)
    if sv[0] < 1e-9 or sv[-1] / sv[0] < 0.05:
        return True
    n = len(pts)
    span = float(np.ptp(pts, axis=0).max()) or 1.0
    lim = 0.002 * span * span
    for i in range(n):
        for j in range(i + 1, n):
            for kk in range(j + 1, n):
                for m in range(kk + 1, n):
                    quad = pts[[i, j, kk, m]]
                    good = True
                    for a, b, d in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
                        u, v = quad[b] - quad[a], quad[d] - quad[a]
                        if abs(u[0] * v[1] - u[1] * v[0]) < lim:
                            good = False
                            break
                    if good:
                        return False
    return True


MASK_W, MASK_H = 320, 180


@dataclass
class Calib:
    """Bitta kameraning kalibrlashi (moslangan model)."""
    H: np.ndarray                       # q_u (to'g'rilangan) → reja (m)
    k: tuple = (0.0, 0.0)
    aspect: float = 16 / 9
    valid: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))   # yaroqli soha (kadrda, 0..1)
    w_sign: float = 1.0                 # kalibrlash nuqtalaridagi w ishorasi (ufqning "pol" tomoni)
    frame_w: int = 1280
    frame_h: int = 720
    _mask: np.ndarray | None = field(default=None, repr=False)

    def in_valid(self, uv) -> np.ndarray:
        """Yaroqli soha ichidami (rasterlangan niqob — har kadrda tez)."""
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        if len(self.valid) < 3:
            return np.isfinite(uv).all(axis=1)
        if self._mask is None:
            m = np.zeros((MASK_H, MASK_W), dtype=np.uint8)
            cv2.fillPoly(m, [np.round(self.valid * [MASK_W - 1, MASK_H - 1]).astype(np.int32)], 1)
            self._mask = m.astype(bool)
        out = np.zeros(len(uv), dtype=bool)
        fin = np.isfinite(uv).all(axis=1)
        if fin.any():
            u = np.round(uv[fin, 0] * (MASK_W - 1)).astype(np.int64)
            v = np.round(uv[fin, 1] * (MASK_H - 1)).astype(np.int64)
            inside = (u >= 0) & (u < MASK_W) & (v >= 0) & (v < MASK_H)
            res = np.zeros(len(u), dtype=bool)
            res[inside] = self._mask[v[inside], u[inside]]
            out[fin] = res
        return out

    def _fwd(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        qu, d = undistort(center(uv, self.aspect), self.k)
        xy, w = apply_h(self.H, qu)
        return xy, (d > MIN_SCALE) & (np.sign(w) == self.w_sign) & np.isfinite(xy).all(axis=1)

    def to_plan(self, uv, min_px_per_m: float = MIN_PX_PER_M) -> tuple[np.ndarray, np.ndarray]:
        """Kadr nuqtalari (0..1) → reja (m) va yaroqlilik niqobi (yaroqli soha, ufq, aniqlik chegarasi)."""
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        n = len(uv)
        if not n:
            return np.zeros((0, 2)), np.zeros(0, dtype=bool)
        du, dv = 1.0 / max(self.frame_w, 1), 1.0 / max(self.frame_h, 1)
        allp, okp = self._fwd(np.r_[uv, uv + [du, 0.0], uv + [0.0, dv]])
        xy, ok = allp[:n], okp[:n] & okp[n:2 * n] & okp[2 * n:]
        if min_px_per_m:
            jx, jy = allp[n:2 * n] - xy, allp[2 * n:] - xy          # metr / piksel
            m2 = np.abs(jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0])
            with np.errstate(divide="ignore", invalid="ignore"):
                ppm = 1.0 / np.sqrt(m2)
            ok &= np.isfinite(ppm) & (ppm >= min_px_per_m)
        ok &= self.in_valid(uv)
        return xy, ok

    def to_image(self, xy) -> tuple[np.ndarray, np.ndarray]:
        """Reja (m) → kadr (0..1) va "ufqning pol tomonida"mi (yaroqli soha tekshirilmaydi)."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        if not len(xy):
            return np.zeros((0, 2)), np.zeros(0, dtype=bool)
        try:
            Hi = np.linalg.inv(self.H)
        except np.linalg.LinAlgError:
            return np.full((len(xy), 2), np.nan), np.zeros(len(xy), dtype=bool)
        qu, w = apply_h(Hi, xy)
        q = distort(qu, self.k)
        uv = uncenter(q, self.aspect)
        _, d = undistort(q, self.k)
        # H⁻¹ ning w si H nikiga teskari proporsional — ishorasi bir xil: "pol tomonida"
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(uv).all(axis=1) & (d > MIN_SCALE) & (np.sign(w) == self.w_sign)
        return uv, ok

    def px_per_m(self, xy) -> np.ndarray:
        """Rejadagi nuqta atrofida kadr aniqligi (piksel / metr, geometrik o'rtacha) — bir nechta kamera bir joyni
        ko'rsa, eng aniq ko'radigani shu joyning "egasi"."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        d = 0.25
        n = len(xy)
        allp, ok = self.to_image(np.r_[xy, xy + [d, 0.0], xy + [0.0, d]])
        a, bx, by = allp[:n], allp[n:2 * n], allp[2 * n:]
        sz = np.array([self.frame_w, self.frame_h], dtype=np.float64)
        jx, jy = (bx - a) * sz / d, (by - a) * sz / d
        with np.errstate(invalid="ignore"):
            out = np.sqrt(np.abs(jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]))
        good = ok[:n] & ok[n:2 * n] & ok[2 * n:] & np.isfinite(out)
        return np.where(good, out, 0.0)

    def to_json(self) -> dict:
        return {"H": [[float(v) for v in r] for r in self.H], "k": [float(self.k[0]), float(self.k[1])],
                "aspect": float(self.aspect),
                "valid": [[round(float(x), 5), round(float(y), 5)] for x, y in self.valid], "w_sign": float(self.w_sign),
                "frame_w": int(self.frame_w), "frame_h": int(self.frame_h)}

    @classmethod
    def from_json(cls, d) -> "Calib | None":
        try:
            H = np.asarray(d["H"], dtype=np.float64)
            if H.shape != (3, 3) or not np.isfinite(H).all():
                return None
            k = d.get("k") or [0.0, 0.0]
            return cls(H=H, k=(float(k[0]), float(k[1])), aspect=float(d.get("aspect") or 16 / 9),
                       valid=np.asarray(d.get("valid") or [], dtype=np.float64).reshape(-1, 2),
                       w_sign=float(d.get("w_sign") or 1.0), frame_w=int(d.get("frame_w") or 1280),
                       frame_h=int(d.get("frame_h") or 720))
        except (KeyError, TypeError, ValueError, IndexError):
            return None


def valid_region(uv, grow: float = VALID_GROW) -> np.ndarray:
    """Kalibrlash nuqtalari qavariq qobig'i, markazdan grow marta kengaytirilgan va kadr bilan kesilgan (0..1)."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    if len(uv) < 3:
        return np.zeros((0, 2))
    hull = cv2.convexHull(uv.astype(np.float32)).reshape(-1, 2).astype(np.float64)
    c = hull.mean(axis=0)
    return np.clip(c + (hull - c) * grow, 0.0, 1.0)


def _model(q: np.ndarray, plan: np.ndarray, k) -> np.ndarray | None:
    qu, d = undistort(q, k)
    if (d <= MIN_SCALE).any():
        return None
    return _fit_h(qu, plan)


def _pred(H: np.ndarray, q: np.ndarray, k) -> np.ndarray:
    qu, _ = undistort(q, k)
    return apply_h(H, qu)[0]


def _loo(q: np.ndarray, plan: np.ndarray, k) -> np.ndarray | None:
    n = len(q)
    if n < MIN_LOO_POINTS:
        return None
    out = np.zeros(n)
    for i in range(n):
        m = np.ones(n, dtype=bool)
        m[i] = False
        H = _model(q[m], plan[m], k)
        if H is None:
            return None
        out[i] = float(np.hypot(*(_pred(H, q[i:i + 1], k)[0] - plan[i])))
    return out


def _rms(v) -> float:
    v = np.asarray(v, dtype=np.float64)
    return float(np.sqrt(np.mean(v * v))) if len(v) else 0.0


def choose_k(q: np.ndarray, plan: np.ndarray) -> tuple[float, float]:
    """Egrilikni LOO xatosi bo'yicha tanlash, "oddiyroq model afzal" qoidasi bilan (fayl boshidagi izohga qarang)."""
    n = len(q)
    if n < MIN_K1_POINTS:
        return (0.0, 0.0)
    cands = [(float(a), 0.0) for a in K1_GRID]
    if n >= MIN_K2_POINTS:
        cands += [(float(a), float(b)) for a in K1_GRID[::2] for b in K2_GRID if abs(b) > 1e-9]
    scores = []
    for k in cands:
        lo = _loo(q, plan, k)
        if lo is not None and np.isfinite(lo).all():
            scores.append((_rms(lo), k))
    base = _loo(q, plan, (0.0, 0.0))
    base_r = _rms(base) if base is not None and np.isfinite(base).all() else None
    if not scores:
        return (0.0, 0.0)
    best_r = min(r for r, _ in scores)
    # "Oddiylik": avval bitta parametr (k2 = 0), keyin |k1| + |k2| kichigi
    ok = sorted(((1 if abs(k[1]) > 1e-9 else 0, abs(k[0]) + abs(k[1]), k, r) for r, k in scores
                 if r <= best_r * (1 + K_TOL)), key=lambda x: (x[0], x[1]))
    _, _, k, r = ok[0]
    if base_r is not None and r >= base_r * (1 - K_GAIN):
        return (0.0, 0.0)
    return (round(k[0], 4), round(k[1], 4))


SUSPECT_MIN_RMS = 0.25     # qoldiq shundan katta bo'lsa, "noto'g'ri bosilgan" nuqta qidiriladi
SUSPECT_GAIN = 0.5         # ... usiz qolganlarning qoldig'i shuncha marta kamaysa — o'sha nuqta gumonli


def on_line(pts: np.ndarray, tol: float = 0.2) -> int:
    """Bitta to'g'ri chiziqqa (tol m ichida) tushadigan eng ko'p nuqtalar soni."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    best = 0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = pts[j] - pts[i]
            L = float(np.hypot(*d))
            if L < 1e-6:
                continue
            dist = np.abs((pts[:, 0] - pts[i, 0]) * d[1] - (pts[:, 1] - pts[i, 1]) * d[0]) / L
            best = max(best, int((dist <= tol).sum()))
    return best


def suspect(q: np.ndarray, plan: np.ndarray, k) -> int | None:
    """Bitta noto'g'ri bosilgan nuqtani topish: qaysi nuqtasiz qolgan nuqtalar eng yaxshi mos keladi. LOO xatosi bunga
    yaramaydi — yomon nuqta modelni buzib, boshqa (ayniqsa uzoqdagi) nuqtalarning LOO xatosini ham oshiradi."""
    n = len(q)
    if n < MIN_LOO_POINTS:
        return None
    H = _model(q, plan, k)
    if H is None:
        return None
    base = _rms(np.hypot(*(_pred(H, q, k) - plan).T))
    if base < SUSPECT_MIN_RMS:
        return None
    best = None
    for i in range(n):
        m = np.ones(n, dtype=bool)
        m[i] = False
        Hi = _model(q[m], plan[m], k)
        if Hi is None:
            continue
        r = _rms(np.hypot(*(_pred(Hi, q[m], k) - plan[m]).T))
        if best is None or r < best[0]:
            best = (r, i)
    if best is not None and best[0] < base * SUSPECT_GAIN:
        return best[1]
    return None


def fit(pairs: list[dict], aspect: float, frame_w: int = 1280, frame_h: int = 720) -> dict:
    """pairs: [{"img": [u, v] (0..1), "plan": [x, y] (m), "check": bool}]. Natija: {"ok", "calib" (Calib | None),
    "k", "points": [{..., "resid", "loo", "check_err", "pred"}], "rms", "loo_rms", "loo_max", "check_rms",
    "check_max", "warnings": [...]}"""
    warn: list[str] = []
    pairs = list(pairs)[:MAX_PAIRS]
    cal = [p for p in pairs if not p.get("check")]
    chk = [p for p in pairs if p.get("check")]
    res: dict = {"ok": False, "calib": None, "k": [0.0, 0.0], "points": [dict(p, resid=None, loo=None) for p in pairs],
                 "rms": None, "loo_rms": None, "loo_max": None, "check_rms": None, "check_max": None,
                 "warnings": warn, "n": len(cal), "n_check": len(chk), "hull_area": None}
    if len(cal) < MIN_POINTS:
        warn.append(f"Kamida {MIN_POINTS} ta kalibrlash nuqtasi kerak (hozir {len(cal)} ta)")
        return res
    img = np.array([p["img"] for p in cal], dtype=np.float64)
    plan = np.array([p["plan"] for p in cal], dtype=np.float64)
    if degenerate(img) or degenerate(plan):
        warn.append("Nuqtalar bir chiziqda yoki bir joyda — kadrning turli joylaridan (iloji boricha keng) tanlang")
        return res
    q = center(img, aspect)
    k = choose_k(q, plan)
    H = _model(q, plan, k)
    if H is None:
        warn.append("Gomografiyani hisoblab bo'lmadi — nuqtalarni tekshiring")
        return res
    pred = _pred(H, q, k)
    resid = np.hypot(*(pred - plan).T)
    loo = _loo(q, plan, k)
    _, w = apply_h(H, undistort(q, k)[0])
    w_sign = float(np.sign(np.median(w))) or 1.0
    if (np.sign(w) != w_sign).any():
        warn.append("Ba'zi nuqtalar ufqning narigi tomonida chiqdi — nuqtalar noto'g'ri juftlangan bo'lishi mumkin")
    all_img = np.array([p["img"] for p in pairs], dtype=np.float64)
    calib = Calib(H=H, k=k, aspect=aspect, valid=valid_region(all_img), w_sign=w_sign, frame_w=frame_w, frame_h=frame_h)
    pts, check_err, ci = [], [], 0
    for p in pairs:
        if p.get("check"):
            xy, _ = calib._fwd(np.asarray([p["img"]], dtype=np.float64))
            fin = bool(np.isfinite(xy).all())
            e = float(np.hypot(*(xy[0] - np.asarray(p["plan"], dtype=np.float64)))) if fin else None
            if e is not None:
                check_err.append(e)
            pts.append(dict(p, resid=None, loo=None, check_err=round(e, 3) if e is not None else None,
                            pred=[round(float(v), 3) for v in xy[0]] if fin else None))
        else:
            lo = float(loo[ci]) if loo is not None and np.isfinite(loo[ci]) else None
            pts.append(dict(p, resid=round(float(resid[ci]), 3), loo=round(lo, 3) if lo is not None else None,
                            pred=[round(float(v), 3) for v in pred[ci]]))
            ci += 1
    res.update(ok=True, calib=calib, k=[k[0], k[1]], points=pts, rms=round(_rms(resid), 3))
    if loo is not None and np.isfinite(loo).all():
        res["loo_rms"], res["loo_max"] = round(_rms(loo), 3), round(float(loo.max()), 3)
        bad = [i + 1 for i, v in enumerate(loo) if v > OUTLIER_M]
        if bad:
            warn.append("LOO xatosi 1 m dan katta nuqta(lar): " + ", ".join(f"#{i}" for i in bad) +
                        " — kadrda yoki rejada noto'g'ri bosilgan bo'lishi mumkin")
    elif len(cal) < MIN_LOO_POINTS:
        warn.append(f"{len(cal)} nuqtada xatoni baholab bo'lmaydi — kamida {MIN_LOO_POINTS} ta (yaxshisi 8–12 ta) nuqta "
                    "qo'ying yoki tekshiruv nuqtasi qo'shing")
    sus = suspect(q, plan, k)
    res["suspect"] = sus
    if sus is not None:
        idx = [i for i, p in enumerate(pairs) if not p.get("check")][sus]
        pts[idx]["suspect"] = True
        warn.insert(0, f"#{idx + 1} nuqta ehtimol noto'g'ri bosilgan (usiz qolgan nuqtalar ancha yaxshi mos keladi) — "
                       "kadrda va rejada tekshiring yoki o'chiring")
    if check_err:
        res["check_rms"], res["check_max"] = round(_rms(check_err), 3), round(max(check_err), 3)
    nl = on_line(plan)
    if nl >= 3 and nl >= len(plan) / 2:
        warn.append(f"{nl} ta nuqta rejada bir chiziqda (masalan, hammasi devor tagida) — model bu chiziqdan uzoqda "
                    "noaniq bo'ladi; xonaning o'rtasidan va kameraga yaqin joydan ham nuqta qo'ying")
    hull = cv2.convexHull(img.astype(np.float32)).reshape(-1, 2)
    area = float(cv2.contourArea(hull.astype(np.float32)))
    res["hull_area"] = round(area, 3)
    if area < 0.12:
        warn.append(f"Nuqtalar kadrning kichik qismida (qamrov {area * 100:.0f}%) — undan tashqarida odamlar rejaga "
                    "tushirilmaydi; chetroqdan ham nuqta qo'ying")
    return res


def grid_lines(calib: Calib, w_m: float, h_m: float, step: float = 1.0, samples: int = 80) -> list[list[list[float]]]:
    """Rejaning har `step` metrlik to'ri kadr ustida (tekshirish uchun: chiziqlar pol plitkalariga mos tushishi kerak).
    Faqat yaroqli sohadagi qismlar; natija — kadr nuqtalari (0..1) ro'yxatlari."""
    out = []
    xs = np.arange(0.0, w_m + 1e-6, step)
    ys = np.arange(0.0, h_m + 1e-6, step)
    lines = [np.c_[np.full(samples, x), np.linspace(0, h_m, samples)] for x in xs] + \
            [np.c_[np.linspace(0, w_m, samples), np.full(samples, y)] for y in ys]
    for ln in lines[:400]:
        uv, ok = calib.to_image(ln)
        ok &= calib.in_valid(uv)
        seg: list = []
        for (a, b), good in zip(uv, ok):
            if good:
                seg.append([round(float(a), 4), round(float(b), 4)])
            elif seg:
                if len(seg) > 1:
                    out.append(seg)
                seg = []
        if len(seg) > 1:
            out.append(seg)
    return out


def coverage_polygon(calib: Calib, samples: int = 24) -> np.ndarray:
    """Yaroqli soha chegarasi rejada (m) — egrilik bor, shuning uchun chegara zich namunalanadi."""
    v = calib.valid
    if len(v) < 3:
        return np.zeros((0, 2))
    c = v.mean(axis=0)
    pts = []
    for i in range(len(v)):
        a, b = v[i], v[(i + 1) % len(v)]
        for s in np.linspace(0, 1, samples, endpoint=False):
            pts.append(a + (b - a) * s)
    pts = c + (np.asarray(pts) - c) * 0.995
    xy, ok = calib.to_plan(pts, min_px_per_m=0)
    return xy[ok]
