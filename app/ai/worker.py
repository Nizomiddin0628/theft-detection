"""Video manbalarini qayta ishlash: jonli kamera (veb-kamera, RTSP) va yuklangan video fayl."""
from __future__ import annotations

import os
import platform
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import db
from ..logutil import get_logger
from . import annotate
from .analyzer import Analyzer
from .clipper import RingBuffer
from .engine import Engine, FrameResult
from .tracker import Tracker

log = get_logger("worker")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

PREVIEW_WIDTH = 960


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
        return cv2.VideoCapture(uri, cv2.CAP_FFMPEG)
    return cv2.VideoCapture(uri)


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


@dataclass
class Processor:
    """Bitta manba uchun umumiy AI qadamlari (detektsiya → kuzatuv → tahlil)."""
    source_name: str
    tracker: Tracker = field(default_factory=Tracker)
    analyzer: Analyzer = field(default=None)  # type: ignore[assignment]
    frame_no: int = 0
    last_objects: list = field(default_factory=list)

    def __post_init__(self):
        if self.analyzer is None:
            self.analyzer = Analyzer(self.source_name)

    def step(self, frame: np.ndarray, t: float):
        eng = Engine.get()
        s = db.get_settings()
        size = int(float(s.get("infer_size", 640)))
        pconf = float(s.get("person_conf", 0.4))
        oconf = float(s.get("object_conf", 0.3))
        persons = eng.detect_persons(frame, size=size, conf=min(pconf, 0.3))
        persons = [p for p in persons if p.score >= 0.25]
        # Buyumlar har 2-qadamda (CPU tejash), oraliqda oldingi natija ishlatiladi
        if self.frame_no % 2 == 0:
            self.last_objects = eng.detect_objects(frame, size=size, conf=oconf)
        self.frame_no += 1
        self.tracker.high = pconf
        tracked = self.tracker.update(persons, t)
        h, w = frame.shape[:2]
        alerts, status = self.analyzer.update(tracked, self.last_objects, t, w, h)
        res = FrameResult(tracked, self.last_objects, w, h)
        ann = ([(p.track_id, p.box.copy(), p.kpts.copy()) for p in tracked],
               [(o.box.copy(), o.name, o.kind) for o in self.last_objects],
               {k: {"risk": v["risk"]} for k, v in status.items()})
        return res, ann, alerts, status


def preview_jpeg(frame: np.ndarray, ann: tuple | None, time_text: str | None = None,
                 quality: int = 75) -> bytes:
    h, w = frame.shape[:2]
    scale = min(1.0, PREVIEW_WIDTH / w)
    img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame.copy()
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
        pre = db.fnum("pre_sec", 6)
        post = db.fnum("post_sec", 4)
        self.ring = RingBuffer(seconds=pre + post + 4, max_fps=15)
        self.pending: list[PendingClip] = []
        self.proc = Processor(source["name"])
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name=f"read-{source['id']}")
        self.track_status: dict = {}

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
            while not self.stop_evt.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                now = time.time()
                if pace:
                    frame_i += 1
                    target = t_play0 + frame_i / vid_fps
                    if target > now:
                        time.sleep(target - now)
                        now = time.time()
                with self.frame_lock:
                    self.latest_frame = frame
                    self.latest_t = now
                self.ring.push(frame, now, now)
                n += 1
                if now - t_fps >= 1.0:
                    self.fps_cam = n / (now - t_fps)
                    n, t_fps = 0, now
            cap.release()
            if self.stop_evt.is_set():
                break
            if kind == "file" and not loop:
                self.state = "tugadi"
                break
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
            self.ring.set_annotations(ann)
            self.track_status = status
            self.persons = len(ann[0])
            self.max_risk = max([v["risk"] for v in status.values()], default=0.0)
            self.latest_jpeg = preview_jpeg(frame, ann, annotate.wall_clock(ft))
            for a in alerts:
                self._on_alert(a, ft)
            self._flush_pending(ft)
            n += 1
            now = time.time()
            if now - t_fps >= 1.0:
                self.fps_ai = n / (now - t_fps)
                n, t_fps = 0, now
            spent = time.time() - t0
            wait = 1.0 / ai_fps - spent
            if wait > 0:
                self.stop_evt.wait(wait)
        self._flush_pending(1e18, force=True)
        if self.state not in ("xato", "tugadi"):
            self.state = "to'xtatildi"

    def _on_alert(self, alert: dict, t: float):
        self.alerts += 1
        pre = db.fnum("pre_sec", 6)
        post = db.fnum("post_sec", 4)
        ev_id = self.manager.create_event(alert, source=self.src, job_id=None, video_time=None)
        banner = f"TEKSHIRING: {alert['kind_label']} | ball {alert['score']:.0f}"
        self.pending.append(PendingClip(ev_id, t, t - pre, t + post, alert["track_id"], banner,
                                        alert["box"], True))

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
            "running": self.is_alive(),
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
        step = max(1, int(round(fps / ai_fps)))
        pre, post = db.fnum("pre_sec", 6), db.fnum("post_sec", 4)
        ring = RingBuffer(seconds=pre + post + 4, max_fps=15)
        src = {"id": None, "name": f"Video: {self.job['filename']}", "kind": "upload"}
        proc = Processor(src["name"])
        pending: list[PendingClip] = []
        idx = 0
        last_progress = -1.0
        err = None
        try:
            while not self.stop_evt.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                t = idx / fps
                if idx % step == 0:
                    _, ann, alerts, status = proc.step(frame, t)
                    ring.set_annotations(ann)
                    ring.push(frame, t, time.time())
                    if idx % (step * 3) == 0:
                        self.latest_jpeg = preview_jpeg(frame, ann, f"video {t:5.1f} s")
                    for a in alerts:
                        self.events += 1
                        ev_id = self.manager.create_event(a, source=src, job_id=jid, video_time=t)
                        banner = f"TEKSHIRING: {a['kind_label']} | ball {a['score']:.0f}"
                        pending.append(PendingClip(ev_id, t, t - pre, t + post, a["track_id"], banner,
                                                   a["box"], False))
                else:
                    ring.push(frame, t, time.time())
                for p in [p for p in pending if t >= p.t_end]:
                    pending.remove(p)
                    self.manager.finish_clip(p, ring.slice(p.t_start, p.t_end))
                idx += 1
                if total:
                    self.progress = min(1.0, idx / total)
                    if self.progress - last_progress >= 0.01:
                        last_progress = self.progress
                        db.execute("UPDATE jobs SET progress=?, events_count=? WHERE id=?",
                                   (round(self.progress * 100, 1), self.events, jid))
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
