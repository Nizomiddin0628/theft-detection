"""Video manbalarini qayta ishlash: jonli kamera (veb-kamera, RTSP) va yuklangan video fayl."""
from __future__ import annotations

import os
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import db
from ..logutil import get_logger
from . import annotate, zones as zones_mod
from . import counting  # 1.9.0: sanash chizig'i
from . import safety  # 2.2.0: xavfsizlik va salomatlik
from .analyzer import Analyzer
from .clipper import RingBuffer
from ..ml.features import body_from_kpts, hand_state
from .engine import Engine, FrameResult
from .tracker import Tracker, iou_matrix

log = get_logger("worker")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

PREVIEW_WIDTH = 960
RING_FPS = 12            # klip uchun saqlanadigan kadr tezligi (qo'riqchi ko'rishi uchun yetarli)


def clip_window() -> tuple[float, float]:
    """Klip: hodisadan oldin va keyin necha soniya (sozlamadan)."""
    return db.fnum("pre_sec", 10), db.fnum("post_sec", 10)


def ring_seconds() -> float:
    pre, post = clip_window()
    return pre + post + 3


def susp_label(ts: float, live: bool) -> str:
    if live:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    m, s = divmod(max(ts, 0.0), 60)
    return f"video {int(m)}:{s:04.1f}"


def alert_banner(alert: dict, live: bool) -> str:
    s0 = float(alert.get("susp_start", alert["t"]))
    if safety.is_safety(alert.get("kind")):   # 2.2.0: xavfsizlik signali — ball emas, lahza
        return f"XAVFSIZLIK: {alert['kind_label']} | {susp_label(float(alert['t']), live)}"
    return (f"TEKSHIRING: {alert['kind_label']} | ball {alert['score']:.0f} | "
            f"shubha: {susp_label(s0, live)}")


def make_pending(ev_id: int, alert: dict, t: float, pre: float, post: float, live: bool) -> "PendingClip":
    s0 = float(alert.get("susp_start", t))
    s1 = float(alert.get("susp_end", t))
    # Klip shubhali harakat boshlanishidan kamida `pre` soniya oldin boshlanadi
    t_start = min(t, s0) - pre
    return PendingClip(ev_id, t, t_start, t + post, alert["track_id"], alert_banner(alert, live), alert["box"],
                       live, s0, s1)


def open_capture(kind: str, uri: str) -> cv2.VideoCapture:
    if kind == "webcam":
        idx = int(uri or 0)
        if platform.system() == "Windows":
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(idx)
        else:
            cap = cv2.VideoCapture(idx)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return cap
    if kind == "rtsp":
        # Kamera elektrdan uzilsa yoki tarmoq osilib qolsa o'quvchi abadiy kutmasin: ochish va o'qish 8 s
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_TIMEOUT_MS]
        try:
            return cv2.VideoCapture(uri, cv2.CAP_FFMPEG, params)
        except (TypeError, cv2.error):      # juda eski OpenCV: parametrlarsiz
            return cv2.VideoCapture(uri, cv2.CAP_FFMPEG)
    return cv2.VideoCapture(uri)


RTSP_TIMEOUT_MS = 8000


@dataclass
class PendingClip:
    event_id: int
    t_event: float
    t_start: float
    t_end: float
    track_id: int
    banner: str
    box: list
    live: bool
    susp_start: float | None = None
    susp_end: float | None = None


# ---------------------------------------------------------------------------------------------
# Kamera faolligi — resurslar faqat kerak joyga sarflanadi
#
#   bo'sh  kadrda odam yo'q: faqat arzon harakat tekshiruvi (4 marta/s, modelsiz). Model faqat harakat
#          bo'lsa yoki 4 soniyada bir marta ishlaydi.
#   tinch  odam bor, lekin qo'li hech narsaga uzatilmagan: faqat skelet modeli, sekundiga calm_fps marta
#          (standart 4). Buyum detektori umuman ishlamaydi.
#   faol   qo'l polkaga uzatildi / buyum ushladi / yashirish zonasiga kirdi: to'liq ai_fps, buyum esa
#          faqat o'sha qo'l atrofidagi kichik qismda qidiriladi (kadrda bitta model chaqiruvi).
#
# Ko'p kamerada model navbati shu rejimlar bo'yicha: faol kamera birinchi, keyin tinch, keyin bo'sh
# (engine.PriorityLock). Yuklama oshsa avval tinch/bo'sh kameralar sekinlashadi, faollar emas.
# ---------------------------------------------------------------------------------------------
IDLE, CALM, ACTIVE = "bo'sh", "tinch", "faol"
MODE_PRIO = {ACTIVE: 0, CALM: 1, IDLE: 2}
IDLE_FPS = 4.0           # bo'sh kamerada harakat tekshiruvi chastotasi
IDLE_CHECK_SEC = 4.0     # bo'sh va jim kamerada ham shuncha soniyada bir marta odam qidiriladi (sug'urta)
PERSON_MEMORY_SEC = 2.0  # odam oxirgi ko'ringandan keyin shuncha vaqt "tinch" rejim saqlanadi
ACTIVE_HOLD_SEC = 2.5    # faollik belgisi yo'qolgandan keyin shuncha vaqt "faol" rejim saqlanadi
RING_FPS_IDLE = 4        # bo'sh kamera klip buferiga sekundiga shuncha kadr yozadi (sahna qimirlamaydi)
MOTION_PIX = 18          # piksel o'zgarishi (0-255) shundan katta bo'lsa "harakat"
MOTION_FRAC = 0.003      # kichraytirilgan kadrdagi harakatli piksellar ulushi

