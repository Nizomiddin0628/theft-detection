"""Barcha manbalar, vazifalar va hodisalarni boshqaruvchi markaz."""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path

from .. import db
from ..config import CLIPS_DIR, FEEDBACK_DIR, POSES_DIR, SNAP_DIR, UPLOADS_DIR
from ..logutil import get_logger
from .clipper import make_clip, make_snapshot
from .safety import SAFETY_KINDS
from .worker import FileJob, PendingClip, SourceWorker, open_capture

log = get_logger("manager")


# Login:parol — oxirgi "@" gacha (parolda "@" bo'lishi mumkin: rtsp://admin:Admin@123@10.0.0.9/...)
_CRED_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s]*@")


def mask_uri(uri: str) -> str:
    """rtsp://admin:parol@10.0.0.9:554/... → rtsp://***@10.0.0.9:554/... (panel va API uchun)."""
    return _CRED_RE.sub(r"\1***@", uri or "")


class Manager:
    def __init__(self):
        self.workers: dict[int, SourceWorker] = {}
        self.jobs: dict[int, FileJob] = {}
        self.job_queue: "queue.Queue[int]" = queue.Queue()
        self.lock = threading.RLock()
        self.notifier = None          # Telegram bot (keyinroq ulanadi)
        self.listeners: list = []     # hodisa xabarlarini oluvchilar (web panel SSE): fn(kind, event)
        self._job_thread = threading.Thread(target=self._job_loop, daemon=True, name="jobs")
        self._job_thread.start()
        self._last_cleanup = 0.0
        self.shutting_down = False    # 3.0.0: dastur to'xtayapti — bulut sinovi "bekor" emas, qaytadan tahlil qilinadi
        self.health: dict[int, dict] = {}   # kamera holati: {source_id: {"since", "alerted", "reason"}}
        self._started = time.time()
        self._load_health()
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True, name="health")
        self._health_thread.start()

    # ---------------- Xabar tarqatish ----------------
    def emit(self, kind: str, event_or_id) -> None:
        """Web panelga (SSE) hodisa haqida darhol xabar: new | clip | update."""
        ev = db.get_event(event_or_id) if isinstance(event_or_id, int) else event_or_id
        if not ev:
            return
        for fn in list(self.listeners):
            try:
                fn(kind, ev)
            except Exception:
                pass

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
        self.shutting_down = True
        for sid in list(self.workers):
            self.stop_source(sid)
        for j in list(self.jobs.values()):
            j.stop()

    def source_info(self) -> list[dict]:
        out = []
        nz = {r["source_id"]: r["n"] for r in
              db.rows("SELECT source_id, COUNT(*) AS n FROM zones WHERE enabled=1 GROUP BY source_id")}
        for s in db.rows("SELECT * FROM sources ORDER BY id"):
            w = self.workers.get(s["id"])
            info = w.info() if w and w.is_alive() else {
                "id": s["id"], "name": s["name"], "kind": s["kind"],
                "state": (w.state if w else "to'xtatilgan"), "error": (w.error if w else ""),
                "fps_cam": 0, "fps_ai": 0, "persons": 0, "max_risk": 0, "alerts": 0, "uptime": 0,
                "running": False}
            info["uri"] = mask_uri(s["uri"])   # RTSP login/paroli panelga chiqmaydi (bazada to'liq saqlanadi)
            info["enabled"] = s["enabled"]
            info["zones"] = nz.get(s["id"], 0)
            out.append(info)
        return out

    def raw_snapshot(self, source_id: int, fresh: bool = True) -> bytes | None:
        """Zona chizish uchun toza (belgisiz) kadr.

        Kamera ishlayotgan bo'lsa — uning oxirgi kadri; aks holda manba qisqa ochiladi. Olingan kadr diskka
        saqlanadi: kamera keyin o'chiq bo'lsa ham zonalarni tahrirlash mumkin.
        """
        import cv2

        path = SNAP_DIR / f"src_{int(source_id)}.jpg"
        frame = None
        w = self.workers.get(source_id)
        if w and w.is_alive():
            with w.frame_lock:
                frame = None if w.latest_frame is None else w.latest_frame.copy()
        if frame is None and fresh:
            src = db.row("SELECT * FROM sources WHERE id=?", (source_id,))
            if src:
                cap = open_capture(src["kind"], src["uri"])
                try:
                    for _ in range(1 if src["kind"] == "file" else 8):   # ba'zi kameralar avval qora kadr beradi
                        ok, f = cap.read()
                        if ok and f is not None:
                            frame = f
                finally:
                    cap.release()
        if frame is None:
            return path.read_bytes() if path.exists() else None
        h, wd = frame.shape[:2]
        if wd > 1280:
            frame = cv2.resize(frame, (1280, int(h * 1280 / wd)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        data = buf.tobytes()
        try:
            path.write_bytes(data)
        except OSError:
            pass
        return data

    def preview(self, source_id: int, viewing: bool = False) -> bytes:
        w = self.workers.get(source_id)
        if w and viewing:
            w.last_view = time.time()   # jonli oqim ochiq — oldindan ko'rish har kadrda tayyorlanadi
        return w.latest_jpeg if w else b""

    def autostart(self) -> None:
        for s in db.rows("SELECT id FROM sources WHERE enabled=1"):
            self.start_source(s["id"])
        self.requeue_cloud_jobs()

    def requeue_cloud_jobs(self) -> int:
        """3.0.0: bulut video sinovlari (yuklab olingan, tahlil kutayotgan) dastur qayta ishga tushganda navbatga."""
        ids = [r["id"] for r in db.rows("SELECT id FROM jobs WHERE cloud_test_id IS NOT NULL AND status='navbatda' "
                                        "ORDER BY id")]
        for jid in ids:
            self.job_queue.put(jid)
        return len(ids)

    # ---------------- Video vazifalari ----------------
    def submit_job(self, path: Path, filename: str, zone_source_id: int | None = None) -> int:
        jid = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at, zone_source_id) "
                         "VALUES(?,?,?,?,?,?)", (filename, str(path), "navbatda", 0, time.time(), zone_source_id))
        self.job_queue.put(jid)
        return jid

    def queue_job(self, jid: int) -> None:
        """3.0.0: oldindan yozilgan (holati "navbatda") vazifani navbatga qo'yish — bulut video sinovi."""
        self.job_queue.put(jid)

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
            cloud = bool(job.get("cloud_test_id"))
            try:
                fj.start()
                if cloud:
                    _cloud_changed()
                fj.join()
            finally:
                self.jobs.pop(jid, None)
                if cloud:
                    _cloud_changed()

    # ---------------- Hodisalar ----------------
    def create_event(self, alert: dict, source: dict, job_id: int | None, video_time: float | None,
                     snapshot: dict | None = None, no_clip: bool = False) -> int:
        """Hodisani yozadi va DARHOL xabar beradi (klipni kutmasdan).

        snapshot: {"frame", "ann", "banner", "time_text"} — signal lahzasidagi kadr; undan rasm tayyorlanadi
        va Telegram'ga birinchi bo'lib shu rasm ketadi. Klip keyin finish_clip'da qo'shiladi.
        """
        now = time.time()
        # 3.0.0: bulut panelidan yuborilgan video sinovi — do'kondagi panelda ovozli signal yo'q (ro'yxat yangilanadi).
        # 3.1.0: Telegram'ga esa boradi ("🧪 Video sinovi" belgisi bilan) — signal aniqlansa xabar berish asosiy qoida
        cloud = _is_cloud_job(job_id)
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
            "ml_ch": alert.get("ml_ch"),             # 2.3.0: challenger (soya) model ehtimoli
            "ml_ch_ver": alert.get("ml_ch_ver"),
            "thr": alert.get("thr"),                 # 2.3.0: signal paytidagi haqiqiy chegara
            "ml_w": alert.get("ml_w"),               # 2.3.0: signal paytidagi ML og'irligi (ML o'chiq — NULL)
            "score_raw": alert.get("score_raw"),
            "susp_start": alert.get("susp_start"),
            "susp_end": alert.get("susp_end", alert.get("t")),
            # Jonli kamerada alert["t"] — kadr olingan devor vaqti; video faylda — hodisa yaratilgan vaqt
            "t_detect": float(alert["t"]) if video_time is None else now,
            "status": "yangi",
            **({"clip_error": 1} if no_clip else {}),   # 2.2.0: klip bo'lmaydi — "tayyorlanmoqda" deb va'da qilinmasin
        })
        if snapshot is not None:
            try:
                name = f"ev{ev_id:05d}_{time.strftime('%Y%m%d_%H%M%S')}_snap.jpg"
                clean = name.replace("_snap.jpg", "_snap_clean.jpg")
                if make_snapshot(snapshot["frame"], snapshot.get("ann"), alert["track_id"], snapshot.get("banner", ""),
                                 snapshot.get("time_text"), CLIPS_DIR / name, clean_path=CLIPS_DIR / clean):
                    db.update_event(ev_id, thumb_path=name,
                                    thumb_clean=clean if (CLIPS_DIR / clean).exists() else None)
            except Exception:
                log.exception("Hodisa #%d rasmini tayyorlab bo'lmadi", ev_id)
        pose_path = POSES_DIR / f"event_{ev_id:05d}.json"
        pose_path.write_text(json.dumps({
            "event_id": ev_id, "track_id": alert["track_id"], "t_event": alert["t"],
            "kind": alert["kind"], "seq": alert.get("pose_seq", [])}), encoding="utf-8")
        db.update_event(ev_id, pose_path=pose_path.name)
        log.info("Hodisa #%d: %s, ball %.0f (%s)", ev_id, alert["kind_label"], alert["score"],
                 source.get("name"))
        # Klipni kutmasdan: web panelga va Telegram'ga darhol (bulut sinovi — panelda ovozsiz, Telegram'ga baribir)
        self.emit("update" if cloud else "new", ev_id)
        if self.notifier is not None and _tg_allowed(job_id, cloud, ev_id):
            try:
                self.notifier.notify_alert(ev_id)
            except Exception:
                log.exception("Telegramga darhol xabar berishda xato")
        if job_id is None and alert.get("kind") in SAFETY_KINDS:
            # 2.2.0: kritik xavfsizlik signali — SMS (sozlangan bo'lsa; alohida oqimda, kamera kutmaydi)
            try:
                from .. import sms

                sms.notify_event(ev_id)
            except Exception:  # noqa: BLE001
                log.exception("SMS navbatiga qo'yishda xato")
        return ev_id

    def inject_alert(self, alert: dict) -> bool:
        """2.2.0: kameradan tashqari signal (do'kon bo'yicha gavjumlik) — eng ko'p odam ko'rayotgan jonli kameraga
        beriladi (rasm va klip o'sha kameradan). Jonli kamera bo'lmasa — rasmsiz hodisa."""
        live = [w for w in list(self.workers.values())
                if w.is_alive() and w.src.get("kind") != "file" and w.state == "ishlayapti"]
        live = [w for w in live if getattr(w, "injected", None) is not None]
        if live:
            w = max(live, key=lambda x: x.persons)
            w.injected.append(dict(alert))
            return True
        self.create_event(alert, source={"id": None, "name": "Do'kon"}, job_id=None, video_time=None, no_clip=True)
        return False

    def finish_clip(self, p: PendingClip, frames: list) -> None:
        stem = f"ev{p.event_id:05d}_{time.strftime('%Y%m%d_%H%M%S')}"
        susp = (p.susp_start, p.susp_end) if p.susp_start is not None else None
        ev0 = db.get_event(p.event_id) or {}
        tg_ok = self.notifier is not None and _tg_allowed(ev0.get("job_id"), None, p.event_id)
        early = [False]
        got: dict = {}

        def on_clean(r: dict) -> None:
            # 3.1.0: toza klip tayyor — Telegram belgili nusxani kutmaydi (u faqat toza nusxani yuboradi)
            got.update(r)
            upd_ = {"clip_clean": r["clip_clean"]}
            if r.get("thumb_clean"):
                upd_["thumb_clean"] = r["thumb_clean"]
                if ev0.get("thumb_clean") and ev0["thumb_clean"] != r["thumb_clean"]:
                    (CLIPS_DIR / ev0["thumb_clean"]).unlink(missing_ok=True)
            db.update_event(p.event_id, **upd_)
            if tg_ok:
                early[0] = True
                try:
                    self.notifier.notify_clip(p.event_id)
                except Exception:
                    log.exception("Telegramga klip yuborishda xato")
        try:
            res = make_clip(frames, p.t_event, p.track_id, p.banner, p.live, CLIPS_DIR, stem, p.box, susp,
                            on_clean=on_clean)
        except Exception:
            log.exception("Klip yaratishda xato (hodisa #%d)", p.event_id)
            # toza nusxa allaqachon yozilgan bo'lsa (on_clean) — u yo'qolmasin
            res = {"clip": None, "thumb": None, "crop": None, "clip_start": got.get("clip_start"),
                   "clip_clean": got.get("clip_clean"), "thumb_clean": got.get("thumb_clean")}
        if not res.get("clip") and res.get("clip_clean"):
            # belgili nusxa yozilmadi — toza nusxa asosiy klip bo'ladi (bitta fayl ikki maydonda turmasin: bulutga
            # ikki marta ketmasin, panelda bir xil ikki video almashtirilmasin)
            res["clip"], res["clip_clean"] = res["clip_clean"], None
        old = db.get_event(p.event_id) or {}
        upd = {"clip_path": res["clip"], "crop_path": res["crop"], "clip_start": res.get("clip_start"),
               # 3.1.0: chizmalarsiz nusxa (Telegram'ga shu ketadi). U yozilmasa — eski (belgili) klip yuboriladi
               "clip_clean": res.get("clip_clean"),
               # Klip chiqmadi — Telegram xabarida "tayyorlanmoqda" o'rniga "klip yaratilmadi" yoziladi
               "clip_error": 0 if res["clip"] else 1}
        if res["thumb"]:
            upd["thumb_path"] = res["thumb"]
            # Signal lahzasidagi tezkor rasm endi kerak emas — klipdan olingan rasm bilan almashadi
            if old.get("thumb_path") and old["thumb_path"] != res["thumb"]:
                (CLIPS_DIR / old["thumb_path"]).unlink(missing_ok=True)
        if res.get("thumb_clean"):
            upd["thumb_clean"] = res["thumb_clean"]
            if old.get("thumb_clean") and old["thumb_clean"] != res["thumb_clean"]:
                (CLIPS_DIR / old["thumb_clean"]).unlink(missing_ok=True)
        db.update_event(p.event_id, **upd)
        self.emit("clip", p.event_id)
        if tg_ok and not early[0]:
            try:
                self.notifier.notify_clip(p.event_id)
            except Exception:
                log.exception("Telegramga klip yuborishda xato")
        self._cleanup()

    # 1.9.0: "Kamera ishlamayapti" xabari yuborilgan kameralar bazada eslab qolinadi — dastur shu paytda qayta ishga
    # tushsa (yangilanish, kompyuter o'chib-yondi) "Kamera tiklandi" xabari yo'qolmasin va "ishlamayapti" takrorlanmasin
    HEALTH_KEY = "camera_health_alerted"

    def _load_health(self) -> None:
        try:
            saved = json.loads(db.get_setting(self.HEALTH_KEY, "") or "{}")
        except Exception:  # noqa: BLE001 — baza hali tayyor emas yoki yozuv buzilgan: eslab qolinmagan deb olinadi
            saved = {}
        for k, v in (saved.items() if isinstance(saved, dict) else []):
            try:
                self.health[int(k)] = {"since": float(v["since"]), "alerted": True, "reason": str(v.get("reason", "")),
                                       "name": str(v.get("name", "")), "restored": True}
            except (KeyError, TypeError, ValueError):
                continue

    def _save_health(self) -> None:
        data = {str(k): {"since": h["since"], "reason": h.get("reason", ""), "name": h.get("name", "")}
                for k, h in self.health.items() if h.get("alerted")}
        try:
            db.set_settings({self.HEALTH_KEY: json.dumps(data, ensure_ascii=False)})
        except Exception:  # noqa: BLE001
            log.exception("Kamera holatini saqlab bo'lmadi")

    def _health_loop(self) -> None:
        """Kamera uzilsa yoki ishdan chiqsa — belgilangan vaqtdan keyin Telegram'ga xabar, tiklansa — yana xabar."""
        while True:
            time.sleep(5)
            try:
                self._health_check()
            except Exception:
                log.exception("Kamera holatini tekshirishda xato")

    def _health_check(self) -> None:
        now = time.time()
        from . import safety

        safety.store_check(self)          # 2.2.0: do'kon bo'yicha gavjumlik ("hozir ichkarida")
        alert_sec = db.fnum("camera_alert_sec", 60)
        enabled = db.flag("camera_alerts")
        seen = set()
        for sid, w in list(self.workers.items()):
            if w.src.get("kind") == "file":
                continue   # video fayl tugashi — nosozlik emas
            seen.add(sid)
            reason = None
            if not w.is_alive():
                reason = "dastur ichida manba to'xtab qoldi"
            elif w.state in ("xato", "qayta ulanmoqda"):
                reason = w.error or w.state
            elif now - w.started_at > 20 and (not w.latest_t or now - w.latest_t > 15):
                # 1.9.0: ulanib, lekin bitta ham kadr bermagan kamera (masalan, NVR kanali o'lik) ham
                reason = "kameradan kadr kelmayapti"
            h = self.health.get(sid)
            if reason:
                if h is None:
                    self.health[sid] = h = {"since": now, "alerted": False, "reason": reason, "name": w.src["name"]}
                h["reason"] = reason
                if not h["alerted"] and now - h["since"] >= alert_sec:
                    h["alerted"] = True
                    self._save_health()
                    log.warning("Kamera ishlamayapti: %s (%s)", w.src["name"], reason)
                    self._sync_health(w.src, "down", reason, h["since"])
                    if enabled and self.notifier is not None:
                        from ..telegram_bot import esc
                        self.notifier.send_text_all(
                            f"📷 <b>Kamera ishlamayapti:</b> {esc(w.src['name'])}\n"
                            f"Sabab: {esc(reason)}\n"
                            f"Qachondan: {time.strftime('%H:%M:%S', time.localtime(h['since']))}")
            elif h is not None:
                if h.get("restored") and not (w.latest_t and w.latest_t >= w.started_at and now - w.latest_t <= 15):
                    continue    # qayta ishga tushgach kamera hali yangi kadr bermagan — "tiklandi" deyishga erta
                self.health.pop(sid, None)
                if h["alerted"]:
                    self._save_health()
                    secs = now - h["since"]
                    dur = f"{secs / 60:.0f} daqiqa" if secs >= 90 else f"{secs:.0f} soniya"
                    log.info("Kamera tiklandi: %s (%s)", w.src["name"], dur)
                    self._sync_health(w.src, "up", f"{dur} ishlamadi", h["since"])
                    if enabled and self.notifier is not None:
                        from ..telegram_bot import esc
                        self.notifier.send_text_all(f"✅ <b>Kamera tiklandi:</b> {esc(w.src['name'])} "
                                                    f"({dur} ishlamadi)")
        gone = [s for s in self.health if s not in seen
                and not (self.health[s].get("restored") and now - self._started < 120)]   # ishga tushish payti
        for sid in gone:
            self.health.pop(sid, None)   # foydalanuvchi o'zi to'xtatgan manba
        if gone:
            self._save_health()

    @staticmethod
    def _sync_health(src: dict, state: str, reason: str, since: float | None) -> None:
        """Kamera holati o'zgarishi bulut navbatiga (sozlangan bo'lsa)."""
        try:
            from .. import sync

            sync.enqueue_health(src, state, reason, since)
        except Exception:  # noqa: BLE001
            log.exception("Kamera holatini bulut navbatiga qo'yishda xato")

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
            # Rad etilgan hodisa — modelni o'qitish uchun eng qimmatli ma'lumot: video o'chiriladi,
            # lekin skelet izi va qo'riqchi javobi arxivda qoladi
            archive_feedback(ev)
            try:
                from .. import sync

                sync.forget_event(ev["id"])
            except Exception:  # noqa: BLE001
                pass
            delete_event_files(ev)
            db.execute("DELETE FROM events WHERE id=?", (ev["id"],))
            db.execute("DELETE FROM tg_messages WHERE event_id=?", (ev["id"],))
        if old:
            log.info("%d ta eski hodisa o'chirildi", len(old))
        # Kechikish jurnali ham cheksiz o'smasin (hodisalar bilan bir xil muddat)
        db.execute("DELETE FROM notify_log WHERE t_sent < ?", (cutoff,))
        # Eski yuklangan videolar (tahlil qilib bo'lingan) ham disk to'lmasligi uchun o'chiriladi
        running = {str(j.job.get("path")) for j in list(self.jobs.values())}
        live_files = {str(w.src.get("uri")) for w in list(self.workers.values()) if w.is_alive()}
        for f in UPLOADS_DIR.glob("*"):
            try:
                if (f.is_file() and f.stat().st_mtime < cutoff and str(f) not in running
                        and str(f) not in live_files):
                    f.unlink()
            except OSError:
                pass


