"""Skelet (17 ta COCO nuqta) geometriyasi va ML uchun belgilar (feature) hisoblash.

Koordinatalar tana markaziga (son o'rtasi) ko'chiriladi va tana uzunligiga (yelka→son)
bo'linadi. Shunday qilib kamera masofasi va o'lchamdan qat'i nazar bir xil son chiqadi.
y o'qi pastga qarab o'sadi: yelkalar taxminan y = -1, sonlar y = 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

NOSE, LEYE, REYE, LEAR, REAR = 0, 1, 2, 3, 4
LSHO, RSHO, LELB, RELB, LWRI, RWRI = 5, 6, 7, 8, 9, 10
LHIP, RHIP, LKNE, RKNE, LANK, RANK = 11, 12, 13, 14, 15, 16

CONF_THR = 0.30

# Zonalar (normallashtirilgan birlikda)
POCKET_RADIUS = 0.42      # son nuqtasidan masofa
POCKET_Y = (-0.20, 0.55)  # bel/cho'ntak balandligi
TORSO_Y = (-0.45, -0.02)  # qorin / pastki ko'krak balandligi (kiyim ichiga)
ABOVE_WAIST_Y = -0.45     # bundan yuqori — "faol" qo'l (polkaga, ko'krak oldiga)
REACH_EXT = 0.95          # bilak yelkadan shuncha uzoq bo'lsa — qo'l uzatilgan
ELBOW_BENT = 150.0        # tirsak burchagi bundan kichik bo'lsa — qo'l bukilgan


@dataclass
class Body:
    norm: np.ndarray          # (17, 2), ko'rinmagan nuqtalar NaN
    conf: np.ndarray          # (17,)
    scale: float              # piksel / birlik
    origin: np.ndarray        # (2,) piksel
    hips_visible: bool
    shoulders_visible: bool
    half_width: float         # yelka yarim kengligi (birlikda)
    hip_half: float = 0.25    # son yarim kengligi (birlikda)


def _vis(k: np.ndarray, i: int) -> bool:
    return bool(k[i, 2] >= CONF_THR)


def body_from_kpts(kpts: np.ndarray, box: np.ndarray | None = None) -> Body | None:
    """Kadr koordinatalaridagi (17,3) skeletdan normallashtirilgan tana."""
    k = np.asarray(kpts, dtype=np.float32)
    sh_vis = _vis(k, LSHO) and _vis(k, RSHO)
    hip_vis = _vis(k, LHIP) and _vis(k, RHIP)
    if sh_vis:
        mid_sh = (k[LSHO, :2] + k[RSHO, :2]) / 2
        sh_w = float(np.linalg.norm(k[LSHO, :2] - k[RSHO, :2]))
    elif _vis(k, LSHO) or _vis(k, RSHO):
        i = LSHO if _vis(k, LSHO) else RSHO
        mid_sh = k[i, :2].copy()
        sh_w = 0.0
    else:
        return None
    if hip_vis:
        mid_hip = (k[LHIP, :2] + k[RHIP, :2]) / 2
        torso = float(np.linalg.norm(mid_hip - mid_sh))
    elif _vis(k, LHIP) or _vis(k, RHIP):
        i = LHIP if _vis(k, LHIP) else RHIP
        mid_hip = np.array([mid_sh[0], k[i, 1]], dtype=np.float32)
        torso = float(abs(k[i, 1] - mid_sh[1]))
    else:
        mid_hip = None
        torso = 0.0
    if torso < 1.0:
        # Sonlar ko'rinmaydi (masalan, stol oldida o'tirgan odam): yelka kengligidan taxmin
        if sh_w > 1.0:
            torso = sh_w * 1.35
        elif box is not None:
            torso = float(box[3] - box[1]) * 0.30
        else:
            return None
    if mid_hip is None:
        mid_hip = mid_sh + np.array([0.0, torso], dtype=np.float32)
    scale = max(torso, 1.0)
    norm = (k[:, :2] - mid_hip) / scale
    conf = k[:, 2].copy()
    norm[conf < CONF_THR] = np.nan
    half_w = (sh_w / scale) / 2 if sh_w > 0 else 0.35
    if hip_vis:
        hip_half = float(np.linalg.norm(k[LHIP, :2] - k[RHIP, :2])) / scale / 2
    else:
        hip_half = half_w * 0.65
    return Body(norm=norm, conf=conf, scale=scale, origin=mid_hip, hips_visible=hip_vis,
                shoulders_visible=sh_vis, half_width=half_w, hip_half=hip_half)


def hand_state(b: Body, side: str) -> dict:
    """Bitta qo'l holati: zonalar, uzatilganlik, balandlik."""
    wi, si, hi = (LWRI, LSHO, LHIP) if side == "L" else (RWRI, RSHO, RHIP)
    w = b.norm[wi]
    st = {"vis": bool(not np.isnan(w[0])), "x": float("nan"), "y": float("nan"),
          "d_hip": float("nan"), "ext": float("nan"), "pocket": False, "torso": False,
          "active": False, "reach": False, "conf": float(b.conf[wi]), "elbow": float("nan"),
          "bent": False}
    if not st["vis"]:
        return st
    st["x"], st["y"] = float(w[0]), float(w[1])
    hips = [b.norm[i] for i in (LHIP, RHIP) if not np.isnan(b.norm[i][0])]
    if not hips:
        hips = [np.array([-b.half_width, 0.0]), np.array([b.half_width, 0.0])]
    d_hip = min(float(np.linalg.norm(w - h)) for h in hips)
    st["d_hip"] = d_hip
    sh = b.norm[si]
    if not np.isnan(sh[0]):
        st["ext"] = float(np.linalg.norm(w - sh))
    # Tirsak burchagi: osilib turgan qo'l deyarli to'g'ri (~170°), cho'ntakka/kiyim ichiga
    # kirayotgan qo'l esa bukilgan bo'ladi.
    ei = LELB if side == "L" else RELB
    el = b.norm[ei]
    if not np.isnan(el[0]) and not np.isnan(sh[0]):
        v1, v2 = sh - el, w - el
        cosang = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6))
        st["elbow"] = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    else:
        st["elbow"] = float("nan")
    bent = math.isnan(st["elbow"]) or st["elbow"] < ELBOW_BENT
    st["bent"] = bool(bent)
    in_pocket_band = POCKET_Y[0] <= w[1] <= POCKET_Y[1] and abs(w[0]) <= b.half_width + 0.30
    # Osilib turgan qo'l sondan tashqarida va pastroqda bo'ladi; cho'ntakka kirgan qo'l esa
    # son chizig'ida yoki ichkarida, bel balandligida turadi.
    inward = abs(float(w[0])) <= b.hip_half + 0.06 and w[1] <= 0.16
    st["inward"] = bool(inward)
    st["pocket"] = bool((d_hip <= POCKET_RADIUS or in_pocket_band) and (bent or inward))
    st["torso"] = bool(TORSO_Y[0] <= w[1] <= TORSO_Y[1] and abs(w[0]) <= max(b.half_width * 0.75, 0.18))
    lateral = abs(float(w[0])) >= b.half_width + 0.55
    extended = (not math.isnan(st["ext"])) and st["ext"] >= REACH_EXT
    # Pastga osilib turgan to'g'ri qo'l ham "uzun" ko'rinadi, shuning uchun uzatilganlik
    # faqat qo'l bel balandligidan yuqorida yoki tanadan yon tomonga uzoqda bo'lsa hisoblanadi.
    st["reach"] = bool(extended and (w[1] <= ABOVE_WAIST_Y or lateral))
    st["active"] = bool(w[1] <= ABOVE_WAIST_Y or st["reach"])
    return st