# Buyum qidirish: faqat faol qo'l atrofida
HAND_CROP_SIZE = 320     # qo'l atrofidagi qism shu o'lchamga kattalashtiriladi (kichik buyum ham ko'rinadi)
PERSON_CROP_SIZE = 416   # faol odamning butun atrofi (sumka/ryukzak uchun)
PERSON_CHECK_SEC = 1.5   # faol odam atrofida sumka tekshiruvi oralig'i
BAG_TTL = 4.0            # topilgan sumka shuncha soniya eslab qolinadi
HOLD_TRACK_SEC = 0.5     # qo'lda ko'tarib yurilgan buyum shuncha soniyada bir marta qayta tekshiriladi
ENGAGE_SPEED = 0.6       # ko'tarilgan qo'l shundan tez harakatlansa (tana o'lchami/soniya) — biror narsa olmoqda
ENGAGE_LATERAL = 0.25    # ko'tarilgan qo'l tanadan yon tomonga shuncha chiqqan bo'lsa — polkaga uzatilgan
ENGAGE_HIGH_Y = -0.95    # qo'l yelka balandligidan yuqorida (tana koordinatasida, son = 0, yelka ≈ -1)
MISS_BACKOFF = 4         # qo'l yonida ketma-ket shuncha qidiruvda hech narsa topilmasa —
BACKOFF_SEC = 0.5        # o'sha qo'l shuncha soniyada bir martagina qidiriladi (masalan, telefonda gaplashish)
PATROL_SEC = 0.5         # ko'tarilgan (yoki stol/peshtaxta oldida turgan odamning) qo'li shuncha soniyada bir
                         # marta "nazorat" uchun qidiriladi — sekin olingan buyum ham o'tkazib yuborilmaydi


def _expand(box, frac: float) -> np.ndarray:
    b = np.asarray(box, dtype=np.float32)
    w, h = b[2] - b[0], b[3] - b[1]
    return np.array([b[0] - w * frac, b[1] - h * frac, b[2] + w * frac, b[3] + h * frac], dtype=np.float32)


def plausible_held(o, scale: float, head: np.ndarray) -> bool:
    """Qo'l atrofida topilgan narsa haqiqatan qo'ldagi buyum bo'lishi mumkinmi.

    Kattalashtirilgan qism ichida model ba'zan odamning o'z yengini yoki sochini buyum deb o'ylaydi:
    juda katta ramkalar va bosh nuqtalarini o'z ichiga olgan ramkalar tashlab yuboriladi.
    """
    b = o.box
    bw, bh = float(b[2] - b[0]), float(b[3] - b[1])
    sc = max(scale, 1.0)
    if max(bw, bh) > 1.0 * sc or bw * bh > 0.5 * sc * sc:
        return False
    for x, y in head:
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            return False
    return True


