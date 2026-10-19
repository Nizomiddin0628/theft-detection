"""3.0.0: bulut panelidan yuborilgan video sinovlari ("Video sinovi") — tahlil shu do'kon kompyuterida.

Oqim:
  1. Egasi bulut panelida videoni yuklaydi (video bulutda vaqtincha turadi).
  2. Bulut hello javobida `video_test` buyrug'ini beradi → `accept`: jobs jadvalida cloud_test_id bilan qator
     ("yuklanmoqda"), video bulutdan yuklab olinadi (hajm va sha256 tekshiriladi, chala fayl .part).
  3. Video oddiy "Video tahlili" navbatiga qo'yiladi (Manager.queue_job) — jonli kameralar ustun turadi.
  4. Holat, foiz va natija (hodisalar soni, xato) har hello'da bulutga ketadi (`report`); hodisalar va kliplar
     odatdagi navbat orqali (job_id bilan) — bulut ularni sinovga bog'laydi. Video tahlili hodisalari uchun
     bulutda bildirishnoma yuborilmaydi (jonli emas).
  5. Bulutda bekor qilinsa — `video_test_cancel` buyrug'i (`cancel`).

Dastur tahlil paytida qayta ishga tushsa — db.init_db sinovni yangi qator bilan qaytadan navbatga qo'yadi
(oldingi chala tahlil hodisalari natijaga aralashmaydi); yuklab olinayotgani qaytadan yuklanadi (`resume`).
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from pathlib import Path

import httpx

from . import db
from .config import UPLOADS_DIR
from .logutil import get_logger

log = get_logger("cloud_tests")

MAX_SIZE = 2_000_000_000              # bulutniki bundan ancha kichik (sukut 300 MB) — himoya chegarasi
RETRY_DELAYS = (10.0, 60.0, 300.0)    # yuklab olish xatosidan keyin kutish (keyin — "xato")
MAX_RESTARTS = 2                      # tahlil shuncha marta dastur qayta ishga tushishi bilan chala qolsa — "xato"
REPORT_DAYS = 3                       # bulutga shuncha kunlik sinovlar holati yuboriladi (takroriy, idempotent)
ACTIVE = ("yuklanmoqda", "navbatda", "ishlanmoqda")
METRICS_MAX = 16_000                  # 3.1.0: o'lchovlar JSON chegarasi (bayt; FileJob kattasini qisqartiradi)
METRICS_RESEND_SEC = 6 * 3600         # bulut "saqlandi" demasa (eski bulut) — birinchi yetkazilgandan shuncha keyin to'xtaydi
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".3gp", ".ts"}

_q: "queue.Queue[int]" = queue.Queue()
_cancel: set[int] = set()
_thread: list = [None]
_cur: list = [None]                   # joriy SyncWorker (bulut sozlamasi o'zgarsa worker almashadi — oqim eskisini ushlamaydi)
_lock = threading.Lock()


class _Cancelled(Exception):
    pass


def _meta(job: dict) -> dict:
    try:
        m = json.loads(job.get("cloud_meta") or "{}")
        return m if isinstance(m, dict) else {}
    except ValueError:
        return {}


def _set(jid: int, **kw) -> None:
    if kw:
        cols = ", ".join(f"{k}=?" for k in kw)
        db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*kw.values(), jid))


def _display_name(raw) -> str:
    name = Path(str(raw or "video.mp4")).name.strip()[:120] or "video.mp4"
    return re.sub(r"[\x00-\x1f]", "", name)


def _changed() -> None:
    try:
        from . import sync

        sync.hello_soon()
    except Exception:  # noqa: BLE001
        pass


def active() -> bool:
    """Bulutdan kelgan sinov hali tugamaganmi (sync tez-tez hello yuboradi — panelda foiz yangilanib tursin)."""
    return bool(db.scalar(f"SELECT 1 FROM jobs WHERE cloud_test_id IS NOT NULL AND status IN ({','.join('?' * len(ACTIVE))}) "
                          "LIMIT 1", ACTIVE))


# ----------------------------------------------------------------------------- buyruqlar
def accept(cmd: dict, worker) -> bool:
    """`video_test` buyrug'i: sinovni qabul qilib, yuklab olish navbatiga qo'yadi. Takror kelsa — e'tiborsiz.
    Noto'g'ri buyruq — False (baribir ack qilinadi: qayta yuborishdan foyda yo'q)."""
    try:
        tid = int(cmd.get("test_id") or 0)
        size = int(cmd.get("size") or 0)
    except (TypeError, ValueError):
        tid = size = 0
    sha = str(cmd.get("sha256") or "").lower()
    if tid <= 0 or size <= 0 or size > MAX_SIZE or not re.fullmatch(r"[0-9a-f]{64}", sha):
        log.warning("Bulut video sinovi buyrug'i noto'g'ri: %s", {k: cmd.get(k) for k in ("test_id", "size")})
        return False
    token = str(cmd.get("token") or "")[:40]
    old = db.row("SELECT id, cloud_meta FROM jobs WHERE cloud_test_id=? ORDER BY id DESC LIMIT 1", (tid,))
    if old:
        if _meta(old).get("token", "") == token:
            return True                   # takroriy buyruq (ack yetmagan) — allaqachon qabul qilingan
        # Bulutda shu raqam boshqa sinovga berilgan (masalan, baza qayta tiklangan) — eski vazifa ajratiladi
        db.execute("UPDATE jobs SET cloud_test_id=NULL WHERE cloud_test_id=?", (tid,))
    name = _display_name(cmd.get("filename"))
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXT:
        ext = ".mp4"
    zsid = None
    try:
        z = int(cmd.get("zone_source_id") or 0)
        if z > 0 and db.row("SELECT id FROM sources WHERE id=?", (z,)):
            zsid = z
    except (TypeError, ValueError):
        pass
    meta = {"size": size, "sha256": sha, "tries": 0, "token": token}
    if cmd.get("zones"):
        # 3.1.0: panelda video kadri ustiga chizilgan zonalar — shu tahlil uchun kamera zonalari o'rniga
        try:
            from .ai import zones as zones_mod

            zs = zones_mod.from_list(cmd.get("zones"))
            if zs:
                meta["zones"] = zones_mod.to_list(zs)
                zsid = None
        except Exception:  # noqa: BLE001 — zonasiz ham tahlil qilinadi
            log.exception("Video sinovi zonalarini o'qib bo'lmadi")
    dest = UPLOADS_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_bulut{tid}{ext}"
    jid = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at, zone_source_id, cloud_test_id, "
                     "cloud_meta) VALUES(?,?,?,?,?,?,?,?)",
                     (name, str(dest), "yuklanmoqda", 0, time.time(), zsid, tid, json.dumps(meta)))
    log.info("Bulutdan video sinovi #%d qabul qilindi: %s (%.1f MB) — yuklab olinmoqda", tid, name, size / 1e6)
    _start(worker)
    _q.put(jid)
    _changed()
    return True


