"""Daqiqalik ko'rsatkichlar: har kamera (odamlar, AI tezligi, faol rejim ulushi, ishlagan vaqt, signallar)
va tizim (CPU, RAM, bulut navbati). Har SAMPLE_SEC soniyada bir namuna olinadi, daqiqa tugagach `metrics_minute`
ga yoziladi va bulut navbatiga qo'yiladi. Hisobotlar (8-bosqich) va odam sanash (3-bosqich) shu poydevorga quriladi.

Resurs: namuna olish — sozlamalar va manbalar jadvalidan bir so'rov, hisob yo'q; 8 GB kompyuterda sezilmaydi.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict

from . import db, sync
from .logutil import get_logger

log = get_logger("metrics")

SAMPLE_SEC = 5.0
KEEP_DAYS = 90       # lokal daqiqalik ko'rsatkichlar shuncha kun saqlanadi


class _Acc:
    __slots__ = ("n", "persons_sum", "persons_max", "fps_sum", "active_n", "up_n", "alerts0", "alerts1", "name")

    def __init__(self, name: str = ""):
        self.n = 0
        self.persons_sum = 0.0
        self.persons_max = 0
        self.fps_sum = 0.0
        self.active_n = 0
        self.up_n = 0
        self.alerts0 = None
        self.alerts1 = 0
        self.name = name


class MetricsSampler(threading.Thread):
    def __init__(self, manager):
        super().__init__(daemon=True, name="metrics")
        self.manager = manager
        self.stop_evt = threading.Event()
        self.acc: dict[int, _Acc] = defaultdict(_Acc)
        self.sys_acc = {"n": 0, "cpu": 0.0, "ram": 0.0}
        self.cur_minute = int(time.time() // 60) * 60
        self.last_prune = 0.0
        try:
            import psutil

            self._ps = psutil
            self._proc = psutil.Process()
            psutil.cpu_percent(None)   # birinchi chaqiruv 0 qaytaradi — kalibrovka
        except Exception:  # noqa: BLE001
            self._ps = None
            self._proc = None

    def stop(self) -> None:
        self.stop_evt.set()

    # ------------------------------------------------------------------
    def sample(self) -> None:
        for s in self.manager.source_info():
            a = self.acc[int(s["id"])]
            a.name = s.get("name") or a.name
            a.n += 1
            if s.get("running"):
                a.up_n += 1
                p = int(s.get("persons") or 0)
                a.persons_sum += p
                a.persons_max = max(a.persons_max, p)
                a.fps_sum += float(s.get("fps_ai") or 0)
                if s.get("mode") == "faol":
                    a.active_n += 1
            al = int(s.get("alerts") or 0)
            if a.alerts0 is None:
                a.alerts0 = al
            a.alerts1 = al
        if self._ps is not None:
            try:
                self.sys_acc["n"] += 1
                self.sys_acc["cpu"] += self._ps.cpu_percent(None)
                self.sys_acc["ram"] += self._ps.virtual_memory().percent
            except Exception:  # noqa: BLE001
                pass

    def flush(self, minute: int) -> list[dict]:
        rows: list[dict] = []
        for sid, a in self.acc.items():
            if a.n == 0:
                continue
            up = a.up_n
            row = {"minute": minute, "source_id": sid, "source_name": a.name,
                   "persons_avg": round(a.persons_sum / up, 2) if up else 0.0, "persons_max": a.persons_max,
                   "fps_ai": round(a.fps_sum / up, 2) if up else 0.0,
                   "active_pct": round(a.active_n / up, 3) if up else 0.0,
                   "up_pct": round(up / a.n, 3), "alerts": max(0, a.alerts1 - (a.alerts0 or 0)),
                   "cpu": None, "ram": None, "extra": None}
            rows.append(row)
        n = self.sys_acc["n"]
        extra = None
        try:
            extra = {"outbox": sync.pending()["total"] if sync.configured() else 0}
            if self._proc is not None:
                extra["rss_mb"] = round(self._proc.memory_info().rss / (1 << 20), 1)
        except Exception:  # noqa: BLE001
            pass
        rows.append({"minute": minute, "source_id": 0, "source_name": "tizim", "persons_avg": None, "persons_max": None,
                     "fps_ai": None, "active_pct": None, "up_pct": None, "alerts": None,
                     "cpu": round(self.sys_acc["cpu"] / n, 1) if n else None,
                     "ram": round(self.sys_acc["ram"] / n, 1) if n else None,
                     "extra": json.dumps(extra) if extra else None})
        # Sanash yig'indisi, yozish va bulut navbati — bitta qulf ostida: shu payt yozilgan kechikkan o'tish (recount)
        # yoki eskisi navbatda yangisini almashtirib qo'yishi mumkin emas
        with _count_lock:
            for r in rows:
                if r["source_id"]:
                    _add_counts(r)
            _write(rows)
            try:
                sync.enqueue_metrics(rows)
            except Exception:  # noqa: BLE001
                log.exception("Ko'rsatkichlarni bulut navbatiga qo'yishda xato")
        self.acc = defaultdict(_Acc)
        self.sys_acc = {"n": 0, "cpu": 0.0, "ram": 0.0}
        try:
            from .ai import queues            # 2.0.0: kassa navbati — daqiqalik yig'indi, 3- va 4-qoida

            queues.on_minute(minute)
        except Exception:  # noqa: BLE001
            log.exception("Navbat daqiqasida xato")
        if time.time() - self.last_prune > 3600:
            self.last_prune = time.time()
            db.execute("DELETE FROM metrics_minute WHERE minute < ?", (time.time() - KEEP_DAYS * 86400,))
        return rows

    def run(self) -> None:
        try:
            n = backfill_counts()
            if n:
                log.info("Sanash: %d daqiqa yig'indisi tiklandi (dastur daqiqa o'rtasida to'xtagan edi)", n)
        except Exception:  # noqa: BLE001
            log.exception("Sanash yig'indisini tiklab bo'lmadi")
        while not self.stop_evt.wait(SAMPLE_SEC):
            try:
                self.sample()
                m = int(time.time() // 60) * 60
                if m != self.cur_minute:
                    self.flush(self.cur_minute)
                    self.cur_minute = m
            except Exception:
                log.exception("Ko'rsatkich yig'ishda xato")


COUNT_KEYS = ("in", "out", "staff_in", "staff_out", "offhours", "off_in", "off_out")
_count_lock = threading.RLock()


def _write(rows: list[dict]) -> None:
    with db.tx() as c:
        for r in rows:
            c.execute("INSERT OR REPLACE INTO metrics_minute(minute, source_id, source_name, persons_avg, persons_max, "
                      "fps_ai, active_pct, up_pct, alerts, cpu, ram, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["minute"], r["source_id"], r["source_name"], r["persons_avg"], r["persons_max"],
                       r["fps_ai"], r["active_pct"], r["up_pct"], r["alerts"], r["cpu"], r["ram"], r["extra"]))


def _add_counts(row: dict) -> None:
    """1.9.0: sanash chizig'i bor kamera — shu daqiqadagi kirdi/chiqdi (xaridorlar), xodimlar, ish vaqtidan tashqari.
    Lokal bazada `extra` JSON'ida, bulutga — yozuvning o'zida (in, out, ...)."""
    try:
        from .ai import counting

        cnt = counting.minute_counts(int(row["source_id"]), int(row["minute"]))
    except Exception:  # noqa: BLE001
        log.exception("Sanash yig'indisini olib bo'lmadi")
        return
    if not cnt:
        return
    row.update(cnt)
    try:
        extra = json.loads(row["extra"]) if row.get("extra") else {}
    except (TypeError, ValueError):
        extra = {}
    extra.update(cnt)
    row["extra"] = json.dumps(extra)


def _stored_counts(r: dict) -> dict:
    try:
        extra = json.loads(r["extra"]) if r.get("extra") else {}
    except (TypeError, ValueError):
        extra = {}
    return {k: extra[k] for k in COUNT_KEYS if k in extra}


def recount(source_id: int, minute: int, create: bool = False) -> bool:
    """O'tgan daqiqaning sanash yig'indisini qayta hisoblash — lokal va bulutga (o'zgargan bo'lsa).
    Qachon: odam keyin xodim deb aniqlandi; o'tish daqiqa yopilgandan keyin yozildi (AI kechikdi);
    dastur daqiqa o'rtasida to'xtab, o'sha daqiqa yozilmay qoldi (create=True — qator yaratiladi)."""
    from .ai import counting

    with _count_lock:
        r = db.row("SELECT * FROM metrics_minute WHERE minute=? AND source_id=?", (minute, source_id))
        if not r:
            if not create:
                return False             # daqiqa hali yopilmagan — flush o'zi to'g'ri hisoblaydi
            name = db.scalar("SELECT MAX(source_name) FROM counts WHERE source_id=? AND t>=? AND t<?",
                             (source_id, minute, minute + 60))
            r = {"minute": minute, "source_id": source_id, "source_name": name, "persons_avg": None,
                 "persons_max": None, "fps_ai": None, "active_pct": None, "up_pct": None, "alerts": None,
                 "cpu": None, "ram": None, "extra": None}
        elif _stored_counts(r) == counting.minute_counts(source_id, minute):
            return False                 # o'zgarmagan — bulutga qayta yuborilmaydi
        try:
            extra = json.loads(r["extra"]) if r.get("extra") else {}
        except (TypeError, ValueError):
            extra = {}
        for k in COUNT_KEYS:
            extra.pop(k, None)
        r["extra"] = json.dumps(extra) if extra else None
        _add_counts(r)
        _write([r])
        try:
            sync.enqueue_metrics([r])
        except Exception:  # noqa: BLE001
            log.exception("Ko'rsatkichni bulut navbatiga qo'yishda xato")
    return True


