"""Sintetik skelet ketma-ketliklari (2.2.0 xavfsizlik sinovlari): tik turish, yiqilish, o'tirib qolish, cho'kkalash,
egilish, tiz cho'kish, sekin yotish, uzoqlashish. COCO 17 nuqta, tana birligida (bo'y = 1, oyoq ostida y = 0, y yuqoriga),
keyin kadrga (piksel) o'tkaziladi. Bu mantiq sinovi: haqiqiy detektor shovqini va to'silishlar — ambianic videolari va
do'kondagi darvozada."""
from __future__ import annotations

import math

import numpy as np

# Tik turgan odam (bo'y = 1): burun, ko'zlar, quloqlar, yelkalar, tirsaklar, bilaklar, sonlar, tizzalar, to'piqlar
STAND = np.array([
    (0.00, 0.93), (-0.02, 0.95), (0.02, 0.95), (-0.05, 0.94), (0.05, 0.94),
    (-0.11, 0.81), (0.11, 0.81), (-0.14, 0.63), (0.14, 0.63), (-0.15, 0.47), (0.15, 0.47),
    (-0.08, 0.52), (0.08, 0.52), (-0.08, 0.28), (0.08, 0.28), (-0.08, 0.04), (0.08, 0.04)], dtype=np.float64)
SQUAT = np.array([
    (0.05, 0.60), (0.03, 0.62), (0.07, 0.62), (0.00, 0.61), (0.10, 0.61),
    (-0.08, 0.48), (0.14, 0.48), (0.00, 0.34), (0.22, 0.34), (0.12, 0.26), (0.28, 0.26),
    (-0.05, 0.20), (0.09, 0.20), (0.10, 0.30), (0.24, 0.30), (-0.02, 0.04), (0.12, 0.04)], dtype=np.float64)
SIT_FLOOR = np.array([
    (0.02, 0.52), (0.00, 0.54), (0.04, 0.54), (-0.03, 0.53), (0.07, 0.53),
    (-0.10, 0.40), (0.12, 0.40), (-0.14, 0.24), (0.16, 0.24), (-0.16, 0.08), (0.18, 0.08),
    (-0.06, 0.07), (0.08, 0.07), (0.20, 0.16), (0.32, 0.16), (0.45, 0.03), (0.55, 0.03)], dtype=np.float64)
BEND = np.array([
    (0.42, 0.47), (0.43, 0.49), (0.43, 0.49), (0.40, 0.50), (0.40, 0.50),
    (0.30, 0.55), (0.30, 0.45), (0.33, 0.35), (0.33, 0.30), (0.33, 0.14), (0.33, 0.10),
    (0.00, 0.56), (0.00, 0.48), (-0.04, 0.28), (0.04, 0.28), (-0.08, 0.04), (0.08, 0.04)], dtype=np.float64)
KNEEL = np.array([
    (0.02, 0.68), (0.00, 0.70), (0.04, 0.70), (-0.03, 0.69), (0.07, 0.69),
    (-0.10, 0.56), (0.12, 0.56), (-0.13, 0.40), (0.15, 0.40), (-0.14, 0.26), (0.16, 0.26),
    (-0.07, 0.29), (0.09, 0.29), (-0.05, 0.03), (0.07, 0.03), (-0.30, 0.03), (-0.20, 0.03)], dtype=np.float64)


def rotated(theta_deg: float, base: np.ndarray = STAND) -> np.ndarray:
    """Butun tanani oyoq atrofida θ ga oldinga (+x) qiyshaytirish; 90° — yonboshlab yotgan. Pol ostiga tushmaydi."""
    t = math.radians(theta_deg)
    x, y = base[:, 0], base[:, 1]
    xr = x * math.cos(t) + y * math.sin(t)
    yr = -x * math.sin(t) + y * math.cos(t)
    yr = yr - min(0.0, yr.min() - 0.03)
    return np.stack([xr, yr], 1)


LYING = rotated(90)


