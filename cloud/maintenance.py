"""Bulutga xizmat ko'rsatish (1.8.0): kliplarni saqlash muddati, disk zaxirasi, bazaning zaxira nusxasi.

Bulut serveri ichida (bildirishnoma worker'i har 20 s `tick()` ni chaqiradi — o'zi soatiga/kuniga bir marta ishlaydi)
yoki qo'lda:
    python -m cloud.maintenance backup            # hozir zaxira nusxa (SQLite yoki pg_dump)
    python -m cloud.maintenance prune             # muddati o'tgan kliplarni hozir o'chirish
    python -m cloud.maintenance status            # disk, kliplar, oxirgi zaxira

Muhit: CLOUD_CLIP_KEEP_DAYS (30; 0 — o'chirilmaydi), CLOUD_CLIP_KEEP_CONFIRMED_DAYS (180 — tasdiqlangan hodisa klipi),
CLOUD_MIN_FREE_GB (2 — diskda shundan kam joy qolsa eng eski tasdiqlanmagan kliplar o'chiriladi),
CLOUD_BACKUP_DIR (cloud/data/backups), CLOUD_BACKUP_HOURS (24; 0 — avtomatik zaxira yo'q), CLOUD_BACKUP_KEEP (7).
Hodisa yozuvi o'chmaydi — faqat klip/rasm fayli (panelda "klip saqlanmagan").
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db as cdb
from .db import CLIPS_DIR, DATA_DIR

log = logging.getLogger("hushyor.cloud.maintenance")


def _num(key: str, default: float) -> float:
    from .notify import _env

    try:
        return float(_env(key, str(default)) or default)
    except ValueError:
        return default


def settings() -> dict:
    from .notify import _env

    return {"clip_days": _num("CLOUD_CLIP_KEEP_DAYS", 30), "confirmed_days": _num("CLOUD_CLIP_KEEP_CONFIRMED_DAYS", 180),
            "min_free_gb": _num("CLOUD_MIN_FREE_GB", 2), "backup_hours": _num("CLOUD_BACKUP_HOURS", 24),
            "backup_keep": int(_num("CLOUD_BACKUP_KEEP", 7)),
            "backup_dir": Path(_env("CLOUD_BACKUP_DIR") or DATA_DIR / "backups")}


def _drop_files(ev: dict) -> int:
    freed = 0
    for col in ("clip_file", "thumb_file", "clip_clean"):
        rel = ev.get(col)
        if not rel:
            continue
        p = (CLIPS_DIR / rel).resolve()
        if CLIPS_DIR.resolve() in p.parents and p.is_file():
            freed += p.stat().st_size
            p.unlink(missing_ok=True)
    cdb.execute("UPDATE events SET clip_file=NULL, thumb_file=NULL, clip_clean=NULL WHERE uid=:u", {"u": ev["uid"]})
    return freed


def free_gb(path: Path | None = None) -> float:
    path = path or CLIPS_DIR            # kliplar turgan disk (boshqa diskka ulangan bo'lishi mumkin)
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 2 ** 30


# Muddat bulut soati bo'yicha (received_at): edge soati noto'g'ri bo'lsa (CMOS batareyasi) yoki eski hodisalar
# navbatdan kech kelsa ham klip kelgan zahoti o'chib ketmasin
_AGE = "COALESCE(received_at, created_at)"
LOW_DISK_PROTECT_SEC = 86400            # disk to'lganda ham oxirgi 1 sutkadagi kliplarga tegilmaydi


def prune_clips(now: float | None = None, cfg: dict | None = None) -> dict:
    """Muddati o'tgan kliplar (tasdiqlangan — uzoqroq saqlanadi), diskda joy kam bo'lsa eng eskilari,
    chala qolgan yuklamalar (.part, 1 kundan eski)."""
    now = now or time.time()
    cfg = cfg or settings()
    out = {"expired": 0, "low_disk": 0, "parts": 0, "freed_mb": 0.0}
    freed = 0
    if cfg["clip_days"] > 0:
        rows = cdb.rows("SELECT uid, clip_file, thumb_file, clip_clean, status FROM events WHERE (clip_file IS NOT NULL OR "
                        f"thumb_file IS NOT NULL) AND ((status <> 'tasdiqlangan' AND {_AGE} < :a) OR "
                        f"(status = 'tasdiqlangan' AND {_AGE} < :b))",
                        {"a": now - cfg["clip_days"] * 86400,
                         "b": now - max(cfg["confirmed_days"], cfg["clip_days"]) * 86400})
        for ev in rows:
            freed += _drop_files(ev)
            out["expired"] += 1
    if cfg["min_free_gb"] > 0 and free_gb() < cfg["min_free_gb"]:
        # 3.2.0: avval eski zaxira nusxalar (eng yangi 2 tasi qoladi) — dalil kliplaridan oldin
        try:
            bd = Path(cfg["backup_dir"])
            if bd.is_dir() and _rotate(bd, 2):
                log.warning("Diskda joy kam — eski zaxira nusxalar o'chirildi (eng yangi 2 tasi qoldi)")
        except OSError:
            pass
    if cfg["min_free_gb"] > 0 and free_gb() < cfg["min_free_gb"]:
        # Disk to'lib qolsa yangi kliplar yozilmay qoladi — eng eski tasdiqlanmagan kliplardan boshlab bo'shatiladi.
        # Oxirgi sutka kliplari himoyada; o'chirish joy bo'shatmasa (disk boshqa narsadan to'lgan yoki kliplar boshqa
        # diskda) — to'xtaydi: behuda klip o'chirilmaydi.
        stalled = 0
        for _ in range(200):
            before = free_gb()
            if before >= cfg["min_free_gb"]:
                break
            # 3.2.0: eng ko'p klip saqlagan filialdan boshlab (bitta filial diskni to'ldirsa, boshqalarning dalillari
            # birinchi bo'lib o'chmasin)
            heavy = cdb.row("SELECT branch_id, COUNT(*) AS n FROM events WHERE clip_file IS NOT NULL AND "
                            f"status <> 'tasdiqlangan' AND {_AGE} < :p GROUP BY branch_id ORDER BY n DESC LIMIT 1",
                            {"p": now - LOW_DISK_PROTECT_SEC})
            rows = cdb.rows("SELECT uid, clip_file, thumb_file, clip_clean FROM events WHERE clip_file IS NOT NULL AND "
                            f"status <> 'tasdiqlangan' AND {_AGE} < :p AND branch_id = :b ORDER BY {_AGE} LIMIT 20",
                            {"p": now - LOW_DISK_PROTECT_SEC, "b": heavy["branch_id"]}) if heavy else []
            if not rows:
                log.warning("Diskda joy kam (%.1f GB), lekin o'chirsa bo'ladigan eski klip qolmadi", before)
                break
            got = 0
            for ev in rows:
                got += _drop_files(ev)
                out["low_disk"] += 1
            freed += got
            if got > 0 and free_gb() <= before + 1e-6:
                stalled += 1
                if stalled >= 2:
                    log.error("Kliplar o'chirildi, lekin diskda joy ko'paymadi — disk boshqa narsadan to'lgan; "
                              "tozalash to'xtatildi (%.1f GB bo'sh)", free_gb())
                    break
            else:
                stalled = 0
    if CLIPS_DIR.is_dir():
        for p in CLIPS_DIR.glob("*/*.part"):
            try:
                if now - p.stat().st_mtime > 86400:
                    freed += p.stat().st_size
                    p.unlink(missing_ok=True)
                    out["parts"] += 1
            except OSError:
                pass
    out["freed_mb"] = round(freed / 2 ** 20, 1)
    if out["expired"] or out["low_disk"] or out["parts"]:
        log.info("Kliplar tozalandi: %s", out)
    return out


def _sqlite_path() -> Path | None:
    url = cdb.db_url()
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.replace("sqlite:///", "", 1))


def backup(dest: Path | None = None, keep: int | None = None) -> Path:
    """Bazaning izchil zaxira nusxasi + VAPID kaliti (push obunalari shu kalitga bog'liq).
    SQLite — onlayn backup API (server ishlab turganda ham to'g'ri); PostgreSQL — pg_dump (custom format)."""
    cfg = settings()
    dest = Path(dest or cfg["backup_dir"])
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src = _sqlite_path()
    if src is not None:
        # 3.2.0: diskda joy yetmasa zaxira olinmaydi (zaxira diskni to'ldirib, xizmatni to'xtatmasin)
        try:
            need = src.stat().st_size * 2 + cfg["min_free_gb"] * 2 ** 30
            if shutil.disk_usage(dest).free < need:
                raise RuntimeError(f"diskda joy yetarli emas ({shutil.disk_usage(dest).free / 2 ** 30:.1f} GB bo'sh) — "
                                   "zaxira olinmadi")
        except OSError:
            pass
        raw = dest / f"cloud-{stamp}.db"
        s = sqlite3.connect(str(src), timeout=30)
        try:
            d = sqlite3.connect(str(raw))
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
        # 3.2.0: siqilgan holda saqlanadi (daqiqalik JSON ko'rsatkichlar ~10 baravar kichrayadi)
        out = dest / f"cloud-{stamp}.db.gz"
        try:
            with open(raw, "rb") as fi, gzip.open(out, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo, 1 << 20)
            raw.unlink(missing_ok=True)
        except OSError:
            out.unlink(missing_ok=True)
            out = raw
    else:
        exe = shutil.which("pg_dump")
        if not exe:
            raise RuntimeError("pg_dump topilmadi (apt install postgresql-client)")
        out = dest / f"cloud-{stamp}.pgdump"
        from sqlalchemy.engine import make_url

        u = make_url(cdb.db_url())
        # Parol buyruq qatorida emas (ps/proc'da ko'rinadi) — PGPASSWORD orqali
        env = dict(os.environ, PGPASSWORD=u.password or "")
        args = [exe, "--format=custom", "--no-owner", f"--file={out}", "-h", u.host or "127.0.0.1",
                "-p", str(u.port or 5432), "-U", u.username or "postgres", u.database or ""]
        r = subprocess.run(args, capture_output=True, text=True, timeout=3600, env=env)
        if r.returncode != 0:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump xato: {r.stderr.strip()[:300]}")
    vapid = DATA_DIR / "vapid_private.pem"
    if vapid.is_file():
        shutil.copy2(vapid, dest / f"vapid-{stamp}.pem")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    keep = cfg["backup_keep"] if keep is None else keep
    # Aylanish faqat sozlangan zaxira papkasida va faqat shu skript yaratgan nomlar (cloud-YYYYmmdd-HHMMSS.db):
    # qo'lda qo'yilgan "cloud-yangilashdan-oldin.db" kabi fayllar o'chmaydi va haqiqiy zaxirani siqib chiqarmaydi
    if keep > 0 and dest.resolve() == Path(cfg["backup_dir"]).resolve():
        _rotate(dest, keep)
    cdb.kv_set("last_backup", f"{time.time():.0f}|{out.name}")
    log.info("Zaxira nusxa: %s (%.1f MB)", out, out.stat().st_size / 2 ** 20)
    return out


_BACKUP_RX = re.compile(r"cloud-\d{8}-\d{6}\.(db|db\.gz|pgdump)")


def _rotate(dest: Path, keep: int) -> int:
    """Faqat shu skript yaratgan nomlar (cloud-YYYYmmdd-HHMMSS.db[.gz] / .pgdump, vapid-...pem): eng yangi `keep` tasi
    qoladi. Qo'lda qo'yilgan "cloud-yangilashdan-oldin.db" kabi fayllar o'chmaydi."""
    n = 0
    mine = sorted((p for p in dest.glob("cloud-*") if _BACKUP_RX.fullmatch(p.name)), key=lambda p: p.name[:21],
                  reverse=True)
    for p in mine[keep:]:
        p.unlink(missing_ok=True)
        n += 1
    rxv = re.compile(r"vapid-\d{8}-\d{6}\.pem")
    vap = sorted((p for p in dest.glob("vapid-*.pem") if rxv.fullmatch(p.name)), key=lambda p: p.name, reverse=True)
    for p in vap[keep:]:
        p.unlink(missing_ok=True)
    return n


def last_backup() -> tuple[float, str]:
    v = cdb.kv_get("last_backup", "")
    try:
        t, name = v.split("|", 1)
        return float(t), name
    except ValueError:
        return 0.0, ""


_last = {"prune": 0.0, "backup_try": 0.0}
_backup_thread: list = [None]
_loaded = time.time()
_warned: set[str] = set()


def tick(now: float | None = None) -> None:
    """Davriy ish (worker'dan): kliplar — soatiga bir marta; zaxira — CLOUD_BACKUP_HOURS da bir marta."""
    now = now or time.time()
    cfg = settings()
    if now - _last["prune"] >= 3600:
        _last["prune"] = now
        try:
            prune_clips(now, cfg)
        except Exception:  # noqa: BLE001
            log.exception("Kliplarni tozalashda xato")
    # Zaxira: server ishga tushgach 5 daqiqadan keyin (yuklama tinchigach), keyin har CLOUD_BACKUP_HOURS
    if cfg["backup_hours"] > 0 and now - _loaded >= 300 and now - _last["backup_try"] >= 600:
        t, _ = last_backup()
        if now - t >= cfg["backup_hours"] * 3600:
            _last["backup_try"] = now
            if _sqlite_path() is None and not shutil.which("pg_dump"):
                if "pg_dump" not in _warned:
                    _warned.add("pg_dump")
                    log.warning("Avtomatik zaxira o'tkazib yuborildi: pg_dump yo'q (VPS'da systemd taymeri bilan oling)")
                return
            if _backup_thread[0] is not None and _backup_thread[0].is_alive():
                return

            def run() -> None:
                try:
                    backup()
                except Exception:  # noqa: BLE001
                    log.exception("Avtomatik zaxira nusxa olinmadi")

            # Alohida oqimda: katta baza yoki pg_dump (soatgacha) bildirishnoma monitorini (oflayn, eskalatsiya) to'xtatmasin
            _backup_thread[0] = threading.Thread(target=run, daemon=True, name="cloud-backup")
            _backup_thread[0].start()


def status() -> dict:
    t, name = last_backup()
    n_clips = cdb.scalar("SELECT COUNT(*) FROM events WHERE clip_file IS NOT NULL") or 0
    size = sum(p.stat().st_size for p in CLIPS_DIR.glob("*/*") if p.is_file()) if CLIPS_DIR.is_dir() else 0
    return {"db": cdb.engine().dialect.name, "clips": int(n_clips), "clips_mb": round(size / 2 ** 20, 1),
            "free_gb": round(free_gb(), 1), "last_backup": name or None,
            "last_backup_age_h": round((time.time() - t) / 3600, 1) if t else None, **{
                k: (str(v) if isinstance(v, Path) else v) for k, v in settings().items()}}


def main(argv: list[str] | None = None) -> int:
    import json

    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    cdb.init_db()
    if cmd == "backup":
        print("Zaxira nusxa:", backup(Path(argv[1]) if len(argv) > 1 else None))
    elif cmd == "prune":
        print(json.dumps(prune_clips(), ensure_ascii=False))
    elif cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
