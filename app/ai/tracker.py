"""Yengil odam kuzatuvchisi (ByteTrack g'oyasi asosida): har odamga doimiy ID beradi."""
from __future__ import annotations

import numpy as np

from .engine import Person


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-6)


class _Track:
    __slots__ = ("id", "box", "vel", "last_ts", "hits", "kpts")

    def __init__(self, tid: int, box: np.ndarray, ts: float, kpts: np.ndarray):
        self.id = tid
        self.box = box.astype(np.float32)
        self.vel = np.zeros(4, dtype=np.float32)
        self.last_ts = ts
        self.hits = 1
        self.kpts = kpts

    def predict(self, ts: float) -> np.ndarray:
        dt = min(max(ts - self.last_ts, 0.0), 1.0)
        return self.box + self.vel * dt

    def update(self, box: np.ndarray, ts: float, kpts: np.ndarray) -> None:
        dt = max(ts - self.last_ts, 1e-3)
        new_vel = (box - self.box) / dt
        self.vel = 0.6 * self.vel + 0.4 * new_vel
        self.box = box.astype(np.float32)
        self.last_ts = ts
        self.hits += 1
        self.kpts = kpts


class Tracker:
    def __init__(self, max_lost_sec: float = 2.0, high: float = 0.5, iou_thr: float = 0.25):
        self.tracks: list[_Track] = []
        self.next_id = 1
        self.max_lost = max_lost_sec
        self.high = high
        self.iou_thr = iou_thr

    def _match(self, tracks: list[_Track], dets: list[Person], ts: float, thr: float):
        if not tracks or not dets:
            return [], list(range(len(tracks))), list(range(len(dets)))
        tb = np.array([t.predict(ts) for t in tracks])
        db = np.array([d.box for d in dets])
        ious = iou_matrix(tb, db)
        matches, used_t, used_d = [], set(), set()
        order = np.dstack(np.unravel_index(np.argsort(-ious, axis=None), ious.shape))[0]
        for ti, di in order:
            if ious[ti, di] < thr:
                break
            if ti in used_t or di in used_d:
                continue
            matches.append((ti, di))
            used_t.add(ti)
            used_d.add(di)
        un_t = [i for i in range(len(tracks)) if i not in used_t]
        un_d = [i for i in range(len(dets)) if i not in used_d]
        return matches, un_t, un_d

    def update(self, persons: list[Person], ts: float) -> list[Person]:
        high = [p for p in persons if p.score >= self.high]
        low = [p for p in persons if p.score < self.high]
        m1, un_t, un_d = self._match(self.tracks, high, ts, self.iou_thr)
        for ti, di in m1:
            self.tracks[ti].update(high[di].box, ts, high[di].kpts)
            high[di].track_id = self.tracks[ti].id
        rest = [self.tracks[i] for i in un_t]
        m2, _, _ = self._match(rest, low, ts, self.iou_thr)
        for ti, di in m2:
            rest[ti].update(low[di].box, ts, low[di].kpts)
            low[di].track_id = rest[ti].id
        for di in un_d:
            p = high[di]
            t = _Track(self.next_id, p.box, ts, p.kpts)
            self.next_id += 1
            self.tracks.append(t)
            p.track_id = t.id
        self.tracks = [t for t in self.tracks if ts - t.last_ts <= self.max_lost]
        return [p for p in persons if p.track_id >= 0]

    def active_ids(self) -> set[int]:
        return {t.id for t in self.tracks}
