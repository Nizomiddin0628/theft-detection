"""Barcha manbalar, vazifalar va hodisalarni boshqaruvchi markaz."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from .. import db
from ..config import CLIPS_DIR, POSES_DIR
from ..logutil import get_logger
from .clipper import make_clip
from .worker import FileJob, PendingClip, SourceWorker

log = get_logger("manager")


class Manager:
    def __init__(self):
        self.workers: dict[int, SourceWorker] = {}
        self.jobs: dict[int, FileJob] = {}
        self.job_queue: "queue.Queue[int]" = queue.Queue()
        self.lock = threading.RLock()
        self.notifier = None          # Telegram bot (keyinroq ulanadi)
        self.listeners: list = []     # yangi hodisa haqida xabar oluvchilar (SSE)
        self._job_thread = threading.Thread(target=self._job_loop, daemon=True, name="jobs")
        self._job_thread.start()
        self._last_cleanup = 0.0

    # ---------------- Manbalar ----------------
    def start_source(self, source_id: int) -> tuple[bool, str]:
        src = db.row("SELECT * FROM sources WHERE id=?", (source_id,))
        if not src:
            return False, "Manba topilmadi"
        with self.lock:
            w = self.workers.get(source_id)
            if w and w.is_alive():
                return True, "Allaqachon ishlayapti"
            if src["kind"] == "webcam":
                for other in self.workers.values():
                    if (other.is_alive() and other.src["kind"] == "webcam"
                            and other.src["uri"] == src["uri"]):
                        return False, "Bu kamera boshqa manbada ishlatilmoqda"
            w = SourceWorker(src, self)
            self.workers[source_id] = w
            w.start()
        log.info("Manba ishga tushdi: %s (%s)", src["name"], src["kind"])
        return True, "Ishga tushdi"

    def stop_source(self, source_id: int) -> None:
        with self.lock:
            w = self.workers.pop(source_id, None)
        if w:
            w.stop()
            log.info("Manba to'xtatildi: %s", w.src["name"])

    def stop_all(self) -> None:
        for sid in list(self.workers):
            self.stop_source(sid)
        for j in list(self.jobs.values()):
            j.stop()

    def source_info(self) -> list[dict]:
        out = []
        for s in db.rows("SELECT * FROM sources ORDER BY id"):
            w = self.workers.get(s["id"])
            info = w.info() if w and w.is_alive() else {
                "id": s["id"], "name": s["name"], "kind": s["kind"],
                "state": (w.state if w else "to'xtatilgan"), "error": (w.error if w else ""),
                "fps_cam": 0, "fps_ai": 0, "persons": 0, "max_risk": 0, "alerts": 0, "uptime": 0,
                "running": False}
            info["uri"] = s["uri"]
            info["enabled"] = s["enabled"]
            out.append(info)
        return out

    def preview(self, source_id: int) -> bytes:
        w = self.workers.get(source_id)
        return w.latest_jpeg if w else b""

    def autostart(self) -> None:
        for s in db.rows("SELECT id FROM sources WHERE enabled=1"):
            self.start_source(s["id"])

    # ---------------- Video vazifalari ----------------
    def submit_job(self, path: Path, filename: str) -> int:
        jid = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at) VALUES(?,?,?,?,?)",
                         (filename, str(path), "navbatda", 0, time.time()))
        self.job_queue.put(jid)
        return jid

    def stop_job(self, jid: int) -> None:
        j = self.jobs.get(jid)
        if j:
            j.stop()
        else:
            db.execute("UPDATE jobs SET status='toxtatildi' WHERE id=? AND status='navbatda'", (jid,))

    def job_preview(self, jid: int) -> bytes:
        j = self.jobs.get(jid)
        return j.latest_jpeg if j else b""

    def _job_loop(self):
        while True:
            jid = self.job_queue.get()
            job = db.row("SELECT * FROM jobs WHERE id=?", (jid,))
            if not job or job["status"] != "navbatda":
                continue
            fj = FileJob(job, self)
            self.jobs[jid] = fj
            try:
                fj.start()
                fj.join()
            finally:
                self.jobs.pop(jid, None)

    # ---------------- Hodisalar ----------------
    def create_event(self, alert: dict, source: dict, job_id: int | None, video_time: float | None) -> int:
        ev_id = db.insert_event({
            "source_id": source.get("id"),
            "source_name": source.get("name"),
            "job_id": job_id,
            "created_at": time.time(),
            "video_time": video_time,
            "score": alert["score"],
            "level": alert["level"],
            "kind": alert["kind"],
            "reasons": alert["reasons"],
            "breakdown": alert["breakdown"],
            "track_id": alert["track_id"],
            "ml_prob": alert.get("ml_prob"),
            "status": "yangi",
        })
        pose_path = POSES_DIR / f"event_{ev_id:05d}.json"
        pose_path.write_text(json.dumps({
            "event_id": ev_id, "track_id": alert["track_id"], "t_event": alert["t"],
            "kind": alert["kind"], "seq": alert.get("pose_seq", [])}), encoding="utf-8")
        db.update_event(ev_id, pose_path=pose_path.name)
        log.info("Hodisa #%d: %s, ball %.0f (%s)", ev_id, alert["kind_label"], alert["score"],
                 source.get("name"))
        return ev_id

    def finish_clip(self, p: PendingClip, frames: list) -> None:
        stem = f"ev{p.event_id:05d}_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            res = make_clip(frames, p.t_event, p.track_id, p.banner, p.live, CLIPS_DIR, stem, p.box)
        except Exception:
            log.exception("Klip yaratishda xato (hodisa #%d)", p.event_id)
            res = {"clip": None, "thumb": None, "crop": None}
        db.update_event(p.event_id, clip_path=res["clip"], thumb_path=res["thumb"], crop_path=res["crop"])
        ev = db.get_event(p.event_id)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:
                pass
        if self.notifier is not None:
            try:
                self.notifier.notify_event(p.event_id)
            except Exception:
                log.exception("Telegramga yuborishda xato")
        self._cleanup()

    def _cleanup(self) -> None:
        """Eski hodisa fayllarini o'chirish (sozlamadagi kun soniga ko'ra)."""
        if time.time() - self._last_cleanup < 3600:
            return
        self._last_cleanup = time.time()
        days = db.fnum("retention_days", 30)
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        old = db.rows("SELECT * FROM events WHERE created_at < ? AND status != 'tasdiqlangan'", (cutoff,))
        for ev in old:
            delete_event_files(ev)
            db.execute("DELETE FROM events WHERE id=?", (ev["id"],))
        if old:
            log.info("%d ta eski hodisa o'chirildi", len(old))


def delete_event_files(ev: dict) -> None:
    for key, base in (("clip_path", CLIPS_DIR), ("thumb_path", CLIPS_DIR), ("crop_path", CLIPS_DIR),
                      ("pose_path", POSES_DIR)):
        name = ev.get(key)
        if name:
            try:
                (base / name).unlink(missing_ok=True)
            except OSError:
                pass


_manager: Manager | None = None


def get_manager() -> Manager:
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager
