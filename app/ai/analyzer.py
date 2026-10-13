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
from .zones import EMPTY as NO_ZONES, ZoneSet

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
    "exit": "Chiqish tomon ketmoqda",
}
BASE_POINTS = {"pocket": 35, "torso": 40, "bag": 40, "offscreen": 30}
SIDE_LABEL = {"L": "Chap", "R": "O'ng"}
MIN_WRIST_CONF = 0.45

# Xaridorning o'z buyumlari: cho'ntakka solinsa ham o'g'irlik emas (odatda o'z telefoni)
PERSONAL_CLASSES = {67: "telefon"}
PERSONAL_PENALTY = 20
# Dwell'dan keyin shuncha soniya davomida ball qayta hisoblanadi (keyin paydo bo'lgan dalil ham hisobga olinadi)
EVAL_WINDOW = 2.0
# Boshqa odam ramkasi bilan ustma-ust tushish ulushi — bundan ko'p bo'lsa skelet aralashib ketadi
OCCLUSION_FRAC = 0.40
# Tana o'lchami keskin o'zgarsa (skelet boshqa odamga "sakrasa") kadr tashlab yuboriladi
SCALE_JUMP = (0.70, 1.45)
# Bir kamerada takroriy signal: shu vaqt ichida va shu joyda (IoU) bo'lsa — o'sha odam
DUP_SEC = 3.0
DUP_IOU = 0.30
# Kamera "faol" hisoblanadigan belgilar (worker shu bo'yicha kadr tezligi va buyum qidirishni tanlaydi)
ENGAGE_REACH_SEC = 1.5
ENGAGE_HOLD_SEC = 3.0
# Zonalar
STAFF_SEC = 2.0          # xodim zonasida shuncha turgan odam xodim deb belgilanadi
SHELF_SEC = 3.0          # qo'l polka zonasidan chiqqandan keyin shuncha vaqt "polkadan olgan" hisoblanadi
EXIT_MEMORY_SEC = 300.0  # signal/yashirishdan keyin shuncha vaqt ichida eshikka kelsa — "chiqib ketmoqda"
EXIT_HIDDEN_SCORE = 65   # signal bo'lmagan, lekin mahsulotni yashirgani ko'ringan odam eshikka kelsa


def _covered(pt: np.ndarray, regions: list | None) -> bool:
    """Nuqta shu kadrda buyum qidirilgan sohalardan biri ichidami (None — butun kadr qidirilgan)."""
    if regions is None:
        return True
    return any(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3] for r in regions)


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
    hold_box: np.ndarray | None = None      # qo'ldagi buyumning oxirgi ramkasi (ko'rsatish uchun)
    hold_wrist: np.ndarray | None = None    # o'sha paytdagi bilak joyi (ramka qo'l bilan birga suriladi)
    last_vis_t: float = -1e9
    last_vis_yfrac: float = 0.0
    last_vis_vy: float = 0.0
    prev_y: float = float("nan")
    prev_t: float = 0.0
    alerted_zone_t: float = -1e9
    entry_active_t: float = -1e9      # zonaga kirishdan oldin qo'l oxirgi marta faol bo'lgan vaqt
    zone_entry_conf: float = 0.0
    cand_cls: int = -1
    cand_center: np.ndarray = field(default_factory=lambda: np.zeros(2))
    cand_wrist: np.ndarray = field(default_factory=lambda: np.zeros(2))
    cand_t: float = -1e9
    carry: int = 0
    zone_check_t: float = -1e9        # zonada qo'l atrofi oxirgi marta qidirilgan vaqt
    zone_seen_t: float = -1e9         # zonaga kirgandan keyin buyum oxirgi marta ko'ringan vaqt
    last_shelf_t: float = -1e9        # qo'l oxirgi marta polka zonasida bo'lgan vaqt
    shelf_name: str = ""
    shelf_delta: int = 0              # o'sha polka zonasining sezgirligi (chegaraga qo'shiladi)


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
    scales: deque = field(default_factory=lambda: deque(maxlen=12))
    skipped: int = 0
    staff: bool = False               # xodim zonasida turgan — signal berilmaydi
    staff_enter_t: float = -1e9
    hidden_t: float = -1e9            # mahsulot yashirilgani birinchi ko'ringan vaqt (shu epizodda)
    hidden_last_t: float = -1e9
    in_door: bool = False             # oldingi kadrda eshik zonasida edimi
    door_enter_t: float = -1e9
    hidden_name: str = ""
    alert_score: float = 0.0          # shu odam uchun berilgan signallarning eng yuqori bali
    last_alert_kind: str = ""
    exit_alerted: bool = False


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