@dataclass
class Processor:
    """Bitta manba uchun umumiy AI qadamlari (detektsiya → kuzatuv → buyum (kerak bo'lsa) → tahlil)."""
    source_name: str
    source_id: int | None = None                        # zonalar shu manbadan olinadi
    background: bool = False                            # fon vazifasi (baholash): jonli kameralardan keyin navbat
    engine: object = None                               # boshqa model to'plami bilan baholash uchun (odatda umumiy)
    tracker: Tracker = field(default_factory=Tracker)
    analyzer: Analyzer = field(default=None)  # type: ignore[assignment]
    last_objects: list = field(default_factory=list)
    prev_small: np.ndarray | None = None
    last_full_t: float = -1e9
    last_person_t: float = -1e9
    last_engaged_t: float = -1e9
    mode: str = IDLE
    idle_skips: int = 0
    ran: bool = False                                   # oxirgi qadamda model ishladimi
    hands: dict = field(default_factory=dict)           # (track, qo'l) -> oldingi joy, vaqt, oxirgi qidiruv
    person_check_t: dict = field(default_factory=dict)  # track -> oxirgi sumka tekshiruvi
    bags: list = field(default_factory=list)            # [(vaqt, Obj)] — yaqinda topilgan sumkalar
    counts: dict = field(default_factory=lambda: {IDLE: 0, CALM: 0, ACTIVE: 0, "skip": 0, "obj": 0})
    zones: zones_mod.ZoneSet = field(default_factory=zones_mod.ZoneSet)
    counter: "counting.LineCounter" = field(default=None)  # type: ignore[assignment]   # 1.9.0: sanash chizig'i
    safety: "safety.SafetyMonitor" = field(default=None)  # type: ignore[assignment]   # 2.2.0: xavfsizlik qoidalari

    def __post_init__(self):
        if self.analyzer is None:
            self.analyzer = Analyzer(self.source_name)
        if getattr(self, "counter", None) is None:
            # 1.9.0: jonli kamera — bazaga yoziladi; video tahlili va aniqlik sinovi (background) — xotirada
            self.counter = counting.LineCounter(self.source_id, self.source_name, persist=not self.background)
        if self.safety is None:
            self.safety = safety.SafetyMonitor(self.source_id, self.source_name, background=self.background)

    # ------------------------------------------------------------------
    def interval(self, ai_fps: float, calm_fps: float) -> float:
        """Joriy rejimda ikki AI qadami orasidagi vaqt (soniya)."""
        ai_fps = max(1.0, ai_fps)
        if self.mode == ACTIVE:
            return 1.0 / ai_fps
        if self.mode == CALM:
            if counting.lines_from(self.zones):
                return 1.0 / ai_fps  # 1.9.0: sanash chizig'i bor kamera — izlar uzilmasin
            return 1.0 / max(1.0, min(ai_fps, calm_fps))
        return 1.0 / min(ai_fps, IDLE_FPS)

    def ring_fps(self) -> float:
        return RING_FPS if self.mode != IDLE else RING_FPS_IDLE

    def _motion(self, frame: np.ndarray) -> float:
        small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
        g = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
        prev, self.prev_small = self.prev_small, g
        if prev is None:
            return 1.0
        moved = cv2.absdiff(g, prev) > MOTION_PIX
        mask = self.zones.motion_mask(96, 54)    # e'tiborsiz zonadagi harakat (televizor, deraza) hisobga olinmaydi
        if mask is not None:
            return float(moved[mask].mean()) if mask.any() else 0.0
        return float(moved.mean())

    # ------------------------------------------------------------------
    def step(self, frame: np.ndarray, t: float):
        h, w = frame.shape[:2]
        self.zones = zones_mod.load(self.source_id)
        motion = self._motion(frame)
        if self.mode == IDLE and motion < MOTION_FRAC and 0 <= t - self.last_full_t < IDLE_CHECK_SEC:
            # Bo'sh va jim kadr — modellar ishlatilmaydi
            self.idle_skips += 1
            self.ran = False
            self.counts["skip"] += 1
            self.last_objects = []
            alerts, status = self.analyzer.update([], [], t, w, h, searched=[])
            self.counter.update([], t, w, h, self.zones, status)  # 1.9.0
            # 2.2.0: jim kadrda ham — tungi/chiqish/olov qoidalari kadr bilan ishlaydi, kutayotgan yiqilish tasdig'i
            alerts = alerts + self.safety.update([], t, w, h, frame, self.zones, status, detected=False)
            return FrameResult([], [], w, h), ([], [], {}), alerts, status
        self.last_full_t = t
        self.ran = True
        self.counts[self.mode] += 1
        eng = self.engine or Engine.get()
        s = db.get_settings()
        size = int(float(s.get("infer_size", 640)))
        pconf = float(s.get("person_conf", 0.4))
        oconf = float(s.get("object_conf", 0.3))
        persons = eng.detect_persons(frame, size=size, conf=min(pconf, 0.3),
                                     prio=2 if self.background else MODE_PRIO[self.mode])
        persons = [p for p in persons if p.score >= 0.25]
        if self.zones:
            # E'tiborsiz zonadagi "odamlar" (televizor, oynadagi aks, deraza ortidagi ko'cha) kuzatilmaydi.
            # Joy oyoq bo'yicha: deraza oldida turgan xaridor (tanasi derazani to'sgan) chiqarib tashlanmaydi
            persons = [p for p in persons if not self.zones.at(
                "etiborsiz", ((p.box[0] + p.box[2]) / 2, p.box[3]), w, h)]
        if persons:
            self.last_person_t = t
        self.tracker.high = pconf
        tracked = self.tracker.update(persons, t)
        items, searched, hot = self._search_objects(eng, frame, tracked, t, oconf) if tracked else ([], [], False)
        bags = self._alive_bags(t)
        objects = items + bags
        alerts, status = self.analyzer.update(tracked, objects, t, w, h, searched=searched, zones=self.zones)
        self.counter.update(tracked, t, w, h, self.zones, status)  # 1.9.0
        alerts = alerts + self.safety.update(tracked, t, w, h, frame, self.zones, status,  # 2.2.0
                                             loose=[p for p in persons if p.track_id < 0])
        self._update_mode(t, tracked, hot)
        self.last_objects = objects
        # Rasmda faqat qo'ldagi mahsulot chiziladi (status["held"]); polkadagi buyumlar ramkasi
        # faqat sozlamada yoqilsa
        drawn = objects if s.get("show_objects", "0") == "1" else []
        res = FrameResult(tracked, objects, w, h)
        ann = ([(p.track_id, p.box.copy(), p.kpts.copy()) for p in tracked],
               [(o.box.copy(), o.name, o.kind) for o in drawn],
               {k: {"risk": v["risk"], "held": v.get("held"), "staff": v.get("staff", False)}
                for k, v in status.items()})
        return res, ann, alerts, status

    # ------------------------------------------------------------------
    def _update_mode(self, t: float, tracked: list, engaged_now: bool) -> None:
        if engaged_now or (tracked and self.analyzer.engaged(t, [p.track_id for p in tracked])):
            self.last_engaged_t = t
        recent_person = 0 <= t - self.last_person_t <= PERSON_MEMORY_SEC
        if recent_person and 0 <= t - self.last_engaged_t <= ACTIVE_HOLD_SEC:
            self.mode = ACTIVE
        elif recent_person:
            self.mode = CALM
        else:
            self.mode = IDLE
        if len(self.hands) > 64:
            self.hands = {k: v for k, v in self.hands.items() if 0 <= t - v["t"] <= 10}
            self.person_check_t = {k: v for k, v in self.person_check_t.items() if 0 <= t - v <= 10}

    def _alive_bags(self, t: float) -> list:
        self.bags = [(bt, o) for bt, o in self.bags if 0 <= t - bt <= BAG_TTL]
        return [o for _, o in self.bags]

    def _add_bags(self, t: float, found: list) -> None:
        new = [o for o in found if o.kind == "bag"]
        if not new:
            return
        nb = np.array([o.box for o in new], dtype=np.float32)
        old = [(bt, o) for bt, o in self.bags
               if not (iou_matrix(np.array([o.box], dtype=np.float32), nb) > 0.3).any()]
        self.bags = old + [(t, o) for o in new]

    def _hand_candidates(self, tracked: list, t: float, w: int = 0, h: int = 0) -> list[dict]:
        """Buyum qidirishga arziydigan qo'llar (eng muhimi birinchi).

        0 — buyum ushlagan qo'l yashirish zonasida: buyum hali ko'rinyaptimi (yo'qoldimi)?
        1 — yonida buyum ko'ringan qo'l: birga siljiyaptimi (har kadr); "qo'lda" tasdiqlangandan keyin —
            HOLD_TRACK_SEC da bir marta
        2 — polkaga uzatilgan / yelkadan baland / ko'tarilib tez harakatlanayotgan qo'l: biror narsa olyaptimi?
        3 — boshqa ko'tarilgan qo'l (yoki stol oldida o'tirgan odam qo'li): PATROL_SEC da bir marta nazorat.
        Pastga osilib turgan qo'llar qidirilmaydi. 2 (uzoq vaqt bo'sh chiqqan) va 3 kamerani "faol" qilmaydi.
        """
        out = []
        for p in tracked:
            body = body_from_kpts(p.kpts, p.box)
            if body is None:
                continue
            mem = self.analyzer.tracks.get(p.track_id)
            head = p.kpts[0:5][p.kpts[0:5, 2] >= 0.3, :2]
            for side, wi in (("L", 9), ("R", 10)):
                key = (p.track_id, side)
                st = self.hands.setdefault(key, {"pt": None, "t": -1e9, "search_t": -1e9, "miss": 0})
                hs = hand_state(body, side)
                if not (hs["vis"] and hs["conf"] >= 0.4):
                    continue
                pt = p.kpts[wi, :2].astype(np.float32)
                speed = 0.0
                if st["pt"] is not None and 0 < t - st["t"] <= 0.6:
                    speed = float(np.linalg.norm(pt - st["pt"])) / max(body.scale, 1.0) / (t - st["t"])
                st["pt"], st["t"] = pt, t
                hm = mem.hands[side] if mem else None
                held = bool(hm is not None and hm.hold_name and 0 <= t - hm.hold_t <= 4.0)
                # qo'l yonida buyum ko'rindi, lekin hali "ushlangan" deb tasdiqlanmagan (qo'l bilan birga
                # siljishini kuzatish kerak)
                tracking = bool(hm is not None and 0 <= t - hm.cand_t <= 0.8)
                if held and hm.zone:
                    prio = 0
                elif tracking or held:
                    if not tracking and 0 <= t - st["search_t"] < HOLD_TRACK_SEC:
                        continue
                    prio = 1
                elif (hs["reach"] or (hs["active"] and (speed >= ENGAGE_SPEED or hs["y"] <= ENGAGE_HIGH_Y or
                                                         abs(hs["x"]) >= body.half_width + ENGAGE_LATERAL))
                      or (self.zones and self.zones.at("polka", pt, w, h))):
                    if not 0 <= t - st["search_t"] <= 1.5:
                        st["miss"] = 0            # yangi harakat boshlandi
                    if st["miss"] >= MISS_BACKOFF and 0 <= t - st["search_t"] < BACKOFF_SEC:
                        continue
                    prio = 2
                elif (hs["active"] or not body.hips_visible) and not 0 <= t - st["search_t"] < PATROL_SEC:
                    prio = 3
                else:
                    continue
                half = max(body.scale * (0.75 if prio == 0 else 0.55), 40.0)
                out.append({"prio": prio, "last": st["search_t"], "key": key, "tid": p.track_id,
                            "pt": pt, "pad": float(body.scale) * 0.2,
                            "cold": prio == 3 or (prio == 2 and st["miss"] >= MISS_BACKOFF),
                            "box": np.array([pt[0] - half, pt[1] - half, pt[0] + half, pt[1] + half],
                                            dtype=np.float32),
                            "pbox": p.box, "head": head,
                            # o'lcham mezoni: tana uzunligi (sonlar ko'rinmasa taxminiy — odam ramkasidan ham)
                            "scale": max(float(body.scale), 0.3 * float(p.box[3] - p.box[1]))})
        out.sort(key=lambda c: (c["prio"], c["last"]))
        return out

    def _search_objects(self, eng, frame: np.ndarray, tracked: list, t: float,
                        oconf: float) -> tuple[list, list, bool]:
        """Kadrda ko'pi bilan bitta buyum detektori chaqiruvi — eng muhim qo'l atrofida.

        Faol odam atrofi (sumka uchun) PERSON_CHECK_SEC da bir marta tekshiriladi, lekin faqat qo'l
        atrofi bir marta qidirilgandan keyin (buyumni topish birinchi o'rinda).
        """
        cands = self._hand_candidates(tracked, t, frame.shape[1], frame.shape[0])
        if not cands:
            return [], [], False
        c = cands[0]
        self.counts["obj"] += 1
        tid = c["tid"]
        if (c["prio"] != 0 and c["last"] > -1e8
                and not 0 <= t - self.person_check_t.get(tid, -1e9) < PERSON_CHECK_SEC):
            region = _expand(c["pbox"], 0.25)
            found = eng.detect_objects_in(frame, region, size=PERSON_CROP_SIZE, conf=oconf,
                                          prio=2 if self.background else 0, src="person")
            self.person_check_t[tid] = t
            for k in ((tid, "L"), (tid, "R")):
                if k in self.hands:
                    self.hands[k]["search_t"] = t
        else:
            region = c["box"]
            found = eng.detect_objects_in(frame, region, size=HAND_CROP_SIZE, conf=oconf,
                                          prio=2 if self.background else 0, src="hand")
        self._add_bags(t, found)
        items = [o for o in found if o.kind == "item" and plausible_held(o, c["scale"], c["head"])]
        if c["box"] is region:
            for cc in cands:   # ikkinchi qo'l ham shu qism ichida bo'lsa — u ham tekshirildi
                x, y = cc["pt"]
                if region[0] <= x <= region[2] and region[1] <= y <= region[3]:
                    st = self.hands[cc["key"]]
                    st["search_t"] = t
                    pad = cc["pad"]
                    hit = any(o.box[0] - pad <= x <= o.box[2] + pad and o.box[1] - pad <= y <= o.box[3] + pad
                              for o in items)
                    st["miss"] = 0 if hit else st["miss"] + 1
        # "Sovigan" qo'l (uzoq vaqt hech narsa topilmagan) kamerani faol rejimda ushlab turmaydi
        return items, [region], not c["cold"]