def lerp(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    u = min(max(u, 0.0), 1.0)
    return a * (1 - u) + b * u


class Pp:
    def __init__(self, tid: int, box, kpts, score: float = 0.9):
        self.track_id = tid
        self.box = np.asarray(box, dtype=np.float32)
        self.kpts = np.asarray(kpts, dtype=np.float32)
        self.score = score


def to_image(body: np.ndarray, cx: float, floor_y: float, hpx: float, conf: float = 0.9, rng=None,
             noise: float = 0.0, frame_h: int = 720) -> tuple[np.ndarray, np.ndarray]:
    """Tana nuqtalari → kadr: (17, 3) skelet va ramka (bosh tepasi ~0,07 bo'y yuqorida, chetlar bilan)."""
    pts = body.copy()
    if rng is not None and noise > 0:
        pts = pts + rng.normal(0, noise, pts.shape)
    X = cx + pts[:, 0] * hpx
    Y = floor_y - pts[:, 1] * hpx
    k = np.zeros((17, 3), dtype=np.float32)
    k[:, 0], k[:, 1], k[:, 2] = X, Y, conf
    head = np.array([X[:5].mean(), Y[:5].min() - 0.06 * hpx])
    xs = np.concatenate([X, [head[0]]])
    ys = np.concatenate([Y, [head[1]]])
    m = 0.03 * hpx
    box = np.array([xs.min() - m, ys.min() - m, xs.max() + m, min(ys.max() + m, frame_h - 1)], dtype=np.float32)
    return k, box


def sequence(segments: list[tuple], fps: float = 8.0, tid: int = 1, cx: float = 640.0, floor_y: float = 600.0,
             hpx: float = 400.0, t0: float = 0.0, noise: float = 0.0, seed: int = 0, frame_h: int = 720,
             scale_fn=None) -> list[tuple[float, list]]:
    """segments: [(davomiylik, fn(u) -> tana nuqtalari | None)] — None: odam ko'rinmaydi (detektor topmadi).
    Natija: [(t, [Pp])] har AI qadami uchun."""
    rng = np.random.default_rng(seed)
    out = []
    t = t0
    dt = 1.0 / fps
    for dur, fn in segments:
        n = max(1, int(round(dur * fps)))
        for i in range(n):
            u = i / max(n - 1, 1)
            body = fn(u)
            hp = hpx * (scale_fn(t - t0) if scale_fn else 1.0)
            if body is None:
                out.append((t, []))
            else:
                k, b = to_image(body, cx, floor_y, hp, rng=rng, noise=noise, frame_h=frame_h)
                out.append((t, [Pp(tid, b, k)]))
            t += dt
    return out


def const(body):
    return lambda u: body


def rot(a, b):
    return lambda u: rotated(a + (b - a) * u)


def morph(a, b):
    return lambda u: lerp(a, b, u)


def gone():
    return lambda u: None


SCENARIOS = {
    # nom: (bo'laklar, kutilgan: "fall" | "lying" | None)
    "oldinga yiqilish": ([(3, const(STAND)), (0.8, rot(0, 90)), (6, const(LYING))], "fall"),
    "yiqilib, detektor yo'qotdi": ([(3, const(STAND)), (0.8, rot(0, 90)), (0.3, const(LYING)), (5, gone())], "fall"),
    "o'tirib qolib yiqilish (cho'kib, keyin yotdi)": ([(2, const(STAND)), (1.0, morph(STAND, SIT_FLOOR)),
                                                     (0.5, const(SIT_FLOOR)), (0.8, morph(SIT_FLOOR, LYING)),
                                                     (6, const(LYING))], "fall"),
    "yiqilib, darhol turdi": ([(3, const(STAND)), (0.8, rot(0, 90)), (0.6, const(LYING)), (0.8, rot(90, 0)),
                               (5, const(STAND))], None),
    "cho'kkalash (pastki polka)": ([(2, const(STAND)), (0.8, morph(STAND, SQUAT)), (8, const(SQUAT)),
                                    (0.8, morph(SQUAT, STAND)), (2, const(STAND))], None),
    "egilib poldan olish": ([(2, const(STAND)), (0.8, morph(STAND, BEND)), (4, const(BEND)),
                             (0.8, morph(BEND, STAND)), (2, const(STAND))], None),
    "tiz cho'kish (tik gavda)": ([(2, const(STAND)), (1.0, morph(STAND, KNEEL)), (6, const(KNEEL)),
                                  (1.0, morph(KNEEL, STAND)), (2, const(STAND))], None),
    "polga sekin o'tirish": ([(2, const(STAND)), (2.5, morph(STAND, SIT_FLOOR)), (10, const(SIT_FLOOR))], None),
    "sekin yotib qolish (≥ 20 s)": ([(2, const(STAND)), (2.5, morph(STAND, SIT_FLOOR)), (4, const(SIT_FLOOR)),
                                     (3, morph(SIT_FLOOR, LYING)), (26, const(LYING))], "lying"),
    "boshidan yotgan odam (≥ 20 s)": ([(26, const(LYING))], "lying"),
    "qisqa yotish (10 s), keyin turdi": ([(2, const(STAND)), (2.5, morph(STAND, SIT_FLOOR)), (3, morph(SIT_FLOOR, LYING)),
                                         (10, const(LYING)), (3, morph(LYING, SIT_FLOOR)), (2, morph(SIT_FLOOR, STAND)),
                                         (3, const(STAND))], None),
}


# ----------------------------------------------------------------------------- janjal (2.3.0)
def two_people(fn_a, fn_b, dur: float, fps: float = 8.0, xa: float = 520.0, xb: float = 760.0, hpx: float = 400.0,
               floor_y: float = 640.0, noise: float = 0.0, seed: int = 0, move_a=None, move_b=None) -> list:
    """Ikki odam: fn_x(t) -> tana nuqtalari (bo'y birligida, x — o'ng tomon ikkinchi odamga qarab). B odam ko'zgu
    aksida (A ga qaragan). move_x(t) -> markaz siljishi (piksel)."""
    rng = np.random.default_rng(seed)
    out = []
    n = int(round(dur * fps))
    for i in range(n):
        t = i / fps
        ps = []
        for tid, fn, x0, mv, mirror in ((1, fn_a, xa, move_a, False), (2, fn_b, xb, move_b, True)):
            body = fn(t).copy()
            if mirror:
                body[:, 0] = -body[:, 0]
            cx = x0 + (mv(t) if mv else 0.0)
            k, b = to_image(body, cx, floor_y, hpx, rng=rng, noise=noise)
            ps.append(Pp(tid, b, k))
        out.append((t, ps))
    return out


def punching(freq: float = 2.0, reach: float = 0.55):
    """O'ng qo'l bilan oldinga (+x) zarba: bilak yelkadan ikkinchi odamning boshi tomon va qaytib."""
    def fn(t):
        b = STAND.copy()
        ph = 0.5 - 0.5 * np.cos(2 * np.pi * freq * t)             # 0..1..0
        b[10] = (0.15 + reach * ph, 0.55 + 0.30 * ph)              # o'ng bilak
        b[8] = (0.14 + 0.5 * reach * ph, 0.63 + 0.15 * ph)         # o'ng tirsak
        return b
    return fn


def gesturing(freq: float = 1.0, amp: float = 0.1):
    """Gaplashib qo'l silkitish: bilak kichik doira bo'ylab."""
    def fn(t):
        b = STAND.copy()
        a = 2 * np.pi * freq * t
        b[10] = (0.22 + amp * np.cos(a), 0.62 + amp * np.sin(a))
        b[8] = (0.16, 0.66)
        return b
    return fn


def handshake():
    def fn(t):
        b = STAND.copy()
        ext = min(1.0, t / 1.0)
        b[10] = (0.15 + 0.30 * ext, 0.55 + 0.03 * np.sin(2 * np.pi * 2 * t) * (t > 1.0))
        b[8] = (0.14 + 0.15 * ext, 0.62)
        return b
    return fn


def standing(t):
    return STAND


def people(specs: list[tuple], dur: float, fps: float = 8.0, hpx: float = 400.0, floor_y: float = 640.0,
           noise: float = 0.0, seed: int = 0) -> list:
    """Bir necha odam: specs = [(trek, fn(t), markaz x, ko'zgu (chapga qaragan), move(t) | None)]."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(int(round(dur * fps))):
        t = i / fps
        ps = []
        for tid, fn, x0, mirror, mv in specs:
            body = fn(t).copy()
            if mirror:
                body[:, 0] = -body[:, 0]
            k, b = to_image(body, x0 + (mv(t) if mv else 0.0), floor_y, hpx, rng=rng, noise=noise)
            ps.append(Pp(tid, b, k))
        out.append((t, ps))
    return out


def burst(fn, t0: float, t1: float):
    """fn faqat [t0, t1) oralig'ida, qolgan vaqt — tik turish."""
    return lambda t: fn(t) if t0 <= t < t1 else STAND