def _covered_frac(a: np.ndarray, others: list[np.ndarray]) -> float:
    """a ramkasining qancha qismi boshqa odamlar ramkasi bilan ustma-ust (eng kattasi)."""
    area = max(float((a[2] - a[0]) * (a[3] - a[1])), 1.0)
    best = 0.0
    for b in others:
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        if w > 0 and h > 0:
            best = max(best, float(w * h) / area)
    return best


class Analyzer:
    def __init__(self, source_name: str = ""):
        self.tracks: dict[int, TrackMem] = {}
        self.source_name = source_name
        self.recent_alerts: deque = deque(maxlen=20)   # (t, box) — takroriy signalni aniqlash uchun

    # ------------------------------------------------------------------
    def _params(self) -> dict:
        s = db.get_settings()

        def f(k, d):
            try:
                return float(s.get(k, d))
            except (TypeError, ValueError):
                return d
        return {
            "threshold": f("threshold", 60),
            "dwell": f("dwell_sec", 0.45),
            "cooldown": f("cooldown_sec", 15),
            "use_ml": s.get("use_ml", "1") == "1",
            "ml_weight": f("ml_weight", 10),
            "personal": s.get("personal_items", "1") == "1",
            "sig": {k: s.get(f"signal_{k}", "1") == "1"
                    for k in ("pocket", "torso", "bag", "vanish", "looking", "offscreen", "exit")},
        }

    def _is_duplicate(self, t: float, box: np.ndarray) -> bool:
        """Kuzatuvchi ID ni almashtirib yuborsa, o'sha odam uchun ikkinchi signal chiqmasin.
        Boshqa joydagi boshqa odamning signali esa bloklanmaydi."""
        for t0, b0 in self.recent_alerts:
            if t - t0 < DUP_SEC and _box_iou(box, b0) > DUP_IOU:
                return True
        return False

    @staticmethod
    def _reset_zones(mem: "TrackMem") -> None:
        for hm in mem.hands.values():
            hm.zone = None
            hm.zone_from_active = False

    # ------------------------------------------------------------------
    def update(self, persons: list[Person], objects: list[Obj], t: float,
               frame_w: int, frame_h: int, searched: list | None = None,
               zones: ZoneSet | None = None) -> tuple[list[dict], dict[int, dict]]:
        """Bitta kadr tahlili.

        searched — shu kadrda buyum qidirilgan sohalar (qo'l atrofi). None bo'lsa buyumlar butun kadrdan
        qidirilgan deb olinadi. Qidirilmagan qo'l uchun "buyum yo'q" degan xulosa chiqarilmaydi.
        zones — kamera zonalari (polka, kassa, eshik, xodim; e'tiborsiz zonadagilar bu yerga kelmaydi).
        """
        base = self._params()
        p = base
        zs = zones if zones is not None else NO_ZONES
        alerts: list[dict] = []
        status: dict[int, dict] = {}
        items = [o for o in objects if o.kind == "item"]
        bags = [o for o in objects if o.kind == "bag"]
        ml = BehaviorModel.get() if base["use_ml"] else None
        all_boxes = [pp.box for pp in persons]

        for pi, person in enumerate(persons):
            tid = person.track_id
            mem = self.tracks.setdefault(tid, TrackMem())
            mem.last_seen = t
            p = base
            foot = np.array([(person.box[0] + person.box[2]) / 2, person.box[3]], dtype=np.float32)
            if zs:
                # Xodim: xodimlar zonasida STAFF_SEC turgan odam — kadrdan chiqquncha xodim
                if zs.at("xodim", foot, frame_w, frame_h):
                    if mem.staff_enter_t < -1e8:
                        mem.staff_enter_t = t
                    if t - mem.staff_enter_t >= STAFF_SEC:
                        mem.staff = True
                else:
                    mem.staff_enter_t = -1e9
                if mem.staff:
                    mem.risk = 0.0
                    status[tid] = {"risk": 0.0, "label": "xodim", "box": person.box, "staff": True}
                    continue
                # Eshik: shubhali odam chiqish tomon ketmoqda (skelet sifati muhim emas)
                door = zs.at("eshik", foot, frame_w, frame_h)
                if door and not mem.in_door:
                    mem.door_enter_t = t
                mem.in_door = door is not None
                if door and not mem.exit_alerted and base["sig"]["exit"]:
                    ex = self._exit_alert(mem, person, t, door, base)
                    if ex:
                        alerts.append(ex)
                # Kassa: to'lov paytida sumkaga solish va qo'lni pastga tushirish — oddiy holat
                kassa = zs.at("kassa", foot, frame_w, frame_h)
                if kassa:
                    p = dict(base)
                    p["sig"] = dict(base["sig"], bag=False, offscreen=False)
                    p["threshold"] = base["threshold"] + kassa.threshold_delta
            body = body_from_kpts(person.kpts, person.box)
            k = person.kpts
            good_quality = (body is not None and k[5, 2] >= 0.4 and k[6, 2] >= 0.4 and
                            (person.box[3] - person.box[1]) >= frame_h * 0.12)
            # Odamlar bir-birini to'sganda pose model ularning nuqtalarini aralashtirib yuboradi
            # (bir odamning qo'li ikkinchisining tanasiga "tushadi") — bunday kadrlar tahlil qilinmaydi.
            occluded = _covered_frac(person.box, all_boxes[:pi] + all_boxes[pi + 1:]) > OCCLUSION_FRAC
            # Tana uzunligi (yelka→son) faqat ikkala son ko'ringanda solishtiriladi: sonlar ko'rinmay
            # qolganda uzunlik boshqa usulda taxmin qilinadi va bu "sakrash" emas
            jump = False
            if body is not None and body.hips_visible and body.shoulders_visible:
                if len(mem.scales) >= 4:
                    ratio = body.scale / max(float(np.median(mem.scales)), 1e-3)
                    jump = not (SCALE_JUMP[0] <= ratio <= SCALE_JUMP[1])
                mem.scales.append(body.scale)
            if not good_quality or occluded or jump:
                mem.risk *= 0.9
                mem.skipped += 1
                if occluded:
                    self._reset_zones(mem)
                status[tid] = {"risk": mem.risk, "label": "", "box": person.box}
                continue
            mem.hist.append((t, person.kpts.copy(), person.box.copy()))
            if len(mem.hist) < 3:
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
                searched_here = _covered(wpt, searched)
                if confident and searched_here:
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
                        if hm.hold_name and it.cls == hm.hold_cls and t - hm.hold_t <= ENGAGE_HOLD_SEC:
                            # Allaqachon "qo'lda" deb tasdiqlangan buyum hali ham qo'lda — muddatini yangilaymiz
                            hm.hold_t, hm.hold_box, hm.hold_wrist = t, it.box.copy(), wpt.copy()
                        if hm.cand_cls == it.cls and t - hm.cand_t <= 0.8:
                            d_wr = wpt - hm.cand_wrist
                            d_it = c - hm.cand_center
                            # Qo'l ham, buyum ham siljigan va birga siljigan bo'lishi kerak
                            # (polkada qimirlamay turgan buyum yonida qo'l biroz qimirlasa — bu ushlash emas)
                            if (np.linalg.norm(d_wr) > 0.05 * body.scale and
                                    np.linalg.norm(d_it) > 0.04 * body.scale and
                                    np.linalg.norm(d_it - d_wr) < 0.35 * body.scale):
                                hm.carry += 1
                        else:
                            hm.carry = 0
                        hm.cand_cls, hm.cand_center, hm.cand_wrist, hm.cand_t = it.cls, c, wpt.copy(), t
                        if hm.carry >= 2:
                            hm.hold_t, hm.hold_name, hm.hold_cls = t, it.name, it.cls
                            hm.hold_box, hm.hold_wrist = it.box.copy(), wpt.copy()
                    elif t - hm.cand_t > 0.8:
                        hm.carry = 0
                elif t - hm.cand_t > 0.8:
                    hm.carry = 0
                if confident:
                    if hs["active"]:
                        hm.last_active_t = t
                    if hs["reach"]:
                        hm.last_reach_t = t
                    shelf = zs.at("polka", wpt, frame_w, frame_h) if zs else None
                    if shelf:
                        # Qo'l polka zonasida — mahsulot olinyapti (past polkada ham, tepadan qaragan kamerada ham)
                        hm.last_shelf_t, hm.shelf_name, hm.shelf_delta = t, shelf.name, shelf.threshold_delta
                        hm.last_active_t = t
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
                    hm.entry_active_t = max(hm.last_active_t, hm.hold_t)
                    hm.zone_low_conf = False
                    hm.zone_entry_conf = hs["conf"] if hs["vis"] else 0.0
                    hm.zone_check_t = -1e9
                    hm.zone_seen_t = -1e9
                # Qo'l zonada va yaqinda buyum ushlagan bo'lsa: buyum hali ko'rinyaptimi?
                # (faqat shu kadrda qo'l/odam atrofi haqiqatan qidirilgan bo'lsa hisoblanadi)
                if zone and hm.hold_name and t - hm.hold_t <= 4.0:
                    if hs["vis"] and searched_here:
                        hm.zone_check_t = t
                    pb = _expand(person.box, 0.3)
                    if any(o.cls == hm.hold_cls and _box_iou(o.box, pb) > 0 and
                           _covered(np.array([(o.box[0] + o.box[2]) / 2, (o.box[1] + o.box[3]) / 2]), searched)
                           for o in items):
                        hm.zone_seen_t = t
                # Kirishda qo'l aniq ko'ringan, keyin ko'rinishi keskin pasaygan bo'lsa — yashirilgan
                if zone and hm.zone_entry_conf >= 0.6 and (not hs["vis"] or hs["conf"] < 0.42):
                    hm.zone_low_conf = True

                # Ball dwell lahzasida bir marta emas, keyingi EVAL_WINDOW soniya davomida har kadrda
                # qayta hisoblanadi: buyum biroz keyin yo'qolsa yoki model ehtimoli keyin oshsa ham ushlanadi.
                in_dwell = zone and p["dwell"] <= t - hm.zone_enter_t <= p["dwell"] + EVAL_WINDOW
                if in_dwell and hm.zone_from_active and hm.alerted_zone_t < hm.zone_enter_t:
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
                    if 0 <= t - hm.last_shelf_t <= SHELF_SEC:
                        pts += 10
                        bd.append([f"Qo'l polka zonasida bo'lgan ({hm.shelf_name})", 10])
                        reasons.append(f"Bundan oldin qo'l '{hm.shelf_name}' polka zonasiga kirgan")
                    elif t - hm.last_reach_t <= 2.5:
                        pts += 5
                        bd.append(["Oldin qo'l uzatilgan (polkaga)", 5])
                        reasons.append("Bundan oldin qo'l uzatilgan (polkadan olish)")
                    held_recent = t - hm.hold_t <= 4.0 and hm.hold_name
                    personal = held_recent and p["personal"] and hm.hold_cls in PERSONAL_CLASSES
                    if personal:
                        # O'z telefonini cho'ntakka qaytarish — do'kondagi eng ko'p uchraydigan oddiy harakat
                        pts -= PERSONAL_PENALTY
                        bd.append([f"Qo'lda {hm.hold_name} — odatda xaridorning o'z buyumi", -PERSONAL_PENALTY])
                        reasons.append(f"Qo'lda {hm.hold_name} bor edi — bu ko'pincha xaridorning o'z buyumi "
                                       f"(ishonch pasaytirildi)")
                    elif held_recent:
                        pts += 15
                        bd.append([f"Qo'lda buyum bor edi: {hm.hold_name}", 15])
                        reasons.append(f"Qo'lda '{hm.hold_name}' ko'rindi")
                        in_hand_now = (t - hm.hold_t) < 0.2
                        # "Yo'qoldi" faqat tekshirilgan bo'lsa: qo'l zonada yaqinda qidirildi va buyum
                        # topilmadi, yoki qo'l buyum bilan kadrdan chiqib ketdi
                        seen_now = t - hm.zone_seen_t <= 0.4
                        # oxirgi tekshiruv yaqinda bo'lgan VA unda buyum ko'rinmagan
                        checked = ((0 <= t - hm.zone_check_t <= 0.6 and hm.zone_seen_t < hm.zone_check_t)
                                   or zone == "offscreen")
                        if in_hand_now:
                            pts -= 15
                            bd.append(["Buyum hali qo'lda ko'rinmoqda", -15])
                            reasons.append("Lekin buyum hali qo'lda ko'rinib turibdi")
                        elif p["sig"]["vanish"] and checked and not seen_now:
                            pts += 20
                            bd.append(["Buyum keyin ko'rinmay qoldi", 20])
                            reasons.append(f"'{hm.hold_name}' keyin ko'rinmay qoldi")
                    # Shubhali harakat boshlanishi: qo'l oxirgi marta faol (yuqorida / buyum bilan)
                    # bo'lgan lahza, lekin zonaga kirishdan 2.5 s dan uzoq emas
                    start = hm.zone_enter_t
                    if hm.entry_active_t > -1e8:
                        start = max(hm.zone_enter_t - 2.5, min(hm.entry_active_t, hm.zone_enter_t))
                    candidates.append((pts, zone, side, reasons, bd, start))

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
                pts, zone, side, reasons, bd, susp_start = candidates[0]
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
                hs_ = mem.hands[side]
                thr = p["threshold"] + (hs_.shelf_delta if 0 <= t - hs_.last_shelf_t <= SHELF_SEC else 0)
                if (score >= thr and t - mem.last_alert_t >= p["cooldown"]
                        and not self._is_duplicate(t, person.box)):
                    mem.last_alert_t = t
                    mem.alert_score = max(mem.alert_score, score)
                    mem.last_alert_kind = KIND_LABELS[zone]
                    mem.hands[side].alerted_zone_t = t
                    self.recent_alerts.append((t, person.box.copy()))
                    level = "yuqori" if score >= 75 else ("o'rta" if score >= 55 else "past")
                    alerts.append({
                        "track_id": tid, "t": t, "score": score, "level": level, "kind": zone,
                        "kind_label": KIND_LABELS[zone], "side": side, "reasons": reasons,
                        "breakdown": bd, "ml_prob": mem.ml_prob, "box": person.box.tolist(),
                        "susp_start": float(susp_start), "susp_end": float(t),
                        "pose_seq": [[float(a), kk.tolist(), bb.tolist()] for a, kk, bb in list(mem.hist)],
                    })
            else:
                mem.risk *= 0.93
            mem.label = (f"{mem.risk:.0f}" if mem.risk >= 10 else "")
            held = self._held_view(mem, person, t)
            for hv in held or []:
                if hv["state"] == "yashirildi?":
                    if t - mem.hidden_last_t > 5.0:      # yangi epizod
                        mem.hidden_t = t
                    mem.hidden_last_t, mem.hidden_name = t, hv["name"]
            status[tid] = {"risk": mem.risk, "ml": mem.ml_prob, "box": person.box, "held": held}

        # Eski izlarni tozalash
        for tid in [k for k, m in self.tracks.items() if t - m.last_seen > 10]:
            del self.tracks[tid]
        return alerts, status

    @staticmethod
    def _held_view(mem: "TrackMem", person: Person, t: float) -> list | None:
        """Qo'ldagi buyumni ko'rsatish uchun: nomi va qo'l bilan birga suriladigan ramka.

        * "qo'lda"      — buyum oxirgi 1.2 s ichida qo'l bilan birga harakatlangan;
        * "yashirildi?" — buyumli qo'l yashirish zonasiga kirgan va buyum endi ko'rinmayapti.
        """
        out = []
        for side, hm in mem.hands.items():
            if hm.hold_box is None or not hm.hold_name or t - hm.hold_t > 4.0:
                continue
            wi = 9 if side == "L" else 10
            box = hm.hold_box.copy()
            if person.kpts[wi, 2] >= 0.3 and hm.hold_wrist is not None:
                d = person.kpts[wi, :2] - hm.hold_wrist   # buyum qo'l bilan birga yuradi
                box[[0, 2]] += d[0]
                box[[1, 3]] += d[1]
            if t - hm.hold_t <= 1.2:
                out.append({"name": hm.hold_name, "box": box.tolist(), "state": "qo'lda"})
            elif hm.zone in ("pocket", "torso", "bag", "offscreen"):
                out.append({"name": hm.hold_name, "box": box.tolist(), "state": "yashirildi?"})
        return out or None

    def _exit_alert(self, mem: "TrackMem", person: Person, t: float, door, base: dict) -> dict | None:
        """Avval shubha bo'lgan odam eshik (chiqish) zonasiga keldi."""
        # Faqat shubhadan KEYIN eshik zonasiga kirgan bo'lsa (signal paytida allaqachon eshik oldida turgan
        # odamga ikkinchi xabar berilmaydi); shubha EXIT_MEMORY_SEC dan eski bo'lmasin
        recent_alert = 0 <= mem.door_enter_t - mem.last_alert_t and t - mem.last_alert_t <= EXIT_MEMORY_SEC
        hidden = 0 <= mem.door_enter_t - mem.hidden_t and t - mem.hidden_t <= EXIT_MEMORY_SEC
        if not (recent_alert or hidden) or self._is_duplicate(t, person.box):
            return None
        reasons, bd = [], []
        score = 0.0
        if recent_alert:
            score = mem.alert_score
            ago = t - mem.last_alert_t
            reasons.append(f"Bu odam uchun {ago:.0f} s oldin signal bo'lgan: {mem.last_alert_kind} "
                           f"(ball {mem.alert_score:.0f})")
            bd.append([f"Avvalgi signal: {mem.last_alert_kind}", int(round(mem.alert_score))])
        if hidden:
            if EXIT_HIDDEN_SCORE > score:
                bd.append([f"Mahsulot yashirilgani ko'ringan: {mem.hidden_name}", int(EXIT_HIDDEN_SCORE - score)])
                score = float(EXIT_HIDDEN_SCORE)
            reasons.append(f"'{mem.hidden_name}' qo'lda ko'rinib, keyin yashirilgan edi")
        reasons.append(f"Chiqish zonasiga keldi: {door.name}")
        thr = base["threshold"] + door.threshold_delta
        if score < thr:
            return None
        mem.exit_alerted = True
        mem.last_alert_t = t
        self.recent_alerts.append((t, person.box.copy()))
        level = "yuqori" if score >= 75 else ("o'rta" if score >= 55 else "past")
        return {
            "track_id": person.track_id, "t": t, "score": float(min(100.0, score)), "level": level,
            "kind": "exit", "kind_label": KIND_LABELS["exit"], "side": None, "reasons": reasons,
            "breakdown": bd, "ml_prob": mem.ml_prob, "box": person.box.tolist(),
            "susp_start": float(t - 3.0),     # eshik tomon yurib kelgan oxirgi soniyalar
            "susp_end": float(t), "pose_seq": [],   # yurish — o'qitish uchun ishlatilmaydi
        }

    def engaged(self, t: float, track_ids) -> bool:
        """Kamerada "faol" harakat bormi: qo'l polkaga uzatilgan, buyum ushlangan, qo'l faol holatdan
        yashirish zonasiga kirgan yoki odamning xavf bali oshgan."""
        for tid in track_ids:
            mem = self.tracks.get(tid)
            if mem is None:
                continue
            if mem.risk >= 10:
                return True
            for hm in mem.hands.values():
                if (0 <= t - hm.last_reach_t <= ENGAGE_REACH_SEC or 0 <= t - hm.hold_t <= ENGAGE_HOLD_SEC
                        or 0 <= t - hm.last_shelf_t <= ENGAGE_REACH_SEC or 0 <= t - hm.cand_t <= 0.8):
                    return True
                if hm.zone and hm.zone_from_active and 0 <= t - hm.zone_enter_t <= 3.0:
                    return True
        return False

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