def preview_jpeg(frame: np.ndarray, ann: tuple | None, time_text: str | None = None,
                 quality: int = 75, zones=None) -> bytes:
    h, w = frame.shape[:2]
    scale = min(1.0, PREVIEW_WIDTH / w)
    img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame.copy()
    if zones:
        zones_mod.draw(img, zones)
    if ann:
        persons, objects, status = ann
        annotate.draw(img, persons, objects, status, scale=scale, time_text=time_text)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


class SourceWorker(threading.Thread):
    """Jonli manba: alohida o'quvchi oqim + AI oqimi."""

    def __init__(self, source: dict, manager):
        super().__init__(daemon=True, name=f"src-{source['id']}")
        self.src = source
        self.manager = manager
        self.stop_evt = threading.Event()
        self.latest_frame: np.ndarray | None = None
        self.latest_t = 0.0
        self.frame_lock = threading.Lock()
        self.latest_jpeg: bytes = b""
        self.state = "ishga tushmoqda"
        self.error = ""
        self.fps_cam = 0.0
        self.fps_ai = 0.0
        self.persons = 0
        self.max_risk = 0.0
        self.alerts = 0
        self.started_at = time.time()
        self.ring = RingBuffer(seconds=ring_seconds(), max_fps=RING_FPS)
        self.pending: list[PendingClip] = []
        self.proc = Processor(source["name"], source_id=source.get("id"))
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name=f"read-{source['id']}")
        self.track_status: dict = {}
        self.last_view = 0.0          # jonli oqimni kimdir oxirgi marta ko'rgan vaqt
        self.last_preview_t = 0.0
        self.loop_no = 0              # video fayl necha marta qaytadan boshlangani
        self.want_fps = float(RING_FPS)   # o'quvchi kadrni shu tezlikda "ochadi" (qolganlari faqat o'tkaziladi)
        self.injected: deque = deque(maxlen=20)   # 2.2.0: tashqaridan berilgan signal (do'kon gavjumligi) — shu kamera kadri bilan

    # ------------------------------------------------------------------
    def stop(self):
        self.stop_evt.set()

    def _read_loop(self):
        kind, uri = self.src["kind"], self.src["uri"]
        loop = bool(self.src.get("loop", 1))
        while not self.stop_evt.is_set():
            cap = open_capture(kind, uri)
            if not cap.isOpened():
                self.state = "xato"
                self.error = ("Kamerani ochib bo'lmadi. Boshqa dastur ishlatmayotganini va ruxsat "
                              "berilganini tekshiring." if kind == "webcam" else f"Manbani ochib bo'lmadi: {uri}")
                log.error("%s: %s", self.src["name"], self.error)
                if self.stop_evt.wait(3.0):
                    break
                continue
            self.state = "ishlayapti"
            self.error = ""
            vid_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if vid_fps <= 1 or vid_fps > 120:
                vid_fps = 25.0
            pace = kind == "file"
            n, t_fps = 0, time.time()
            t_play0 = time.time()
            frame_i = 0
            last_ret = 0.0
            while not self.stop_evt.is_set():
                # grab() kadrni faqat o'qiydi; rangli rasmga aylantirish (retrieve) faqat AI yoki klip
                # buferiga kerak bo'lganda — 25–30 kadr/s kameradan odatda 4–12 tasi
                if not cap.grab():
                    break
                now = time.time()
                if pace:
                    frame_i += 1
                    target = t_play0 + frame_i / vid_fps
                    if target > now:
                        time.sleep(target - now)
                        now = time.time()
                n += 1
                if now - t_fps >= 1.0:
                    self.fps_cam = n / (now - t_fps)
                    n, t_fps = 0, now
                if now - last_ret < 0.9 / max(self.want_fps, 1.0):
                    continue
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue
                last_ret = now
                with self.frame_lock:
                    self.latest_frame = frame
                    self.latest_t = now
                self.ring.min_dt = 1.0 / self.proc.ring_fps()
                self.ring.push(frame, now, now)
            cap.release()
            if self.stop_evt.is_set():
                break
            if kind == "file" and not loop:
                self.state = "tugadi"
                break
            if kind == "file":
                # Video qaytadan aylanmoqda: ko'rsatish davom etadi, lekin bir xil voqea uchun
                # takroriy hodisa va Telegram xabari yaratilmaydi (faqat birinchi o'tishda)
                self.loop_no += 1
                if getattr(self.proc, "counter", None) is not None:
                    self.proc.counter.persist = False  # 1.9.0: video qaytadan aylanmoqda — takror sanalmaydi
            if kind != "file":
                self.state = "qayta ulanmoqda"
                log.warning("%s: oqim uzildi, qayta ulanmoqda", self.src["name"])
                self.stop_evt.wait(2.0)

    def run(self):
        self.reader.start()
        last_t = 0.0
        t_fps, n = time.time(), 0
        while not self.stop_evt.is_set():
            ai_fps = max(1.0, db.fnum("ai_fps", 8))
            calm_fps = db.fnum("calm_fps", 4)
            with self.frame_lock:
                frame, ft = self.latest_frame, self.latest_t
            if frame is None or ft == last_t:
                time.sleep(0.01)
                if not self.reader.is_alive() and self.state in ("tugadi",):
                    break
                continue
            last_t = ft
            t0 = time.time()
            try:
                _, ann, alerts, status = self.proc.step(frame, ft)
            except Exception as e:
                self.state = "xato"
                self.error = f"AI xatosi: {e}"
                log.exception("AI xatosi (%s)", self.src["name"])
                time.sleep(1)
                continue
            if self.state == "xato" and self.error.startswith("AI xatosi"):
                # Bitta kadrdagi xato o'tib ketdi — kamera ishlayapti (aks holda "ishlamayapti" xabari ketib,
                # "tiklandi" hech qachon kelmas edi)
                self.state, self.error = "ishlayapti", ""
                log.info("%s: AI yana ishlayapti", self.src["name"])
            self.ring.set_annotations(ann)
            self.ring.seconds = ring_seconds()
            self.track_status = status
            self.persons = len(ann[0])
            self.max_risk = max([v["risk"] for v in status.values()], default=0.0)
            # Oldindan ko'rish rasmi faqat kimdir jonli oqimni ko'rayotganda har kadrda tayyorlanadi,
            # aks holda 2 soniyada bir marta (boshqaruv paneli rasmi uchun)
            now = time.time()
            if now - self.last_view < 3.0 or now - self.last_preview_t >= 2.0:
                self.latest_jpeg = preview_jpeg(frame, ann, annotate.wall_clock(ft), zones=self.proc.zones)
                self.last_preview_t = now
            if self.loop_no == 0:
                for a in alerts:
                    self._on_alert(a, ft, frame, ann)
            while self.injected:
                a = self.injected.popleft()
                if self.loop_no == 0:
                    a["t"] = ft
                    self._on_alert(a, ft, frame, ann)
            self._flush_pending(ft)
            n += self.proc.ran          # "AI kadr/s" — faqat model ishlagan qadamlar (bo'sh kamerada ~0.3)
            now = time.time()
            if now - t_fps >= 1.0:
                self.fps_ai = n / (now - t_fps)
                n, t_fps = 0, now
            # Keyingi qadam vaqti kamera rejimiga bog'liq: faol — ai_fps, tinch — calm_fps, bo'sh — 4/s
            interval = self.proc.interval(ai_fps, calm_fps)
            self.want_fps = max(self.proc.ring_fps(), 1.0 / interval)
            wait = interval - (time.time() - t0)
            if wait > 0:
                self.stop_evt.wait(wait)
        self._flush_pending(1e18, force=True)
        if self.state not in ("xato", "tugadi"):
            self.state = "to'xtatildi"

    def _on_alert(self, alert: dict, t: float, frame: np.ndarray | None = None, ann: tuple | None = None):
        self.alerts += 1
        pre, post = clip_window()
        snap = None if frame is None else {"frame": frame, "ann": ann, "banner": alert_banner(alert, True),
                                           "time_text": annotate.wall_clock(t)}
        ev_id = self.manager.create_event(alert, source=self.src, job_id=None, video_time=None, snapshot=snap)
        self.pending.append(make_pending(ev_id, alert, t, pre, post, live=True))

    def _flush_pending(self, t: float, force: bool = False):
        done = [p for p in self.pending if force or t >= p.t_end]
        for p in done:
            self.pending.remove(p)
            frames = self.ring.slice(p.t_start, p.t_end)
            threading.Thread(target=self.manager.finish_clip, args=(p, frames), daemon=True).start()

    def info(self) -> dict:
        return {
            "id": self.src["id"], "name": self.src["name"], "kind": self.src["kind"],
            "state": self.state, "error": self.error, "fps_cam": round(self.fps_cam, 1),
            "fps_ai": round(self.fps_ai, 1), "persons": self.persons, "max_risk": round(self.max_risk),
            "alerts": self.alerts, "uptime": int(time.time() - self.started_at),
            "running": self.is_alive(), "mode": self.proc.mode,
        }


