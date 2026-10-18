"""Bulut demo (2.6.0, 10-bosqich): 3 filialli namuna kompaniya — mijozga bulut panelini ko'rsatish uchun.

    python -m cloud.demo                   # http://127.0.0.1:9090   (demo@hushyor.uz / demo12345)
    python -m cloud.demo --reset           # demo ma'lumotini o'chirib qaytadan yaratish
    python -m cloud.demo --port 9091
    python -m cloud.demo --seed-only       # faqat ma'lumot yaratish

Alohida papka va baza: cloud/data/demo (haqiqiy bulut bazasiga, Telegram botiga, emailga, filial tokenlariga TEGMAYDI —
muhitdagi CLOUD_* sozlamalar bo'sh qilinadi). Ma'lumot edge yuboradigan ko'rinishda (Sync API) yoziladi — panel haqiqiy
filiallardagidek ko'rsatadi: 30 kunlik tashriflar, 3 hafta navbat, 2 hafta oqim/heatmap, signallar (klip bilan — namuna
videodan), ko'rsatkichlar, tayyorlik va pilot. Server ishlab turganda har daqiqa har filialdan "hello" va daqiqalik
ko'rsatkich taqlid qilinadi — filiallar onlayn, kassalar holati jonli ko'rinadi.

Filiallar ataylab har xil: Yunusobodda kutish uzoqroq, Samarqandda xato signallar ko'p va o'tgan haftada kompyuter
bir kun o'chiq bo'lgan — "Taqqoslash" sahifasida tavsiyalar chiqadi.
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "data" / "demo"
EMAIL, PASSWORD = "demo@hushyor.uz", "demo12345"
COMPANY = "Namuna savdo (DEMO)"
VERSION_EDGE = "2.6.0"
DAYS = 30
QUEUE_DAYS = 21
FLOW_DAYS = 14
WORK = (9, 22)
HOUR_W = {9: 3, 10: 5, 11: 7, 12: 8, 13: 9, 14: 9, 15: 8, 16: 8, 17: 11, 18: 13, 19: 12, 20: 8, 21: 5}
DAY_F = (1.0, 0.95, 0.97, 1.0, 1.1, 1.35, 1.2)          # dushanba … yakshanba
REVIEWERS = ("Qorovul Aziz", "Qorovul Bekzod", "Menejer Dilnoza", "Qorovul Jasur")
REASONS = {"pocket": ["Qo'l cho'ntakka kirdi", "Buyum qo'ldan yo'qoldi"],
           "torso": ["Qo'l kiyim ichiga kirdi", "Atrofga qarab oldi"],
           "bag": ["Buyum sumkaga tushdi", "Polka oldida uzoq turdi"]}
# Do'kon rejasi (metrda): bo'lim nomi, rangi, to'rtburchak (x0, y0, x1, y1), og'irligi
SECTIONS = [("Kassa", "#ef4444", (1, 9, 6, 11.5), 1.6), ("Sut mahsulotlari", "#3b82f6", (1, 1, 6, 4), 1.2),
            ("Non", "#f59e0b", (7, 1, 11, 4), 1.0), ("Meva-sabzavot", "#22c55e", (12, 1, 17, 5), 1.3),
            ("Ichimliklar", "#8b5cf6", (7, 6, 11, 8.5), 0.9), ("Maishiy kimyo", "#64748b", (12, 6.5, 17, 8.5), 0.5)]
PLAN_W, PLAN_H, CELL = 18.0, 12.0, 0.5
# Filiallar: f — tashrif ko'lami, wait — kutish ko'paytmasi, false — xato signal ulushi, outage — o'tgan haftada
# kompyuter o'chiq bo'lgan soatlar, readiness — (tayyor/jami, muammolar, ogohlantirishlar), pilot — necha kun oldin
BRANCHES = [
    {"slug": "chilonzor", "name": "Chilonzor filiali", "address": "Toshkent, Chilonzor tumani, Bunyodkor ko'chasi",
     "f": 1.0, "kassa": ["1-kassa", "2-kassa"], "wait": 1.0, "false": 0.35, "outage": 0,
     "readiness": (9, 9, [], []), "pilot": 23},
    {"slug": "yunusobod", "name": "Yunusobod filiali", "address": "Toshkent, Yunusobod tumani, Amir Temur shoh ko'chasi",
     "f": 1.35, "kassa": ["1-kassa", "2-kassa", "3-kassa"], "wait": 1.9, "false": 0.3, "outage": 0,
     "readiness": (8, 9, [], ["Avtomatik ishga tushish"]), "pilot": 9},
    {"slug": "samarqand", "name": "Samarqand filiali", "address": "Samarqand, Registon ko'chasi",
     "f": 0.7, "kassa": ["1-kassa"], "wait": 0.9, "false": 0.7, "outage": 20,
     "readiness": (7, 9, ["Kameralar: 1 ta kamera ishlamayapti"], ["Telegram bot"]), "pilot": None},
]
# Muhitdagi haqiqiy sozlamalar demo serverga o'tmasin (bot, email, tokenlar, zaxira papkasi)
BLANK = ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_SMTP_USER", "CLOUD_SMTP_PASSWORD", "CLOUD_SMTP_FROM",
         "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN", "CLOUD_PUBLIC_URL", "CLOUD_LEADS_CHAT", "CLOUD_LEADS_EMAIL",
         "CLOUD_BACKUP_DIR", "CLOUD_DB_URL", "TELEGRAM_BOT_TOKEN")


def demo_env() -> dict:
    env = {k: "" for k in BLANK}
    env.update({"CLOUD_DEMO": "1", "CLOUD_DATA_DIR": str(DEMO_DIR), "CLOUD_BACKUP_HOURS": "0"})
    return env


def _off() -> int:
    from . import notify

    return int(round(notify.cfg.tz_offset * 3600))


def _day0(t: float) -> float:
    off = _off()
    return (int(t + off) // 86400) * 86400 - off


def _wday(day0: float) -> int:
    return time.gmtime(day0 + _off() + 43200).tm_wday


def _plan_public(pid: int) -> dict:
    nx, ny = int(PLAN_W / CELL), int(PLAN_H / CELL)
    return {"id": pid, "name": "Savdo zali", "w_m": PLAN_W, "h_m": PLAN_H, "cell": CELL, "nx": nx, "ny": ny,
            "grid": f"{nx}x{ny}@{CELL}",
            "sections": [{"id": i + 1, "name": nm, "color": col, "pts": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}
                         for i, (nm, col, (x0, y0, x1, y1), _w) in enumerate(SECTIONS)]}


def is_empty() -> bool:
    from . import db as cdb

    return not cdb.scalar("SELECT COUNT(*) FROM companies")


def _clip_files(n: int = 3) -> list[tuple[bytes, bytes]]:
    """Namuna videodan qisqa kliplar (edge demosi bilan bir xil usul). OpenCV yo'q (bulut VPS) — kliplarsiz."""
    import tempfile

    tmp = None
    prev = os.environ.get("HUSHYOR_DATA_DIR")
    try:
        if "app.config" not in sys.modules:
            # Kliplar vaqtinchalik papkada yasaladi (edge demosi va haqiqiy data/ ga tegilmaydi), keyin o'chiriladi
            tmp = Path(tempfile.mkdtemp(prefix="hushyor_cdemo_"))
            os.environ["HUSHYOR_DATA_DIR"] = str(tmp / "demo")
        sys.path.insert(0, str(ROOT.parent))
        from app import demo as edemo
        from app.config import CLIPS_DIR

        out = []
        for clip, thumb in edemo._clips(n):
            if clip and (CLIPS_DIR / clip).is_file() and (CLIPS_DIR / thumb).is_file():
                out.append(((CLIPS_DIR / clip).read_bytes(), (CLIPS_DIR / thumb).read_bytes()))
        return out
    except Exception:  # noqa: BLE001 — klip ixtiyoriy
        return []
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
            if prev is None:
                os.environ.pop("HUSHYOR_DATA_DIR", None)
            else:
                os.environ["HUSHYOR_DATA_DIR"] = prev


