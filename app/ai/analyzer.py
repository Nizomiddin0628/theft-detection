"""Shubhali xatti-harakat tahlilchisi.

Har bir odam (track) uchun qo'llar holatini kuzatadi va quyidagi ketma-ketliklarni qidiradi:
  * qo'l faol holatda (polkaga uzatilgan / buyum ushlagan) → bel/cho'ntak zonasiga tushib, u yerda turib qoldi
  * qo'l faol holatda → qorin/ko'krak markaziga (kiyim ichiga) kirdi
  * qo'l → sumka/ryukzak ichiga kirdi
  * qo'l buyum bilan → kadrdan pastga chiqib ketdi (stol oldida turgan odam, veb-kamera)
Qo'shimcha dalillar: qo'lda buyum bo'lgani, buyum keyin ko'rinmay qolgani, atrofga qarash,
ML modeli ehtimolligi. Natija 0–100 ball va tushunarli sabablar ro'yxati.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .. import db
from ..ml.features import body_from_kpts, hand_state, window_features  # noqa: F401
from ..ml.model import BehaviorModel
from .engine import Obj, Person

ZONE_LABELS = {
    "pocket": "bel/cho'ntak sohasiga",
    "torso": "kiyim ichiga (qorin/ko'krak)",
    "bag": "sumka/ryukzak ichiga",
    "offscreen": "kadrdan pastga (cho'ntak yoki stol ostiga)",
}
KIND_LABELS = {
    "pocket": "Cho'ntakka solish",
    "torso": "Kiyim ichiga yashirish",
    "bag": "Sumkaga solish",
    "offscreen": "Qo'l kadrdan yashirildi",
}
BASE_POINTS = {"pocket": 35, "torso": 40, "bag": 40, "offscreen": 30}
SIDE_LABEL = {"L": "Chap", "R": "O'ng"}
MIN_WRIST_CONF = 0.45


@dataclass
class HandMem:
    last_active_t: float = -1e9
    last_reach_t: float = -1e9
    zone: str | None = None
    zone_enter_t: float = 0.0
    zone_from_active: bool = False
    zone_low_conf: bool = False
    hold_t: float = -1e9
    hold_name: str = ""
    hold_cls: int = -1
    last_vis_t: float = -1e9
    last_vis_yfrac: float = 0.0
    last_vis_vy: float = 0.0
    prev_y: float = float("nan")
    prev_t: float = 0.0
    fired_zone_t: float = -1e9
    zone_entry_conf: float = 0.0
    cand_cls: int = -1
    cand_center: np.ndarray = field(default_factory=lambda: np.zeros(2))
    cand_wrist: np.ndarray = field(default_factory=lambda: np.zeros(2))
    cand_t: float = -1e9
    carry: int = 0


@dataclass
class TrackMem:
    hist: deque = field(default_factory=lambda: deque(maxlen=120))
    hands: dict = field(default_factory=lambda: {"L": HandMem(), "R": HandMem()})
    last_alert_t: float = -1e9
    head_x: deque = field(default_factory=lambda: deque(maxlen=60))
    ml_prob: float | None = None
    ml_t: float = -1e9
    risk: float = 0.0
    last_seen: float = 0.0
    label: str = ""


def _expand(box: np.ndarray, frac: float) -> np.ndarray:
    w, h = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] - w * frac, box[1] - h * frac, box[2] + w * frac, box[3] + h * frac])


def _inside(pt: np.ndarray, box: np.ndarray) -> bool:
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def _box_iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Analyzer:
    def __init__(self, source_name: str = ""):
        self.tracks: dict[int, TrackMem] = {}
        self.source_name = source_name
        self.last_source_alert = -1e9

    # ------------------------------------------------------------------
    def _params(self) -> dict:
        s = db.get_settings()

        def f(k, d):
            try:
                return float(s.get(k, d))
            except (TypeError, ValueError):
                return d
        return {
            "threshold": f("threshold", 35),
            "dwell": f("dwell_sec", 0.3),
            "cooldown": f("cooldown_sec", 15),
            "use_ml": s.get("use_ml", "1") == "1",
            "ml_weight": f("ml_weight", 30),
            "sig": {k: s.get(f"signal_{k}", "1") == "1"
                    for k in ("pocket", "torso", "bag", "vanish", "looking", "offscreen")},
        }

    # ------------------------------------------------------------------
    def update(self, persons: list[Person], objects: list[Obj], t: float,
               frame_w: int, frame_h: int) -> tuple[list[dict], dict[int, dict]]:
        p = self._params()
        alerts: list[dict] = []
        status: dict[int, dict] = {}
        items = [o for o in objects if o.kind == "item"]
        bags = [o for o in objects if o.kind == "bag"]
        ml = BehaviorModel.get() if p["use_ml"] else None

        for person in persons:
            tid = person.track_id
            mem = self.tracks.setdefault(tid, TrackMem())
            mem.last_seen = t
            mem.hist.append((t, person.kpts.copy(), person.box.copy()))
            body = body_from_kpts(person.kpts, person.box)
            k = person.kpts
            good_quality = (k[5, 2] >= 0.4 and k[6, 2] >= 0.4 and
                            (person.box[3] - person.box[1]) >= frame_h * 0.12 and len(mem.hist) >= 3)
            if body is None or not good_quality:
                mem.risk *= 0.9
                status[tid] = {"risk": mem.risk, "label": "", "box": person.box}
                continue
            nose = body.norm[0]
            if not np.isnan(nose[0]):
                mem.head_x.append((t, float(nose[0])))

            # Odamga tegishli sumkalar
            pbox_big = _expand(person.box, 0.25)
            my_bags = [b for b in bags if _box_iou(b.box, pbox_big) > 0.02]

            candidates: list[tuple[int, str, str, list, list]] = []
            for side in ("L", "R"):
                hm: HandMem = mem.hands[side]
                hs = hand_state(body, side)
                wi = 9 if side == "L" else 10
                wpt = person.kpts[wi, :2]

                confident = hs["vis"] and hs["conf"] >= MIN_WRIST_CONF
                if confident:
                    # Qo'lda buyum bormi? Buyum qo'l bilan birga harakatlansagina "ushlangan" deb olinadi
                    # (polkadagi buyum yonidan qo'l o'tib ketsa hisoblanmaydi).
                    pad = body.scale * 0.15
                    parea = (person.box[2] - person.box[0]) * (person.box[3] - person.box[1])
                    near = []
                    for it in items:
                        b = it.box
                        if (b[2] - b[0]) * (b[3] - b[1]) > parea * 0.6:
                            continue
                        if b[0] - pad <= wpt[0] <= b[2] + pad and b[1] - pad <= wpt[1] <= b[3] + pad:
                            c = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
                            near.append((float(np.linalg.norm(c - wpt)), it, c))
                    if near:
                        _, it, c = min(near, key=lambda x: x[0])
                        if hm.cand_cls == it.cls and t - hm.cand_t <= 0.8:
                            d_wr = wpt - hm.cand_wrist
                            d_it = c - hm.cand_center
                            if (np.linalg.norm(d_wr) > 0.05 * body.scale and
                                    np.linalg.norm(d_it - d_wr) < 0.35 * body.scale):
                                hm.carry += 1
                        else:
                            hm.carry = 0
                        hm.cand_cls, hm.cand_center, hm.cand_wrist, hm.cand_t = it.cls, c, wpt.copy(), t
                        if hm.carry >= 2:
                            hm.hold_t, hm.hold_name, hm.hold_cls = t, it.name, it.cls
                    elif t - hm.cand_t > 0.8:
                        hm.carry = 0
                    if hs["active"]:
                        hm.last_active_t = t
                    if hs["reach"]:
                        hm.last_reach_t = t
                if hs["vis"]:
                    # Vertikal tezlik (kadrdan chiqib ketishni aniqlash uchun)
                    yfrac = float(wpt[1]) / max(frame_h, 1)
                    if not math.isnan(hm.prev_y) and t > hm.prev_t:
                        hm.last_vis_vy = (yfrac - hm.prev_y) / (t - hm.prev_t)
                    hm.prev_y, hm.prev_t = yfrac, t
                    hm.last_vis_t, hm.last_vis_yfrac = t, yfrac

                # Hozirgi zona
                zone = None
                if hs["vis"]:
                    in_bag = any(_inside(wpt, _expand(b.box, 0.15)) for b in my_bags)
                    if in_bag and p["sig"]["bag"]:
                        zone = "bag"
                    elif hs["torso"] and p["sig"]["torso"]:
                        other = hand_state(body, "R" if side == "L" else "L")
                        if not other["torso"]:  # qo'l qovushtirish emas
                            zone = "torso"
                    elif hs["pocket"] and p["sig"]["pocket"]:
                        zone = "pocket"
                else:
                    if hm.zone in ("pocket", "torso", "bag") and t - hm.last_vis_t <= 1.5:
                        # Qo'l zonada turib ko'rinmay qoldi — kiyim/sumka ichiga kirgan bo'lishi mumkin
                        zone = hm.zone
                        hm.zone_low_conf = True
                    else:
                        # Qo'l ko'rinmay qoldi: kadr pastidan chiqib ketdimi?
                        gone_down = (t - hm.last_vis_t <= 1.0 and
                                     (hm.last_vis_yfrac >= 0.80 or hm.last_vis_vy > 0.25) and
                                     person.box[3] >= frame_h * 0.90)
                        if gone_down and p["sig"]["offscreen"]:
                            zone = "offscreen"

                recent_active = (t - hm.last_active_t <= 1.6) or (t - hm.hold_t <= 2.0)
                if zone != hm.zone:
                    entering_ok = confident or zone == "offscreen"
                    hm.zone = zone
                    hm.zone_enter_t = t
                    hm.zone_from_active = bool(zone and entering_ok and recent_active and hm.last_active_t < t)
                    hm.zone_low_conf = False
                    hm.zone_entry_conf = hs["conf"] if hs["vis"] else 0.0
                # Kirishda qo'l aniq ko'ringan, keyin ko'rinishi keskin pasaygan bo'lsa — yashirilgan
                if zone and hm.zone_entry_conf >= 0.6 and (not hs["vis"] or hs["conf"] < 0.42):
                    hm.zone_low_conf = True

                if zone and hm.zone_from_active and t - hm.zone_enter_t >= p["dwell"] \
                        and hm.fired_zone_t < hm.zone_enter_t:
                    pts = BASE_POINTS[zone]
                    reasons = [f"{SIDE_LABEL[side]} qo'l faol holatdan {ZONE_LABELS[zone]} bordi "
                               f"({t - hm.zone_enter_t:.1f} s)"]
                    bd = [[KIND_LABELS[zone], pts]]
                    if zone in ("pocket",) and not body.hips_visible:
                        pts = int(pts * 0.7)
                        bd[0][1] = pts
                        reasons.append("Sonlar ko'rinmaydi — bel zonasi taxminiy")
                    if hm.zone_low_conf and zone in ("pocket", "torso"):
                        pts += 10
                        bd.append(["Qo'l yashirindi (ko'rinish pasaydi)", 10])
                        reasons.append("Qo'l kiyim orqasiga kirib, ko'rinishi pasaydi")
                    if t - hm.last_reach_t <= 2.5:
                        pts += 5
                        bd.append(["Oldin qo'l uzatilgan (polkaga)", 5])
                        reasons.append("Bundan oldin qo'l uzatilgan (polkadan olish)")
                    held_recent = t - hm.hold_t <= 4.0 and hm.hold_name
                    if held_recent:
                        pts += 15
                        bd.append([f"Qo'lda buyum bor edi: {hm.hold_name}", 15])
                        reasons.append(f"Qo'lda '{hm.hold_name}' ko'rindi")
                        in_hand_now = (t - hm.hold_t) < 0.2
                        near_now = any(o.cls == hm.hold_cls and
                                       _box_iou(o.box, _expand(person.box, 0.3)) > 0 for o in items)
                        if in_hand_now:
                            pts -= 15
                            bd.append(["Buyum hali qo'lda ko'rinmoqda", -15])
                            reasons.append("Lekin buyum hali qo'lda ko'rinib turibdi")
                        elif p["sig"]["vanish"] and not near_now:
                            pts += 20
                            bd.append(["Buyum keyin ko'rinmay qoldi", 20])
                            reasons.append(f"'{hm.hold_name}' keyin ko'rinmay qoldi")
                    candidates.append((pts, zone, side, reasons, bd))
                    hm.fired_zone_t = t

            # Atrofga qarash
            look_pts = 0
            if p["sig"]["looking"] and len(mem.head_x) >= 6:
                xs = [x for (tt, x) in mem.head_x if t - tt <= 5.0]
                turns = 0
                for i in range(2, len(xs)):
                    if (xs[i] - xs[i - 1]) * (xs[i - 1] - xs[i - 2]) < 0 and abs(xs[i] - xs[i - 1]) > 0.08:
                        turns += 1
                if turns >= 3:
                    look_pts = 10

            # ML ehtimolligi (sekundiga ~2 marta)
            if ml is not None and t - mem.ml_t >= 0.5 and len(mem.hist) >= 6:
                mem.ml_prob = ml.predict(list(mem.hist))
                mem.ml_t = t

            if candidates:
                candidates.sort(key=lambda c: c[0], reverse=True)
                pts, zone, side, reasons, bd = candidates[0]
                # Qarshi dalillar: yurib ketayotgan odam va orqa tomondan ko'rinish
                speed = self._speed(mem, t)
                if speed > 0.35:
                    pts -= 20
                    bd.append(["Odam yurib ketmoqda (oddiy harakat ehtimoli)", -20])
                    reasons.append("Odam bu paytda yurib ketayotgan edi (ishonch pasaytirildi)")
                if k[0, 2] < 0.3 and k[1, 2] < 0.3 and k[2, 2] < 0.3:
                    pts -= 10
                    bd.append(["Orqa tomondan ko'rinish — qo'llar yaxshi ko'rinmaydi", -10])
                    reasons.append("Odam orqa tomondan ko'rinmoqda (ishonch pasaytirildi)")
                if look_pts:
                    pts += look_pts
                    bd.append(["Atrofga qarash (bosh tez-tez burildi)", look_pts])
                    reasons.append("Bundan oldin atrofga qaragan")
                if ml is not None and mem.ml_prob is not None:
                    # 50% dan past ehtimollik ball qo'shmaydi; 100% da to'liq og'irlik
                    mlp = round(p["ml_weight"] * max(0.0, mem.ml_prob - 0.5) * 2)
                    if mlp:
                        pts += mlp
                        bd.append([f"AI model ehtimoli {mem.ml_prob:.0%}", mlp])
                    reasons.append(f"Xulq modeli: {mem.ml_prob:.0%} ehtimollik")
                score = float(max(0, min(100, pts)))
                mem.risk = max(mem.risk, score)
                if (score >= p["threshold"] and t - mem.last_alert_t >= p["cooldown"]
                        and t - self.last_source_alert >= 3.0):
                    mem.last_alert_t = t
                    self.last_source_alert = t
                    level = "yuqori" if score >= 75 else ("o'rta" if score >= 55 else "past")
                    alerts.append({
                        "track_id": tid, "t": t, "score": score, "level": level, "kind": zone,
                        "kind_label": KIND_LABELS[zone], "side": side, "reasons": reasons,
                        "breakdown": bd, "ml_prob": mem.ml_prob, "box": person.box.tolist(),
                        "pose_seq": [[float(a), k.tolist(), b.tolist()] for a, k, b in list(mem.hist)],
                    })
            else:
                mem.risk *= 0.93
            mem.label = (f"{mem.risk:.0f}" if mem.risk >= 10 else "")
            status[tid] = {"risk": mem.risk, "ml": mem.ml_prob, "box": person.box}

        # Eski izlarni tozalash
        for tid in [k for k, m in self.tracks.items() if t - m.last_seen > 10]:
            del self.tracks[tid]
        return alerts, status

    @staticmethod
    def _speed(mem: TrackMem, t: float) -> float:
        """Odamning harakat tezligi: ramka balandligi / soniya (oxirgi ~1 s)."""
        past = [h for h in mem.hist if t - 1.2 <= h[0] <= t - 0.6]
        if not past:
            return 0.0
        t0, _, b0 = past[0]
        _, _, b1 = mem.hist[-1]
        c0 = np.array([(b0[0] + b0[2]) / 2, (b0[1] + b0[3]) / 2])
        c1 = np.array([(b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2])
        hgt = max(float(b1[3] - b1[1]), 1.0)
        return float(np.linalg.norm(c1 - c0) / hgt / max(t - t0, 1e-3))

    def pose_window(self, track_id: int, t_from: float, t_to: float) -> list:
        mem = self.tracks.get(track_id)
        if not mem:
            return []
        return [[float(a), k.tolist(), b.tolist()] for a, k, b in mem.hist if t_from <= a <= t_to]


def now() -> float:
    return time.time()
