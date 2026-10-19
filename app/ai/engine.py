"""Odam, skelet va buyum modellari (ONNX; ONNX Runtime yoki OpenVINO orqali, PyTorch kerak emas).

Model to'plamlari va litsenziyalari — model_zoo.py, ishga tushirish muhiti — runtime.py.
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import env
from ..logutil import get_logger

log = get_logger("engine")

# OpenCV o'zining ichki oqimlarini kamroq ishlatsin (AI bilan CPU uchun talashmasin)
cv2.setNumThreads(2)


def ai_threads() -> int:
    """AI uchun ajratiladigan CPU oqimlari soni.

    Barcha yadrolarni olish kompyuterni "qotiradi": kamera o'qish, video yozish, brauzer va
    Windows uchun 1-2 yadro bo'sh qoldiriladi. .env da AI_THREADS bilan o'zgartirish mumkin.
    """
    v = env("AI_THREADS")
    if v.strip().isdigit() and int(v) > 0:
        return int(v)
    try:
        import psutil

        phys = psutil.cpu_count(logical=False) or os.cpu_count() or 2
    except Exception:  # pragma: no cover
        phys = os.cpu_count() or 2
    if phys <= 2:
        return phys
    return max(2, min(4, phys - 2))

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]

# Do'konda "mahsulot" bo'lishi mumkin bo'lgan, qo'lda ko'tariladigan buyumlar
# (galstuk, fen, to'p kabi sinflar ataylab olinmagan: model ularni ko'pincha yeng, soch va boshga
# adashtiradi)
ITEM_CLASSES = {
    39: "shisha", 40: "qadah", 41: "stakan", 42: "vilka", 43: "pichoq", 44: "qoshiq", 45: "kosa",
    46: "banan", 47: "olma", 48: "sendvich", 49: "apelsin", 50: "brokkoli", 51: "sabzi",
    52: "hot-dog", 53: "pitsa", 54: "donut", 55: "tort", 64: "sichqoncha", 65: "pult",
    67: "telefon", 73: "kitob", 74: "soat", 75: "vaza", 76: "qaychi", 77: "o'yinchoq",
    79: "tish cho'tkasi",
}
BAG_CLASSES = {24: "ryukzak", 26: "sumka", 28: "chamadon"}

KPT_NAMES = [
    "burun", "chap ko'z", "o'ng ko'z", "chap quloq", "o'ng quloq", "chap yelka", "o'ng yelka",
    "chap tirsak", "o'ng tirsak", "chap bilak", "o'ng bilak", "chap son", "o'ng son",
    "chap tizza", "o'ng tizza", "chap to'piq", "o'ng to'piq",
]


@dataclass
class Person:
    box: np.ndarray            # x1, y1, x2, y2 (asl kadr koordinatalarida)
    score: float
    kpts: np.ndarray           # (17, 3): x, y, ishonch
    track_id: int = -1


@dataclass
class Obj:
    box: np.ndarray
    score: float
    cls: int
    name: str
    kind: str                  # item | bag
    src: str = "global"        # global (butun kadr) | hand (qo'l atrofi kattalashtirilgan)


@dataclass
class FrameResult:
    persons: list[Person] = field(default_factory=list)
    objects: list[Obj] = field(default_factory=list)
    width: int = 0
    height: int = 0


# Navbatda ustunlik: faol kamera so'rovi tinch kameranikidan 0.3 s, bo'sh kameranikidan 1 s "oldinroq"
# hisoblanadi. Shunday qilib yuklama oshganda avval tinch/bo'sh kameralar sekinlashadi, lekin ular ham
# abadiy kutib qolmaydi (eng uzoq kutgan so'rov baribir o'tadi).
PRIO_DELAY = {0: 0.0, 1: 0.3, 2: 1.0}
PRIO_NAMES = {0: "faol", 1: "tinch", 2: "bo'sh"}


class PriorityLock:
    """Model navbati: bir vaqtda bitta so'rov; muddati (kelgan vaqt + ustunlik kechikishi) eng erta
    bo'lgan so'rov birinchi o'tadi (earliest-deadline-first)."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._busy = False
        self._heap: list[tuple[float, int]] = []
        self._seq = itertools.count()
        self.calls = {k: 0 for k in PRIO_DELAY}
        self.wait_sum = {k: 0.0 for k in PRIO_DELAY}
        self.busy_sum = {k: 0.0 for k in PRIO_DELAY}

    @contextmanager
    def hold(self, prio: int = 1):
        prio = prio if prio in PRIO_DELAY else 1
        t_in = time.perf_counter()
        with self._cv:
            ticket = (t_in + PRIO_DELAY[prio], next(self._seq))
            heapq.heappush(self._heap, ticket)
            while self._busy or self._heap[0] != ticket:
                self._cv.wait()
            heapq.heappop(self._heap)
            self._busy = True
        t_go = time.perf_counter()
        try:
            yield
        finally:
            t_out = time.perf_counter()
            with self._cv:
                self._busy = False
                self.calls[prio] += 1
                self.wait_sum[prio] += t_go - t_in
                self.busy_sum[prio] += t_out - t_go
                self._cv.notify_all()

    def stats(self) -> dict:
        """Har ustunlik bo'yicha: chaqiruvlar soni, o'rtacha kutish va ishlash vaqti (ms)."""
        out = {}
        for k, name in PRIO_NAMES.items():
            n = self.calls[k]
            if n:
                out[name] = {"calls": n, "wait_ms": round(self.wait_sum[k] / n * 1000, 1),
                             "run_ms": round(self.busy_sum[k] / n * 1000, 1)}
        return out