def cancel(cmd: dict, manager) -> None:
    """`video_test_cancel`: yuklab olinayotgan bo'lsa — to'xtatiladi; navbatda yoki tahlilda — Manager.stop_job."""
    try:
        tid = int(cmd.get("test_id") or 0)
    except (TypeError, ValueError):
        return
    job = db.row("SELECT * FROM jobs WHERE cloud_test_id=? ORDER BY id DESC LIMIT 1", (tid,))
    if not job or job["status"] not in ACTIVE:
        return
    if job["status"] == "yuklanmoqda":
        _cancel.add(job["id"])
        _set(job["id"], status="toxtatildi", finished_at=time.time(), error="bulut panelida bekor qilindi")
    elif manager is not None:
        manager.stop_job(job["id"])
    else:
        _set(job["id"], status="toxtatildi", finished_at=time.time(), error="bulut panelida bekor qilindi")
    log.info("Bulut video sinovi #%d bekor qilindi", tid)
    _changed()


def report() -> list[dict]:
    """hello uchun: oxirgi kunlardagi bulut sinovlari holati (bulut uni idempotent yangilaydi)."""
    since = time.time() - REPORT_DAYS * 86400
    out = []
    for r in db.rows("SELECT id, cloud_test_id, status, progress, events_count, duration, frames, error, cloud_meta, "
                     "metrics, finished_at FROM jobs WHERE cloud_test_id IS NOT NULL AND created_at >= ? ORDER BY id DESC "
                     "LIMIT 30", (since,)):
        item = {"id": int(r["cloud_test_id"]), "token": _meta(r).get("token", ""), "job_id": int(r["id"]),
                "status": r["status"], "progress": float(r["progress"] or 0), "events": int(r["events_count"] or 0),
                "duration": r["duration"], "frames": r["frames"], "error": str(r["error"] or "")[:300]}
        offered = _meta(r).get("metrics_offered_at")
        if (r["status"] == "tayyor" and r.get("metrics") and not _meta(r).get("metrics_sent")
                and len(r["metrics"]) <= METRICS_MAX
                and not (isinstance(offered, (int, float)) and time.time() - offered > METRICS_RESEND_SEC)):
            # 3.1.0: navbat, sanash, zonalar (kichik JSON) — bulut "saqlandi" deguncha (hello javobidagi
            # video_tests_stored); keyin har hello'da takrorlanmaydi
            try:
                m = json.loads(r["metrics"])
                if isinstance(m, dict):
                    item["metrics"] = m
            except ValueError:
                pass
        out.append(item)
    return out