CLOUD_TEST_TG_MAX = 20     # 3.1.0: bitta bulut sinovidan Telegram'ga ko'pi bilan shuncha signal (qolgani — natijada)


def _tg_allowed(job_id, cloud: bool | None = None, event_id: int | None = None) -> bool:
    """Telegram'ga yuboriladimi: jonli kamera va do'kondagi yuklangan video — doim; bulut Video sinovi — birinchi
    CLOUD_TEST_TG_MAX ta signali (uzun videoda yuzlab xabar ketmasin)."""
    if job_id is None:
        return True
    if cloud is None:
        cloud = _is_cloud_job(job_id)
    if not cloud:
        return True
    try:                      # shu hodisa sinovdagi nechanchi signal (keyingilari hisobga olinmaydi)
        ids = [int(job_id)]
        # dastur qayta ishga tushib sinov yangi qator bilan qaytadan tahlil qilingan bo'lsa — oldingi urinishlarda
        # yuborilganlar ham sanaladi (har qayta urinish yangi 20 ta xabar bermasin)
        try:
            meta = json.loads(db.scalar("SELECT cloud_meta FROM jobs WHERE id=?", (int(job_id),)) or "{}")
            ids += [int(x) for x in (meta.get("prev_jobs") or [])[:5]]
        except (TypeError, ValueError, AttributeError):
            pass
        n = db.scalar(f"SELECT COUNT(*) FROM events WHERE job_id IN ({','.join('?' for _ in ids)}) AND id<=?",
                      (*ids, int(event_id) if event_id is not None else 2 ** 62)) or 0
    except Exception:  # noqa: BLE001
        return True
    return n <= CLOUD_TEST_TG_MAX