def letterbox(img: np.ndarray, size: int, rect: bool = True, stride: int = 32) -> tuple[np.ndarray, float, int, int]:
    """Kadrni model kirishiga moslash.

    rect=True: kvadrat emas, kadr nisbatidagi to'rtburchak (32 ga karrali). 16:9 kamerada
    640x640 o'rniga 640x384 — hisob ~40% kam, natija bir xil (modellar dinamik o'lchamli).
    """
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    if rect:
        th = int(math.ceil(nh / stride) * stride)
        tw = int(math.ceil(nw / stride) * stride)
    else:
        th = tw = size
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nh, nw) != (h, w) else img
    top = (th - nh) // 2
    left = (tw - nw) // 2
    canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, r, left, top


def _to_blob(img: np.ndarray) -> np.ndarray:
    x = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(x[None])


def _nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    if len(boxes_xyxy) == 0:
        return []
    xywh = np.column_stack([boxes_xyxy[:, 0], boxes_xyxy[:, 1],
                            boxes_xyxy[:, 2] - boxes_xyxy[:, 0], boxes_xyxy[:, 3] - boxes_xyxy[:, 1]])
    idx = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), 0.0, iou)
    return [int(i) for i in np.array(idx).reshape(-1)] if len(idx) else []


_YOLOX_GRIDS: dict = {}


def _yolox_grids(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    key = (h, w)
    if key not in _YOLOX_GRIDS:
        g, st = [], []
        for stride in (8, 16, 32):
            hs, ws = h // stride, w // stride
            xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
            g.append(np.stack((xv, yv), 2).reshape(-1, 2))
            st.append(np.full((hs * ws, 1), stride))
        _YOLOX_GRIDS[key] = (np.concatenate(g).astype(np.float32), np.concatenate(st).astype(np.float32))
    return _YOLOX_GRIDS[key]


def decode_yolo(out: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """YOLO11 (1, 4+C, N) -> qutilar (N,4 xyxy) va sinf ballari (N,C)."""
    o = out[0].T
    cx, cy, w, h = o[:, 0], o[:, 1], o[:, 2], o[:, 3]
    return np.column_stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]), o[:, 4:]