# ----------------------------------------------------------------------------------
# Oyna (window) belgilari — ML klassifikatori uchun
# ----------------------------------------------------------------------------------

def _stats(a: list[float], prefix: str) -> dict:
    arr = np.array([v for v in a if v is not None and not math.isnan(v)], dtype=np.float32)
    if len(arr) == 0:
        return {f"{prefix}_mean": np.nan, f"{prefix}_min": np.nan, f"{prefix}_max": np.nan,
                f"{prefix}_std": np.nan}
    return {f"{prefix}_mean": float(arr.mean()), f"{prefix}_min": float(arr.min()),
            f"{prefix}_max": float(arr.max()), f"{prefix}_std": float(arr.std())}


def _max_run(mask: list[bool], times: list[float]) -> float:
    best, start = 0.0, None
    for m, t in zip(mask, times):
        if m and start is None:
            start = t
        elif not m and start is not None:
            best = max(best, t - start)
            start = None
    if start is not None and times:
        best = max(best, times[-1] - start)
    return best


def _entries(mask: list[bool]) -> int:
    return sum(1 for i in range(1, len(mask)) if mask[i] and not mask[i - 1])


def _reach_then_zone(active: list[bool], zone: list[bool], times: list[float], gap: float = 1.6) -> float:
    """Qo'l avval faol (yuqorida/uzatilgan) bo'lib, keyin zonaga tushgan holatlar ulushi."""
    last_active = -1e9
    hits = 0
    for a, z, t in zip(active, zone, times):
        if a:
            last_active = t
        elif z and t - last_active <= gap:
            hits += 1
    return hits / max(len(times), 1)


FEATURE_NAMES: list[str] = []


