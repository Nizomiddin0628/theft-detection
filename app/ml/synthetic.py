"""Sun'iy (sintetik) skelet ketma-ketliklari generatori.

Haqiqiy o'g'irlik videolari bo'lmaganda boshlang'ich modelni o'qitish va tizimni sinash uchun
ishlatiladi. Kinematik model: tana o'lchamlari, kamera burchagi (old/yon/qiya), yurish, shovqin
va nuqtalarning yo'qolishi tasodifiy tanlanadi. Xatti-harakatlar shablonlari:

  Shubhali (1): polkadan olib cho'ntakka / shim beliga / ko'ylak ichiga / sumkaga solish,
                qo'lni kadrdan pastga yashirish (stol oldida, faqat yuqori tana ko'rinadi)
  Oddiy (0):    turish, yurish, polkaga qo'l uzatib qaytarish, buyumni ko'rib joyiga qo'yish,
                telefonda gaplashish, boshni qashish, qo'l qovushtirish, qo'llar cho'ntakda turishi,
                telefonga qarash, savatga solish, belni qisqa tegib o'tish
"""
from __future__ import annotations

import math

import numpy as np

FPS = 15.0
W, H = 1280, 720

POSITIVE = ["pocket", "waistband", "shirt", "bag", "offscreen"]
NEGATIVE = ["idle", "walk", "reach_return", "inspect_putback", "phone_call", "head_touch",
            "arms_crossed", "hands_in_pockets", "look_phone", "basket", "brief_touch"]


def _smooth(a: float) -> float:
    a = min(max(a, 0.0), 1.0)
    return a * a * (3 - 2 * a)


def _elbow(S: np.ndarray, Wr: np.ndarray, u: float, f: float, outward: float) -> tuple[np.ndarray, np.ndarray]:
    """Yelka S va bilak nishoni Wr bo'yicha tirsak va (cheklangan) bilak holati."""
    d_vec = Wr - S
    d = float(np.linalg.norm(d_vec))
    reach = (u + f) * 0.999
    if d > reach:
        Wr = S + d_vec / d * reach
        d = reach
    d = max(d, abs(u - f) + 1e-3)
    a = (u * u - f * f + d * d) / (2 * d)
    h = math.sqrt(max(u * u - a * a, 0.0))
    dirv = (Wr - S) / d
    P = S + a * dirv
    perp = np.array([-dirv[1], dirv[0]])
    E1, E2 = P + h * perp, P - h * perp
    E = E1 if (E1[0] - S[0]) * outward >= (E2[0] - S[0]) * outward else E2
    return E, Wr