def seed(days: int = DAYS, now: float | None = None, clips: bool = True) -> dict:
    """Bo'sh bulut bazasini to'ldiradi. Qaytaradi: yozuvlar soni."""
    import io

    from . import db as cdb
    from . import server

    now = time.time() if now is None else now
    rnd = random.Random(20261018)
    today = _day0(now)
    start = _day0(today - days * 86400 + 43200)
    day_list = []
    d = start
    while d <= today:
        day_list.append(d)
        d = _day0(d + 36 * 3600)
    cid = cdb.create_company(COMPANY)
    cdb.create_user(cid, EMAIL, PASSWORD, "Demo egasi", "owner")
    stats = {"branches": 0, "events": 0, "visits_min": 0, "queue_min": 0, "metrics": 0, "flow_hours": 0, "clips": 0}
    bids = []
    for spec in BRANCHES:
        bid, _tok = cdb.create_branch(cid, spec["name"], f"demo-{spec['slug']}", spec["address"])
        bids.append(bid)
        cdb.execute("UPDATE branches SET created_at=:t WHERE id=:b", {"t": start - 7 * 86400, "b": bid})
        stats["branches"] += 1
        _seed_branch(rnd, bid, spec, day_list, now, stats)
    # Rahbar (manager) va qorovul — "Xodimlar" sahifasi bo'sh turmasin
    cdb.create_user(cid, "menejer@demo.hushyor.uz", "Menejer2026", "Dilnoza (menejer)", "manager", bids[:2])
    cdb.create_user(cid, "qorovul@demo.hushyor.uz", "Qorovul2026", "Aziz (qorovul)", "guard", bids[:1])
    # Signallar (edge ko'rinishida, Sync API orqali) va kliplar
    files = _clip_files() if clips else []
    for bi, spec in enumerate(BRANCHES):
        b = cdb.row("SELECT * FROM branches WHERE id=:b", {"b": bids[bi]})
        items = _events(rnd, b, spec, day_list, now)
        for i in range(0, len(items), server.MAX_ITEMS):
            server._edge_events(b, items[i:i + server.MAX_ITEMS])
        stats["events"] += len(items)
        if files:
            theft = [x for x in items if x["kind"] in ("pocket", "torso", "bag")]
            # Klip: hamma javobsiz va oxirgi 6 ta signal (bosh sahifadagi "Tekshirish kerak" ro'yxati videoli bo'lsin)
            for k, it in enumerate([x for x in theft[:-6] if x["status"] == "yangi"] + theft[-6:]):
                clip, thumb = files[k % len(files)]
                server._store_clip(b, it["uid"], io.BytesIO(clip), thumb)
                stats["clips"] += 1
    tick(now)
    cdb.kv_set("demo_seeded_at", str(now))
    return stats