def metrics_offered(test_ids) -> None:
    """3.1.0: o'lchovlar hello bilan bulutga yetdi (HTTP 200) — birinchi marta qachon (bulut "saqlandi" demasa,
    METRICS_RESEND_SEC dan keyin qayta yuborilmaydi; aloqa yo'q paytlar hisobga kirmaydi)."""
    now = time.time()
    for tid in list(test_ids)[:50]:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        for r in db.rows("SELECT id, cloud_meta FROM jobs WHERE cloud_test_id=? AND status='tayyor'", (tid,)):
            meta = _meta(r)
            if not isinstance(meta.get("metrics_offered_at"), (int, float)):
                meta["metrics_offered_at"] = now
                _set(r["id"], cloud_meta=json.dumps(meta))


def metrics_delivered(test_ids) -> int:
    """3.1.0: bulut o'lchovlarni saqladi (hello javobi) — endi hisobotda yuborilmaydi."""
    n = 0
    for tid in test_ids[:50]:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        for r in db.rows("SELECT id, cloud_meta FROM jobs WHERE cloud_test_id=? AND status='tayyor'", (tid,)):
            meta = _meta(r)
            if not meta.get("metrics_sent"):
                meta["metrics_sent"] = True
                _set(r["id"], cloud_meta=json.dumps(meta))
                n += 1
    return n


def recover() -> int:
    """Dastur ishga tushganda (web lifespan, faqat asosiy dasturda — init_db'ni chaqiradigan boshqa buyruqlarda emas):
    tahlil paytida to'xtab qolgan bulut sinovi yangi qator bilan qaytadan navbatga (oldingi chala tahlil hodisalari
    natijaga aralashmaydi). MAX_RESTARTS dan ko'p bo'lsa — "xato" (video dasturni yiqitayotgan bo'lishi mumkin)."""
    n = 0
    now = time.time()
    for r in db.rows("SELECT * FROM jobs WHERE cloud_test_id IS NOT NULL AND status='ishlanmoqda'"):
        meta = _meta(r)
        restarts = int(meta.get("restarts") or 0) + 1
        if restarts > MAX_RESTARTS:
            _set(r["id"], status="xato", finished_at=now,
                 error=f"tahlil {MAX_RESTARTS} marta chala qoldi (dastur qayta ishga tushdi) — video do'kon kompyuteriga "
                       "og'irlik qilayotgan bo'lishi mumkin")
            continue
        meta["restarts"] = restarts
        # 3.1.0: oldingi urinish(lar) — Telegram cheklovi (CLOUD_TEST_TG_MAX) sinov bo'yicha sanalsin
        meta["prev_jobs"] = [*[int(x) for x in (meta.get("prev_jobs") or []) if str(x).isdigit()], int(r["id"])][-5:]
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='toxtatildi', cloud_test_id=NULL, error=?, finished_at=? WHERE id=?",
                      ("dastur qayta ishga tushdi — sinov qaytadan tahlil qilinadi", now, r["id"]))
            c.execute("INSERT INTO jobs(filename, path, status, progress, created_at, zone_source_id, cloud_test_id, "
                      "cloud_meta) VALUES(?,?,?,?,?,?,?,?)", (r["filename"], r["path"], "navbatda", 0, now,
                                                             r["zone_source_id"], r["cloud_test_id"], json.dumps(meta)))
        n += 1
    if n:
        log.info("Bulut video sinovlari: %d ta chala tahlil qaytadan navbatga qo'yildi", n)
    return n


