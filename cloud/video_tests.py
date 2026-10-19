"""3.0.0: "Video sinovi" — bulut panelida video yuklanadi, tahlilni filialning do'kon kompyuteri (edge) qiladi.

Nega tahlil bulutda emas: bulut serverida AI kutubxonalari va modellar yo'q (requirements-cloud.txt — yengil), VPS
odatda kichik (1 GB) va boshqa saytlar bilan bo'lishilgan. Edge esa AI uchun moslangan va natija (hodisa, klip)
odatdagi Sync API orqali qaytadi.

Oqim (docs/SYNC_API.md, "Video sinovi"):
  panel  POST /api/v1/video-tests?branch_id=&name=[&zone_source_id=]  (tana — videoning o'zi, Content-Length bilan)
         → fayl DATA_DIR/video_tests/<id>.<ext> (sha256 hisoblanadi), video_tests qatori "kutilmoqda",
           commands jadvaliga `video_test` buyrug'i (hello javobida edge'ga ketadi)
  edge   GET  /api/v1/edge/video-tests/<id>/file  → videoni yuklab oladi (hajm va sha256 tekshiradi)
  edge   hello → "video_tests": [{id, job_id, status, progress, events, duration, error}] → holat shu yerda yangilanadi
  edge   hodisalar va kliplar — odatdagi /edge/events, /edge/events/<uid>/clip (data.job_id = sinovning job_id)
Tugagach (tayyor / xato / bekor) video bulutdan o'chiriladi; 2 kunda olinmagan sinov — "xato".

Cheklovlar (.env): CLOUD_VIDEO_MAX_MB (sukut 300), CLOUD_VIDEO_QUOTA_MB (bulutda bir vaqtda saqlanadigan jami, 2000),
CLOUD_VIDEO_MIN_FREE_MB (diskda qolishi shart bo'lgan bo'sh joy, 1024 — server boshqa saytlar bilan bo'lishilgan
bo'lsa ular ham to'lib qolmasin), bir kompaniyada bir vaqtda ko'pi bilan ACTIVE_MAX ta tugamagan sinov.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from . import db as cdb
from .db import DATA_DIR

log = logging.getLogger("hushyor.cloud.video")

VIDEO_DIR = DATA_DIR / "video_tests"
EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
ACTIVE = ("kutilmoqda", "yuklanmoqda", "navbatda", "ishlanmoqda")
FINAL = ("tayyor", "xato", "bekor")
ACTIVE_MAX = 5
PICKUP_DAYS = 2                    # shuncha kunda edge olmagan sinov — "xato"
STALE_DAYS = 7                     # shuncha kunda tugamagan sinov — "xato"
MIN_EDGE = (3, 0, 0)               # video sinovini tushunadigan eng eski edge versiyasi
# edge holati → bulut holati
EDGE_STATUS = {"yuklanmoqda": "yuklanmoqda", "navbatda": "navbatda", "ishlanmoqda": "ishlanmoqda",
               "tayyor": "tayyor", "xato": "xato", "toxtatildi": "bekor"}
STATUS_UZ = {"kutilmoqda": "Do'kon kompyuterini kutmoqda", "yuklanmoqda": "Do'kon kompyuteriga yuklanmoqda",
             "navbatda": "Tahlil navbatida", "ishlanmoqda": "Tahlil qilinmoqda", "tayyor": "Tayyor",
             "xato": "Xato", "bekor": "Bekor qilindi"}


def _mb(key: str, default: int) -> int:
    try:
        return max(1, int(float(cdb.env(key) or default)))
    except ValueError:
        return default


def max_bytes() -> int:
    return _mb("CLOUD_VIDEO_MAX_MB", 300) * 1024 * 1024


def quota_bytes() -> int:
    return _mb("CLOUD_VIDEO_QUOTA_MB", 2000) * 1024 * 1024


def min_free_bytes() -> int:
    return _mb("CLOUD_VIDEO_MIN_FREE_MB", 1024) * 1024 * 1024


def limits() -> dict:
    return {"max_mb": max_bytes() // (1 << 20), "quota_mb": quota_bytes() // (1 << 20), "exts": list(EXTS),
            "active_max": ACTIVE_MAX}


def version_tuple(v) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(v or ""))[:3]
    return tuple(int(x) for x in nums) if nums else (0,)


def edge_ok(branch: dict) -> bool:
    return version_tuple(branch.get("version")) >= MIN_EDGE


def file_path(test: dict) -> Path:
    ext = Path(str(test.get("filename") or "")).suffix.lower()
    return VIDEO_DIR / f"{int(test['id'])}{ext if ext in EXTS else '.mp4'}"


def clean_name(raw) -> str:
    name = Path(str(raw or "")).name.strip()
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", name)[:120]
    return name or "video.mp4"


def magic_ok(head: bytes) -> bool:
    """Video konteyner imzosi (bulutda OpenCV yo'q — to'liq tekshiruvni edge qiladi): MP4/MOV/M4V (ftyp),
    AVI (RIFF....AVI ), MKV/WebM (EBML)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return True
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return True
    return head[:4] == b"\x1a\x45\xdf\xa3"


