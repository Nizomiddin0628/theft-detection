"""Halqali bufer (oxirgi N soniya kadrlar) va hodisa klipini H.264 MP4 qilib yozish."""
from __future__ import annotations

import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..logutil import get_logger
from . import annotate

log = get_logger("clipper")

MAX_CLIP_WIDTH = 960


@dataclass
class BufFrame:
    t: float                 # vaqt (jonli: time.time(), fayl: video vaqti)
    wall: float              # devor soati
    jpeg: bytes
    scale: float             # asl kadrdan saqlangan kadrga koeffitsient
    ann: tuple | None        # (persons, objects, status) asl koordinatalarda


class RingBuffer:
    def __init__(self, seconds: float = 15.0, max_fps: float = 15.0):
        self.seconds = seconds
        self.min_dt = 1.0 / max_fps if max_fps > 0 else 0
        self.frames: deque[BufFrame] = deque()
        self.lock = threading.Lock()
        self._last_t = -1e9
        self.ann = None

    def set_annotations(self, ann: tuple) -> None:
        self.ann = ann

    def push(self, frame: np.ndarray, t: float, wall: float) -> None:
        if t < self._last_t:
            # Video qaytadan boshlandi (vaqt orqaga ketdi) — eski kadrlar endi kerak emas
            with self.lock:
                self.frames.clear()
            self._last_t = -1e9
        if t - self._last_t < self.min_dt:
            return
        self._last_t = t
        h, w = frame.shape[:2]
        scale = min(1.0, MAX_CLIP_WIDTH / w)
        small = cv2.resize(frame, (int(w * scale) // 2 * 2, int(h * scale) // 2 * 2),
                           interpolation=cv2.INTER_AREA) if scale < 1 else frame
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return
        with self.lock:
            self.frames.append(BufFrame(t, wall, buf.tobytes(), scale, self.ann))
            while self.frames and t - self.frames[0].t > self.seconds:
                self.frames.popleft()

    def slice(self, t0: float, t1: float) -> list[BufFrame]:
        with self.lock:
            return [f for f in self.frames if t0 <= f.t <= t1]


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _decode(f: BufFrame) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)


def render_frames(frames: list[BufFrame], highlight: int | None, banner: str,
                  live: bool, susp: tuple[float, float] | None = None) -> list[np.ndarray]:
    out = []
    t0, t1 = frames[0].t, frames[-1].t
    for f in frames:
        img = _decode(f)
        if img is None:
            continue
        tt = annotate.wall_clock(f.wall) if live else f"video {f.t:6.1f} s"
        if f.ann:
            persons, objects, status = f.ann
            annotate.draw(img, persons, objects, status, highlight=highlight, scale=f.scale,
                          banner=banner, time_text=tt)
        else:
            annotate.draw(img, [], [], {}, banner=banner, time_text=tt)
        if susp is not None:
            annotate.draw_timeline(img, f.t, t0, t1, susp[0], susp[1])
        out.append(img)
    return out


def write_mp4(images: list[np.ndarray], path: Path, fps: float) -> bool:
    if not images:
        return False
    h, w = images[0].shape[:2]
    w2, h2 = w // 2 * 2, h // 2 * 2
    exe = ffmpeg_exe()
    fps = max(1.0, min(30.0, fps))
    if exe:
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w2}x{h2}",
               "-r", f"{fps:.3f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            for img in images:
                if img.shape[0] != h2 or img.shape[1] != w2:
                    img = cv2.resize(img, (w2, h2))
                proc.stdin.write(np.ascontiguousarray(img).tobytes())
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="ignore")
            proc.wait(timeout=120)
            if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
                return True
            log.warning("ffmpeg xatosi (%s): %s", proc.returncode, err[-300:])
        except Exception as e:
            log.warning("ffmpeg ishlamadi: %s", e)
    # Zaxira: OpenCV yozuvchisi
    for fourcc in ("avc1", "mp4v"):
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (w2, h2))
        if vw.isOpened():
            for img in images:
                vw.write(cv2.resize(img, (w2, h2)) if img.shape[:2] != (h2, w2) else img)
            vw.release()
            if path.exists() and path.stat().st_size > 0:
                return True
    return False


def make_clip(frames: list[BufFrame], event_t: float, highlight: int, banner: str, live: bool,
              out_dir: Path, stem: str, person_box: list | None,
              susp: tuple[float, float] | None = None) -> dict:
    """Klip, eskiz (thumbnail) va odam qirqimini yaratadi. Fayl nomlarini qaytaradi.

    susp: (shubha boshlanishi, signal vaqti) — klipda vaqt chizig'i va "SHUBHALI LAHZA" belgisi.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {"clip": None, "thumb": None, "crop": None, "clip_start": None}
    if not frames:
        return res
    res["clip_start"] = frames[0].t
    images = render_frames(frames, highlight, banner, live, susp)
    if not images:
        return res
    dur = max(frames[-1].t - frames[0].t, 1e-3)
    fps = (len(images) - 1) / dur if len(images) > 1 else 10.0
    clip_path = out_dir / f"{stem}.mp4"
    if write_mp4(images, clip_path, fps):
        res["clip"] = clip_path.name
    # Hodisa lahzasidagi kadr
    idx = int(np.argmin([abs(f.t - event_t) for f in frames]))
    idx = min(idx, len(images) - 1)
    thumb = images[idx]
    thumb_path = out_dir / f"{stem}.jpg"
    cv2.imwrite(str(thumb_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 88])
    res["thumb"] = thumb_path.name
    if person_box is not None:
        raw = _decode(frames[idx])
        if raw is not None:
            s = frames[idx].scale
            b = np.asarray(person_box, dtype=np.float32) * s
            bw, bh = b[2] - b[0], b[3] - b[1]
            x1 = int(max(0, b[0] - bw * 0.15))
            y1 = int(max(0, b[1] - bh * 0.10))
            x2 = int(min(raw.shape[1], b[2] + bw * 0.15))
            y2 = int(min(raw.shape[0], b[3] + bh * 0.10))
            if x2 - x1 > 10 and y2 - y1 > 10:
                crop_path = out_dir / f"{stem}_crop.jpg"
                cv2.imwrite(str(crop_path), raw[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 90])
                res["crop"] = crop_path.name
    return res