def _is_cloud_job(job_id) -> bool:
    """3.0.0: vazifa bulut panelidagi Video sinovimi (jobs.cloud_test_id)."""
    if job_id is None:
        return False
    try:
        return bool(db.scalar("SELECT cloud_test_id FROM jobs WHERE id=?", (int(job_id),)))
    except Exception:  # noqa: BLE001
        return False


def _cloud_changed() -> None:
    """3.0.0: bulut video sinovining holati o'zgardi — bulut paneli tezroq bilsin (navbatdan tashqari hello)."""
    try:
        from .. import sync

        sync.hello_soon()
    except Exception:  # noqa: BLE001
        pass


def archive_feedback(ev: dict) -> None:
    """Tekshirilgan (tasdiqlangan / rad etilgan) hodisaning skelet izi va javobini arxivga yozadi.

    Hodisa o'chirilsa ham, bu ma'lumot modelni qayta o'qitishda ishlatiladi. 2.3.0: javob berilmagan (yangi) hodisa ham
    yoziladi — jonli darvoza uni "javobsiz" deb sanaydi (o'qitish faqat ✅/❌ larni oladi).
    """
    if ev.get("status") not in ("tasdiqlangan", "rad_etilgan", "yangi"):
        return
    src = POSES_DIR / ev["pose_path"] if ev.get("pose_path") else None
    try:
        # 2.3.0: skelet izi bo'lmasa ham javob yoziladi — jonli xato darvozasi o'chirilgan ❌ larni ham sanasin
        # (o'qitish uchun izsiz yozuv yaroqsiz — feedback_windows uni o'tkazib yuboradi)
        data = json.loads(src.read_text(encoding="utf-8")) if src is not None and src.exists() else {}
        data.update({"event_id": ev["id"], "status": ev["status"], "kind": ev.get("kind"),
                     "score": ev.get("score"), "source_name": ev.get("source_name"),
                     "source_id": ev.get("source_id"), "job_id": ev.get("job_id"),
                     "created_at": ev.get("created_at"), "archived_at": time.time()})
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        (FEEDBACK_DIR / f"ev{int(ev['id']):05d}.json").write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        log.exception("Hodisa #%s javobini arxivlab bo'lmadi", ev.get("id"))


def delete_event_files(ev: dict) -> None:
    for key, base in (("clip_path", CLIPS_DIR), ("thumb_path", CLIPS_DIR), ("crop_path", CLIPS_DIR),
                      ("clip_clean", CLIPS_DIR), ("thumb_clean", CLIPS_DIR), ("pose_path", POSES_DIR)):
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