def stop_local(jid: int) -> bool:
    """Edge panelidagi "To'xtatish": bulutdan yuklab olinayotgan sinov (Manager.stop_job uni bilmaydi)."""
    job = db.row("SELECT * FROM jobs WHERE id=?", (jid,))
    if not job or not job.get("cloud_test_id") or job["status"] != "yuklanmoqda":
        return False
    _cancel.add(jid)
    _set(jid, status="toxtatildi", finished_at=time.time(), error="do'kon panelida to'xtatildi")
    _changed()
    return True


def resume(worker) -> int:
    """Dastur qayta ishga tushganda: yuklab olinayotgan sinovlar qaytadan navbatga."""
    ids = [r["id"] for r in db.rows("SELECT id FROM jobs WHERE cloud_test_id IS NOT NULL AND status='yuklanmoqda'")]
    if ids:
        _start(worker)
        for jid in ids:
            _q.put(jid)
    return len(ids)


# ----------------------------------------------------------------------------- yuklab olish
def _start(worker) -> None:
    with _lock:
        _cur[0] = worker
        t = _thread[0]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_loop, daemon=True, name="cloud-tests")
        _thread[0] = t
        t.start()


def _loop() -> None:
    """Yuklab olish oqimi: har vazifa uchun JORIY worker (uning HTTP mijozi va manzili) olinadi. Navbat bo'sh va
    worker to'xtagan bo'lsa — chiqadi (keyingi _start yangisini ochadi; tekshiruv qulf ostida — poyga yo'q)."""
    while True:
        try:
            jid = _q.get(timeout=5)
        except queue.Empty:
            w = _cur[0]
            if w is None or w.stop_evt.is_set():
                with _lock:
                    if _q.empty():
                        _thread[0] = None
                        return
            continue
        w = _cur[0]
        if w is None or w.stop_evt.is_set():
            _q.put(jid)                   # worker almashmoqda yoki dastur to'xtayapti — yangisini kutamiz
            time.sleep(1.0)
            continue
        try:
            download(jid, w)
        except Exception:  # noqa: BLE001
            log.exception("Bulut video sinovini yuklab olishda kutilmagan xato (vazifa #%s)", jid)