class Body:
    def __init__(self, rng: np.random.Generator, upper_only: bool | None = None):
        self.rng = rng
        self.L = rng.uniform(60, 170)
        self.view = rng.choice(["front", "oblique", "side", "back"], p=[0.45, 0.3, 0.15, 0.10])
        wf = {"front": 1.0, "back": 1.0, "oblique": rng.uniform(0.55, 0.8), "side": rng.uniform(0.2, 0.4)}[self.view]
        self.hw_sh = rng.uniform(0.36, 0.46) * wf
        self.hw_hip = rng.uniform(0.22, 0.30) * wf
        self.u = rng.uniform(0.55, 0.66)
        self.f = rng.uniform(0.50, 0.60)
        self.mirror = 1.0 if rng.random() < 0.5 else -1.0
        self.upper_only = (rng.random() < 0.18) if upper_only is None else upper_only
        self.cx = rng.uniform(250, W - 250)
        if self.upper_only:
            self.L = rng.uniform(150, 260)
            self.hip_y = H + rng.uniform(0.05, 0.35) * self.L
        else:
            self.hip_y = rng.uniform(0.35, 0.65) * H
        self.noise = rng.uniform(0.012, 0.03)

    def side_sign(self, side: str) -> float:
        # COCO "chap" — odamning chap tomoni; old ko'rinishda u rasmning o'ng tomonida
        s = 1.0 if side == "L" else -1.0
        if self.view == "back":
            s = -s
        return s * self.mirror

    def rest(self, side: str) -> np.ndarray:
        s = self.side_sign(side)
        return np.array([s * (self.hw_hip + 0.12), 0.24])

    def pose(self, wL: np.ndarray, wR: np.ndarray, origin: np.ndarray, head_dx: float,
             confL: float, confR: float) -> tuple[np.ndarray, np.ndarray]:
        rng = self.rng
        k = np.zeros((17, 3), dtype=np.float32)
        n = {}
        sL, sR = self.side_sign("L"), self.side_sign("R")
        n[5] = np.array([sL * self.hw_sh, -1.0])
        n[6] = np.array([sR * self.hw_sh, -1.0])
        n[11] = np.array([sL * self.hw_hip, 0.0])
        n[12] = np.array([sR * self.hw_hip, 0.0])
        n[13] = np.array([sL * self.hw_hip * 0.9, 0.95])
        n[14] = np.array([sR * self.hw_hip * 0.9, 0.95])
        n[15] = np.array([sL * self.hw_hip * 0.85, 1.85])
        n[16] = np.array([sR * self.hw_hip * 0.85, 1.85])
        n[0] = np.array([head_dx, -1.45])
        n[1] = np.array([head_dx + sL * 0.07, -1.52])
        n[2] = np.array([head_dx + sR * 0.07, -1.52])
        n[3] = np.array([head_dx * 0.6 + sL * 0.15, -1.47])
        n[4] = np.array([head_dx * 0.6 + sR * 0.15, -1.47])
        eL, wl = _elbow(n[5], wL, self.u, self.f, sL)
        eR, wr = _elbow(n[6], wR, self.u, self.f, sR)
        n[7], n[9], n[8], n[10] = eL, wl, eR, wr
        conf = np.full(17, 0.0, dtype=np.float32)
        for i in range(17):
            conf[i] = rng.uniform(0.75, 1.0)
        if self.view == "back":
            conf[[0, 1, 2]] = rng.uniform(0.05, 0.25, 3)
        if self.view == "side":
            far = [6, 8, 10, 12, 14, 16, 2, 4] if self.mirror > 0 else [5, 7, 9, 11, 13, 15, 1, 3]
            conf[far] *= rng.uniform(0.35, 0.8)
        conf[9] *= confL
        conf[10] *= confR
        for i in range(17):
            if rng.random() < 0.03:
                conf[i] = rng.uniform(0.05, 0.25)
        for i in range(17):
            p = origin + (n[i] + rng.normal(0, self.noise, 2)) * self.L
            k[i, 0], k[i, 1] = p[0], p[1]
            k[i, 2] = conf[i]
            if self.upper_only and p[1] > H:
                k[i, 2] = 0.0
        vis = k[k[:, 2] > 0.3]
        if len(vis):
            x1, y1 = vis[:, 0].min(), vis[:, 1].min()
            x2, y2 = vis[:, 0].max(), vis[:, 1].max()
            pad = 0.12 * self.L
            box = np.array([x1 - pad, y1 - pad * 1.5, x2 + pad, min(y2 + pad, H - 1)], dtype=np.float32)
        else:
            box = np.array([0, 0, 1, 1], dtype=np.float32)
        return k, box