class DiskFull(Exception):
    """Yuklash paytida diskdagi bo'sh joy CLOUD_VIDEO_MIN_FREE_MB dan kamaydi."""


# Yuklanayotgan (hali bazada yo'q) videolar uchun zahira: bir vaqtdagi bir nechta yuklash chegaralarni aylanib o'tmasin
_res_lock = threading.Lock()
_reserved: dict[int, list[int]] = {}       # company_id -> [soni, bayt]


def _free_bytes() -> int:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return shutil.disk_usage(VIDEO_DIR).free
    except OSError:
        return 0


def company_stored(company_id: int) -> int:
    """Kompaniyaning bulutda turgan (tahlil kutayotgan) videolari hajmi."""
    return int(cdb.scalar("SELECT COALESCE(SUM(size), 0) FROM video_tests WHERE company_id=:c AND file_deleted=:f",
                          {"c": company_id, "f": False}) or 0)


def active_count(company_id: int) -> int:
    return int(cdb.scalar("SELECT COUNT(*) FROM video_tests WHERE company_id=:c AND status IN "
                          "('kutilmoqda','yuklanmoqda','navbatda','ishlanmoqda')", {"c": company_id}) or 0)


def reserve(company_id: int, size: int) -> tuple[int, str] | None:
    """Yuklashdan oldin (qulf ostida, zahira bilan): faol sinovlar soni, kompaniya kvotasi va diskdagi bo'sh joy.
    Muammo bo'lsa — (HTTP kod, xabar); bo'lmasa joy zahiralanadi (release bilan qaytariladi)."""
    if size > max_bytes():
        return 413, f"video juda katta: ko'pi bilan {max_bytes() // (1 << 20)} MB"
    with _res_lock:
        cnt, rb = _reserved.get(company_id, [0, 0])
        if active_count(company_id) + cnt >= ACTIVE_MAX:
            return 429, f"bir vaqtda ko'pi bilan {ACTIVE_MAX} ta sinov — avvalgilari tugashini kuting"
        if company_stored(company_id) + rb + size > quota_bytes():
            return 413, "bulutda tahlil kutayotgan videolaringiz juda ko'p — birortasi tugashini kuting yoki bekor qiling"
        total_res = sum(v[1] for v in _reserved.values())
        if _free_bytes() - total_res - size < min_free_bytes():
            return 507, "serverda bo'sh joy yetarli emas — kichikroq video yuklang"
        _reserved[company_id] = [cnt + 1, rb + size]
    return None


def release(company_id: int, size: int) -> None:
    with _res_lock:
        cnt, rb = _reserved.get(company_id, [0, 0])
        cnt, rb = max(0, cnt - 1), max(0, rb - size)
        if cnt:
            _reserved[company_id] = [cnt, rb]
        else:
            _reserved.pop(company_id, None)


