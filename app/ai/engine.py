"""ONNX Runtime orqali YOLO11 pose (skelet) va obyekt detektsiyasi.

PyTorch kerak emas: modellar oldindan ONNX formatiga o'tkazilgan.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import DET_MODEL, POSE_MODEL, env
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


@dataclass
class FrameResult:
    persons: list[Person] = field(default_factory=list)
    objects: list[Obj] = field(default_factory=list)
    width: int = 0
    height: int = 0


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


class Engine:
    """Barcha kameralar uchun umumiy model sessiyalari (ORT run() thread-safe)."""

    _instance: "Engine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime as ort

        providers = []
        avail = ort.get_available_providers()
        for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider", "DmlExecutionProvider"):
            if p in avail:
                providers.append(p)
        providers.append("CPUExecutionProvider")
        self.threads = ai_threads()
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Standart holatda ORT oqimlari ish bo'lmasa ham "aylanib" CPU ni 100% band qiladi
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        if not POSE_MODEL.exists():
            raise FileNotFoundError(f"Pose modeli topilmadi: {POSE_MODEL}")
        self.pose = ort.InferenceSession(str(POSE_MODEL), opts, providers=providers)
        self.det = ort.InferenceSession(str(DET_MODEL), opts, providers=providers) if DET_MODEL.exists() else None
        self.providers = self.pose.get_providers()
        self.pose_input = self.pose.get_inputs()[0].name
        self.det_input = self.det.get_inputs()[0].name if self.det else None
        shape = self.pose.get_inputs()[0].shape
        self.fixed_size = shape[2] if isinstance(shape[2], int) else None
        # Bir nechta kamera bir vaqtda modelni chaqirsa, oqimlar bir-biriga xalaqit beradi —
        # navbat bilan ishlatish umumiy tezlikni oshiradi va kompyuterni qotirmaydi
        self.run_lock = threading.Lock()
        log.info("Modellar yuklandi. Qurilma: %s, AI oqimlari: %d", ", ".join(self.providers), self.threads)

    @classmethod
    def get(cls) -> "Engine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = Engine()
            return cls._instance

    def _size(self, size: int) -> int:
        if self.fixed_size:
            return self.fixed_size
        return max(320, int(round(size / 32)) * 32)

    def _prep(self, frame: np.ndarray, size: int):
        s = self._size(size)
        return letterbox(frame, s, rect=not self.fixed_size)

    def detect_persons(self, frame: np.ndarray, size: int = 640, conf: float = 0.4,
                       iou: float = 0.5) -> list[Person]:
        img, r, left, top = self._prep(frame, size)
        blob = _to_blob(img)
        with self.run_lock:
            out = self.pose.run(None, {self.pose_input: blob})[0][0].T  # (N, 56)
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

    def detect_objects(self, frame: np.ndarray, size: int = 640, conf: float = 0.3,
                       iou: float = 0.5) -> list[Obj]:
        if self.det is None:
            return []
        img, r, left, top = self._prep(frame, size)
        blob = _to_blob(img)
        with self.run_lock:
            out = self.det.run(None, {self.det_input: blob})[0][0].T  # (N, 84)
        scores_all = out[:, 4:]
        cls = scores_all.argmax(1)
        scores = scores_all[np.arange(len(cls)), cls]
        wanted = np.isin(cls, list(ITEM_CLASSES) + list(BAG_CLASSES))
        m = (scores >= conf) & wanted
        out, cls, scores = out[m], cls[m], scores[m]
        if len(out) == 0:
            return []
        cx, cy, w, h = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        boxes = np.column_stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
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
