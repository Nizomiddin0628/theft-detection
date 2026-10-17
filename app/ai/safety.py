"""Xavfsizlik va salomatlik (2.2.0, 6-bosqich): yiqilish, odam yotibdi, tungi kirish, gavjumlik, to'silgan chiqish,
olov (tajriba).

Signallar o'g'irlik signallari bilan bir xil yo'ldan o'tadi: `Processor.step()` qaytargan alerts → hodisa (rasm,
Telegram, klip, ✅/❌, bulut). Farqi: ball doim 100, daraja "yuqori", "Bu ayblov emas" yozuvi yo'q, xodimlar ham
hisobga olinadi (yiqilish va yotish uchun), kritik turlar SMS bilan ham (app/sms.py).

Qoidalar (sozlamalar `safety_*`, panel: Xavfsizlik):

  fall     Yiqilish. Har odam uchun har kadrda skelet bo'yicha holat: "tik" (son baland), "yotgan" (son pastda va gavda
           yotiq / ramka past / juda yassi) yoki "oraliq" (o'tirgan, cho'kkalagan). Yiqilish — "tik" holatdan 2 s ichida
           "yotgan" holatga o'tish. Tasdiq: keyingi 2 s ichida odam tik turmadi (trek yo'qolsa — yerda deb hisoblanadi,
           o'sha joyda tik odam paydo bo'lsa — bekor).
  lying    Odam yotibdi. Yotgan holat ≥ 20 s, harakatsiz (sekin yotib qolgan ham — yiqilish ko'rinmagan holatlar).
  night    Tungi kirish. Ish vaqtidan (`work_hours`) tashqarida, yopilgandan keyin va ochilishdan oldin 20 daqiqa
           hisobga olinmaydi; yoki o'z jadvali (`safety_night_hours`). Odam ≥ 2 s ko'rinsa.
  crowd    Gavjumlik. Do'konda "hozir ichkarida" (sanash) yoki bitta kadrdagi odamlar soni chegaradan ≥ 60 s oshsa.
  blocked  Favqulodda chiqish to'silgan. Zona `favqulodda` + "bo'sh holatini eslab qolish". Kadr eslab qolingan holat
           bilan katakchalar bo'yicha solishtiriladi (yorug'lik farqi tekislanadi), faqat zonada odam yo'q paytda;
           ≥ 10 daqiqa to'silgan tursa.
  fire     Olov (tajriba, sukut bo'yicha o'chiq). Olov rangi + miltillash ≥ 10 s. Tutunni oddiy usul bilan ishonchli
           aniqlab bo'lmaydi — shuning uchun tutun yo'q. Qizil/to'q sariq kiyim, lampa, ekran xato signal berishi mumkin.

Video tahlilida (background) faqat fall, lying, crowd (kadr bo'yicha) va fire ishlaydi.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import db
from ..config import DEFAULT_SETTINGS
from ..logutil import get_logger

log = get_logger("safety")

SAFETY_KINDS = ("fall", "lying", "night", "crowd", "blocked", "fire")
KIND_LABELS = {"fall": "Yiqilish", "lying": "Odam yotibdi", "night": "Tungi kirish", "crowd": "Gavjumlik",
               "blocked": "Chiqish to'silgan", "fire": "Olov (tajriba)"}
# Telegram / bulut sarlavhasi (o'g'irlikdagi "Shubhali harakat — tekshirib ko'ring" o'rniga)
HEADLINES = {"fall": "🆘 Yiqilish — yordam kerak bo'lishi mumkin",
             "lying": "🆘 Odam yotibdi — yordam kerak bo'lishi mumkin",
             "night": "🌙 Tungi kirish — ish vaqtidan tashqari odam",
             "crowd": "👥 Gavjumlik — odam soni me'yordan oshdi",
             "blocked": "🚪 Favqulodda chiqish to'silgan",
             "fire": "🔥 Olov belgisi (tajriba) — darhol tekshiring"}
CRITICAL = ("fall", "lying", "night", "fire", "blocked")   # SMS uchun tanlash mumkin bo'lgan turlar
EXIT_KIND = "favqulodda"
BACKGROUND_KINDS = ("fall", "lying", "crowd", "fire")

# --- yiqilish va yotish (o'lchovlar: docs/SAFETY.md; YOLO11n-pose, ambianic videolari)
KP_CONF = 0.35
FALL_WINDOW = 2.0        # "tik" holatdan "yotgan" holatgacha ko'pi bilan shuncha soniya
FALL_CONFIRM = 2.0       # yotgan holatdan keyin shuncha soniya tik turmasa — signal
COLLAPSE_LIE = 1.5       # cho'kib yiqilish: son yerga tekkandan yotgan holatgacha ko'pi bilan
COLLAPSE_TOTAL = 2.5     # cho'kib yiqilish: tik holatdan yotgan holatgacha jami ko'pi bilan
HREF_SEC = 3.0           # tik bo'yi (H_ref): oxirgi shuncha soniyadagi tik kadrlarning eng kattasi (uzoqlashgan odam
                         # kichrayadi — eski katta qiymat uni "cho'kkalagan" qilib ko'rsatmasin)
LOST_KEEP = 6.0          # yo'qolgan trek shuncha soniya eslab qolinadi (to'silish, yotgan odamni detektor ko'rmasligi)
INHERIT_GAP = 3.0        # yangi trek shu vaqt ichida yo'qolgan trek o'rnida paydo bo'lsa — o'sha odam
LYING_GAP = 6.0          # yotgan holat kuzatuvlari orasidagi eng katta uzilish (detektor goh ko'rmaydi)
LYING_DOWN_FRAC = 0.6    # oynadagi kuzatuvlarning shuncha ulushi "yotgan" bo'lsin
LYING_MOVE = 0.6         # oynada markaz siljishi (H_ref ulushi) shundan kam — harakatsiz
FALL_SUPPRESS_LYING = 300.0   # yiqilish signali bo'lgan odam uchun shuncha vaqt "yotibdi" signali berilmaydi
LYING_REPEAT = 600.0     # o'sha joyda "yotibdi" signali shuncha vaqtda bir marta
SPOT_COOLDOWN = 60.0     # bir joyda (bir odam) takroriy yiqilish signali
SENS = {"normal": {"hip": 0.25, "collapse": False}, "high": {"hip": 0.33, "collapse": True}}

# --- boshqa qoidalar
NIGHT_MIN_SEC = 2.0      # tungi kirish: odam shuncha soniya ko'rinsin (bitta kadrdagi xato detektsiya emas)
NIGHT_END_SEC = 120.0    # shuncha vaqt odam ko'rinmasa — tashrif tugadi (keyingi kirish — yangi signal)
CROWD_DIP_SEC = 10.0     # chegaradan qisqa tushishlar (detektor odamni yo'qotishi) hisobga olinmaydi
CROWD_REARM_SEC = 120.0
EXIT_EVERY = 5.0         # chiqish zonasi shuncha soniyada bir tekshiriladi
EXIT_CELL = 8            # katakcha (px, kichraytirilgan rasmda)
EXIT_W = 160             # eslab qolingan rasm eni (px)
EXIT_DIFF = 22.0         # katakcha o'rtacha yorqinlik farqi (yorug'lik tekislangandan keyin)
EXIT_EDGE = 0.12         # katakcha chegaralar zichligi farqi
EXIT_LIGHT = 60.0        # o'rtacha yorqinlik shundan ko'p farq qilsa — solishtirilmaydi (chiroq o'chgan/yongan)
FIRE_EVERY = 0.25
FIRE_W = 160
FIRE_MIN_FRAC = 0.002    # olov rangidagi piksellar ulushi (kadrning)
FIRE_FLICKER = 0.25      # ketma-ket niqoblar farqi / birlashmasi
FIRE_HIT_FRAC = 0.7      # oynadagi namunalarning shuncha ulushi olovga o'xshasin

DEFAULTS = {k: v for k, v in DEFAULT_SETTINGS.items() if k.startswith("safety_")}   # app/config.py
RANGES = {"safety_lying_sec": (10, 600), "safety_night_grace_min": (0, 180), "safety_crowd_inside": (0, 5000),
          "safety_crowd_cam": (0, 200), "safety_crowd_sec": (10, 3600), "safety_blocked_min": (1, 240),
          "safety_blocked_frac": (0.1, 0.9), "safety_fire_sec": (3, 120), "safety_cooldown_min": (1, 720)}
FLAGS = ("safety_fall", "safety_lying", "safety_night", "safety_crowd", "safety_blocked", "safety_fire")


def is_safety(kind) -> bool:
    return kind in SAFETY_KINDS


def _num(s: dict, key: str) -> float:
    try:
        return float(s.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def _on(s: dict, key: str) -> bool:
    return str(s.get(key, DEFAULTS[key])) in ("1", "true", "on", "yes")


# ============================================================================= skelet holati
UP, DOWN, MID = "tik", "yotgan", "oraliq"


@dataclass
class Pose:
    state: str
    h: float
    w: float
    ar: float
    hr: float                      # h / H_ref
    hip_low: float | None          # (ramka pasti − son) / H_ref
    ang: float | None              # gavda burchagi vertikaldan (°)
    vt: float | None               # gavdaning tik balandligi (son y − yelka y) / H_ref
    truncated: bool                # ramka kadr pastiga tegib turibdi (oyoqlar kadrdan tashqarida)


def _pt(k: np.ndarray, idx: tuple) -> np.ndarray | None:
    vis = [k[i, :2] for i in idx if k[i, 2] >= KP_CONF]
    return np.mean(vis, axis=0) if vis else None


def skeleton_height(kpts: np.ndarray) -> float:
    """Tik bo'y taxmini skeletdan: yelka→son→tizza→to'piq (bir tomon, uchala bo'g'in ko'rinsa) ≈ bo'yning 0,8 qismi.
    Yon tomonga yotgan odamda ham to'g'ri (ramka balandligidan farqli). 0 — baholab bo'lmaydi."""
    best = 0.0
    for sh, hp, kn, an in ((5, 11, 13, 15), (6, 12, 14, 16)):
        if min(kpts[i, 2] for i in (sh, hp, kn, an)) < KP_CONF:
            continue
        L = sum(float(np.linalg.norm(kpts[a, :2] - kpts[b, :2])) for a, b in ((sh, hp), (hp, kn), (kn, an)))
        best = max(best, L / 0.8)
    return best


def classify(box, kpts, h_ref: float, frame_h: int, hip_thr: float = 0.25) -> Pose:
    """Bitta kuzatuv: tik / yotgan / oraliq.

    yotgan: son pastda (hip_low ≤ hip_thr) VA (gavda yotiq: tik balandligi ≤ 0,15·H yoki burchak ≥ 55°, yoki ramka
            ≤ 0,45·H, yoki ramka juda yassi ≥ 1,3). Lekin gavda tik (≥ 0,2·H, < 40°) bo'lsa — o'tirgan/cho'kkalagan,
            yotgan emas (do'konda pastki polkaga cho'kkalash — eng ko'p uchraydigan xato signal manbai).
            Son ko'rinmasa: ramka yassi (≥ 1,2) va past (≤ 0,5·H).
            Ramka kadr pastiga tegsa (oyoq kadrdan tashqarida — son "pastda" ko'rinadi): faqat juda yassi va yotiq.
    tik:    son o'z ramkasi bo'yining ≥ 0,4 qismida va ramka ≥ 0,6·H; son ko'rinmasa — ramka ≥ 0,8·H va tik (≤ 0,8).
    """
    b = np.asarray(box, dtype=np.float32)
    k = np.asarray(kpts, dtype=np.float32)
    h = max(float(b[3] - b[1]), 1.0)
    w = max(float(b[2] - b[0]), 1.0)
    ar = w / h
    H = max(float(h_ref), h, 1.0)
    hr = h / H
    truncated = float(b[3]) >= 0.98 * frame_h
    hip = _pt(k, (11, 12))
    sh = _pt(k, (5, 6))
    hip_low = None if hip is None else (float(b[3]) - float(hip[1])) / H
    ang = vt = None
    if hip is not None and sh is not None:
        d = sh - hip
        ang = math.degrees(math.atan2(abs(float(d[0])), -float(d[1])))
        vt = float(hip[1] - sh[1]) / H
    flat = ang is not None and ang >= 55
    if truncated:
        down = ar >= 1.3 and (ang is None or ang >= 55) and hr <= 0.5
        up = not down and ar <= 0.75 and (ang is None or ang < 30)
    elif hip_low is not None:
        lowish = (vt is not None and vt <= 0.15) or flat or hr <= 0.45 or ar >= 1.3
        upright_torso = vt is not None and vt >= 0.2 and ang is not None and ang < 40 and hr > 0.35
        # Gavda aniq yotiq va ramka past: son biroz balandroq bo'lsa ham (yiqilayotgan / yonboshlab yotgan)
        horizontal = vt is not None and vt <= 0.1 and flat and hr <= 0.55 and hip_low <= hip_thr + 0.08
        down = (hip_low <= hip_thr and lowish and not upright_torso) or horizontal
        # tik: son o'z ramkasining pastki yarmidan baland (uzoqlashayotgan odam kichrayadi — H_ref ga emas, o'z bo'yiga)
        up = not down and hip_low * H / h >= 0.4 and hr >= 0.6
    else:
        down = ar >= 1.2 and hr <= 0.5
        up = not down and hr >= 0.8 and ar <= 0.8
    state = DOWN if down else (UP if up else MID)
    return Pose(state, h, w, ar, hr, hip_low, ang, vt, truncated)


@dataclass
class TrackSt:
    tid: int
    first_t: float
    last_t: float
    box: np.ndarray
    hist: deque = field(default_factory=deque)       # (t, state, cx, cy) — vaqt bo'yicha qisqartiriladi (_observe)
    ups: deque = field(default_factory=lambda: deque(maxlen=200))    # (t, h) — tik kadrlar
    skel: deque = field(default_factory=lambda: deque(maxlen=60))    # (t, skelet bo'yi)
    h_max: float = 0.0
    h_ref: float = 0.0
    last_up_t: float = -1e9
    last_up_h: float = 0.0
    last_state: str = MID
    down_since: float | None = None
    floor_t: float | None = None       # tik holatdan keyin son birinchi marta pastga (≤ chegara) tushgan vaqt
    pending: dict | None = None        # {"t_down", "deadline", "box", "reasons"}
    fall_alert_t: float = -1e9
    lying_alerted: bool = False
    kpts: np.ndarray | None = None


def _center(b) -> tuple[float, float]:
    return (float(b[0] + b[2]) / 2, float(b[1] + b[3]) / 2)


def _feet_near(person_box, spot_box, h_ref: float) -> bool:
    """Tik odamning oyog'i (ramka pasti o'rtasi) yotgan joy atrofida (0,5·H_ref chegara bilan)."""
    fx, fy = float(person_box[0] + person_box[2]) / 2, float(person_box[3])
    m = 0.5 * h_ref
    return spot_box[0] - m <= fx <= spot_box[2] + m and spot_box[1] - m <= fy <= spot_box[3] + m


class FallDetector:
    """Yiqilish va "odam yotibdi" — bitta kamera, trek bo'yicha (xodimlar ham)."""

    def __init__(self):
        self.tracks: dict[int, TrackSt] = {}
        self.lost: dict[int, TrackSt] = {}
        self.spot_alerts: list[tuple[float, np.ndarray]] = []     # (vaqt, ramka) — yiqilish signallari
        self.lying_alerts: list[tuple[float, np.ndarray]] = []    # (vaqt, ramka) — "yotibdi" signallari

    # ------------------------------------------------------------------
    def _h_ref(self, st: TrackSt, t: float) -> float:
        ups = [h for tt, h in st.ups if 0 <= t - tt <= HREF_SEC]
        sk = [v for tt, v in st.skel if 0 <= t - tt <= 3.0]
        base = max(ups) if ups else (st.h_ref or st.h_max)
        return max(base, max(sk) if sk else 0.0, 1.0)

    def _inherit(self, tid: int, box, kpts, t: float, frame_h: int, hip_thr: float) -> TrackSt | None:
        """Yangi trek: yaqinda yo'qolgan trek o'rnida (to'silish, yotgan odamni detektor qayta topdi) — o'sha odam.
        Holat o'sha yo'qolgan odamning tik bo'yi bilan baholanadi: tik turgan odam meros olmaydi (yotgan odam turib
        ketsa — uning epizodi baribir tugaydi; yoniga kelgan boshqa odam yotganning holatini "o'g'irlamasin")."""
        cx, cy = _center(box)
        best, bd = None, 1e18
        for lid, st in self.lost.items():
            if not 0 <= t - st.last_t <= INHERIT_GAP:
                continue
            lx, ly = _center(st.box)
            d = math.hypot(cx - lx, cy - ly)
            lim = 0.6 * max(st.h_ref or st.h_max, float(box[3] - box[1]), float(box[2] - box[0]))
            if d <= lim and d < bd:
                best, bd = lid, d
        if best is None:
            return None
        ref = self.lost[best]
        if classify(box, kpts, ref.h_ref or ref.h_max, frame_h, hip_thr).state == UP:
            return None
        st = self.lost.pop(best)
        st.tid = tid
        return st

    def update(self, tracked: list, t: float, frame_h: int, sens: str = "normal", lying_sec: float = 20.0,
               fall_on: bool = True, lying_on: bool = True, loose: list | None = None) -> list[dict]:
        """tracked — kuzatuvchi bergan odamlar; loose — kuzatuvchi trekka ulamagan past ishonchli detektsiyalar
        (yerda yotgan odamni detektor ko'pincha 0,25–0,4 ishonch bilan topadi: yangi trek ochilmaydi). Ular faqat
        yaqinda yo'qolgan trek o'rnida bo'lsa — o'sha odamning davomi sifatida ishlatiladi."""
        cfg = SENS.get(sens, SENS["normal"])
        out: list[dict] = []
        seen = set()
        ups_now: list = []        # shu kadrdagi tik odamlar ramkalari (yo'qolgan trek kutayotgan signalni bekor qilish)
        for p in tracked:
            tid = int(p.track_id)
            seen.add(tid)
            box = np.asarray(p.box, dtype=np.float32)
            kpts = np.asarray(p.kpts, dtype=np.float32)
            # Kuzatuvchi o'sha ID ni qaytardi (bir-ikki kadr ko'rinmagan) — o'sha odam, holati (kutayotgan signal ham)
            # davom etadi: tik tursa signal bekor bo'ladi
            st = self.tracks.get(tid) or self.lost.pop(tid, None)
            if st is None:
                st = self._inherit(tid, box, kpts, t, frame_h, cfg["hip"]) or TrackSt(tid, t, t, box)
            self.tracks[tid] = st
            self._observe(st, box, kpts, t, frame_h, cfg, fall_on, lying_on, lying_sec, ups_now, out)
        for p in loose or []:
            box = np.asarray(p.box, dtype=np.float32)
            cx, cy = _center(box)
            best, bd = None, 1e18
            for lid, st in self.lost.items():
                if not 0 < t - st.last_t <= LOST_KEEP:
                    continue
                lx, ly = _center(st.box)
                d = math.hypot(cx - lx, cy - ly)
                if d <= 0.6 * max(st.h_ref or st.h_max, float(box[3] - box[1]), float(box[2] - box[0])) and d < bd:
                    best, bd = lid, d
            if best is None:
                continue
            st = self.lost.pop(best)
            self.tracks[st.tid] = st
            seen.add(st.tid)
            self._observe(st, box, np.asarray(p.kpts, dtype=np.float32), t, frame_h, cfg, fall_on, lying_on,
                          lying_sec, ups_now, out)
        # Yo'qolgan treklar: kutayotgan yiqilish signali saqlanadi (yerda yotgan odamni detektor ko'pincha topmaydi)
        for tid in [k for k in self.tracks if k not in seen]:
            st = self.tracks.pop(tid)
            if cfg["collapse"] and st.pending is None and fall_on:
                self._collapse(st, t)
            self.lost[tid] = st
        for tid, st in list(self.lost.items()):
            if t - st.last_t > LOST_KEEP and st.pending is None:
                self.lost.pop(tid, None)
        # Kutayotgan signallar
        for st in list(self.tracks.values()) + list(self.lost.values()):
            pd = st.pending
            if pd is None:
                continue
            if st.tid not in seen and any(
                    _feet_near(b, pd["box"], pd["h_ref"]) and classify(b, k, pd["h_ref"], frame_h, cfg["hip"]).state == UP
                    for b, k in ups_now):
                st.pending = None                # o'sha joyda (yiqilgan odam bo'yida) tik odam paydo bo'ldi — turib ketgan
                continue
            if t >= pd["deadline"]:
                st.pending = None
                st.fall_alert_t = t
                st.lying_alerted = True          # yiqilish signali bor — "yotibdi" takrorlanmaydi
                self.spot_alerts.append((t, pd["box"].copy()))
                out.append(self._fall_alert(st, pd, t, lost=st.tid not in seen))
        self.spot_alerts = [(tt, b) for tt, b in self.spot_alerts if t - tt <= FALL_SUPPRESS_LYING]
        return out

    def _observe(self, st: TrackSt, box, kpts, t: float, frame_h: int, cfg: dict, fall_on: bool, lying_on: bool,
                 lying_sec: float, ups_now: list, out: list) -> None:
        h = float(box[3] - box[1])
        skh = skeleton_height(kpts)
        st.h_max = max(st.h_max, h)
        if skh > 0:
            st.skel.append((t, skh))
        st.h_ref = self._h_ref(st, t)
        pose = classify(box, kpts, st.h_ref, frame_h, cfg["hip"])
        if pose.state == UP:
            st.ups.append((t, h))
            st.h_ref = max(st.h_ref, h)
            ups_now.append((box, kpts))
        cx, cy = _center(box)
        st.hist.append((t, pose.state, cx, cy))
        keep = max(lying_sec, 30.0) + 15.0       # "yotibdi" oynasi to'liq sig'sin (600 s gacha), undan eskisi kerak emas
        while st.hist and st.hist[0][0] < t - keep:
            st.hist.popleft()
        prev_up_t = st.last_up_t
        st.box, st.last_t, st.kpts = box, t, kpts
        # --- yiqilish
        if pose.state == UP:
            st.last_up_t, st.last_up_h = t, h
            st.down_since = st.floor_t = None
            st.lying_alerted = False
            st.pending = None                    # turib ketdi (yoki hech yiqilmagan) — kutayotgan signal bekor
        else:
            if (st.floor_t is None and pose.hip_low is not None and not pose.truncated
                    and pose.hip_low <= cfg["hip"]):
                st.floor_t = t
        if pose.state == DOWN:
            if st.down_since is None:
                st.down_since = t
            # Yiqilish: tik → yotgan ≤ 2 s; yoki "cho'kib yiqilish": tik → son yerda ≤ 2 s, keyin ≤ 1,5 s ichida yotdi va
            # hammasi ≤ 2,5 s (polga sekin o'tirib, keyin yotgan odam — yiqilish emas, u "odam yotibdi" qoidasida)
            quick = 0 <= t - prev_up_t <= FALL_WINDOW or (
                st.floor_t is not None and 0 <= st.floor_t - prev_up_t <= FALL_WINDOW
                and t - st.floor_t <= COLLAPSE_LIE and t - prev_up_t <= COLLAPSE_TOTAL)
            if (fall_on and st.pending is None and quick
                    and t - st.fall_alert_t > SPOT_COOLDOWN and not self._recent_spot(t, box)):
                st.pending = {"t_down": t, "t_up": prev_up_t, "deadline": t + FALL_CONFIRM, "box": box.copy(),
                              "pose": pose, "h_ref": st.h_ref}
        st.last_state = pose.state
        # --- yotibdi
        if lying_on and not st.lying_alerted and st.down_since is not None and t - st.down_since >= lying_sec:
            a = self._lying_check(st, t, lying_sec)
            if a is not None:
                st.lying_alerted = True
                out.append(a)

    def flush(self, t: float) -> list[dict]:
        """Jim kadr (model ishlamadi) yoki video oxiri: muddati o'tgan kutayotgan signallar."""
        out = []
        for st in list(self.tracks.values()) + list(self.lost.values()):
            pd = st.pending
            if pd is not None and t >= pd["deadline"]:
                st.pending = None
                st.fall_alert_t = t
                st.lying_alerted = True
                self.spot_alerts.append((t, pd["box"].copy()))
                out.append(self._fall_alert(st, pd, t, lost=True))
        return out

    def _recent_spot(self, t: float, box) -> bool:
        cx, cy = _center(box)
        for tt, b in self.spot_alerts:
            if 0 <= t - tt <= SPOT_COOLDOWN and b[0] - 20 <= cx <= b[2] + 20 and b[1] - 20 <= cy <= b[3] + 20:
                return True
        return False

    def _collapse(self, st: TrackSt, t: float) -> None:
        """Sezgir rejim: odam tik turgan (≤ 1,2 s oldin), ramka keskin pasaygan (≤ 0,65·H, tepasi ≥ 0,3·H tushgan) va
        shu zahoti ko'rinmay qoldi — yerga yiqilgan bo'lishi mumkin. Do'konda polka orqasiga cho'kkalash ham shunday
        ko'rinadi — shuning uchun faqat "Sezgirlik: yuqori" da."""
        if not 0 <= st.last_t - st.last_up_t <= 1.2 or st.last_up_h <= 0:
            return
        h = float(st.box[3] - st.box[1])
        if h > 0.65 * st.last_up_h:
            return
        tops = [(tt, cy) for tt, s, cx, cy in st.hist if s == UP and st.last_up_t - 0.1 <= tt <= st.last_up_t]
        top_up = (tops[-1][1] - st.last_up_h / 2) if tops else None
        if top_up is not None and float(st.box[1]) - top_up < 0.3 * st.last_up_h:
            return
        st.pending = {"t_down": st.last_t, "t_up": st.last_up_t, "deadline": st.last_t + FALL_CONFIRM,
                      "box": st.box.copy(), "pose": None, "h_ref": st.h_ref or st.last_up_h, "collapse": True}

    def _fall_alert(self, st: TrackSt, pd: dict, t: float, lost: bool) -> dict:
        dt = max(0.0, pd["t_down"] - pd["t_up"])
        reasons = [f"Odam {dt:.1f} s ichida tik holatdan yerga tushdi" if not pd.get("collapse")
                   else "Odam keskin pastga tushdi va ko'rinmay qoldi (sezgir rejim)"]
        ps = pd.get("pose")
        if ps is not None:
            reasons.append(f"Yerda: ramka bo'yi {ps.hr * 100:.0f}% (tik bo'yidan), son pastda"
                           + (f", gavda {ps.ang:.0f}° yotiq" if ps.ang is not None and ps.ang >= 45 else ""))
        reasons.append(f"{FALL_CONFIRM:.0f} s ichida o'rnidan turmadi" + (" (detektor yerdagi odamni yo'qotdi)" if lost
                                                                       else ""))
        reasons.append("Darhol borib tekshiring — yordam kerak bo'lishi mumkin")
        return _alert("fall", st.tid, t, pd["box"], reasons, susp_start=pd["t_up"], susp_end=pd["t_down"])

    def _lying_check(self, st: TrackSt, t: float, lying_sec: float) -> dict | None:
        win = [x for x in st.hist if t - lying_sec <= x[0] <= t]
        if len(win) < 3:
            return None
        if any(s == UP for _, s, _, _ in win):
            return None
        downs = [x for x in win if x[1] == DOWN]
        if len(downs) / len(win) < LYING_DOWN_FRAC or downs[-1][0] < t - 3.0:
            return None
        ts = [x[0] for x in win]
        if max(np.diff(ts), default=0) > LYING_GAP or ts[0] - (t - lying_sec) > LYING_GAP:
            return None
        if self._recent_fall(t, st.box) or self._recent_lying(t, st.box):
            return None
        xs, ys = [x[2] for x in downs], [x[3] for x in downs]
        move = max(max(xs) - min(xs), max(ys) - min(ys)) / max(st.h_ref, 1.0)
        if move > LYING_MOVE:
            return None
        reasons = [f"Odam {t - st.down_since:.0f} s dan beri yerda yotibdi va deyarli qimirlamayapti",
                   "Yiqilish lahzasi ko'rinmagan bo'lishi mumkin (sekin yotib qolgan, kamera ko'rmagan joyda yiqilgan)",
                   "Borib tekshiring — yordam kerak bo'lishi mumkin"]
        self.lying_alerts.append((t, st.box.copy()))
        return _alert("lying", st.tid, t, st.box, reasons, susp_start=st.down_since, susp_end=t)

    def _recent_lying(self, t: float, box) -> bool:
        """O'sha joyda yaqinda "yotibdi" signali bo'lgan — detektor yotgan odamni yo'qotib, qayta topganda takrorlanmasin."""
        self.lying_alerts = [(tt, b) for tt, b in self.lying_alerts if 0 <= t - tt <= LYING_REPEAT]
        cx, cy = _center(box)
        for tt, b in self.lying_alerts:
            m = 0.5 * max(float(b[3] - b[1]), float(b[2] - b[0]))
            if b[0] - m <= cx <= b[2] + m and b[1] - m <= cy <= b[3] + m:
                return True
        return False

    def _recent_fall(self, t: float, box) -> bool:
        cx, cy = _center(box)
        for tt, b in self.spot_alerts:
            m = 0.5 * max(float(b[3] - b[1]), float(b[2] - b[0]))
            if 0 <= t - tt <= FALL_SUPPRESS_LYING and b[0] - m <= cx <= b[2] + m and b[1] - m <= cy <= b[3] + m:
                return True
        return False


def _alert(kind: str, track_id: int, t: float, box, reasons: list[str], susp_start: float | None = None,
           susp_end: float | None = None, extra: list | None = None) -> dict:
    if kind != "fall" and susp_start is not None:
        # Uzoq davom etgan holat (yotibdi, to'silgan, gavjum): davomiyligi sabablarda; klipdagi "lahza" belgisi esa
        # signalning o'zi atrofida (aks holda butun klip "shubhali" bo'yalardi)
        susp_start = max(float(susp_start), float(t) - 8.0)
    return {"track_id": int(track_id), "t": t, "score": 100.0, "level": "yuqori", "kind": kind,
            "kind_label": KIND_LABELS[kind], "side": None, "reasons": reasons,
            "breakdown": extra if extra is not None else [[KIND_LABELS[kind], 100]], "ml_prob": None,
            "box": None if box is None else [float(v) for v in box],
            "susp_start": float(t if susp_start is None else susp_start),
            "susp_end": float(t if susp_end is None else susp_end), "pose_seq": []}


# Bir nechta kamera bir hodisani ko'rsa (bitta kirgan odam — 4 kamera) — bitta signal
STORE_NIGHT_SEC = 120.0
_store_last: dict[str, float] = {}
_store_lock = threading.Lock()


def store_once(kind: str, t: float, window: float) -> bool:
    """Do'kon bo'yicha: shu tur uchun oxirgi `window` soniyada signal bo'lmagan bo'lsa True (va belgilaydi)."""
    with _store_lock:
        if 0 <= t - _store_last.get(kind, -1e18) < window:
            return False
        _store_last[kind] = t
        return True


# ============================================================================= tungi kirish
def _parse_hours(spec: str) -> tuple[int, int] | None:
    spec = (spec or "").strip().replace(" ", "")
    if "-" not in spec:
        return None
    try:
        a, b = spec.split("-", 1)
        ah, am = (int(v) for v in a.split(":"))
        bh, bm = (int(v) for v in b.split(":"))
    except ValueError:
        return None
    if not (0 <= ah < 24 and 0 <= bh < 24 and 0 <= am < 60 and 0 <= bm < 60):
        return None
    s_, e_ = ah * 60 + am, bh * 60 + bm
    return None if s_ == e_ else (s_, e_)


def _in(cur: int, rng: tuple[int, int]) -> bool:
    s_, e_ = rng
    return s_ <= cur < e_ if s_ < e_ else (cur >= s_ or cur < e_)


def night_schedule(s: dict | None = None) -> dict:
    """Tungi qoida jadvali: {"mode": "own"|"work"|None, "text"}."""
    s = s if s is not None else db.get_settings()
    own = _parse_hours(s.get("safety_night_hours", ""))
    if own:
        return {"mode": "own", "range": own, "text": str(s.get("safety_night_hours")).replace(" ", "")}
    work = _parse_hours(s.get("work_hours", ""))
    if work:
        g = int(_num(s, "safety_night_grace_min"))
        return {"mode": "work", "range": work, "grace": g,
                "text": f"ish vaqtidan ({s.get('work_hours')}) tashqari, yopilgandan keyin va ochilishdan oldin "
                        f"{g} daqiqa hisobga olinmaydi"}
    return {"mode": None, "text": "jadval yo'q: Tashriflar sahifasida ish vaqtini yoki shu yerda o'z jadvalini kiriting"}


def is_night(t: float, sched: dict) -> bool:
    if not sched.get("mode"):
        return False
    lt = time.localtime(t)
    cur = lt.tm_hour * 60 + lt.tm_min
    if sched["mode"] == "own":
        return _in(cur, sched["range"])
    s_, e_ = sched["range"]
    g = int(sched.get("grace", 0))
    # Ish vaqtini ikki tomonga g daqiqa kengaytiramiz (yopilgandan keyin yig'ishtirish, ochilishdan oldin kelish)
    ext = ((s_ - g) % 1440, (e_ + g) % 1440)
    if (e_ - s_) % 1440 + 2 * g >= 1440:
        return False
    return not _in(cur, ext)


# ============================================================================= chiqish zonasi
def exit_ref_path(zone_id: int):
    from ..config import DATA_DIR

    d = DATA_DIR / "safety"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"exit_{int(zone_id)}.png"


def _zone_crop(frame: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, tuple] | None:
    """Zona atrofidagi kulrang qism (EXIT_W enida) va ko'pburchak niqobi."""
    h, w = frame.shape[:2]
    p = np.asarray(pts, dtype=np.float32) * np.array([w, h], dtype=np.float32)
    x0, y0 = max(int(p[:, 0].min()), 0), max(int(p[:, 1].min()), 0)
    x1, y1 = min(int(math.ceil(p[:, 0].max())), w), min(int(math.ceil(p[:, 1].max())), h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = frame[y0:y1, x0:x1]
    s = EXIT_W / max(x1 - x0, 1)
    cw, ch = EXIT_W, max(EXIT_CELL * 2, int(round((y1 - y0) * s)))
    g = cv2.cvtColor(cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    mask = np.zeros((ch, cw), dtype=np.uint8)
    q = (p - np.array([x0, y0], dtype=np.float32)) * np.array([cw / (x1 - x0), ch / (y1 - y0)], dtype=np.float32)
    cv2.fillPoly(mask, [np.round(q).astype(np.int32)], 1)
    return g, mask.astype(bool), (x0, y0, x1, y1)


def exit_compare(ref: np.ndarray, cur: np.ndarray, mask: np.ndarray) -> dict:
    """Katakchalar bo'yicha: o'zgargan katakchalar ulushi (0..1). Yorug'lik farqi o'rtacha yorqinlik bilan tekislanadi;
    farq juda katta bo'lsa (chiroq o'chgan) — {"ok": False}."""
    if ref.shape != cur.shape:
        cur = cv2.resize(cur, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
    m_ref = float(ref[mask].mean()) if mask.any() else float(ref.mean())
    m_cur = float(cur[mask].mean()) if mask.any() else float(cur.mean())
    if abs(m_ref - m_cur) > EXIT_LIGHT or m_cur < 20:
        return {"ok": False, "why": "yorug'lik juda farq qiladi", "light": round(m_cur - m_ref, 1)}
    cur_n = np.clip(cur.astype(np.float32) * (m_ref / max(m_cur, 1.0)), 0, 255)
    e_ref = cv2.Canny(ref, 50, 150) > 0
    e_cur = cv2.Canny(cur_n.astype(np.uint8), 50, 150) > 0
    diff = np.abs(cur_n - ref.astype(np.float32))
    H, W = ref.shape
    n = changed = 0
    for y in range(0, H - EXIT_CELL + 1, EXIT_CELL):
        for x in range(0, W - EXIT_CELL + 1, EXIT_CELL):
            mk = mask[y:y + EXIT_CELL, x:x + EXIT_CELL]
            if mk.mean() < 0.5:
                continue
            n += 1
            d = float(diff[y:y + EXIT_CELL, x:x + EXIT_CELL][mk].mean())
            e = abs(float(e_cur[y:y + EXIT_CELL, x:x + EXIT_CELL][mk].mean())
                    - float(e_ref[y:y + EXIT_CELL, x:x + EXIT_CELL][mk].mean()))
            if d > EXIT_DIFF or e > EXIT_EDGE:
                changed += 1
    return {"ok": n > 0, "frac": changed / n if n else 0.0, "cells": n, "light": round(m_cur - m_ref, 1)}


def remember_exit(zone: dict, frame: np.ndarray, persons: list | None = None) -> dict:
    """"Bo'sh holatini eslab qolish": zona atrofi rasmi diskka, ma'lumoti `safety_exits` ga."""
    from . import zones as zones_mod

    pts = zones_mod.parse_points(zone["points"], zone["kind"])
    if pts is None:
        raise ValueError("zona nuqtalari noto'g'ri")
    res = _zone_crop(frame, pts)
    if res is None:
        raise ValueError("zona juda kichik")
    g, mask, (x0, y0, x1, y1) = res
    for b in persons or []:
        if b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0:
            raise ValueError("zonada odam turibdi — u ketgach qayta bosing")
    path = exit_ref_path(zone["id"])
    ok, buf = cv2.imencode(".png", g)
    if not ok:
        raise ValueError("rasmni saqlab bo'lmadi")
    path.write_bytes(buf.tobytes())            # cv2.imwrite Windows'da lotin bo'lmagan yo'lda jim xato qilardi
    h, w = frame.shape[:2]
    db.execute("INSERT INTO safety_exits(zone_id, source_id, img, points, frame_w, frame_h, mean, created_at) "
               "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(zone_id) DO UPDATE SET source_id=excluded.source_id, img=excluded.img, "
               "points=excluded.points, frame_w=excluded.frame_w, frame_h=excluded.frame_h, mean=excluded.mean, "
               "created_at=excluded.created_at",
               (int(zone["id"]), zone.get("source_id"), path.name, json.dumps(np.round(pts, 4).tolist()), w, h,
                float(g[mask].mean()) if mask.any() else float(g.mean()), time.time()))
    forget_state(int(zone["id"]))
    return {"zone_id": int(zone["id"]), "created_at": time.time()}


def forget_state(zone_id: int) -> None:
    """Eslab qolingan rasm o'zgardi: kesh, panel holati va kameralardagi hisob (EXIT_GEN orqali) yangilanadi."""
    _exit_cache.pop(int(zone_id), None)
    EXIT_STATE.pop(int(zone_id), None)
    EXIT_GEN[int(zone_id)] = EXIT_GEN.get(int(zone_id), 0) + 1


_exit_cache: dict[int, tuple] = {}
EXIT_GEN: dict[int, int] = {}           # zona -> "bo'sh holati" versiyasi (eslab qolish/o'chirishda oshadi)
EXIT_STATE: dict[int, dict] = {}        # zona -> joriy holat (panel uchun): frac, blocked_since, checked_at, why


def _exit_ref(zone) -> np.ndarray | None:
    """Eslab qolingan rasm (zona nuqtalari o'zgargan bo'lsa — eskirgan, None)."""
    hit = _exit_cache.get(zone.id)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    r = db.row("SELECT * FROM safety_exits WHERE zone_id=?", (zone.id,))
    ref = None
    if r:
        try:
            same = np.allclose(np.asarray(json.loads(r["points"]), dtype=np.float32), np.round(zone.pts, 4), atol=2e-3)
        except (TypeError, ValueError):
            same = False
        p = exit_ref_path(zone.id)
        if same and p.exists():
            ref = cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    _exit_cache[zone.id] = (time.time(), ref)
    return ref


# ============================================================================= olov (tajriba)
def fire_mask(frame: np.ndarray, ignore: np.ndarray | None = None) -> np.ndarray:
    """Olov rangidagi piksellar (R yuqori, R ≥ G > B, R − B katta) — kichraytirilgan kadrda."""
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (FIRE_W, max(1, int(round(h * FIRE_W / w)))), interpolation=cv2.INTER_AREA)
    b, g, r = [small[:, :, i].astype(np.int16) for i in range(3)]
    m = (r > 190) & (g > 100) & (r >= g) & (g > b) & (r - b > 70)
    if ignore is not None and ignore.shape == m.shape:
        m &= ~ignore
    return m


# ============================================================================= kamera monitori
class SafetyMonitor:
    """Bitta kamera uchun barcha xavfsizlik qoidalari. Processor.step() har qadamda chaqiradi (jim kadrda ham)."""

    def __init__(self, source_id: int | None, source_name: str = "", background: bool = False):
        self.source_id = source_id
        self.source_name = source_name
        self.background = background
        self.falls = FallDetector()
        self.last: dict[str, float] = {}               # tur -> oxirgi signal vaqti
        self.night_first: float | None = None
        self.night_seen: float = -1e9
        self.night_hits = 0
        self.night_alerted = False
        self.crowd_over: float | None = None
        self.crowd_below: float | None = None
        self.crowd_alerted = False
        self.crowd_peak = 0
        self.exit_t = -1e9
        self.exits: dict[int, dict] = {}
        self.fire_t = -1e9
        self.fire_prev: np.ndarray | None = None
        self.fire_hist: deque = deque(maxlen=2000)      # (t, hit, frac, box)
        self.fire_since: float | None = None
        self.fire_last_hit = -1e9
        self.persons_now = 0
        self.person_boxes: list = []
        self._err_t = 0.0
        self.cpu = 0.0
        self.n = 0

    # ------------------------------------------------------------------
    def update(self, tracked: list, t: float, w: int, h: int, frame: np.ndarray | None, zones=None,
               status: dict | None = None, detected: bool = True, loose: list | None = None) -> list[dict]:
        t0 = time.perf_counter()
        try:
            return self._update(tracked, t, w, h, frame, zones, detected, loose)
        except Exception:  # noqa: BLE001 — xavfsizlik qoidasidagi xato o'g'irlik tahlilini to'xtatmasin
            if time.time() - self._err_t > 60:
                self._err_t = time.time()
                log.exception("Xavfsizlik qoidalarida xato (%s)", self.source_name)
            return []
        finally:
            self.cpu += time.perf_counter() - t0
            self.n += 1

    def _update(self, tracked, t, w, h, frame, zones, detected, loose=None) -> list[dict]:
        s = db.get_settings()
        out: list[dict] = []
        if detected:
            self.persons_now = len(tracked)
            self.person_boxes = [np.asarray(p.box, dtype=np.float32) for p in tracked]
            out += self.falls.update(tracked, t, h, s.get("safety_fall_sens", "normal"), _num(s, "safety_lying_sec"),
                                     fall_on=_on(s, "safety_fall"), lying_on=_on(s, "safety_lying"), loose=loose)
            if not self.background and _on(s, "safety_night"):
                out += self._night(tracked, t, s)
            if _on(s, "safety_crowd") and _num(s, "safety_crowd_cam") > 0:
                out += self._crowd(tracked, t, s)
        else:
            out += self.falls.flush(t)
        if frame is not None and not self.background and _on(s, "safety_blocked") and zones and zones.of(EXIT_KIND):
            out += self._exits(frame, t, zones, s)
        if frame is not None and _on(s, "safety_fire"):
            out += self._fire(frame, t, zones, s)
        return out

    def _cool(self, kind: str, t: float, s: dict) -> bool:
        return t - self.last.get(kind, -1e18) >= _num(s, "safety_cooldown_min") * 60

    # ------------------------------------------------------------------ tungi kirish
    def _night(self, tracked, t, s) -> list[dict]:
        sched = night_schedule(s)
        if not tracked:
            if self.night_first is not None and t - self.night_seen > NIGHT_END_SEC:
                self.night_first, self.night_hits, self.night_alerted = None, 0, False
            return []
        if not is_night(t, sched):
            self.night_first, self.night_hits, self.night_alerted = None, 0, False
            return []
        if self.night_first is None or t - self.night_seen > NIGHT_END_SEC:
            self.night_first, self.night_hits, self.night_alerted = t, 0, False
        self.night_seen = t
        self.night_hits += 1
        if self.night_alerted or self.night_hits < 3 or t - self.night_first < NIGHT_MIN_SEC or not self._cool("night", t, s):
            return []
        self.night_alerted = True
        if not store_once("night", t, STORE_NIGHT_SEC):
            return []                          # o'sha kirishni boshqa kamera allaqachon xabar qildi (bitta SMS yetarli)
        self.last["night"] = t
        big = max(tracked, key=lambda p: float((p.box[2] - p.box[0]) * (p.box[3] - p.box[1])))
        n = len(tracked)
        reasons = [f"Ish vaqtidan tashqarida kamerada {n} ta odam ({time.strftime('%H:%M', time.localtime(t))})",
                   f"Jadval: {sched['text']}",
                   "Xodim bo'lsa — ❌ bosing; begona bo'lsa — darhol choralar ko'ring"]
        return [_alert("night", big.track_id, t, big.box, reasons, susp_start=self.night_first, susp_end=t)]

    # ------------------------------------------------------------------ gavjumlik (kadr)
    def _crowd(self, tracked, t, s) -> list[dict]:
        thr = int(_num(s, "safety_crowd_cam"))
        n = len(tracked)
        if n > thr:
            self.crowd_below = None
            if self.crowd_over is None:
                self.crowd_over, self.crowd_peak = t, n
            self.crowd_peak = max(self.crowd_peak, n)
        else:
            if self.crowd_below is None:
                self.crowd_below = t
            if self.crowd_over is not None and t - self.crowd_below > CROWD_DIP_SEC:
                self.crowd_over = None
            if self.crowd_alerted and t - self.crowd_below > CROWD_REARM_SEC:
                self.crowd_alerted = False
        dur = _num(s, "safety_crowd_sec")
        if self.crowd_over is None or self.crowd_alerted or t - self.crowd_over < dur or not self._cool("crowd", t, s):
            return []
        self.crowd_alerted = True
        self.last["crowd"] = t
        reasons = [f"Kadrda {n} ta odam (chegara {thr}, eng ko'pi {self.crowd_peak}) — {t - self.crowd_over:.0f} s dan beri",
                   "Odamlar oqimini boshqaring: qo'shimcha kassa, kirishni vaqtincha cheklash"]
        return [_alert("crowd", -1, t, None, reasons, susp_start=self.crowd_over, susp_end=t)]

    # ------------------------------------------------------------------ to'silgan chiqish
    def _exits(self, frame, t, zones, s) -> list[dict]:
        if t - self.exit_t < EXIT_EVERY:
            return []
        self.exit_t = t
        from .counting import in_work_hours

        out = []
        working = in_work_hours(t, s.get("work_hours", ""))
        thr = _num(s, "safety_blocked_frac")
        need = _num(s, "safety_blocked_min") * 60
        for z in zones.of(EXIT_KIND):
            gen = EXIT_GEN.get(z.id, 0)
            st = self.exits.get(z.id)
            if st is None or st.get("gen") != gen:     # bo'sh holati qayta eslab qolindi / o'chirildi — hisob yangidan
                st = self.exits[z.id] = {"since": None, "alerted": False, "clear_t": None, "gen": gen}
            ref = _exit_ref(z)
            state = EXIT_STATE.setdefault(z.id, {})
            state.update(checked_at=t, source_id=self.source_id, name=z.name)
            if ref is None:
                st.update(since=None, alerted=False, clear_t=None)
                state.update(frac=None, why="bo'sh holati eslab qolinmagan", blocked_since=None)
                continue
            if not working:                            # kechasi qo'yilib, ertalabgacha olingan quti — signal emas
                st.update(since=None, alerted=False, clear_t=None)
                state.update(why="ish vaqtidan tashqari", blocked_since=None)
                continue
            res = _zone_crop(frame, z.pts)
            if res is None:
                continue
            g, mask, (x0, y0, x1, y1) = res
            if any(b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0 for b in self.person_boxes):
                state.update(why="zonada odam bor — kutilmoqda")
                continue
            if ref.shape != g.shape:
                mask = cv2.resize(mask.astype(np.uint8), (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            cmp_ = exit_compare(ref, g, mask)
            if not cmp_["ok"]:
                state.update(why=cmp_.get("why", "solishtirib bo'lmadi"))
                continue
            frac = cmp_["frac"]
            state.update(frac=round(frac, 3), why=None)
            if frac >= thr:
                st["clear_t"] = None
                if st["since"] is None:
                    st["since"] = t
            elif frac < 0.7 * thr:
                if st["clear_t"] is None:
                    st["clear_t"] = t
                if t - st["clear_t"] >= 30:        # 30 s bo'sh tursa — epizod tugadi
                    st["since"], st["alerted"] = None, False
            state["blocked_since"] = st["since"]
            if st["since"] is not None and not st["alerted"] and frac >= thr and t - st["since"] >= need:
                st["alerted"] = True
                box = [float(x0), float(y0), float(x1), float(y1)]
                reasons = [f"\"{z.name}\" zonasining {frac * 100:.0f}% qismi bo'sh holatidan farq qiladi "
                           f"(chegara {thr * 100:.0f}%)",
                           f"{(t - st['since']) / 60:.0f} daqiqadan beri to'silgan",
                           "Favqulodda chiqish yo'lini bo'shating (yong'in xavfsizligi talabi)"]
                out.append(_alert("blocked", -1, t, box, reasons, susp_start=st["since"], susp_end=t))
        return out

    # ------------------------------------------------------------------ olov (tajriba)
    def _fire(self, frame, t, zones, s) -> list[dict]:
        if t - self.fire_t < FIRE_EVERY:
            return []
        self.fire_t = t
        h, w = frame.shape[:2]
        sh = max(1, int(round(h * FIRE_W / w)))
        ignore = np.zeros((sh, FIRE_W), dtype=bool)
        if zones:
            m = zones.motion_mask(FIRE_W, sh)
            if m is not None:
                ignore |= ~m
        sc = FIRE_W / w
        for b in self.person_boxes:        # odamlar ustidagi rang (kiyim) hisobga olinmaydi
            x0, y0, x1, y1 = (int(max(0, b[0] * sc)), int(max(0, b[1] * sc)), int(min(FIRE_W, b[2] * sc + 1)),
                              int(min(sh, b[3] * sc + 1)))
            ignore[y0:y1, x0:x1] = True
        m = fire_mask(frame, ignore)
        frac = float(m.mean())
        flick = 0.0
        if self.fire_prev is not None and self.fire_prev.shape == m.shape:
            uni = int((m | self.fire_prev).sum())
            flick = int((m ^ self.fire_prev).sum()) / uni if uni else 0.0
        self.fire_prev = m
        hit = frac >= FIRE_MIN_FRAC and flick >= FIRE_FLICKER
        box = None
        if hit:
            ys, xs = np.nonzero(m)
            box = [float(xs.min() / sc), float(ys.min() / sc), float((xs.max() + 1) / sc), float((ys.max() + 1) / sc)]
        self.fire_hist.append((t, hit, frac, box))
        if hit and (self.fire_since is None or t - self.fire_last_hit > 2.0):
            self.fire_since = t                 # yangi epizod (2 s dan ko'p uzilishdan keyin)
        if hit:
            self.fire_last_hit = t
        need = _num(s, "safety_fire_sec")
        if not hit or self.fire_since is None or t - self.fire_since < need:
            return []
        win = [x for x in self.fire_hist if self.fire_since <= x[0] <= t]
        hits = [x for x in win if x[1]]
        if len(win) < 4 or len(hits) / len(win) < FIRE_HIT_FRAC or not self._cool("fire", t, s):
            return []
        self.last["fire"] = t
        reasons = [f"Olov rangidagi miltillovchi joy {need:.0f} s davomida (kadrning {max(x[2] for x in hits) * 100:.1f}%)",
                   "TAJRIBA rejimi: qizil/to'q sariq kiyim, lampa, ekran ham shunday ko'rinishi mumkin — kamerani "
                   "ko'rib tekshiring", "Tutun aniqlanmaydi — haqiqiy yong'in signalizatsiyasining o'rnini bosmaydi"]
        return [_alert("fire", -1, t, box, reasons, susp_start=self.fire_since, susp_end=t)]

    # ------------------------------------------------------------------
    def info(self) -> dict:
        return {"persons": self.persons_now, "crowd_since": self.crowd_over,
                "night_active": self.night_first is not None,
                "update_ms": round(self.cpu * 1000 / max(self.n, 1), 3)}


# ============================================================================= do'kon bo'yicha gavjumlik
class StoreCrowd:
    """"Hozir ichkarida" (sanash chizig'i) chegaradan ≥ safety_crowd_sec oshsa — signal eng ko'p odam ko'rgan kameraga
    (rasm va klip o'sha kameradan). Manager'ning kamera holati oqimidan 5 s da bir chaqiriladi."""

    def __init__(self):
        self.over: float | None = None
        self.below: float | None = None
        self.alerted = False
        self.last = -1e18
        self.peak = 0
        self.check_t = -1e9
        self.inside: int | None = None
        self.lock = threading.Lock()

    def check(self, manager, now: float | None = None, inside: int | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self.lock:
            if now - self.check_t < 9.5 and inside is None:
                return None
            self.check_t = now
            s = db.get_settings()
            thr = int(_num(s, "safety_crowd_inside"))
            if thr <= 0 or not _on(s, "safety_crowd"):
                self.over, self.alerted = None, False
                return None
            if inside is None:
                from .. import visits

                occ = visits.occupancy()
                if occ is None:
                    return None
                inside = int(occ["inside"])
            self.inside = inside
            if inside > thr:
                self.below = None
                if self.over is None:
                    self.over, self.peak = now, inside
                self.peak = max(self.peak, inside)
            else:
                if self.below is None:
                    self.below = now
                if self.over is not None and now - self.below > CROWD_DIP_SEC:
                    self.over = None
                if self.alerted and now - self.below > CROWD_REARM_SEC:
                    self.alerted = False
            if (self.over is None or self.alerted or now - self.over < _num(s, "safety_crowd_sec")
                    or now - self.last < _num(s, "safety_cooldown_min") * 60):
                return None
            self.alerted = True
            self.last = now
            reasons = [f"Do'konda hozir {inside} kishi (chegara {thr}, eng ko'pi {self.peak}) — "
                       f"{now - self.over:.0f} s dan beri", "Hisob: sanash chizig'i (kirdi − chiqdi)",
                       "Odamlar oqimini boshqaring: qo'shimcha kassa, kirishni vaqtincha cheklash"]
            alert = _alert("crowd", -1, now, None, reasons, susp_start=self.over, susp_end=now)
        if manager is not None:
            manager.inject_alert(alert)
        return alert


STORE_CROWD = StoreCrowd()


def store_check(manager) -> None:
    try:
        STORE_CROWD.check(manager)
    except Exception:  # noqa: BLE001
        log.exception("Do'kon gavjumligini tekshirishda xato")


# ============================================================================= panel / bot uchun
def today_counts(t0: float, t1: float | None = None) -> dict:
    t1 = t1 or time.time() + 60
    marks = ",".join("?" for _ in SAFETY_KINDS)
    rows = db.rows(f"SELECT kind, status, COUNT(*) AS n FROM events WHERE created_at>=? AND created_at<? AND job_id IS NULL "
                   f"AND kind IN ({marks}) GROUP BY kind, status", (t0, t1, *SAFETY_KINDS))
    out = {k: {"n": 0, "ok": 0, "no": 0} for k in SAFETY_KINDS}
    for r in rows:
        o = out[r["kind"]]
        o["n"] += r["n"]
        if r["status"] == "tasdiqlangan":
            o["ok"] += r["n"]
        elif r["status"] == "rad_etilgan":
            o["no"] += r["n"]
    return out


def status_text() -> str:
    """Telegram /xavfsizlik."""
    from html import escape

    lt = time.localtime()
    d0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    c = today_counts(d0)
    s = db.get_settings()
    lines = ["🛟 <b>Xavfsizlik — bugun</b>"]
    total = sum(v["n"] for v in c.values())
    if not total:
        lines.append("Bugun xavfsizlik signali bo'lmadi ✅")
    for k in SAFETY_KINDS:
        v = c[k]
        if v["n"]:
            lines.append(f"• {KIND_LABELS[k]}: <b>{v['n']}</b> (✅ {v['ok']}, ❌ {v['no']})")
    last = db.rows(f"SELECT id, kind, created_at, source_name, status FROM events WHERE job_id IS NULL AND kind IN "
                   f"({','.join('?' for _ in SAFETY_KINDS)}) ORDER BY id DESC LIMIT 5", SAFETY_KINDS)
    if last:
        lines += ["", "<b>Oxirgilari:</b>"]
        for e in last:
            lines.append(f"#{e['id']} · {time.strftime('%d.%m %H:%M', time.localtime(e['created_at']))} · "
                         f"{KIND_LABELS.get(e['kind'], e['kind'])} · {escape(str(e['source_name'] or ''))}")
    on = [KIND_LABELS[k] for k in SAFETY_KINDS if _on(s, f"safety_{k}")]
    lines += ["", "Yoqilgan qoidalar: " + (", ".join(on) if on else "yo'q")]
    sched = night_schedule(s)
    if _on(s, "safety_night"):
        lines.append("Tungi kirish: " + escape(sched["text"]))
    return "\n".join(lines)
