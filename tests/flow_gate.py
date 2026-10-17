"""5-bosqich darvozasi: "belgilangan nuqtalarda rejadagi xato ≤ 0,5 m; bo'lim tashriflari qo'lda tekshiruvga mos".

Avval panelda: **Oqim** → **Aniqlik sinovi**:
  * "Nuqtada turish" — polga belgi qo'yiladi (joyi rejada o'lchanadi), odam belgida turadi, tizim 3 s o'lchaydi.
    Har kalibrlangan kamerada kamida 5 ta o'lchov, 3 xil joyda;
  * "Qo'lda sanash" — 1–3 bo'limni kuzating, bo'limga kirgan har xaridorda "+1". Kamida 2 × 30 daqiqa.
Keyin:

    python tests/flow_gate.py                    # data/hushyor.db (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/flow_gate.py --db boshqa.db

Mezonlar (app/flow_eval.py — panel ham aynan shularni ishlatadi). Chiqish kodi: 0 — o'tdi, 1 — o'tmadi,
2 — ma'lumot yetarli emas.
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

from app import flow_eval as fe  # noqa: E402  (toza modul: bazaga ham, sozlamaga ham tegmaydi)


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def m(v) -> str:
    return "—" if v is None else f"{v:.2f} m"


def main() -> int:
    for st in (sys.stdout, sys.stderr):          # Windows konsoli "≥" ni chiqara olmasa — yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Oqim va heatmap darvozasi (5-bosqich)")
    ap.add_argument("--db", default=str(ROOT / "data" / "hushyor.db"))
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print(f"Baza topilmadi: {db}")
        return 2
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        try:
            cams = [dict(r) for r in c.execute("SELECT c.source_id, c.updated_at, s.name FROM cam_calib c LEFT JOIN "
                                               "sources s ON s.id=c.source_id WHERE c.enabled=1 ORDER BY c.source_id")]
            tests = {x["source_id"]: [dict(r) for r in c.execute(
                "SELECT * FROM flow_points WHERE source_id=? AND t>=? ORDER BY id DESC",
                (x["source_id"], float(x["updated_at"] or 0)))] for x in cams}
            sess = [dict(r) for r in c.execute("SELECT * FROM flow_sessions ORDER BY id DESC LIMIT 30")]
            for s in sess:
                t1 = s["ended_at"] or time.time()
                s["_autos"] = [dict(r) for r in c.execute(
                    "SELECT t, section_id FROM section_visits WHERE plan_id=? AND t>=? AND t<=?",
                    (s["plan_id"], s["started_at"] - fe.MATCH_TOL, t1 + fe.MATCH_TOL))]
        except sqlite3.OperationalError as e:
            print(f"Bazada oqim jadvallari yo'q (2.1.0 hali ishga tushmagan?): {e}")
            return 2
    finally:
        c.close()

    print("1) Nuqtada turish (har kamera, oxirgi kalibrlashdan keyin)")
    if not cams:
        print("   Kalibrlangan kamera yo'q.")
    pts = []
    for x in cams:
        g = fe.point_gate(tests[x["source_id"]])
        pts.append(g)
        st = "O'TDI" if g["ok"] else ("ma'lumot kam" if not g["enough"] else "O'TMADI")
        print(f"   {x['name'] or x['source_id']}: {g['n']} o'lchov, {g['spots']} joy, 90% xato {m(g['p90'])}, "
              f"o'rtacha {m(g['mean'])}, eng katta {m(g['max'])} — {st}")
    print(f"   Darvoza: har kamerada ≥ {fe.POINT_MIN} o'lchov, ≥ {fe.POINT_SPOTS} joy (≥ {fe.SPOT_SEP:g} m oraliq), 90% "
          f"o'lchovda xato ≤ {fe.POINT_MAX_ERR:g} m")

    res = []
    for s in sess:
        taps = json.loads(s.get("taps") or "[]")
        secs = json.loads(s.get("sections") or "[]")
        r = fe.compare(taps, s["_autos"], secs)
        r.update(ended_at=s["ended_at"], duration=round((s["ended_at"] or time.time()) - s["started_at"]),
                 started_at=s["started_at"])
        res.append(r)
    sg = fe.sessions_gate(res)
    print("\n2) Bo'lim tashriflari — qo'lda sanash")
    for r in res[:10]:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["started_at"]))
        st = "davom etyapti" if not r["ended_at"] else ("mos" if fe.session_ok(r) else
                                                         ("qisqa" if r["duration"] < fe.VISIT_MIN_SEC else "mos emas"))
        print(f"   {when}  {r['duration'] // 60} daq  qo'lda {r['manual']}  avto {r['auto']}  juftlangan {r['matched']}  "
              f"aniqlik {pct(r['accuracy'])}  juftlash {pct(r['match_rate'])} — {st}")
    print(f"   Darvoza: oxirgi {fe.VISIT_SESSIONS} ta ≥ {fe.VISIT_MIN_SEC // 60} daqiqalik sinov, har birida aniqlik ≥ "
          f"{pct(fe.VISIT_ACC)} va juftlash (±{fe.MATCH_TOL:g} s) ≥ {pct(fe.VISIT_MATCH)}, jami ≥ {fe.VISIT_MIN_MANUAL} bosish"
          f" — " + ("O'TDI" if sg["ok"] else ("ma'lumot kam" if not sg["enough"] else "O'TMADI")))

    pts_ok = bool(pts) and all(g["ok"] for g in pts)
    pts_enough = bool(pts) and all(g["enough"] for g in pts)
    print()
    if pts_ok and sg["ok"]:
        print("XULOSA: 5-bosqich darvozasi O'TDI — nuqtalarda xato ≤ 0,5 m, bo'lim tashriflari qo'lda sanashga mos.")
        return 0
    if (pts_enough and not pts_ok) or (sg["enough"] and not sg["ok"]):
        print("XULOSA: O'TMADI.")
        if pts_enough and not pts_ok:
            print("  Nuqta xatosi katta: kalibrlashga ko'proq nuqta qo'shing (8–12, kadrning hamma qismidan, ayniqsa xato "
                  "katta chiqqan joy atrofidan); faqat pol sathidagi nuqtalarni bosing; reja masshtabini tekshiring. Kadr "
                  "ustidagi 1 m to'r plitkalarga mos tushishi kerak. Uzoq joyda xato tabiiy ravishda katta — u yerni boshqa "
                  "kamera yaqinroqdan ko'rsa, o'sha kamerani ham kalibrlang.")
        if sg["enough"] and not sg["ok"]:
            print("  Tashriflar mos emas: bo'lim chegarasi xaridor turadigan yo'lakni qamrab olsin (polka ustini emas); "
                  "bir-biriga yopishgan bo'limlar orasida 0,5–1 m bo'shliq qoldiring; qo'lda sanashda o'tib ketganlarni "
                  "emas, to'xtaganlarni bosing.")
        return 1
    print("XULOSA: ma'lumot yetarli emas — Oqim sahifasidagi Aniqlik sinovi kerak.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
