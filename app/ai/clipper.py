"""Halqali bufer (oxirgi N soniya kadrlar) va hodisa klipini H.264 MP4 qilib yozish."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
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
CLIP_FPS = 12


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

    def due(self, t: float) -> bool:
        """Shu vaqtdagi kadr buferga yoziladimi (kadrni ochish kerakmi)."""
        return t < self._last_t or t - self._last_t >= self.min_dt

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


def iter_rendered(frames: list[BufFrame], highlight: int | None, banner: str,
                  live: bool, susp: tuple[float, float] | None = None, with_index: bool = False,
                  clean: bool = False):
    """Kadrlarni birma-bir chizib beradi (hammasini xotiraga yig'maydi — 20 s klip ~370 MB bo'lardi).
    3.1.0: clean=True — hech narsa chizilmaydi (skelet, ramka, yozuvlar yo'q): Telegram uchun toza klip."""
    t0, t1 = frames[0].t, frames[-1].t
    for i, f in enumerate(frames):
        img = _decode(f)
        if img is None:
            continue
        if clean:
            yield (i, img) if with_index else img
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
        yield (i, img) if with_index else img


def render_frames(frames: list[BufFrame], highlight: int | None, banner: str,
                  live: bool, susp: tuple[float, float] | None = None) -> list[np.ndarray]:
    return list(iter_rendered(frames, highlight, banner, live, susp))


FFMPEG_TIMEOUT = 120


def write_mp4(images, path: Path, fps: float) -> bool:
    """H.264 MP4 yozish. images — ro'yxat yoki har chaqirilganda yangi iterator qaytaradigan funksiya
    (ffmpeg ishlamasa, zaxira yozuvchi uchun kadrlar qaytadan chiziladi)."""
    make_iter = images if callable(images) else (lambda: iter(images))
    it = make_iter()
    first = next(it, None)
    if first is None:
        return False
    h, w = first.shape[:2]
    w2, h2 = w // 2 * 2, h // 2 * 2
    exe = ffmpeg_exe()
    fps = max(1.0, min(30.0, fps))

    def fit(img):
        return cv2.resize(img, (w2, h2)) if img.shape[0] != h2 or img.shape[1] != w2 else img

    if exe:
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w2}x{h2}",
               "-r", f"{fps:.3f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # stderr faylga (PIPE to'lib qolsa ffmpeg osilib qolardi); ffmpeg osilsa 120 s da to'xtatiladi
            with tempfile.TemporaryFile() as errf:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf, creationflags=flags)
                # Qo'riqchi taymer: yozish paytida ham (stdin bloklangan bo'lsa) osilib qolmaydi
                killed = threading.Event()
                guard = threading.Timer(FFMPEG_TIMEOUT, lambda: (killed.set(), proc.kill()))
                guard.daemon = True
                guard.start()
                try:
                    proc.stdin.write(np.ascontiguousarray(fit(first)).tobytes())
                    for img in it:
                        proc.stdin.write(np.ascontiguousarray(fit(img)).tobytes())
                    proc.stdin.close()
                    proc.wait(timeout=FFMPEG_TIMEOUT)
                except BaseException:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(10)
                    if killed.is_set():
                        raise RuntimeError(f"ffmpeg {FFMPEG_TIMEOUT} s da tugamadi — to'xtatildi") from None
                    raise
                finally:
                    guard.cancel()
                if killed.is_set():
                    raise RuntimeError(f"ffmpeg {FFMPEG_TIMEOUT} s da tugamadi — to'xtatildi")
                errf.seek(0)
                err = errf.read().decode(errors="ignore")
            if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
                return True
            log.warning("ffmpeg xatosi (%s): %s", proc.returncode, err[-300:])
        except Exception as e:
            log.warning("ffmpeg ishlamadi: %s", e)
    # Zaxira: OpenCV yozuvchisi
    for fourcc in ("avc1", "mp4v"):
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (w2, h2))
        if vw.isOpened():
            for img in make_iter():
                vw.write(fit(img))
            vw.release()
            if path.exists() and path.stat().st_size > 0:
                return True
    return False


def make_snapshot(frame: np.ndarray, ann: tuple | None, highlight: int | None, banner: str,
                  time_text: str | None, path: Path, clean_path: Path | None = None) -> bool:
    """Signal lahzasidagi kadrdan darhol rasm (Telegram'ga birinchi xabar uchun, ~10 ms).
    3.1.0: clean_path — o'sha kadr chizmalarsiz (Telegram'ga toza rasm ketadi)."""
    h, w = frame.shape[:2]
    scale = min(1.0, MAX_CLIP_WIDTH / w)
    img = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 \
        else frame.copy()
    if clean_path is not None:
        clean_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(clean_path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    persons, objects, status = ann if ann else ([], [], {})
    annotate.draw(img, persons, objects, status, highlight=highlight, scale=scale, banner=banner,
                  time_text=time_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85]))