def create(u: dict, branch: dict, name: str, size: int, sha: str, zone_id: int | None, zone_name: str | None,
           place=None) -> dict:
    """Fayl yozib bo'lingach: sinov qatori, fayl yakuniy nomiga (place), keyin edge buyrug'i — bitta tranzaksiyada
    (edge buyruqni ko'rganda fayl allaqachon joyida bo'lsin)."""
    now = time.time()
    token = secrets.token_hex(8)
    with cdb.engine().begin() as c:
        tid = c.execute(cdb.video_tests.insert().values(
            company_id=u["company_id"], branch_id=branch["id"], user_id=u["id"], filename=name, size=size, sha256=sha,
            zone_source_id=zone_id, zone_source_name=zone_name, status="kutilmoqda", progress=0, events=0,
            created_at=now, updated_at=now, file_deleted=False, token=token)).inserted_primary_key[0]
        if place is not None:
            place({"id": int(tid), "filename": name})
        c.execute(cdb.commands.insert().values(
            branch_id=branch["id"], type="video_test", created_at=now,
            payload={"test_id": int(tid), "token": token, "filename": name, "size": int(size), "sha256": sha,
                     "zone_source_id": zone_id}))
    return get(int(tid)) or {}


def get(tid: int) -> dict | None:
    return cdb.row("SELECT t.*, b.name AS branch_name FROM video_tests t JOIN branches b ON b.id=t.branch_id "
                   "WHERE t.id=:i", {"i": int(tid)})


def public(t: dict) -> dict:
    out = {k: t.get(k) for k in ("id", "branch_id", "branch_name", "filename", "size", "status", "progress", "events",
                                 "duration", "error", "created_at", "updated_at", "started_at", "done_at",
                                 "zone_source_id", "zone_source_name", "job_id")}
    out["status_uz"] = STATUS_UZ.get(str(t.get("status")), str(t.get("status")))
    out["active"] = t.get("status") in ACTIVE
    return out


def list_for(u: dict, branch_ids: list[int], limit: int = 50) -> list[dict]:
    if not branch_ids:
        return []
    marks = ", ".join(f":b{i}" for i in range(len(branch_ids)))
    params = {f"b{i}": b for i, b in enumerate(branch_ids)}
    params.update({"c": u["company_id"], "lim": int(limit)})
    rows = cdb.rows("SELECT t.*, b.name AS branch_name FROM video_tests t JOIN branches b ON b.id=t.branch_id "
                    f"WHERE t.company_id=:c AND t.branch_id IN ({marks}) ORDER BY t.id DESC LIMIT :lim", params)
    return [public(r) for r in rows]


def events_for(t: dict, limit: int = 200) -> list[dict]:
    """Sinov hodisalari: shu filialdagi, sinov yaratilgandan keyin kelgan, data.job_id = sinovning edge vazifasi."""
    if not t.get("job_id"):
        return []
    # (branch_id, created_at) indeksi bo'yicha: hodisa vaqti edge soatida — farq uchun 1 kun zahira
    rows = cdb.rows("SELECT uid, created_at, kind, score, status, source_name, clip_file, thumb_file, data FROM events "
                    "WHERE branch_id=:b AND created_at >= :c0 AND received_at >= :t0 ORDER BY created_at LIMIT 5000",
                    {"b": t["branch_id"], "c0": float(t["created_at"]) - 86400, "t0": float(t["created_at"]) - 60})
    out = []
    for r in rows:
        d = cdb.json_load(r.get("data")) or {}
        if d.get("job_id") != t["job_id"]:
            continue
        out.append({"uid": r["uid"], "kind": r["kind"], "score": r["score"], "status": r["status"],
                    "level": d.get("level"), "reasons": [str(x) for x in (d.get("reasons") or [])][:3],
                    "video_time": d.get("video_time"), "clip_file": r["clip_file"], "thumb_file": r["thumb_file"]})
        if len(out) >= limit:
            break
    return out


def delete_file(t: dict) -> None:
    try:
        file_path(t).unlink(missing_ok=True)
    except OSError:
        log.warning("Video sinovi #%s faylini o'chirib bo'lmadi", t.get("id"))


