"""7-bosqich darvozasi: "o'g'irlik signallarida xato ≤ 3 / kamera / kun".

Avval: jonli kameralarda kamida 7 kun ishlang va har o'g'irlik signaliga ✅ yoki ❌ javob bering (javobsizlar ≤ 10%).
Keyin:

    python tests/theft_gate.py                   # data/hushyor.db (HUSHYOR_DATA_DIR bo'lsa — o'sha papkadagi) (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/theft_gate.py --db boshqa.db

Mezonlar: app/theft_eval.py (panel ham aynan shularni ishlatadi). Chiqish kodi: 0 — o'tdi, 1 — o'tmadi,
2 — ma'lumot yetarli emas.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import theft_eval as te  # noqa: E402  (toza modul)

SAFETY = ("fall", "lying", "night", "crowd", "blocked", "fire", "fight")


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="O'g'irlik signallari darvozasi (bazani faqat o'qiydi)")
    data_dir = Path(os.environ.get("HUSHYOR_DATA_DIR") or ROOT / "data")
    ap.add_argument("--db", default=str(data_dir / "hushyor.db"))
    a = ap.parse_args()
    p = Path(a.db)
    if not p.exists():
        print(f"Baza topilmadi: {p}")
        return 2
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    sets = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    t0 = te.window(sets.get("retention_days", "30"), time.time())
    mins = {r["source_id"]: dict(r) for r in con.execute(
        "SELECT source_id, MAX(source_name) AS name, COUNT(*) AS minutes FROM metrics_minute WHERE source_id>0 "
        "AND up_pct>=0.5 AND minute>=? GROUP BY source_id", (t0,))}
    marks = ",".join("?" for _ in SAFETY)
    ev: dict = {}
    for r in con.execute(f"SELECT source_id, status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND source_id IS NOT NULL "
                         f"AND (kind IS NULL OR kind NOT IN ({marks})) AND created_at>=? GROUP BY source_id, status",
                         (*SAFETY, t0)):
        e = ev.setdefault(r["source_id"], {"rejected": 0, "confirmed": 0, "unreviewed": 0})
        e[{"rad_etilgan": "rejected", "tasdiqlangan": "confirmed"}.get(r["status"], "unreviewed")] += r["n"]
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM sources")}
    live_ids = {r[0] for r in con.execute("SELECT id FROM events")}
    con.close()
    te.add_archived(ev, p.parent / "feedback", t0, live_ids, {v: k for k, v in names.items()}, SAFETY)
    cams = [{"source_id": sid, "name": names.get(sid) or (mins.get(sid) or {}).get("name") or f"#{sid}",
             "minutes": (mins.get(sid) or {}).get("minutes", 0),
             **ev.get(sid, {"rejected": 0, "confirmed": 0, "unreviewed": 0})} for sid in sorted(set(mins) | set(ev))]
    g = te.gate(cams)
    print("O'g'irlik signallari darvozasi (7-bosqich)")
    print(f"  Mezon: xato ≤ {te.FP_MAX_DAY:g} / kamera / kun, har kamera ≥ {te.MIN_DAYS} kun, javobsiz ≤ "
          f"{te.MAX_UNREVIEWED * 100:.0f}%; {time.strftime('%d.%m.%Y', time.localtime(t0))} dan")
    for c in g["cams"]:
        rate = "—" if c["rate"] is None else f"{c['rate']:.2f}"
        mark = "o'tmadi" if c["failed"] else (("o'tdi" if c["ok"] else "o'tmadi") if c["enough"]
                                              else f"kam (≥ {te.MIN_DAYS} kun kerak)")
        print(f"   - {c['name']}: {c['days']} kun, ❌ {c['rejected']}, ✅ {c['confirmed']}, javobsiz {c['unreviewed']} "
              f"({c['unreviewed_share'] * 100:.0f}%), kuniga {rate} — {mark}")
    if not g["cams"]:
        print("   (jonli kamera ma'lumoti yo'q)")
    st = g["state"]
    print(f"\nNATIJA: {'OTDI' if st == 'otdi' else ('OTMADI' if st == 'otmadi' else 'MALUMOT YETARLI EMAS')}")
    return 0 if st == "otdi" else (1 if st == "otmadi" else 2)


if __name__ == "__main__":
    sys.exit(main())