def decode_yolox(out: np.ndarray, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """YOLOX (1, N, 5+C) -> qutilar (N,4 xyxy) va sinf ballari (N,C) = obyekt × sinf."""
    o = out[0]
    g, st = _yolox_grids(h, w)
    xy = (o[:, :2] + g) * st
    wh = np.exp(np.minimum(o[:, 2:4], 10.0)) * st
    return np.column_stack([xy - wh / 2, xy + wh / 2]), o[:, 4:5] * o[:, 5:]


RTM_W, RTM_H = 192, 256
RTM_MEAN = np.array([123.675, 116.28, 103.53], np.float32)
RTM_STD = np.array([58.395, 57.12, 57.375], np.float32)
MAX_POSE_PERSONS = 12      # bitta kadrda skeleti hisoblanadigan eng ko'p odam (eng ishonchlilari)


class Engine:
    """Barcha kameralar uchun umumiy modellar (bitta navbat orqali chaqiriladi).

    Model to'plami (model_zoo.MODEL_SETS) va muhit (ONNX Runtime / OpenVINO) sozlamalardan olinadi;
    o'zgartirilganda Engine.reload() yangisini yaratadi — kameralar keyingi kadrda unga o'tadi.
    """

    _instance: "Engine | None" = None
    _lock = threading.Lock()

    def __init__(self, model_set: str | None = None, runtime_pref: str | None = None,
                 save_bench: bool = True, strict: bool = False) -> None:
        """model_set / runtime_pref berilmasa — sozlamadan. save_bench=False: tezlik o'lchovini bazaga yozmaydi
        (baholash uchun yaratiladigan qo'shimcha nusxalar asosiy tanlovga ta'sir qilmasin).
        strict=True: so'ralgan to'plam yuklanmasa xato (YOLO11 ga jimgina o'tmaydi) — panelda almashtirishda
        eski to'plam joyida qoladi."""
        from .. import db
        from . import model_zoo

        s = db.get_settings()
        # HUSHYOR_MODEL_SET — sinov va baholash uchun (sozlamadan ustun); odatda bo'sh. Berilsa — qat'iy: fayli yo'q
        # bo'lsa xato (zaxira to'plam natijasi so'ralgan to'plamniki deb o'tib ketmasin)
        env_set = os.environ.get("HUSHYOR_MODEL_SET") if not model_set else None
        if env_set:
            strict = True
            if env_set not in model_zoo.MODEL_SETS:
                raise ValueError(f"HUSHYOR_MODEL_SET={env_set}: bunday to'plam yo'q ({', '.join(model_zoo.MODEL_SETS)})")
        want = model_set or env_set or s.get("model_set", model_zoo.DEFAULT_SET)
        if want not in model_zoo.MODEL_SETS:
            want = model_zoo.DEFAULT_SET
        self.threads = ai_threads()
        pref = runtime_pref or s.get("ai_runtime", "auto")
        try:
            self._build(want, pref, s, save_bench)
        except Exception as e:  # noqa: BLE001 — buzuq/yo'q model kameralarni to'xtatmasin
            if strict:
                raise
            # 2.7.0: avval litsenziyasi toza zaxira (open-ml — MoveNet Lightning dastur bilan birga keladi), keyin YOLO11
            log.error("%s modellarini yuklab bo'lmadi: %s", want, e)
            last = e
            for fb in model_zoo.FALLBACK:
                if fb == want or model_zoo.missing_required(fb):
                    continue
                log.warning("Vaqtincha %s ishlatiladi (AI model sahifasida ko'rinadi)", fb)
                try:
                    self._build(fb, pref, s, save_bench)
                    break
                except Exception as e2:  # noqa: BLE001
                    log.error("%s ham yuklanmadi: %s", fb, e2)
                    last = e2
            else:
                raise last
        # Bir nechta kamera bir vaqtda modelni chaqirsa, oqimlar bir-biriga xalaqit beradi —
        # navbat bilan ishlatish umumiy tezlikni oshiradi va kompyuterni qotirmaydi
        self.run_lock = PriorityLock()
        log.info("Modellar yuklandi: %s (%s), muhit: %s, qurilma: %s, AI oqimlari: %d", self.model_set,
                 ", ".join(self.licenses), self.runtime, ", ".join(self.providers), self.threads)

    def _build(self, want: str, pref: str, s: dict, save_bench: bool) -> None:
        from .. import db
        from . import model_zoo, runtime

        ms = model_zoo.MODEL_SETS[want]
        # Buyum detektori ixtiyoriy: fayli bo'lmasa skelet va qoidalar ishlayveradi (faqat "qo'lda buyum" yo'q)
        miss = model_zoo.missing_required(want)
        if miss:
            raise FileNotFoundError(f"model fayli yo'q: {', '.join(miss)}")
        self.model_set = want
        self.pose_name = ms["pose"]
        self.pose_kind = model_zoo.kind(self.pose_name)
        self.person_size = int(ms.get("person_size") or 0)
        self.person_name = ms.get("person")
        self.det_name = ms.get("det")
        probe = model_zoo.path(self.person_name or self.pose_name)
        # O'lchov natijalari har model uchun alohida saqlanadi: {barmoq izi: natija}
        try:
            allc = json.loads(s.get("runtime_auto") or "{}")
            if not isinstance(allc, dict) or "choice" in allc:
                allc = {}
        except ValueError:
            allc = {}
        fp = runtime.fingerprint(probe)
        self.runtime, info = runtime.choose(pref, probe, self.threads, allc.get(fp))
        if save_bench and info.get("fp") and allc.get(fp) != info:
            allc[fp] = info
            db.set_settings({"runtime_auto": runtime.dumps(dict(list(allc.items())[-6:]))})
        self.runtime_info = info

        def mk(key: str):
            try:
                return runtime.Runner(model_zoo.path(key), self.runtime, self.threads)
            except Exception as e:  # noqa: BLE001
                if self.runtime != "onnxruntime":
                    log.warning("%s OpenVINO'da yuklanmadi (%s) — ONNX Runtime ishlatiladi", key, e)
                try:
                    return runtime.Runner(model_zoo.path(key), "onnxruntime", self.threads)
                except Exception as e2:
                    # ONNX Runtime ham ocholmadi. 3.2.0: fayl faqat haqiqatan buzuq bo'lsa (fayl tuzilmasi o'qilmaydi)
                    # chetga olinadi — xotira yetmasligi kabi vaqtinchalik xato tufayli model butunlay o'chib qolmasin
                    msg = str(e2)
                    if any(m in msg for m in ("INVALID_PROTOBUF", "Protobuf parsing failed", "INVALID_GRAPH",
                                              "No graph was found")):
                        model_zoo.mark_broken(key)
                    raise

        self.pose = mk(self.pose_name)
        self.person = mk(self.person_name) if self.person_name else None
        self.det = None
        if self.det_name and model_zoo.path(self.det_name).exists():
            try:
                self.det = mk(self.det_name)
            except Exception as e:  # noqa: BLE001
                log.error("Buyum modeli (%s) yuklanmadi: %s — buyum qidirish o'chiq", self.det_name, e)
        elif self.det_name:
            log.warning("Buyum modeli fayli yo'q (%s) — buyum qidirish o'chiq", model_zoo.FILES[self.det_name]["file"])
        for r, key in ((self.person, self.person_name), (self.det, self.det_name)):
            # YOLOX faqat o'lchami o'zgaruvchan (dinamik) shaklda ishlaydi — rasmiy statik 416x416 fayl emas
            if r is not None and key and key.startswith("yolox") and r.fixed_hw:
                raise ValueError(f"{model_zoo.path(key).name} statik o'lchamli — dastur orqali qayta yuklab oling")
        self.providers = self.pose.providers
        self.fixed_size = None
        if self.person is None and self.pose_kind == "yolo" and self.pose.fixed_hw:
            self.fixed_size = self.pose.fixed_hw[0]
        self.licenses = sorted({model_zoo.FILES[k]["license"]
                                for k in (self.pose_name, self.person_name, self.det_name if self.det else None) if k})

    @classmethod
    def get(cls) -> "Engine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = Engine()
            return cls._instance

    @classmethod
    def reload(cls, model_set: str | None = None, runtime_pref: str | None = None) -> "Engine":
        """Sozlama o'zgarganda: yangi modellar yuklanadi. Navbat (run_lock) umumiy qoladi — yangi nusxa
        ko'rinishidan OLDIN beriladi, shunda eski va yangi chaqiruvlar hech qachon to'qnashmaydi."""
        new = Engine(model_set=model_set, runtime_pref=runtime_pref, strict=True)
        with cls._lock:
            old = cls._instance
            if old is not None:
                new.run_lock = old.run_lock
            cls._instance = new
        return new

    def info(self) -> dict:
        return {"model_set": self.model_set, "runtime": self.runtime, "providers": self.providers,
                "threads": self.threads, "licenses": self.licenses, "runtime_info": {
                    k: v for k, v in self.runtime_info.items() if k != "fp"}}

    def _size(self, size: int) -> int:
        if self.fixed_size:
            return self.fixed_size
        return max(320, int(round(size / 32)) * 32)

    def _prep(self, frame: np.ndarray, size: int):
        s = self._size(size)
        return letterbox(frame, s, rect=not self.fixed_size)

    # ------------------------------------------------------------------ odamlar va skelet
    def detect_persons(self, frame: np.ndarray, size: int = 640, conf: float = 0.4,
                       iou: float = 0.5, prio: int = 1) -> list[Person]:
        if self.person is not None:
            return self._persons_topdown(frame, size, conf, iou, prio)
        img, r, left, top = self._prep(frame, size)
        blob = _to_blob(img)
        with self.run_lock.hold(prio):
            out = self.pose(blob)[0].T  # (N, 56)
        out = out[out[:, 4] >= conf]
        if len(out) == 0:
            return []
        cx, cy, w, h = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        boxes = np.column_stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
        keep = _nms(boxes, out[:, 4], iou)
        H, W = frame.shape[:2]
        persons = []
        for i in keep:
            b = boxes[i].copy()
            b[[0, 2]] = (b[[0, 2]] - left) / r
            b[[1, 3]] = (b[[1, 3]] - top) / r
            b[[0, 2]] = b[[0, 2]].clip(0, W - 1)
            b[[1, 3]] = b[[1, 3]].clip(0, H - 1)
            k = out[i, 5:].reshape(17, 3).copy()
            k[:, 0] = (k[:, 0] - left) / r
            k[:, 1] = (k[:, 1] - top) / r
            persons.append(Person(box=b, score=float(out[i, 4]), kpts=k))
        return persons

    def _persons_topdown(self, frame: np.ndarray, size: int, conf: float, iou: float, prio: int) -> list[Person]:
        """Ikki bosqich: YOLOX odamlarni topadi, RTMPose yoki MoveNet har odamni alohida (kattalashtirib) skeletlaydi."""
        from . import model_zoo

        size = self._size(size)
        if self.person_size:
            size = min(size, self.person_size)       # 2.7.0: open-m — odam detektori ≤ 512 px (aniqlik o'sha, tezroq)
        img, r, left, top = letterbox(frame, size, rect=True)
        x = np.ascontiguousarray(img.transpose(2, 0, 1)[None].astype(np.float32))   # YOLOX: BGR, 0..255
        with self.run_lock.hold(prio):
            out = self.person(x)
            boxes, scores = decode_yolox(out, img.shape[0], img.shape[1])
            sc = scores[:, 0]
            m = sc >= conf
            boxes, sc = boxes[m], sc[m]
            keep = _nms(boxes, sc, iou)[:MAX_POSE_PERSONS]
            if not keep:
                return []
            boxes, sc = boxes[keep], sc[keep]
            H, W = frame.shape[:2]
            boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - left) / r).clip(0, W - 1)
            boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - top) / r).clip(0, H - 1)
            if self.pose_kind == "movenet":
                kps = self._movenet(frame, boxes)
            else:
                crops, metas = self._rtm_crops(frame, boxes)
                sx, sy = self._rtm_run(crops)
        if self.pose_kind == "movenet":
            return [Person(box=boxes[i].astype(np.float32), score=float(sc[i]), kpts=k) for i, k in enumerate(kps)]
        kx, ky = sx.argmax(2), sy.argmax(2)
        vals = model_zoo.calibrate(self.pose_name, 0.5 * (sx.max(2) + sy.max(2)))
        persons = []
        for i, (cx, cy, bw, bh) in enumerate(metas):
            k = np.zeros((17, 3), np.float32)
            k[:, 0] = kx[i] / 2.0 / RTM_W * bw + cx - bw / 2
            k[:, 1] = ky[i] / 2.0 / RTM_H * bh + cy - bh / 2
            k[:, 2] = vals[i]
            persons.append(Person(box=boxes[i].astype(np.float32), score=float(sc[i]), kpts=k))
        return persons

    @staticmethod
    def _rtm_crops(frame: np.ndarray, boxes: np.ndarray, pad: float = 1.25):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        crops, metas = [], []
        for b in boxes:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            bw, bh = max(b[2] - b[0], 2.0) * pad, max(b[3] - b[1], 2.0) * pad
            if bw > bh * RTM_W / RTM_H:
                bh = bw * RTM_H / RTM_W
            else:
                bw = bh * RTM_W / RTM_H
            s = RTM_W / bw
            M = np.array([[s, 0, RTM_W / 2 - s * cx], [0, s, RTM_H / 2 - s * cy]], np.float32)
            crop = cv2.warpAffine(rgb, M, (RTM_W, RTM_H), flags=cv2.INTER_LINEAR)
            crops.append(((crop.astype(np.float32) - RTM_MEAN) / RTM_STD).transpose(2, 0, 1))
            metas.append((cx, cy, bw, bh))
        return np.ascontiguousarray(np.stack(crops)), metas

    def _movenet(self, frame: np.ndarray, boxes: np.ndarray, pad: float = 1.25) -> list[np.ndarray]:
        """MoveNet (bitta odam): kvadrat kesma (odam ramkasi × pad, markazda), RGB 0..255 int32 → 17 nuqta (y, x,
        ishonch; kesmaga nisbatan 0..1). Nuqta ishonchi YOLO11 shkalasiga o'tkaziladi (tahlilchi chegaralari)."""
        from . import model_zoo

        S = int(model_zoo.FILES[self.pose_name]["input"])
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        out = []
        for b in boxes:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            side = max(b[2] - b[0], b[3] - b[1], 2.0) * pad
            s = S / side
            M = np.array([[s, 0, S / 2 - s * cx], [0, s, S / 2 - s * cy]], np.float32)
            crop = cv2.warpAffine(rgb, M, (S, S), flags=cv2.INTER_LINEAR)
            y = np.asarray(self.pose(crop[None].astype(np.int32))).reshape(-1, 17, 3)[0]
            k = np.empty((17, 3), np.float32)
            k[:, 0] = y[:, 1] * side + cx - side / 2
            k[:, 1] = y[:, 0] * side + cy - side / 2
            k[:, 2] = model_zoo.calibrate(self.pose_name, y[:, 2].astype(np.float32))
            out.append(k)
        return out

    def _rtm_run(self, crops: np.ndarray):
        sx, sy = self.pose.run_all(crops)[:2]
        return sx, sy

    # ------------------------------------------------------------------ buyumlar
    def detect_objects(self, frame: np.ndarray, size: int = 640, conf: float = 0.3,
                       iou: float = 0.5, prio: int = 1, square: bool = False) -> list[Obj]:
        if self.det is None:
            return []
        yolox = self.det_name.startswith("yolox")
        img, r, left, top = letterbox(frame, max(320, int(round(size / 32)) * 32), rect=not square)
        if yolox:
            x = np.ascontiguousarray(img.transpose(2, 0, 1)[None].astype(np.float32))
        else:
            x = _to_blob(img)
        with self.run_lock.hold(prio):
            out = self.det(x)
        boxes, scores_all = decode_yolox(out, img.shape[0], img.shape[1]) if yolox else decode_yolo(out)
        cls = scores_all.argmax(1)
        scores = scores_all[np.arange(len(cls)), cls]
        wanted = np.isin(cls, list(ITEM_CLASSES) + list(BAG_CLASSES))
        m = (scores >= conf) & wanted
        boxes, cls, scores = boxes[m], cls[m], scores[m]
        if len(boxes) == 0:
            return []
        # Sinf bo'yicha NMS: har bir sinf uchun alohida siljitish
        offs = boxes + (cls[:, None] * 4096.0)
        keep = _nms(offs, scores, iou)
        H, W = frame.shape[:2]
        objs = []
        for i in keep:
            b = boxes[i].copy()
            b[[0, 2]] = ((b[[0, 2]] - left) / r).clip(0, W - 1)
            b[[1, 3]] = ((b[[1, 3]] - top) / r).clip(0, H - 1)
            c = int(cls[i])
            if c in BAG_CLASSES:
                objs.append(Obj(b, float(scores[i]), c, BAG_CLASSES[c], "bag"))
            else:
                objs.append(Obj(b, float(scores[i]), c, ITEM_CLASSES.get(c, COCO_NAMES[c]), "item"))
        return objs

    def stats(self) -> dict:
        return self.run_lock.stats()

    def detect_objects_in(self, frame: np.ndarray, box, size: int = 320, conf: float = 0.25,
                          iou: float = 0.5, prio: int = 0, src: str = "hand") -> list[Obj]:
        """Kadrning bir qismida (masalan, qo'l atrofida) buyum qidirish.

        Qism model kirishiga kattalashtiriladi, shuning uchun butun kadrda juda kichik ko'ringan
        qo'ldagi buyum ham topiladi. Natija ramkalari butun kadr koordinatalariga qaytariladi.
        OpenVINO'da kirish doim kvadrat (bir xil o'lcham — har safar qayta moslashmaydi), ONNX Runtime'da
        qism nisbatida (tezroq).
        """
        H, W = frame.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(W, int(box[2])), min(H, int(box[3]))
        if x2 - x1 < 16 or y2 - y1 < 16:
            return []
        objs = self.detect_objects(frame[y1:y2, x1:x2], size=size, conf=conf, iou=iou, prio=prio,
                                   square=self.runtime == "openvino")
        for o in objs:
            o.box[[0, 2]] += x1
            o.box[[1, 3]] += y1
            o.src = src
        return objs