def _withdraw(c, t: dict) -> None:
    """Sinov buyrug'ini qaytarib olish: edge'ga hali yetkazilmagan bo'lsa — o'chiriladi; yetkazilgan (ack kelmagan
    bo'lsa ham) yoki allaqachon olgan bo'lsa — `video_test_cancel` buyrug'i."""
    pending = [dict(r._mapping) for r in c.execute(cdb.commands.select().where(
        cdb.commands.c.branch_id == t["branch_id"], cdb.commands.c.type == "video_test",
        cdb.commands.c.acked_at.is_(None)))]
    mine = [p for p in pending if (cdb.json_load(p["payload"]) or {}).get("test_id") == t["id"]]
    if mine and all(p["delivered_at"] is None for p in mine):
        c.execute(cdb.commands.delete().where(cdb.commands.c.id.in_([p["id"] for p in mine])))
    else:
        c.execute(cdb.commands.insert().values(branch_id=t["branch_id"], type="video_test_cancel",
                                               created_at=time.time(), payload={"test_id": int(t["id"])}))


def cancel(t: dict) -> str:
    """Bekor qilish (faqat hali tugamagan sinov — shart UPDATE ichida, poyga bo'lsa tugagan holat saqlanadi)."""
    now = time.time()
    with cdb.engine().begin() as c:
        n = c.execute(cdb.video_tests.update().where(cdb.video_tests.c.id == t["id"],
                                                     cdb.video_tests.c.status.in_(ACTIVE)).values(
            status="bekor", updated_at=now, done_at=now, file_deleted=True)).rowcount
        if n:
            _withdraw(c, t)
    if not n:
        return (get(int(t["id"])) or t)["status"]
    delete_file(t)
    return "bekor"


def remove(t: dict) -> None:
    """Tugagan sinov yozuvini ro'yxatdan olib tashlash (hodisalar qoladi — ular Hodisalar sahifasida)."""
    delete_file(t)
    cdb.execute("DELETE FROM video_tests WHERE id=:i", {"i": int(t["id"])})


def apply_edge_report(conn, branch: dict, raw) -> int:
    """hello dagi "video_tests" — faqat shu filialning sinovlari; tugagan holat qaytib "faol"ga o'tmaydi."""
    if not isinstance(raw, list):
        return 0
    now = time.time()
    n = 0
    for x in raw[:30]:
        if not isinstance(x, dict):
            continue
        try:
            tid = int(x.get("id") or 0)
        except (TypeError, ValueError):
            continue
        st = EDGE_STATUS.get(str(x.get("status") or ""))
        if tid <= 0 or st is None:
            continue
        r = conn.execute(cdb.video_tests.select().where(cdb.video_tests.c.id == tid,
                                                        cdb.video_tests.c.branch_id == branch["id"])).first()
        if r is None:
            continue
        t = dict(r._mapping)
        if t.get("token") and str(x.get("token") or "") != t["token"]:
            continue                       # boshqa (eski) sinov haqidagi hisobot — raqam qayta berilgan bo'lsa ham
        if t["status"] in FINAL:
            continue                       # bulutda tugagan (tayyor / xato / bekor) — o'zgarmaydi
        try:
            prog = max(0.0, min(100.0, float(x.get("progress") or 0)))
            evn = max(0, min(100000, int(x.get("events") or 0)))
            job = int(x.get("job_id") or 0) or None
            dur = float(x.get("duration")) if isinstance(x.get("duration"), (int, float)) else None
        except (TypeError, ValueError):
            continue
        vals = {"status": st, "progress": prog, "events": evn, "updated_at": now,
                "error": (str(x.get("error") or "")[:300] or None) if st in ("xato", "bekor") else None}
        if job:
            vals["job_id"] = job
        if dur is not None:
            vals["duration"] = round(dur, 2)
        if st == "ishlanmoqda" and not t.get("started_at"):
            vals["started_at"] = now
        if st in FINAL and not t.get("done_at"):
            vals["done_at"] = now
        if st in ("navbatda", "ishlanmoqda") or st in FINAL:
            vals["file_deleted"] = True    # edge'da tekshirilgan nusxa bor — bulutdagisi endi kerak emas (disk)
        changed = any(t.get(k) != v for k, v in vals.items() if k != "updated_at")
        if not changed:
            continue
        done = conn.execute(cdb.video_tests.update().where(cdb.video_tests.c.id == tid,
                                                           cdb.video_tests.c.status.notin_(FINAL)).values(**vals)).rowcount
        if done and vals.get("file_deleted") and not t.get("file_deleted"):
            delete_file(t)
        n += int(bool(done))
    return n


