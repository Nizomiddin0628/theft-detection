"""4-bosqich darvozasi: "navbat soni ±1 odam aniqlikda namunalarning ≥ 90% ida; kutish vaqti xatosi ≤ 20%".

Avval panelda: **Navbat** → **Aniqlik sinovi**:
  * "Namuna olish" — tizim kadrni ko'rsatadi, siz kadrdagi navbatda turganlarni sanab kiritasiz. Kamida 50 ta namuna
    (har 10 s dan tez emas), ulardan kamida 15 tasida navbatda ≥ 2 kishi bo'lsin — gavjum soatlarda qiling;
  * "Sekundomer" — odam navbatga turganda bosing, to'lab chiqqanda shu sekundomerni to'xtating. Kamida 10 kishi.
Keyin:

    python tests/queue_gate.py                    # data/hushyor.db (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/queue_gate.py --db boshqa.db --days 60

Mezonlar (app/queue_eval.py — panel ham aynan shularni ishlatadi). Chiqish kodi: 0 — o'tdi, 1 — o'tmadi,
2 — ma'lumot yetarli emas.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import queue_eval as qe  # noqa: E402  (toza modul: bazaga ham, sozlamaga ham tegmaydi)


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def main() -> int:
    for st in (sys.stdout, sys.stderr):          # Windows konsoli "≥" ni chiqara olmasa — yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Navbat o'lchash darvozasi (4-bosqich)")
    ap.add_argument("--db", default=str(ROOT / "data" / "hushyor.db"))
    ap.add_argument("--days", type=int, default=30, help="oxirgi necha kundagi namuna va sekundomerlar")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print(f"Baza topilmadi: {db}")
        return 2
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    since = time.time() - max(1, a.days) * 86400
    try:
        try:
            samples = [dict(r) for r in c.execute("SELECT * FROM queue_samples WHERE t>=? ORDER BY id", (since,))]
            tims = [dict(r) for r in c.execute("SELECT * FROM queue_timings WHERE t0>=? ORDER BY id", (since,))]
            autos = []
            if tims:
                t_lo = min(float(x["t0"]) for x in tims) - 120
                autos = [dict(r) for r in c.execute("SELECT zone_id, t_in, t_out, wait FROM queue_waits WHERE t_out>=?",
                                                    (t_lo,))]
        except sqlite3.OperationalError as e:
            print(f"Bazada navbat jadvallari yo'q (2.0.0 hali ishga tushmagan?): {e}")
            return 2
    finally:
        c.close()

    g1 = qe.samples_gate(samples)
    print(f"1) Navbat soni — namunalar (oxirgi {a.days} kun)")
    by_zone: dict = {}
    for s in samples:
        if s.get("manual") is None:
            continue
        z = by_zone.setdefault(s["zone_name"] or s["zone_id"], [0, 0])
        z[0] += 1
        z[1] += int(abs(int(s["auto"]) - int(s["manual"])) <= qe.SAMPLE_TOL)
    for name, (n, good) in by_zone.items():
        print(f"   {name}: {n} namuna, ±{qe.SAMPLE_TOL} ichida {good} ({pct(good / n if n else None)})")
    print(f"   Jami: {g1['n']} namuna (navbatda ≥ 2 kishi: {g1['busy']}), ±{qe.SAMPLE_TOL} ichida {g1['good']} — "
          f"{pct(g1['accuracy'])}, aynan to'g'ri {g1['exact']}, o'rtacha farq {g1['bias'] if g1['bias'] is not None else '—'}"
          f" (manfiy — tizim kam sanaydi)")
    print(f"   Darvoza: ≥ {qe.SAMPLE_MIN} namuna, ulardan ≥ {qe.SAMPLE_MIN_BUSY} tasida navbat, ±{qe.SAMPLE_TOL} ichida ≥ "
          f"{pct(qe.GATE_SAMPLE_ACC)} — " + ("O'TDI" if g1["ok"] else ("yetarli namuna yo'q" if not g1["enough"] else "O'TMADI")))

    res = qe.match_timings(tims, autos)
    g2 = qe.timings_gate(res)
    print(f"\n2) Kutish vaqti — sekundomer (oxirgi {a.days} kun)")
    for r in res[-15:]:
        if r.get("running"):
            continue
        when = time.strftime("%d.%m %H:%M", time.localtime(r["t0"]))
        auto = "topilmadi" if r["auto"] is None else f"{r['auto']:.0f} s"
        st = "kutilmoqda" if r.get("pending") else (pct(r["err"]) if r["err"] is not None else "100% (juftlanmadi)")
        print(f"   {when}  qo'lda {r['manual']:.0f} s  avto {auto}  xato {st}")
    print(f"   Jami: {g2['n']} o'lchov, juftlangan {g2['matched']}, o'rtacha xato {pct(g2['mean_err'])} "
          f"(mediana {pct(g2['median_err'])})")
    print(f"   Darvoza: ≥ {qe.TIMING_MIN} o'lchov, o'rtacha xato ≤ {pct(qe.GATE_WAIT_ERR)} — "
          + ("O'TDI" if g2["ok"] else ("yetarli o'lchov yo'q" if not g2["enough"] else "O'TMADI")))

    print()
    if g1["ok"] and g2["ok"]:
        print("XULOSA: 4-bosqich darvozasi O'TDI — navbat soni ±1 ≥ 90%, kutish vaqti xatosi ≤ 20%.")
        return 0
    if (g1["enough"] and not g1["ok"]) or (g2["enough"] and not g2["ok"]):
        print("XULOSA: O'TMADI.")
        if g1["enough"] and not g1["ok"]:
            if (g1["bias"] or 0) < 0:
                print("  Navbat soni kam: navbatdagilar bir-birini to'sib qo'yyapti. Kamerani balandroq yoki navbatga "
                      "ro'paradan (yon tomondan emas) qo'ying; navbat zonasi butun navbatni (oxirgi odamgacha) qamrasin.")
            else:
                print("  Navbat soni ko'p: zona o'tish yo'lagini ham qamragan yoki kassir navbatga qo'shilyapti. Zonani "
                      "faqat navbat turadigan joyga torroq chizing; peshtaxta ortiga 'Kassir joyi' zonasini chizing.")
        if g2["enough"] and not g2["ok"]:
            fin = [r for r in res if r.get("err") is not None]
            signed = [(r["auto"] - r["manual"]) / max(r["manual"], 1.0) for r in fin]
            unmatched = g2["n"] - g2["matched"]
            if unmatched > g2["n"] * 0.2 or (signed and sum(signed) / len(signed) < 0):
                print("  Kutish kam o'lchanmoqda yoki odam yo'qolyapti: navbatda to'silish ko'p (kuzatuv uziladi). Kamerani "
                      "navbat ustidan/ro'parasidan qo'ying; Sozlamalar → AI tezligini pasaytirmang; zonani to'lov joyi "
                      "bilan birga chizing (to'layotgan odam zonadan chiqib qolmasin).")
            else:
                print("  Kutish ko'p o'lchanmoqda: zona juda katta (to'lagandan keyin ham odam zonada turibdi) — "
                      "zonani to'lov joyi va navbat bilan cheklang; sekundomerni odam navbatga turgan zahoti bosing.")
        return 1
    print("XULOSA: ma'lumot yetarli emas — Navbat sahifasida namuna va sekundomer o'lchovlari kerak.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