def backfill_counts(hours: float = 48) -> int:
    """Ishga tushishda: oxirgi `hours` soatdagi o'tishlari bor, lekin daqiqa qatori yo'q yoki yig'indisi mos
    kelmaydigan daqiqalarni to'g'rilaydi (dastur daqiqa o'rtasida to'xtagan bo'lsa o'sha daqiqa yozilmay qolardi)."""
    now_m = int(time.time() // 60) * 60
    mins = db.rows("SELECT DISTINCT source_id, CAST(t / 60 AS INTEGER) * 60 AS minute FROM counts "
                   "WHERE t >= ? AND t < ? AND source_id IS NOT NULL", (now_m - hours * 3600, now_m))
    return sum(1 for m in mins if recount(int(m["source_id"]), int(m["minute"]), create=True))


def recent_hourly(hours: float, source_id: int | None = None) -> list[dict]:
    """Soatlik o'rtacha — SQL ichida yig'iladi (uzoq oraliq uchun xotiraga yuz minglab qator olinmaydi)."""
    since = time.time() - hours * 3600
    where = "minute >= ?"
    params: list = [since]
    if source_id is not None:
        where += " AND source_id=?"
        params.append(source_id)
    return db.rows(
        "SELECT (minute / 3600) * 3600 AS minute, source_id, MAX(source_name) AS source_name, "
        "ROUND(AVG(persons_avg), 3) AS persons_avg, MAX(persons_max) AS persons_max, ROUND(AVG(fps_ai), 3) AS fps_ai, "
        "ROUND(AVG(active_pct), 3) AS active_pct, ROUND(AVG(up_pct), 3) AS up_pct, SUM(alerts) AS alerts, "
        "ROUND(AVG(cpu), 1) AS cpu, ROUND(AVG(ram), 1) AS ram, NULL AS extra "
        f"FROM metrics_minute WHERE {where} GROUP BY (minute / 3600) * 3600, source_id ORDER BY 1, 2", params)


def recent(hours: float = 24, source_id: int | None = None) -> list[dict]:
    since = time.time() - hours * 3600
    if source_id is None:
        return db.rows("SELECT * FROM metrics_minute WHERE minute >= ? ORDER BY minute", (since,))
    return db.rows("SELECT * FROM metrics_minute WHERE minute >= ? AND source_id=? ORDER BY minute", (since, source_id))

