"""8-bosqich darvozasi (2.4.0): "hisobot 2 hafta har kuni o'z vaqtida keladi, raqamlari to'g'ri, tavsiyalar foydali".

Panel (Hisobotlar sahifasi) ham, buyruq qatoridagi darvoza (tests/report_gate.py) ham shu funksiyani ishlatadi —
bazaga murojaat q(sql, params) -> [dict] orqali (panelda db.rows, CLI'da faqat o'qiladigan sqlite).

Mezonlar:
  1. Oxirgi DAYS ta tugagan kun (hisobotlar yoqilganidan keyingi) har biri uchun kunlik hisobotni rejalashtiruvchi
     Telegram'ga rejadagi vaqtdan ≤ 30 daqiqa kechikib yetkazgan (paneldan qo'lda yuborilgani sanalmaydi); ko'pi bilan
     MAX_MISS kun o'tkazib yuborilishi mumkin.
  2. Raqamlar to'g'ri: yuborilgan har hisobotdagi tashriflar va o'g'irlik signallari soni hisobot yaratilgan paytda
     bazadan mustaqil SQL bilan qayta sanalganiga (reports.checked) teng — keyin hodisa o'chirilsa (❌ larni tozalash,
     saqlash muddati) darvoza buzilmaydi. checked bo'lmasa — hozirgi bazadan.
  3. Kamida MIN_USEFUL xil tavsiya "👍 foydali": oynadagi yuborilgan kunlik hisobotlardagi baholar, tavsiya turi
     bo'yicha (navbat soatlari oralig'i farqi — bitta tur), har kishining har hisobotdagi oxirgi bahosi; 👍 lar 👎 lardan
     ko'p bo'lgan tur — foydali.
"""
from __future__ import annotations

import json
import time

DAYS = 14
MAX_MISS = 1
MIN_USEFUL = 3
ON_TIME_SEC = 1800
SAFETY = ("fall", "lying", "night", "crowd", "blocked", "fire", "fight")


def _day0(t: float) -> float:
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def days_window(since: float, now: float) -> list[float]:
    """Oxirgi DAYS ta tugagan kun (yarim tunlari), hisobotlar yoqilgan kundan keyingi (o'sha kunning o'zi — yo'q:
    u kun to'liq kuzatilmagan)."""
    first = _day0(since + 86400 - 1) if since else 0
    out = []
    d = _day0(_day0(now) - 43200)               # kecha
    while len(out) < DAYS and d >= first:
        out.append(d)
        d = _day0(d - 43200)
    return sorted(out)


def recount(q, t0: float, t1: float) -> dict:
    marks = ",".join("?" for _ in SAFETY)
    vin = q("SELECT SUM(dir='in' AND staff=0 AND offhours=0) AS n FROM counts WHERE t >= ? AND t < ?", (t0, t1))
    th = q(f"SELECT COUNT(*) AS n FROM events WHERE job_id IS NULL AND created_at >= ? AND created_at < ? "
           f"AND (kind IS NULL OR kind NOT IN ({marks}))", (t0, t1, *SAFETY))
    return {"in": int((vin[0]["n"] if vin else 0) or 0), "theft": int((th[0]["n"] if th else 0) or 0)}


def rec_type(key: str) -> str:
    parts = str(key).split(":")                 # app/reports.py: rec_type bilan bir xil
    return ":".join(parts[:2]) if parts[0] == "queue" and len(parts) > 2 else str(key)


def gate(q, since: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    days = days_window(since, now)
    reps = {round(r["t0"]): r for r in q("SELECT * FROM reports WHERE kind='daily' AND t0 >= ?",
                                         (days[0] if days else now,))}
    manual: set[int] = set()
    if reps:
        marks = ",".join("?" for _ in reps)
        manual = {int(x["report_id"]) for x in q(
            f"SELECT DISTINCT report_id FROM report_sends WHERE sent_at IS NOT NULL AND auto=0 AND report_id IN ({marks})",
            tuple(int(r["id"]) for r in reps.values()))}
    rows, on_time, mism = [], 0, []
    for d in days:
        r = reps.get(round(d))
        st = "yo'q"
        late = None
        if r and r.get("sent_at"):
            late = float(r["sent_at"]) - float(r["due_at"] or r["sent_at"])
            st = "o'z vaqtida" if late <= ON_TIME_SEC else "kech"
            on_time += st == "o'z vaqtida"
            try:
                data = json.loads(r["data"] or "{}")
            except ValueError:
                data = {}
            try:
                rc = json.loads(r.get("checked") or "null")
            except ValueError:
                rc = None
            if not isinstance(rc, dict):
                rc = recount(q, float(r["t0"]), float(r["t1"]))
            rc = {"in": int(rc.get("in") or 0), "theft": int(rc.get("theft") or 0)}
            got = {"in": int((data.get("visitors") or {}).get("in") or 0),
                   "theft": int((data.get("theft") or {}).get("total") or 0)}
            if got != rc:
                mism.append({"day": d, "report": got, "db": rc})
        elif r and int(r["id"]) in manual:
            st = "qo'lda yuborilgan"       # darvoza avtomatik yuborishni o'lchaydi
        elif r:
            st = "yuborilmadi"
        rows.append({"day": d, "state": st, "late_sec": None if late is None else round(late)})
    sent_ids = [int(r["id"]) for d0, r in reps.items() if r.get("sent_at") and d0 in {round(x) for x in days}]
    votes: dict[str, list[int]] = {}
    if sent_ids:
        marks = ",".join("?" for _ in sent_ids)
        for f in q(f"SELECT rec_key, useful FROM report_feedback WHERE report_id IN ({marks})", tuple(sent_ids)):
            v = votes.setdefault(rec_type(f["rec_key"]), [0, 0])
            v[0 if int(f["useful"]) else 1] += 1
    n_useful = sum(1 for up, down in votes.values() if up > down)
    n_useless = sum(1 for up, down in votes.values() if down > up)
    missed = len(days) - on_time
    enough = len(days) >= DAYS
    ok = enough and missed <= MAX_MISS and not mism and n_useful >= MIN_USEFUL
    bad = (missed > MAX_MISS and len(days) >= DAYS) or bool(mism)
    return {"days": rows, "on_time": on_time, "missed": missed, "need_days": DAYS, "max_miss": MAX_MISS,
            "mismatch": mism, "useful": n_useful, "useless": n_useless,
            "min_useful": MIN_USEFUL, "enough": enough, "ok": ok,
            "state": "otdi" if ok else ("otmadi" if bad else "yetarli emas")}