def _track(keys: list[tuple[float, np.ndarray, float]], t: float) -> tuple[np.ndarray, float]:
    """keys: [(vaqt, nishon, ishonch)] — chiziqli silliq interpolyatsiya."""
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2]
    for (t0, p0, c0), (t1, p1, c1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            a = _smooth((t - t0) / max(t1 - t0, 1e-6))
            return p0 + (p1 - p0) * a, c0 + (c1 - c0) * a
    return keys[-1][1], keys[-1][2]


def generate(behavior: str, rng: np.random.Generator, with_item: bool = False) -> dict:
    """Bitta ketma-ketlik: {'frames': [(t, kpts, box)], 'label': 0/1, 'dwell': (t0,t1), 'items': [...]}"""
    upper = True if behavior == "offscreen" else (False if behavior in ("walk",) else None)
    b = Body(rng, upper_only=upper)
    if b.upper_only and behavior in ("walk",):
        b.upper_only = False
    active = "L" if rng.random() < 0.5 else "R"
    other = "R" if active == "L" else "L"
    s = b.side_sign(active)
    rest_a, rest_o = b.rest(active), b.rest(other)
    T = rng.uniform(3.4, 4.6)
    t = rng.uniform(0.2, 0.7)
    keys_a: list = [(0.0, rest_a, 1.0)]
    keys_o: list = [(0.0, rest_o, 1.0)]
    dwell = (0.0, 0.0)
    label = 1 if behavior in POSITIVE else 0
    walk_speed = 0.0
    hold_span = None   # buyum qo'lda bo'lgan vaqt oralig'i
    vanish_t = None

    def shelf():
        if rng.random() < 0.6:
            return np.array([s * (b.hw_sh + rng.uniform(0.5, 0.85)), rng.uniform(-1.3, -0.65)])
        return np.array([s * rng.uniform(0.15, 0.35), rng.uniform(-0.95, -0.6)])

    def chest():
        return np.array([s * rng.uniform(0.05, 0.25), rng.uniform(-0.75, -0.5)])

    def add(keys, dt, target, conf=1.0):
        nonlocal t
        t += dt
        keys.append((t, target, conf))

    def hold_here(keys, dt, conf=1.0):
        add(keys, dt, keys[-1][1], conf)

    if behavior in POSITIVE:
        add(keys_a, rng.uniform(0.35, 0.55), shelf())
        h0 = t
        hold_here(keys_a, rng.uniform(0.25, 0.7))
        if rng.random() < 0.5:
            add(keys_a, rng.uniform(0.3, 0.5), chest())
            hold_here(keys_a, rng.uniform(0.2, 0.6))
        if behavior == "pocket":
            tgt = np.array([s * b.hw_hip * rng.uniform(0.7, 1.05), rng.uniform(-0.05, 0.12)])
        elif behavior == "waistband":
            tgt = np.array([s * rng.uniform(0.0, 0.12), rng.uniform(-0.08, 0.06)])
        elif behavior == "shirt":
            tgt = np.array([s * rng.uniform(0.0, 0.15), rng.uniform(-0.42, -0.12)])
        elif behavior == "bag":
            tgt = np.array([s * (b.hw_hip + rng.uniform(0.15, 0.3)), rng.uniform(-0.1, 0.12)])
        else:  # offscreen
            tgt = np.array([s * rng.uniform(0.1, 0.35), rng.uniform(0.35, 0.7)])
        add(keys_a, rng.uniform(0.3, 0.5), tgt)
        hold_span = (h0, t)
        vanish_t = t
        d0 = t
        hidden_conf = 0.0 if behavior == "offscreen" else rng.uniform(0.25, 0.65)
        hold_here(keys_a, 0.12, hidden_conf)   # qo'l kiyim/cho'ntak ichiga kirdi
        hold_here(keys_a, rng.uniform(0.4, 1.3), hidden_conf)
        dwell = (d0, t)
        add(keys_a, rng.uniform(0.3, 0.5), rest_a)
    elif behavior == "idle":
        add(keys_a, T * 0.5, rest_a + rng.normal(0, 0.03, 2))
    elif behavior == "walk":
        walk_speed = rng.uniform(0.6, 1.6) * (1 if rng.random() < 0.5 else -1)
    elif behavior == "reach_return":
        add(keys_a, rng.uniform(0.35, 0.55), shelf())
        hold_here(keys_a, rng.uniform(0.2, 0.6))
        add(keys_a, rng.uniform(0.35, 0.6), rest_a + rng.normal(0, 0.03, 2))
    elif behavior == "inspect_putback":
        sh = shelf()
        add(keys_a, rng.uniform(0.35, 0.55), sh)
        h0 = t
        hold_here(keys_a, rng.uniform(0.2, 0.4))
        add(keys_a, rng.uniform(0.3, 0.5), chest())
        hold_here(keys_a, rng.uniform(0.5, 1.2))
        add(keys_a, rng.uniform(0.3, 0.5), sh)
        hold_span = (h0, t)
        hold_here(keys_a, rng.uniform(0.1, 0.3))
        add(keys_a, rng.uniform(0.35, 0.55), rest_a)
    elif behavior == "phone_call":
        add(keys_a, rng.uniform(0.4, 0.6), np.array([s * rng.uniform(0.1, 0.2), rng.uniform(-1.5, -1.38)]))
        hold_here(keys_a, rng.uniform(1.0, 2.0))
    elif behavior == "head_touch":
        add(keys_a, rng.uniform(0.35, 0.5), np.array([s * rng.uniform(0.0, 0.15), rng.uniform(-1.8, -1.4)]))
        hold_here(keys_a, rng.uniform(0.3, 1.0))
        add(keys_a, rng.uniform(0.35, 0.5), rest_a)
    elif behavior == "arms_crossed":
        tgt_a = np.array([-s * rng.uniform(0.1, 0.2), rng.uniform(-0.6, -0.45)])
        tgt_o = np.array([s * rng.uniform(0.1, 0.2), rng.uniform(-0.6, -0.45)])
        add(keys_a, rng.uniform(0.4, 0.6), tgt_a)
        keys_o.append((t, tgt_o, 1.0))
        hold_here(keys_a, rng.uniform(1.0, 2.0))
        keys_o.append((t, tgt_o, 1.0))
    elif behavior == "hands_in_pockets":
        pa = np.array([s * b.hw_hip * 0.9, rng.uniform(-0.02, 0.1)])
        po = np.array([-s * b.hw_hip * 0.9, rng.uniform(-0.02, 0.1)])
        c = rng.uniform(0.3, 0.6)
        keys_a = [(0.0, pa, c)]
        keys_o = [(0.0, po, c)]
        t = T
    elif behavior == "look_phone":
        tgt_a = np.array([s * rng.uniform(0.05, 0.15), rng.uniform(-0.7, -0.5)])
        tgt_o = np.array([-s * rng.uniform(0.05, 0.15), rng.uniform(-0.7, -0.5)])
        add(keys_a, rng.uniform(0.4, 0.6), tgt_a)
        keys_o.append((t, tgt_o, 1.0))
        hold_here(keys_a, rng.uniform(1.0, 2.0))
        keys_o.append((t, tgt_o, 1.0))
    elif behavior == "basket":
        add(keys_a, rng.uniform(0.35, 0.55), shelf())
        h0 = t
        hold_here(keys_a, rng.uniform(0.2, 0.5))
        add(keys_a, rng.uniform(0.35, 0.55), np.array([s * rng.uniform(0.25, 0.45), rng.uniform(0.35, 0.55)]))
        hold_span = (h0, t + 0.2)
        hold_here(keys_a, rng.uniform(0.2, 0.5))
        add(keys_a, rng.uniform(0.3, 0.5), rest_a)
    elif behavior == "brief_touch":
        add(keys_a, rng.uniform(0.3, 0.45), np.array([s * b.hw_hip * 0.9, rng.uniform(-0.1, 0.1)]))
        hold_here(keys_a, rng.uniform(0.05, 0.22))
        add(keys_a, rng.uniform(0.25, 0.4), rest_a)
    T = max(T, t + rng.uniform(0.3, 0.8))

    look = rng.random() < (0.4 if label else 0.2)
    origin0 = np.array([b.cx, b.hip_y])
    frames, items = [], []
    n = int(T * FPS)
    for i in range(n):
        tt = i / FPS
        wa, ca = _track(keys_a, tt)
        wo, co = _track(keys_o, tt)
        if walk_speed:
            ph = tt * 2 * math.pi * 0.9
            wa = rest_a + np.array([0.12 * math.sin(ph), 0.03 * math.cos(2 * ph)])
            wo = rest_o + np.array([-0.12 * math.sin(ph), 0.03 * math.cos(2 * ph)])
        origin = origin0 + np.array([walk_speed * b.L * tt, 0.0])
        head_dx = 0.12 * math.sin(tt * 2 * math.pi * 0.8) if look else 0.02 * math.sin(tt)
        if active == "L":
            k, box = b.pose(wa, wo, origin, head_dx, ca, co)
        else:
            k, box = b.pose(wo, wa, origin, head_dx, co, ca)
        frames.append((tt, k, box))
        if with_item and hold_span:
            wi = 9 if active == "L" else 10
            if hold_span[0] <= tt <= hold_span[1]:
                c = k[wi, :2]
                r = 0.12 * b.L
                items.append((tt, np.array([c[0] - r, c[1] - r * 1.4, c[0] + r, c[1] + r * 0.6])))
            elif behavior in ("inspect_putback", "basket") and tt > hold_span[1]:
                c = keys_a[-1][1]
                items.append((tt, None))
    return {"frames": frames, "label": label, "dwell": dwell, "behavior": behavior,
            "items": items, "vanish_t": vanish_t, "upper_only": b.upper_only, "active": active}


def sample_windows(n: int, seed: int = 0, window: float = 2.5) -> list[dict]:
    """ML uchun oynalar: ijobiy oynalar yashirish (dwell) lahzasini o'z ichiga oladi."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        pos = i % 2 == 0
        beh = rng.choice(POSITIVE) if pos else rng.choice(NEGATIVE)
        seq = generate(str(beh), rng)
        fr = seq["frames"]
        T = fr[-1][0]
        if seq["label"]:
            d0, d1 = seq["dwell"]
            mid = d0 + min(rng.uniform(0.3, 0.9), max(d1 - d0, 0.3))
            end = min(T, mid + rng.uniform(0.0, 0.5))
        else:
            end = rng.uniform(min(window, T), T)
        win = [f for f in fr if end - window <= f[0] <= end]
        out.append({"frames": win, "label": seq["label"], "behavior": seq["behavior"], "group": f"syn{i}"})
    return out
