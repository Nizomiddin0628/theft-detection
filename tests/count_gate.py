"""3-bosqich darvozasi: "odamlarni sanash aniqligi ≥ 95%".

Avval panelda: **Tashriflar** → **Qo'lda sanash** — eshik oldida (yoki kamera videosini ko'rib) kamida 1 soat
har kirgan/chiqqanda tugmani bosing, keyin "Tugatish". Buni ikki marta (har xil kun yoki soatda) qiling. Keyin:

    python tests/count_gate.py                       # data/hushyor.db (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/count_gate.py --db boshqa.db --days 14

Mezonlar (app/count_eval.py — panel ham aynan shularni ishlatadi):
  1. oxirgi 2 ta ≥ 1 soatlik qo'lda sanash sinovining har birida son aniqligi ≥ 95% (kirdi va chiqdi alohida)
     va juftlash ≥ 95% (har bosish ±4 s ichida avtomatik o'tish bilan; o'tkazib yuborilgan va ortiqcha bir-birini
     yopmasin);
  2. kunlik muvozanat: oxirgi --days kun ichida (bugundan tashqari) ≥ 50 kishi kirgan kunlar soni ≥ 3 va
     |kirdi − chiqdi| / kirdi medianasi ≤ 5% (xodimsiz, ish vaqtidan qat'i nazar — yopilgandan keyin chiqqanlar ham).
Chiqish kodi: 0 — o'tdi, 1 — o'tmadi, 2 — ma'lumot yetarli emas.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import count_eval as ce  # noqa: E402  (toza modul: bazaga ham, sozlamaga ham tegmaydi)


def _connect(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _day_start(t: float) -> float:
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def load_sessions(c: sqlite3.Connection) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM count_sessions ORDER BY id DESC"):
        r = dict(r)
        try:
            taps = json.loads(r.get("taps") or "[]")
        except ValueError:
            taps = []
        t1 = r["ended_at"] or time.time()
        sql = "SELECT t, dir FROM counts WHERE t>=? AND t<=?"
        params: list = [r["started_at"] - ce.MATCH_TOL, t1 + ce.MATCH_TOL]
        if r.get("source_id"):
            sql += " AND source_id=?"
            params.append(r["source_id"])
        autos = [dict(x) for x in c.execute(sql, params)]
        res = ce.compare(taps, autos)
        res.update({"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
                    "duration": round(t1 - r["started_at"]), "note": r.get("note") or ""})
        res["gate_ok"] = ce.session_ok(res)
        out.append(res)
    return out


def load_days(c: sqlite3.Connection, days: int) -> list[dict]:
    """Biznes kunlari (count_eval.business_day: 09:00-22:00 bo'lsa 03:30 dan 03:30 gacha) — hali tugamaganlari yo'q."""
    try:
        r = c.execute("SELECT value FROM settings WHERE key='work_hours'").fetchone()
        spec = (r["value"] if r else "") or ""
    except sqlite3.OperationalError:
        spec = ""
    today = _day_start(time.time())
    now = time.time()
    out = []
    for i in range(0, days + 1):
        day = _day_start(today - i * 86400 + 43200)
        t0, t1 = ce.business_day(day, spec)
        if t1 > now:
            continue                                   # biznes kuni hali tugamagan
        if len(out) >= days:
            break
        r = c.execute("SELECT SUM(dir='in' AND staff=0 AND offhours=0) AS cin, "
                      "SUM(dir='in' AND staff=0) AS ain, SUM(dir='out' AND staff=0) AS aout, SUM(staff=1) AS st "
                      "FROM counts WHERE t>=? AND t<?", (t0, t1)).fetchone()
        # "in"/"out" — muvozanat uchun: xodimsiz, ish vaqtidan qat'i nazar; "customers" — ish vaqtidagi xaridorlar
        out.append({"day": t0, "in": int(r["ain"] or 0), "out": int(r["aout"] or 0), "customers": int(r["cin"] or 0),
                    "staff": int(r["st"] or 0)})
    return out


WEEKDAYS = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def main() -> int:
    for st in (sys.stdout, sys.stderr):          # Windows konsoli (cp866/cp1251) "≥" ni chiqara olmasa — yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Odam sanash darvozasi (3-bosqich)")
    ap.add_argument("--db", default=str(ROOT / "data" / "hushyor.db"))
    ap.add_argument("--days", type=int, default=14, help="kunlik muvozanat uchun oxirgi necha kun (bugundan tashqari)")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print(f"Baza topilmadi: {db}")
        return 2
    c = _connect(db)
    try:
        try:
            sess = load_sessions(c)
            days = load_days(c, max(1, a.days))
        except sqlite3.OperationalError as e:
            print(f"Bazada sanash jadvallari yo'q (1.9.0 hali ishga tushmagan?): {e}")
            return 2
    finally:
        c.close()

    print("1) Qo'lda sanash sinovlari (yangisi birinchi)")
    if not sess:
        print("   Hali sinov yo'q. Panel → Tashriflar → Qo'lda sanash.")
    for s in sess[:10]:
        when = time.strftime("%d.%m %H:%M", time.localtime(s["started_at"]))
        dur = f"{s['duration'] // 60} daq"
        state = "davom etyapti" if not s["ended_at"] else ("OK" if s["gate_ok"] else
                                                           ("qisqa" if s["duration"] < ce.GATE_MIN_SEC else "PAST"))
        print(f"   #{s['id']:<3} {when}  {dur:>7}  kirdi qo'lda/avto {s['in']['manual']}/{s['in']['auto']}  "
              f"chiqdi {s['out']['manual']}/{s['out']['auto']}  o'tkazib yuborilgan {s['in']['missed'] + s['out']['missed']}  "
              f"ortiqcha {s['in']['extra'] + s['out']['extra']}  aniqlik {pct(s['accuracy'])}  "
              f"juftlash {pct(s['match_rate'])}  [{state}]")
    g1 = ce.sessions_gate(sess)
    print(f"   Darvoza: oxirgi {ce.GATE_SESSIONS} ta ≥{ce.GATE_MIN_SEC // 60} daq sinov, har birida aniqlik ≥ "
          f"{pct(ce.GATE_ACC)} va juftlash ≥ {pct(ce.GATE_MATCH)} — "
          + ("O'TDI" if g1["ok"] else ("yetarli sinov yo'q" if not g1["enough"] else "O'TMADI")))

    print(f"\n2) Kunlik muvozanat (oxirgi {len(days)} ta tugagan biznes kuni; ≥ {ce.DRIFT_MIN_IN} kishi kirgan kunlar)")
    for d in days:
        if not d["in"] and not d["out"]:
            continue
        mark = "" if d["in"] >= ce.DRIFT_MIN_IN else "  (kam — hisobga olinmaydi)"
        dr = abs(d["in"] - d["out"]) / d["in"] if d["in"] else None
        lt = time.localtime(d["day"])
        print(f"   {time.strftime('%d.%m', lt)} ({WEEKDAYS[lt.tm_wday]})  kirdi {d['in']:>5}  chiqdi {d['out']:>5}  "
              f"og'ish {pct(dr):>6}  (ish vaqtida xaridor {d['customers']}, xodim o'tishi {d['staff']}){mark}")
    g2 = ce.drift_gate(days)
    print(f"   Darvoza: kamida {ce.DRIFT_MIN_DAYS} kun, og'ish medianasi ≤ {pct(ce.DRIFT_MAX)} — "
          + (f"O'TDI (median {pct(g2['median'])}, eng yomoni {pct(g2['worst'])})" if g2["ok"] else
             ("yetarli kun yo'q" if not g2["enough"] else f"O'TMADI (median {pct(g2['median'])})")))

    print()
    if g1["ok"] and g2["ok"]:
        print("XULOSA: 3-bosqich darvozasi O'TDI — sanash aniqligi ≥ 95%.")
        return 0
    if (g1["enough"] and not g1["ok"]) or (g2["enough"] and not g2["ok"]):
        print("XULOSA: O'TMADI.")
        long_ = [x for x in sess if x["ended_at"] and x["duration"] >= ce.GATE_MIN_SEC][:ce.GATE_SESSIONS]
        missed = sum(x["in"]["missed"] + x["out"]["missed"] for x in long_)
        extra = sum(x["in"]["extra"] + x["out"]["extra"] for x in long_)
        if long_ and missed > extra:
            print("  Ko'pi o'tkazib yuborilgan: kamera odamni ko'rmayapti yoki chiziq kadr chetida. Chiziqni kadr o'rtasiga,"
                  " eshikdan 1–2 qadam ichkariga qo'ying; kechqurun yorug'likni tekshiring; panelda AI tezligi past emasmi.")
        elif long_ and extra > missed:
            print("  Ko'pi ortiqcha: chiziq odamlar to'xtab turadigan joyda (eshik oldi, kassa navbati) — bir odam ikki marta"
                  " sanaladi. Chiziqni odamlar to'xtamasdan o'tadigan joyga suring; bitta eshikni ikki kamera (ikki chiziq)"
                  " sanamayotganini tekshiring.")
        if g2["enough"] and not g2["ok"]:
            print("  Kunlik og'ish katta: kirish va chiqish boshqa-boshqa eshikdan bo'lsa — ikkinchi eshikka ham chiziq"
                  " chizing; ish vaqtini (Tashriflar → Sozlash) to'g'ri qo'ying, aks holda kechki chiqishlar tushib qoladi.")
        return 1
    print("XULOSA: ma'lumot yetarli emas — qo'lda sanash sinovlari va bir necha ish kuni kerak.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