def make_clip(frames: list[BufFrame], event_t: float, highlight: int, banner: str, live: bool,
              out_dir: Path, stem: str, person_box: list | None,
              susp: tuple[float, float] | None = None, clean: bool = True, on_clean=None) -> dict:
    """Klip, eskiz (thumbnail) va odam qirqimini yaratadi. Fayl nomlarini qaytaradi.

    susp: (shubha boshlanishi, signal vaqti) — klipda vaqt chizig'i va "SHUBHALI LAHZA" belgisi.
    3.1.0: clean=True — xuddi shu klip chizmalarsiz ham (`<stem>_clean.mp4`, `<stem>_clean.jpg`): Telegram'ga toza
    video ketadi, panelda "Toza / Belgili" almashtiriladi. Toza nusxa BIRINCHI yoziladi va on_clean(natija) chaqiriladi —
    Telegram belgili nusxa tayyor bo'lishini kutmaydi.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {"clip": None, "thumb": None, "crop": None, "clip_start": None, "clip_clean": None, "thumb_clean": None}
    if not frames:
        return res
    res["clip_start"] = frames[0].t
    dur = max(frames[-1].t - frames[0].t, 1e-3)
    idx = int(np.argmin([abs(f.t - event_t) for f in frames]))   # hodisa lahzasidagi kadr
    kept: dict = {}
    # Buferdagi kadrlar tezligi o'zgaruvchan (bo'sh kamera 4/s, odam bo'lsa 12/s): klip doimiy
    # CLIP_FPS da yoziladi, kadrlar vaqt bo'yicha takrorlanadi — video real tezlikda o'ynaydi
    fps = float(CLIP_FPS) if len(frames) > 1 else 10.0
    n_out = max(1, int(round(dur * fps)) + 1) if len(frames) > 1 else 1

    def stream(plain: bool = False):
        key = "clean" if plain else "thumb"
        it = iter_rendered(frames, highlight, banner, live, susp, with_index=True, clean=plain)
        cur = next(it, None)
        if cur is None:
            return
        nxt = next(it, None)
        t0 = frames[0].t
        for k in range(n_out):
            tk = t0 + k / fps
            while nxt is not None and frames[nxt[0]].t <= tk + 1e-6:
                cur, nxt = nxt, next(it, None)
            i, img = cur
            if i >= idx and f"{key}_i" not in kept:   # hodisa lahzasi (yoki undan keyingi birinchi) kadr
                kept[key], kept[f"{key}_i"] = img.copy(), i
            elif key not in kept:
                kept[key] = img
            yield img

    if clean:
        clean_path = out_dir / f"{stem}_clean.mp4"
        try:
            if write_mp4(lambda: stream(True), clean_path, fps):
                res["clip_clean"] = clean_path.name
        except Exception as e:  # noqa: BLE001 — toza nusxa chiqmasa ham asosiy klip yoziladi
            log.warning("Toza klip yozilmadi (%s): %s", stem, e)
            clean_path.unlink(missing_ok=True)
        if "clean" in kept:
            tc = out_dir / f"{stem}_clean.jpg"
            if cv2.imwrite(str(tc), kept["clean"], [cv2.IMWRITE_JPEG_QUALITY, 88]):
                res["thumb_clean"] = tc.name
        if on_clean is not None and res["clip_clean"]:
            try:
                on_clean(dict(res))
            except Exception:  # noqa: BLE001 — xabar xatosi klip yozishni to'xtatmasin
                log.exception("Toza klip tayyorligi haqida xabarda xato (%s)", stem)
    clip_path = out_dir / f"{stem}.mp4"
    try:
        if write_mp4(stream, clip_path, fps):
            res["clip"] = clip_path.name
    except Exception as e:  # noqa: BLE001
        clip_path.unlink(missing_ok=True)
        if not res["clip_clean"]:
            raise                      # toza nusxa ham yo'q — xato avvalgidek chaqiruvchiga (klip yaratilmadi)
        # Belgili nusxa (chizish) xatosi toza nusxani yo'qotmasin — chaqiruvchi toza nusxani asosiy klip qiladi
        log.warning("Belgili klip yozilmadi (%s): %s — toza nusxa qoladi", stem, e)
        if "thumb_i" not in kept:
            kept.pop("thumb", None)    # chizish hodisa lahzasigacha yetmagan — signal lahzasidagi rasm o'zgarmaydi
    if "thumb" not in kept:
        return res
    try:                               # rasm/qirqim xatosi tayyor klip(lar)ni yo'qotmasin
        thumb_path = out_dir / f"{stem}.jpg"
        if cv2.imwrite(str(thumb_path), kept["thumb"], [cv2.IMWRITE_JPEG_QUALITY, 88]):
            res["thumb"] = thumb_path.name
        idx = min(idx, len(frames) - 1)
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
                    if cv2.imwrite(str(crop_path), raw[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 90]):
                        res["crop"] = crop_path.name
    except Exception:  # noqa: BLE001
        log.exception("Klip rasmi yoki odam qirqimi yozilmadi (%s)", stem)
    return res