def _seed_branch(rnd: random.Random, bid: int, spec: dict, day_list: list[float], now: float, stats: dict) -> None:
    from . import db as cdb

    f = spec["f"]
    from . import report

    today = day_list[-1]
    week, _ = report.period("week", _off(), now)          # oxirgi tugagan hafta (Taqqoslash sukut bo'yicha shuni oladi)
    out0 = week + 2 * 86400 + 6 * 3600                    # o'sha haftada kompyuter o'chiq bo'lgan vaqt (bo'lsa)
    out1 = out0 + spec["outage"] * 3600
    vis, met, qm, fh = [], [], [], []
    plan = _plan_public(1)
    nx, ny = plan["nx"], plan["ny"]
    for di, d in enumerate(day_list):
        wd = _wday(d)
        # --- tizim qatori (har daqiqa — "ishlagan vaqt"), kameralar — oxirgi 2 kun
        m = int(d)
        m_end = min(int(d + 86400), int(now // 60 * 60) - 60)
        while m < m_end:
            if not (out0 <= m < out1):
                met.append({"branch_id": bid, "minute": m, "source_id": 0,
                            "data": {"minute": m, "source_id": 0, "source_name": "tizim",
                                     "cpu": round(rnd.uniform(15, 45), 1), "ram": round(rnd.uniform(40, 62), 1)}})
                if d >= today - 86400:
                    for sid, nm in ((1, "Savdo zali"), (2, spec["kassa"][0] + " navbati"), (3, "Kirish eshigi")):
                        h = time.gmtime(m + _off()).tm_hour
                        busy = WORK[0] <= h < WORK[1]
                        dead = spec["outage"] and sid == 2 and m >= now - 5400   # Samarqand: kassa kamerasi uzilgan
                        met.append({"branch_id": bid, "minute": m, "source_id": sid,
                                    "data": {"minute": m, "source_id": sid, "source_name": nm,
                                             "persons_avg": round(rnd.uniform(0.5, 3.5) * f, 2) if busy else 0.0,
                                             "persons_max": rnd.randint(1, 6) if busy else 0, "fps_ai": 8.0,
                                             "active_pct": 0.6 if busy else 0.05, "up_pct": 0.0 if dead else 1.0,
                                             "alerts": 0}})
            m += 60
        # --- tashriflar (kirish chizig'i) — daqiqalik yig'indi
        per_min: dict[int, list[int]] = {}
        for h, w in HOUR_W.items():
            n = int(round(w * 5 * f * DAY_F[wd] * rnd.uniform(0.85, 1.15)))
            for _ in range(n):
                t = d + h * 3600 + rnd.uniform(0, 3600)
                if t >= now - 60 or out0 <= t < out1:
                    continue
                per_min.setdefault(int(t // 60 * 60), [0, 0, 0])[0] += 1
                to = t + rnd.uniform(6, 30) * 60
                if to < now - 60:
                    per_min.setdefault(int(to // 60 * 60), [0, 0, 0])[1] += 1
        for k in range(3):                                   # xodimlar
            t = d + (8.7 + k * 5) * 3600
            if t < now:
                per_min.setdefault(int(t // 60 * 60), [0, 0, 0])[2] += 1
        for minute, (a, b_, s) in per_min.items():
            vis.append({"branch_id": bid, "minute": minute, "source_id": 3, "n_in": a, "n_out": b_, "n_staff": s,
                        "n_off_in": 0, "n_off_out": 0})
        # --- kassa navbati (oxirgi 3 hafta)
        if di >= len(day_list) - QUEUE_DAYS:
            for zi, zname in enumerate(spec["kassa"]):
                for h in range(WORK[0], WORK[1]):
                    for mi in range(60):
                        minute = int(d + h * 3600 + mi * 60)
                        if minute >= now - 120 or out0 <= minute < out1:
                            break
                        busy = h in (17, 18, 19)
                        load = f * DAY_F[wd] / len(spec["kassa"]) * (1.6 if zi == 0 else 1.0)
                        served = 1 if rnd.random() < min(0.95, (0.9 if busy else 0.45) * load) else 0
                        wait = (rnd.uniform(140, 260) if busy else rnd.uniform(40, 110)) * spec["wait"]
                        ln = (rnd.randint(3, 6) if busy else rnd.randint(0, 2))
                        long_ = rnd.uniform(20, 50) if busy and ln >= 4 else 0.0
                        qm.append({"branch_id": bid, "minute": minute, "zone_id": zi + 1, "zone_name": zname,
                                   "source_id": 2, "t_obs": 60.0, "len_sum": ln * 60.0, "len_max": ln,
                                   "long_sec": round(long_, 1), "busy_sec": 60.0 if ln else 20.0, "cashier_sec": 55.0,
                                   "cashier_known": 1, "served": served, "wait_sum": round(wait * served, 1),
                                   "wait_n": served, "wait_max": round(wait, 1) if served else 0.0})
        # --- oqim va heatmap (oxirgi 2 hafta, soatlik)
        if di >= len(day_list) - FLOW_DAYS:
            for h, w in HOUR_W.items():
                hour = int(d + h * 3600)
                if hour + 3600 > now or out0 <= hour < out1:
                    continue
                n = int(round(w * 4 * f * DAY_F[wd] * rnd.uniform(0.8, 1.2)))
                visits: dict[str, int] = {}
                moves: dict[tuple[int, int], int] = {}
                occ: dict[str, float] = {}
                pas: dict[str, int] = {}
                for _ in range(n):
                    path = rnd.choices(range(1, len(SECTIONS)), weights=[s[3] for s in SECTIONS[1:]],
                                       k=rnd.randint(1, 3)) + [0]
                    prev = None
                    for si in path:
                        sid = si + 1
                        visits[str(sid)] = visits.get(str(sid), 0) + 1
                        if prev is not None and prev != sid:
                            moves[(prev, sid)] = moves.get((prev, sid), 0) + 1
                        prev = sid
                        x0, y0, x1, y1 = SECTIONS[si][2]
                        for _k in range(5):
                            cx = int(min(nx - 1, max(0, _spot(rnd, x0, x1) / CELL)))
                            cy = int(min(ny - 1, max(0, _spot(rnd, y0, y1) / CELL)))
                            key = str(cy * nx + cx)
                            occ[key] = round(occ.get(key, 0.0) + rnd.uniform(4, 15), 1)
                            pas[key] = pas.get(key, 0) + 1
                if visits:
                    fh.append({"branch_id": bid, "plan_id": 1, "hour": hour, "grid": plan["grid"],
                               "visits": sum(visits.values()), "obs": 3600.0,
                               "data": {"visits": visits, "moves": [[a, b_, c] for (a, b_), c in moves.items()],
                                        "occ": occ, "pas": pas, "nx": nx, "ny": ny, "cell": CELL}})
    with cdb.engine().begin() as c:
        for table, rows in ((cdb.metrics_minute, met), (cdb.visits_minute, vis), (cdb.queue_minute, qm),
                            (cdb.flow_hour, fh)):
            for i in range(0, len(rows), 5000):
                c.execute(table.insert(), rows[i:i + 5000])
    stats["metrics"] += len(met)
    stats["visits_min"] += len(vis)
    stats["queue_min"] += len(qm)
    stats["flow_hours"] += len(fh)
    # Kamera holati tarixi (Samarqand: kassa kamerasi uzildi) va navbat signallari
    if spec["outage"]:
        cdb.execute("INSERT INTO health_log(key, branch_id, t, source_id, source_name, state, reason) "
                    "VALUES(:k, :b, :t, 2, :n, 'down', :r)",
                    {"k": f"demo-{spec['slug']}:h1", "b": bid, "t": now - 5400, "n": spec["kassa"][0] + " navbati",
                     "r": "Kameradan kadr kelmayapti (tarmoq kabeli?)"})
    rules = [("R1", "warn", "Navbat uzun", "navbatda 5 kishi, 3 daqiqadan ko'p"),
             ("R2", "warn", "Kassir yo'q", "navbat bor, kassir joyi 2 daqiqa bo'sh")]
    k = 0
    for d in day_list[-7:]:
        for _ in range(rnd.randint(0, 2) + (1 if spec["wait"] > 1.2 else 0)):
            rule, lvl, title, text = rnd.choice(rules)
            t = d + rnd.uniform(17, 19.5) * 3600
            if t >= now:
                continue
            k += 1
            cdb.execute("INSERT INTO queue_alerts(key, company_id, branch_id, t, rule, level, zone_name, source_name, "
                        "title, text, received_at) VALUES(:k, (SELECT company_id FROM branches WHERE id=:b), :b, :t, "
                        ":r, :l, :z, :s, :ti, :tx, :t)",
                        {"k": f"demo-{spec['slug']}:{k}", "b": bid, "t": t, "r": rule, "l": lvl,
                         "z": spec["kassa"][0], "s": spec["kassa"][0] + " navbati", "ti": title, "tx": text})


def _spot(rnd: random.Random, a: float, b: float) -> float:
    """Bo'lim ichida nuqta: o'rtasi gavjumroq (heatmap tekis "dog'" bo'lib qolmasin)."""
    return min(b, max(a, rnd.gauss((a + b) / 2, (b - a) / 4)))


def _events(rnd: random.Random, b: dict, spec: dict, day_list: list[float], now: float) -> list[dict]:
    items = []
    n = 0
    rej = rev = 0
    today = day_list[-1]
    for d in day_list:
        for _ in range(rnd.choice((1, 2, 2, 3, 4)) if spec["f"] >= 1 else rnd.choice((1, 1, 2, 3))):
            t = d + rnd.uniform(10, 21.5) * 3600
            if t >= now - 600:
                continue
            n += 1
            kind = rnd.choice(("pocket", "pocket", "torso", "bag"))
            r = rnd.random()
            # Javobsiz signallar — faqat oxirgi kunlarda (bugun yarmi, kecha-o'tgan kuni chorak); qolgani tekshirilgan
            if (d >= today and r < 0.5) or (d >= today - 2 * 86400 and r < 0.25):
                st = "yangi"
            else:
                # Xato ulushi filialniki atrofida tekis (har haftada ham) — taqqoslash tavsiyasi barqaror chiqsin
                st = "rad_etilgan" if rej < spec["false"] * (rev + 1) else "tasdiqlangan"
                rej += st == "rad_etilgan"
                rev += 1
            score = round(rnd.uniform(62, 94), 1)
            it = {"uid": f"{b['device_id']}:{n}", "local_id": n, "id": n, "device_id": b["device_id"],
                  "source_id": 1, "source_name": "Savdo zali", "created_at": t, "score": score,
                  "level": "yuqori" if score >= 75 else "o'rta", "kind": kind, "reasons": REASONS[kind],
                  "status": st, "track_id": 100 + n, "updated_at": t + 600, "key": f"ev:{n}"}
            if st != "yangi":
                it.update(reviewed_by=rnd.choice(REVIEWERS), reviewed_at=t + rnd.uniform(40, 420))
            items.append(it)
    for frac, kind, st, reason in ((0.3, "fall", "tasdiqlangan", "Odam yiqildi"),
                                   (0.6, "night", "tasdiqlangan", "Ish vaqtidan tashqari odam"),
                                   (0.9, "crowd", "rad_etilgan", "Kassa oldida gavjumlik")):
        d = day_list[int(frac * (len(day_list) - 2))]
        t = d + (23.2 if kind == "night" else 15.5) * 3600
        n += 1
        items.append({"uid": f"{b['device_id']}:{n}", "local_id": n, "id": n, "device_id": b["device_id"],
                      "source_id": 1, "source_name": "Savdo zali", "created_at": t, "score": 100, "level": "yuqori",
                      "kind": kind, "reasons": [reason], "status": st, "reviewed_by": REVIEWERS[2],
                      "reviewed_at": t + 95, "updated_at": t + 600, "key": f"ev:{n}"})
    items.sort(key=lambda x: x["created_at"])
    return items


def tick(now: float | None = None) -> None:
    """Har daqiqa: har filialdan "hello" (kameralar, ichkaridagilar, kassalar, reja, tayyorlik) va daqiqalik qatorlar —
    panel filiallarni onlayn, kassalarni jonli ko'rsatsin."""
    from . import db as cdb
    from . import server

    now = time.time() if now is None else now
    rnd = random.Random(int(now // 60))
    minute = int(now // 60 * 60) - 60
    today = _day0(now)
    h = time.gmtime(now + _off()).tm_hour
    open_ = WORK[0] <= h < WORK[1]
    for spec in BRANCHES:
        b = cdb.row("SELECT * FROM branches WHERE device_id=:d", {"d": f"demo-{spec['slug']}"})
        if not b:
            continue
        vis = cdb.visits_hourly([b["id"]], today, off=_off())
        cin, cout = sum(v["in"] for v in vis), sum(v["out"] for v in vis)
        busy = h in (17, 18, 19)
        queues = []
        for zi, z in enumerate(spec["kassa"]):
            ln = (rnd.randint(2, 6) if busy else rnd.randint(0, 2)) if open_ else 0
            dead = bool(spec["outage"]) and zi == 0
            cash = None if dead else (ln == 0 or rnd.random() < 0.9)
            st = ("nomalum" if dead else "yopiq" if not open_ else "kassir_yoq" if ln and not cash
                  else "navbat" if ln >= 4 else "band" if ln else "bosh")
            queues.append({"zone_id": zi + 1, "name": z, "source_name": z + " navbati", "length": ln,
                           "max_wait": int(ln * 45 * spec["wait"]), "cashier": cash, "state": st, "live": not dead})
        ok, total, bad, warn = spec["readiness"]
        pil = None
        if spec["pilot"]:
            since = _day0(now - spec["pilot"] * 86400 + 43200)
            pil = {"since": since, "until": None, "days": int((today - since) // 86400)}
        cams = [{"id": 1, "name": "Savdo zali", "kind": "rtsp", "state": "ishlayapti", "running": True},
                {"id": 2, "name": spec["kassa"][0] + " navbati", "kind": "rtsp",
                 "state": "qayta ulanmoqda" if spec["outage"] else "ishlayapti", "running": not spec["outage"]},
                {"id": 3, "name": "Kirish eshigi", "kind": "rtsp", "state": "ishlayapti", "running": True}]
        body = {"device_id": b["device_id"], "name": spec["name"], "version": VERSION_EDGE, "cameras": cams,
                "pending": 0, "time": now,
                "occupancy": {"inside": max(0, cin - cout) if open_ else 0, "since": today, "day0": today,
                              "in": cin, "out": cout},
                "queues": queues, "flow_plans": [_plan_public(1)],
                "readiness": {"ok": ok, "total": total, "bad": bad, "warn": warn, "pilot": pil}}
        server._hello(b, body, "127.0.0.1")
        rows = [{"minute": minute, "source_id": 0, "source_name": "tizim", "cpu": round(rnd.uniform(15, 45), 1),
                 "ram": round(rnd.uniform(40, 62), 1)}]
        for c in cams:
            rows.append({"minute": minute, "source_id": c["id"], "source_name": c["name"],
                         "persons_avg": round(rnd.uniform(0.5, 3.0), 2) if open_ else 0.0,
                         "persons_max": rnd.randint(1, 5) if open_ else 0, "fps_ai": 8.0, "active_pct": 0.6,
                         "up_pct": 1.0 if c["running"] else 0.0, "alerts": 0})
        server._edge_metrics(b, [{**r, "key": f"m:{r['minute']}:{r['source_id']}"} for r in rows])


MARK = DEMO_DIR / ".seeded"              # ma'lumot yaratilgan kun (mahalliy sana) — to'liq yaratilgandan keyin yoziladi


def _today() -> str:
    """Kompaniya mahalliy sanasi (CLOUD_TZ_OFFSET) — ma'lumot kunlari ham shu bo'yicha (server UTC bo'lsa ham)."""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + _off()))


def stale() -> str | None:
    """Qayta yaratish kerakmi: papka bor, lekin belgi yo'q (yarim yo'lda to'xtatilgan) yoki boshqa kunda yaratilgan."""
    if not DEMO_DIR.exists() or not any(DEMO_DIR.iterdir()):
        return None
    try:
        day = MARK.read_text(encoding="utf-8").strip()
    except OSError:
        return "oldingi yaratish tugallanmagan"
    return None if day == _today() else f"{day} kuni yaratilgan"


def reset() -> None:
    """Demo papkasini tozalash (faqat cloud/data/demo — haqiqiy bulut bazasiga tegmaydi). Avval nomi o'zgartiriladi:
    papka boshqa oynada ochiq bo'lsa (Windows) — xato darhol, yarim o'chirilmay."""
    if DEMO_DIR.exists():
        tmp = DEMO_DIR.with_name(f"demo_old_{int(time.time())}")
        DEMO_DIR.rename(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


class Ticker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="cloud-demo")
        self.stop_evt = threading.Event()

    def run(self) -> None:
        while not self.stop_evt.wait(60):
            try:
                tick()
            except Exception as e:  # noqa: BLE001
                print("Demo tick xatosi:", e, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Hushyor AI bulut demo (3 filialli namuna kompaniya)")
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--days", type=int, default=DAYS)
    a = ap.parse_args(argv)
    if "cloud.db" in sys.modules:
        print("cloud.demo alohida ishga tushirilishi kerak: python -m cloud.demo", file=sys.stderr)
        return 2
    import socket

    url = f"http://127.0.0.1:{a.port}"
    if not a.seed_only:
        try:
            with socket.create_connection(("127.0.0.1", a.port), timeout=1):
                print(f"{a.port}-port band: demo allaqachon ochiq bo'lishi mumkin — {url}. Boshqa port: --port {a.port + 1}")
                return 2
        except OSError:
            pass
    os.environ.update(demo_env())             # cloud.db shundan keyin import qilinadi (DATA_DIR, baza — demo papkasi)
    from . import notify

    notify.configure()                        # CLOUD_TZ_OFFSET — demo kuni shu bo'yicha
    why = "--reset" if a.reset and DEMO_DIR.exists() else stale()
    if why:
        print(f"Demo ma'lumoti qayta yaratiladi ({why})", flush=True)
        try:
            reset()
        except OSError:
            print("Demo boshqa oynada ochiq — avval uni yoping (Ctrl+C), keyin qayta urinib ko'ring", file=sys.stderr)
            return 2
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    from . import server

    server.init_db()
    if is_empty():
        print("Bulut demo ma'lumoti yaratilmoqda...", flush=True)
        t0 = time.time()
        st = seed(days=max(14, min(a.days, 90)))
        MARK.write_text(_today(), encoding="utf-8")
        print(f"Tayyor ({time.time() - t0:.0f} s): {st}", flush=True)
    else:
        print("Demo ma'lumot bor (qaytadan yaratish: --reset)", flush=True)
    if a.seed_only:
        return 0
    import uvicorn

    server._state["tokens"], server._state["admin"] = [], ""
    server._state["notify_configured"] = True
    tick()
    Ticker().start()
    print("\n" + "=" * 60)
    print(f"  Hushyor AI bulut DEMO:  {url}")
    print(f"  Login / parol:          {EMAIL} / {PASSWORD}")
    print(f"  Sayt (landing):         {url}/sayt/")
    print("  To'xtatish:             Ctrl+C")
    print("=" * 60 + "\n", flush=True)
    uvicorn.run(server.app, host="127.0.0.1", port=a.port, log_level="warning", access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