def window_features(seq: list[tuple[float, np.ndarray, np.ndarray]]) -> np.ndarray | None:
    """seq: [(vaqt_soniya, kpts(17,3), box(4)), ...] — bitta odamning 1.5–3 s lik izi."""
    global FEATURE_NAMES
    if len(seq) < 4:
        return None
    times, bodies, boxes = [], [], []
    for t, k, b in seq:
        body = body_from_kpts(k, b)
        if body is None:
            continue
        times.append(float(t))
        bodies.append(body)
        boxes.append(np.asarray(b, dtype=np.float32))
    if len(bodies) < 4:
        return None
    t0 = times[0]
    times = [t - t0 for t in times]
    dur = max(times[-1], 1e-3)
    feats: dict[str, float] = {"dur": dur, "n": float(len(times))}
    feats["hips_vis"] = float(np.mean([b.hips_visible for b in bodies]))
    feats["sh_vis"] = float(np.mean([b.shoulders_visible for b in bodies]))

    for side in ("L", "R"):
        hs = [hand_state(b, side) for b in bodies]
        vis = [h["vis"] for h in hs]
        feats[f"{side}_vis"] = float(np.mean(vis))
        feats.update(_stats([h["x"] for h in hs], f"{side}_x"))
        feats.update(_stats([h["y"] for h in hs], f"{side}_y"))
        feats.update(_stats([h["d_hip"] for h in hs], f"{side}_dhip"))
        feats.update(_stats([h["ext"] for h in hs], f"{side}_ext"))
        feats.update(_stats([h["elbow"] for h in hs], f"{side}_elbow"))
        pocket = [h["pocket"] for h in hs]
        torso = [h["torso"] for h in hs]
        active = [h["active"] for h in hs]
        feats[f"{side}_pocket_frac"] = float(np.mean(pocket))
        feats[f"{side}_torso_frac"] = float(np.mean(torso))
        feats[f"{side}_active_frac"] = float(np.mean(active))
        feats[f"{side}_pocket_run"] = _max_run(pocket, times)
        feats[f"{side}_torso_run"] = _max_run(torso, times)
        feats[f"{side}_pocket_entries"] = float(_entries(pocket))
        feats[f"{side}_torso_entries"] = float(_entries(torso))
        feats[f"{side}_reach_pocket"] = _reach_then_zone(active, pocket, times)
        feats[f"{side}_reach_torso"] = _reach_then_zone(active, torso, times)
        # Tezlik
        sp = []
        for i in range(1, len(hs)):
            if hs[i]["vis"] and hs[i - 1]["vis"]:
                dt = max(times[i] - times[i - 1], 1e-3)
                sp.append(math.hypot(hs[i]["x"] - hs[i - 1]["x"], hs[i]["y"] - hs[i - 1]["y"]) / dt)
        feats.update(_stats(sp, f"{side}_speed"))
        # Ko'rinmay qolish (qo'l yashirilgan / kadrdan chiqqan)
        lost = 0
        for i in range(1, len(hs)):
            if hs[i - 1]["vis"] and not hs[i]["vis"]:
                lost += 1
        feats[f"{side}_lost"] = float(lost)

    # Ikki qo'l uchun umumlashtirilgan (simmetrik) belgilar
    for key in ("pocket_run", "torso_run", "reach_pocket", "reach_torso", "pocket_frac", "torso_frac",
                "lost", "active_frac"):
        feats[f"max_{key}"] = max(feats[f"L_{key}"], feats[f"R_{key}"])

    # Bosh harakati (atrofga qarash): burunning yelkalarga nisbatan siljishi
    head = []
    for b in bodies:
        n = b.norm[NOSE]
        head.append(float(n[0]) if not np.isnan(n[0]) else np.nan)
    feats.update(_stats(head, "head_x"))
    hx = [h for h in head if not math.isnan(h)]
    turns = 0
    for i in range(2, len(hx)):
        if (hx[i] - hx[i - 1]) * (hx[i - 1] - hx[i - 2]) < 0 and abs(hx[i] - hx[i - 1]) > 0.06:
            turns += 1
    feats["head_turns"] = float(turns)
    ear_asym = []
    for b in bodies:
        le, re = b.conf[LEAR], b.conf[REAR]
        ear_asym.append(float(le - re))
    feats.update(_stats(ear_asym, "ear_asym"))

    # Butun tana harakati
    origins = np.array([b.origin for b in bodies])
    scales = np.array([b.scale for b in bodies])
    move = np.linalg.norm(origins[-1] - origins[0]) / max(float(np.median(scales)), 1.0)
    feats["body_move"] = float(move / dur)
    bh = np.array([(bx[3] - bx[1]) / max(bx[2] - bx[0], 1.0) for bx in boxes])
    feats["box_aspect_mean"] = float(bh.mean())
    knees = []
    for b in bodies:
        kn = [b.norm[i][1] for i in (LKNE, RKNE) if not np.isnan(b.norm[i][1])]
        knees.append(float(np.mean(kn)) if kn else np.nan)
    feats.update(_stats(knees, "knee_y"))

    names = sorted(feats)
    if not FEATURE_NAMES:
        FEATURE_NAMES = names
    return np.array([feats[n] for n in names], dtype=np.float32)


def feature_names() -> list[str]:
    if not FEATURE_NAMES:
        # Sun'iy ketma-ketlik bilan nomlarni hosil qilish
        k = np.zeros((17, 3), dtype=np.float32)
        k[:, 2] = 1.0
        k[LSHO, :2] = (90, 100)
        k[RSHO, :2] = (110, 100)
        k[LHIP, :2] = (92, 150)
        k[RHIP, :2] = (108, 150)
        seq = [(i / 10, k.copy(), np.array([80, 80, 120, 220], dtype=np.float32)) for i in range(6)]
        window_features(seq)
    return list(FEATURE_NAMES)
