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
            rows.append({"minute": minute, "source_id": sid, "source_name": a.name,
                         "persons_avg": round(a.persons_sum / up, 2) if up else 0.0, "persons_max": a.persons_max,
                         "fps_ai": round(a.fps_sum / up, 2) if up else 0.0,
                         "active_pct": round(a.active_n / up, 3) if up else 0.0,
                         "up_pct": round(up / a.n, 3), "alerts": max(0, a.alerts1 - (a.alerts0 or 0)),
                         "cpu": None, "ram": None, "extra": None})
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
        with db.tx() as c:
            for r in rows:
                c.execute("INSERT OR REPLACE INTO metrics_minute(minute, source_id, source_name, persons_avg, persons_max, "
                          "fps_ai, active_pct, up_pct, alerts, cpu, ram, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (r["minute"], r["source_id"], r["source_name"], r["persons_avg"], r["persons_max"],
                           r["fps_ai"], r["active_pct"], r["up_pct"], r["alerts"], r["cpu"], r["ram"], r["extra"]))
        self.acc = defaultdict(_Acc)
        self.sys_acc = {"n": 0, "cpu": 0.0, "ram": 0.0}
        try:
            sync.enqueue_metrics(rows)
        except Exception:  # noqa: BLE001
            log.exception("Ko'rsatkichlarni bulut navbatiga qo'yishda xato")
        if time.time() - self.last_prune > 3600:
            self.last_prune = time.time()
            db.execute("DELETE FROM metrics_minute WHERE minute < ?", (time.time() - KEEP_DAYS * 86400,))
        return rows

    def run(self) -> None:
        while not self.stop_evt.wait(SAMPLE_SEC):
            try:
                self.sample()
                m = int(time.time() // 60) * 60
                if m != self.cur_minute:
                    self.flush(self.cur_minute)
                    self.cur_minute = m
            except Exception:
                log.exception("Ko'rsatkich yig'ishda xato")


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

