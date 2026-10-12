"""PoseLift (WACV 2025) datasetini o'qish.

Datasetni o'z kompyuteringizga yuklab oling (Google Drive havolasi:
https://github.com/TeCSAR-UNCC/PoseLift) va papkani "Model" sahifasida ko'rsating.
Kutilayotgan tuzilma: <papka>/**/<kamera>_<video>.pkl va shu nomdagi .npy yorliq fayli.
Pickle tuzilmasi: {kadr_raqami: [ {odam ID, bbox XYWH, keypoints XYC}, ... ]} — turli
variantlarni qo'llab-quvvatlash uchun o'quvchi moslashuvchan yozilgan.
"""
from __future__ import annotations

import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..logutil import get_logger

log = get_logger("poselift")
FPS = 15.0


def _as_kpts(v) -> np.ndarray | None:
    try:
        a = np.asarray(v, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if a.size == 51:
        return a.reshape(17, 3)
    if a.size == 34:
        k = np.ones((17, 3), dtype=np.float32)
        k[:, :2] = a.reshape(17, 2)
        return k
    return None


def _as_box(v) -> np.ndarray | None:
    try:
        a = np.asarray(v, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if a.size < 4:
        return None
    x, y, w, h = a[:4]
    return np.array([x, y, x + w, y + h], dtype=np.float32)


def _people(frame_val):
    """Kadr qiymatidan (pid, box, kpts) ro'yxatini chiqarish."""
    out = []
    if isinstance(frame_val, dict):
        items = frame_val.items()
        if any(k in frame_val for k in ("keypoints", "kpts", "pose")):
            items = [(frame_val.get("id", frame_val.get("person_id", 0)), frame_val)]
    elif isinstance(frame_val, (list, tuple)):
        items = enumerate(frame_val)
    else:
        return out
    for key, p in items:
        pid, box, kp = key, None, None
        if isinstance(p, dict):
            for k2 in ("id", "pid", "person_id", "track_id", "Person ID"):
                if k2 in p:
                    pid = p[k2]
            for k2 in ("bbox", "box", "bounding_box", "Bounding Box"):
                if k2 in p:
                    box = _as_box(p[k2])
            for k2 in ("keypoints", "kpts", "pose", "Keypoints"):
                if k2 in p:
                    kp = _as_kpts(p[k2])
        elif isinstance(p, (list, tuple)) and len(p) >= 3:
            pid, box, kp = p[0], _as_box(p[1]), _as_kpts(p[2])
        if kp is None:
            continue
        if box is None:
            vis = kp[kp[:, 2] > 0.1]
            if len(vis) == 0:
                continue
            box = np.array([vis[:, 0].min(), vis[:, 1].min(), vis[:, 0].max(), vis[:, 1].max()], dtype=np.float32)
        if kp[:, 2].max() > 1.5:  # ishonch 0-100 shkalada bo'lsa
            kp[:, 2] /= 100.0
        out.append((pid, box, kp))
    return out


def load_video(pkl: Path) -> tuple[dict, np.ndarray | None]:
    with open(pkl, "rb") as f:
        data = pickle.load(f)
    labels = None
    for cand in (pkl.with_suffix(".npy"), *pkl.parent.parent.rglob(pkl.stem + ".npy")):
        if cand.exists():
            labels = np.load(cand).astype(np.float32).reshape(-1)
            break
    tracks: dict = defaultdict(list)
    if isinstance(data, dict):
        frames = data.items()
    else:
        frames = enumerate(data)
    for fk, fv in frames:
        try:
            fi = int(fk)
        except (TypeError, ValueError):
            continue
        for pid, box, kp in _people(fv):
            tracks[str(pid)].append((fi, kp, box))
    for pid in tracks:
        tracks[pid].sort(key=lambda x: x[0])
    return tracks, labels


def windows(root: str | Path, window: float = 2.5, step: float = 0.5, max_videos: int | None = None) -> list[dict]:
    root = Path(root)
    pkls = sorted(root.rglob("*.pkl"))
    if max_videos:
        pkls = pkls[:max_videos]
    out = []
    for pkl in pkls:
        try:
            tracks, labels = load_video(pkl)
        except Exception as e:
            log.warning("PoseLift faylini o'qib bo'lmadi %s: %s", pkl.name, e)
            continue
        if labels is None:
            continue
        wf = int(window * FPS)
        sf = max(1, int(step * FPS))
        for pid, seq in tracks.items():
            if len(seq) < wf // 2:
                continue
            fidx = np.array([s[0] for s in seq])
            for start in range(fidx[0], fidx[-1] - wf + 2, sf):
                sel = [s for s in seq if start <= s[0] < start + wf]
                if len(sel) < wf // 2:
                    continue
                lab_idx = [s[0] for s in sel if s[0] < len(labels)]
                if not lab_idx:
                    continue
                frac = float(labels[lab_idx].mean())
                out.append({
                    "frames": [(s[0] / FPS, s[1], s[2]) for s in sel],
                    "label": 1 if frac >= 0.3 else 0,
                    "group": f"pl_{pkl.stem}",
                    "behavior": "poselift",
                })
    log.info("PoseLift: %d ta video, %d ta oyna", len(pkls), len(out))
    return out