class FileJob(threading.Thread):
    """Yuklangan videoni tez tahlil qilish (real vaqtdan tezroq)."""

    def __init__(self, job: dict, manager):
        super().__init__(daemon=True, name=f"job-{job['id']}")
        self.job = job
        self.manager = manager
        self.stop_evt = threading.Event()
        self.latest_jpeg = b""
        self.progress = 0.0
        self.events = 0

    def stop(self):
        self.stop_evt.set()

    def _progress(self, idx: int, total: int, jid: int) -> None:
        if not total:
            return
        self.progress = min(1.0, idx / total)
        if self.progress - self._last_progress >= 0.01:
            self._last_progress = self.progress
            db.execute("UPDATE jobs SET progress=?, events_count=? WHERE id=?",
                       (round(self.progress * 100, 1), self.events, jid))

    def run(self):
        jid = self.job["id"]
        path = self.job["path"]
        db.execute("UPDATE jobs SET status='ishlanmoqda' WHERE id=?", (jid,))
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            db.execute("UPDATE jobs SET status='xato', error=?, finished_at=? WHERE id=?",
                       ("Videoni ochib bo'lmadi (format qo'llab-quvvatlanmaydi?)", time.time(), jid))
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 1 or fps > 240:
            fps = 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ai_fps = max(1.0, db.fnum("ai_fps", 8))
        calm_fps = db.fnum("calm_fps", 4)
        pre, post = clip_window()
        ring = RingBuffer(seconds=pre + post + 3, max_fps=RING_FPS)
        src = {"id": None, "name": f"Video: {self.job['filename']}", "kind": "upload"}
        # Fon vazifasi: jonli kameralar bo'lsa, model navbatida ular birinchi (video tahlili sekinroq bo'ladi)
        proc = Processor(src["name"], source_id=self.job.get("zone_source_id"), background=True)
        pending: list[PendingClip] = []
        idx = 0
        self._last_progress = -1.0
        last_ai_t = last_prev_t = -1e9
        err = None
        try:
            while not self.stop_evt.is_set():
                if not cap.grab():
                    break
                t = idx / fps
                # Jonli kameradagi kabi: AI qadami oralig'i kamera rejimiga bog'liq (bo'sh/tinch/faol)
                ai_due = t - last_ai_t >= proc.interval(ai_fps, calm_fps) - 0.5 / fps
                ring.min_dt = 1.0 / proc.ring_fps()
                if not ai_due and not ring.due(t):
                    idx += 1
                    self._progress(idx, total, jid)
                    continue
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    idx += 1
                    continue
                if ai_due:
                    last_ai_t = t
                    _, ann, alerts, status = proc.step(frame, t)
                    ring.set_annotations(ann)
                    ring.push(frame, t, time.time())
                    if t - last_prev_t >= 0.4:
                        last_prev_t = t
                        self.latest_jpeg = preview_jpeg(frame, ann, f"video {t:5.1f} s")
                    for a in alerts:
                        self.events += 1
                        snap = {"frame": frame, "ann": ann, "banner": alert_banner(a, False),
                                "time_text": f"video {t:6.1f} s"}
                        ev_id = self.manager.create_event(a, source=src, job_id=jid, video_time=t, snapshot=snap)
                        pending.append(make_pending(ev_id, a, t, pre, post, live=False))
                else:
                    ring.push(frame, t, time.time())
                for p in [p for p in pending if t >= p.t_end]:
                    pending.remove(p)
                    self.manager.finish_clip(p, ring.slice(p.t_start, p.t_end))
                idx += 1
                self._progress(idx, total, jid)
            if not self.stop_evt.is_set() and getattr(proc, "safety", None) is not None:
                # 2.2.0: video tugadi — muddati yetgan yiqilish tasdig'i (evaluation.analyze bilan bir xil)
                t_end = idx / fps
                for a in proc.safety.falls.flush(t_end):
                    self.events += 1
                    ev_id = self.manager.create_event(a, source=src, job_id=jid, video_time=t_end)
                    pending.append(make_pending(ev_id, a, t_end, pre, post, live=False))
        except Exception as e:
            err = str(e)
            log.exception("Video tahlilida xato")
        finally:
            for p in pending:
                self.manager.finish_clip(p, ring.slice(p.t_start, p.t_end))
            cap.release()
        status = "xato" if err else ("toxtatildi" if self.stop_evt.is_set() else "tayyor")
        db.execute("UPDATE jobs SET status=?, progress=?, events_count=?, finished_at=?, duration=?, "
                   "frames=?, error=? WHERE id=?",
                   (status, 100.0 if status == "tayyor" else round(self.progress * 100, 1), self.events,
                    time.time(), round(idx / fps, 2), idx, err, jid))
        log.info("Video tahlili tugadi: %s — %d ta hodisa", self.job["filename"], self.events)