def download(jid: int, worker) -> str:
    """Videoni bulutdan yuklab, tekshirib, tahlil navbatiga qo'yadi. Qaytaradi: yakuniy holat (sinov uchun)."""
    job = db.row("SELECT * FROM jobs WHERE id=?", (jid,))
    if not job or job["status"] != "yuklanmoqda" or not job.get("cloud_test_id"):
        _cancel.discard(jid)
        return job["status"] if job else "yo'q"
    if jid in _cancel:
        _cancel.discard(jid)
        return "toxtatildi"
    meta = _meta(job)
    size, sha = int(meta.get("size") or 0), str(meta.get("sha256") or "")
    dest = Path(job["path"])
    part = dest.with_name(dest.name + ".part")
    url = f"{worker.url}/api/v1/edge/video-tests/{int(job['cloud_test_id'])}/file"
    try:
        h = hashlib.sha256()
        n = 0
        last = 0.0
        with worker.client.stream("GET", url, timeout=httpx.Timeout(60.0, connect=10.0)) as r:
            if r.status_code in (404, 410):
                _set(jid, status="toxtatildi", finished_at=time.time(), error="bulutda video yo'q (bekor qilingan)")
                _changed()
                return "toxtatildi"
            if r.status_code != 200:
                raise RuntimeError(f"bulut javobi HTTP {r.status_code}")
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if jid in _cancel or worker.stop_evt.is_set():
                        raise _Cancelled
                    n += len(chunk)
                    if n > size:
                        raise RuntimeError("fayl kutilganidan katta")
                    h.update(chunk)
                    f.write(chunk)
                    pct = n * 100.0 / size
                    if pct - last >= 5:
                        last = pct
                        _set(jid, progress=round(pct, 1))
        if n != size or h.hexdigest() != sha:
            raise RuntimeError(f"fayl to'liq kelmadi ({n} / {size} bayt yoki sha256 mos emas)")
        part.replace(dest)
    except _Cancelled:
        part.unlink(missing_ok=True)
        _cancel.discard(jid)
        if worker.stop_evt.is_set() and (db.row("SELECT status FROM jobs WHERE id=?", (jid,)) or {}).get("status") == "yuklanmoqda":
            return "yuklanmoqda"            # dastur to'xtayapti — keyingi ishga tushishda resume() davom ettiradi
        return "toxtatildi"
    except Exception as e:  # noqa: BLE001 — tarmoq, disk, bulut 5xx
        part.unlink(missing_ok=True)
        if worker.stop_evt.is_set():
            return "yuklanmoqda"            # dastur to'xtayapti (HTTP mijoz yopildi) — keyingi ishga tushishda resume()
        tries = int(meta.get("tries") or 0) + 1
        meta["tries"] = tries
        if tries <= len(RETRY_DELAYS) and not worker.stop_evt.is_set():
            delay = RETRY_DELAYS[tries - 1]
            _set(jid, cloud_meta=json.dumps(meta), error=f"yuklab olishda xato ({e}) — {int(delay)} s dan keyin qayta")
            log.warning("Bulut video sinovi (vazifa #%d) yuklab olinmadi: %s — %d s dan keyin qayta", jid, e, delay)
            timer = threading.Timer(delay, _q.put, args=(jid,))
            timer.daemon = True
            timer.start()
            return "yuklanmoqda"
        _set(jid, status="xato", finished_at=time.time(), cloud_meta=json.dumps(meta),
             error=f"videoni bulutdan yuklab bo'lmadi: {e}"[:300])
        log.error("Bulut video sinovi (vazifa #%d) yuklab olinmadi: %s", jid, e)
        _changed()
        return "xato"
    if not _readable(dest):
        _set(jid, status="xato", finished_at=time.time(),
             error="Videoni o'qib bo'lmadi. MP4 (H.264) formatini sinab ko'ring.")
        _changed()
        return "xato"
    with db.tx() as c:                     # shu orada bekor qilingan bo'lsa — navbatga qo'yilmaydi
        moved = c.execute("UPDATE jobs SET status='navbatda', progress=0, error=NULL WHERE id=? AND status='yuklanmoqda'",
                          (jid,)).rowcount
    if not moved:
        dest.unlink(missing_ok=True)
        _cancel.discard(jid)
        return "toxtatildi"
    log.info("Bulut video sinovi (vazifa #%d) yuklab olindi — tahlil navbatida", jid)
    mgr = getattr(worker, "manager", None)
    if mgr is not None and hasattr(mgr, "queue_job"):
        mgr.queue_job(jid)
    _changed()
    return "navbatda"


def _readable(path: Path) -> bool:
    try:
        import cv2
    except Exception:  # noqa: BLE001 — sinov muhiti (cv2 yo'q): fayl bor bo'lsa yetarli
        return path.is_file() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    return bool(ok)