def sweep(conn) -> None:
    """Soatlik (server._prune): olinmagan / juda uzoq tugamagan sinovlar — "xato"; tugaganlarning qolgan fayli
    va hech bir sinovga tegishli bo'lmagan fayllar — o'chiriladi."""
    now = time.time()
    rows = [dict(r._mapping) for r in conn.execute(cdb.video_tests.select().where(
        cdb.video_tests.c.status.in_(ACTIVE)))]
    for t in rows:
        if t["status"] == "kutilmoqda" and now - float(t["created_at"]) > PICKUP_DAYS * 86400:
            err = f"Do'kon kompyuteri {PICKUP_DAYS} kun ichida videoni olmadi (o'chiq yoki bulutga ulanmagan)"
        elif now - float(t["created_at"]) > STALE_DAYS * 86400:
            err = f"{STALE_DAYS} kunda tugamadi"
        else:
            continue
        n = conn.execute(cdb.video_tests.update().where(cdb.video_tests.c.id == t["id"],
                                                        cdb.video_tests.c.status.in_(ACTIVE)).values(
            status="xato", error=err, updated_at=now, done_at=now, file_deleted=True)).rowcount
        if n:
            _withdraw(conn, t)             # edge hali olmagan bo'lsa buyruq olinadi, olgan bo'lsa — to'xtatiladi
            delete_file(t)
    keep = {file_path(dict(r._mapping)).name for r in conn.execute(
        cdb.video_tests.select().where(cdb.video_tests.c.status.in_(ACTIVE)))}
    if VIDEO_DIR.is_dir():
        for f in VIDEO_DIR.iterdir():
            try:
                if f.is_file() and f.name not in keep and now - f.stat().st_mtime > 600:
                    f.unlink()
            except OSError:
                pass


_last_sweep = [0.0]


def sweep_tick() -> None:
    """Bulut fon oqimidan (notify.Worker, ~har daqiqa) — o'zi 30 daqiqada bir marta: edge'lar o'chiq bo'lsa ham."""
    now = time.time()
    if now - _last_sweep[0] < 1800:
        return
    _last_sweep[0] = now
    with cdb.engine().begin() as c:
        sweep(c)


class Writer:
    """Yuklanayotgan videoni vaqtinchalik faylga yozish: hajm chegarasi, sha256, birinchi baytlar (imzo).
    Sinov qatori yaratilgach `place` uni yakuniy nomiga (<id>.<ext>) ko'chiradi."""

    def __init__(self, limit: int):
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        self.tmp = VIDEO_DIR / f".up-{secrets.token_hex(8)}"
        self.limit = limit
        self.size = 0
        self.head = b""
        self.h = hashlib.sha256()
        self._checked = 0
        self.f = open(self.tmp, "wb")

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            raise ValueError("limit")
        if self.size - self._checked >= 16 * 1024 * 1024:      # yozish davomida ham: boshqa saytlar to'lib qolmasin
            self._checked = self.size
            if _free_bytes() < min_free_bytes():
                raise DiskFull()
        if len(self.head) < 16:
            self.head += chunk[:16 - len(self.head)]
        self.h.update(chunk)
        self.f.write(chunk)

    def finish(self) -> str:
        self.f.close()
        return self.h.hexdigest()

    def place(self, test: dict) -> None:
        self.tmp.replace(file_path(test))

    def abort(self) -> None:
        try:
            self.f.close()
        finally:
            self.tmp.unlink(missing_ok=True)
