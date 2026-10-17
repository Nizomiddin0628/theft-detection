"""6-bosqich darvozasi: "yiqilish recall ≥ 85%, xato signal ≤ 1 / kamera / hafta".

Avval panelda: **Xavfsizlik** → **Aniqlik sinovi**:
  * do'konda gilam ustida sahnalashtirilgan yiqilish videolarini yuklang (har biriga yiqilish soniyalari), kamida 20 ta
    yiqilish, keyin "Tahlil qilish";
  * jonli kameralarda kamida 7 kun ishlang va har "Yiqilish" / "Odam yotibdi" signaliga ✅ yoki ❌ javob bering.
Keyin:

    python tests/safety_gate.py                  # data/hushyor.db (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/safety_gate.py --db boshqa.db

Mezonlar: app/safety_eval.py (panel ham aynan shularni ishlatadi). Chiqish kodi: 0 — o'tdi, 1 — o'tmadi,
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

from app import safety_eval as se  # noqa: E402  (toza modul: bazaga ham, sozlamaga ham tegmaydi)


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def main() -> int:
    for st in (sys.stdout, sys.stderr):          # Windows konsoli "≥" ni chiqara olmasa — yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Yiqilish darvozasi (bazani faqat o'qiydi)")
    ap.add_argument("--db", default=str(ROOT / "data" / "hushyor.db"))
    a = ap.parse_args()
    p = Path(a.db)
    if not p.exists():
        print(f"Baza topilmadi: {p}")
        return 2
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        vids = [dict(r) for r in con.execute("SELECT * FROM safety_videos ORDER BY id")]
    except sqlite3.OperationalError:
        vids = []                                # 2.2.0 dan oldingi baza
    videos = [{"id": v["id"], "filename": v["filename"], "falls": json.loads(v.get("falls") or "[]"),
               "negative": v.get("negative"), "result": json.loads(v["result"]) if v.get("result") else None}
              for v in vids]
    sets = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    t0 = se.live_window(sets.get("safety_since"), sets.get("retention_days", "30"), time.time())
    mins = {r["source_id"]: dict(r) for r in con.execute(
        "SELECT source_id, MAX(source_name) AS name, COUNT(*) AS minutes FROM metrics_minute WHERE source_id>0 "
        "AND up_pct>=0.5 AND minute>=? GROUP BY source_id", (t0,))}
    marks = ",".join("?" for _ in se.LIVE_KINDS)
    ev: dict = {}
    for r in con.execute(f"SELECT source_id, status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND source_id IS NOT NULL "
                         f"AND kind IN ({marks}) AND created_at>=? GROUP BY source_id, status", (*se.LIVE_KINDS, t0)):
        e = ev.setdefault(r["source_id"], {"rejected": 0, "confirmed": 0, "unreviewed": 0})
        e[{"rad_etilgan": "rejected", "tasdiqlangan": "confirmed"}.get(r["status"], "unreviewed")] += r["n"]
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM sources")}
    con.close()
    cams = [{"source_id": sid, "name": names.get(sid) or (mins.get(sid) or {}).get("name") or f"#{sid}",
             "minutes": (mins.get(sid) or {}).get("minutes", 0),
             **ev.get(sid, {"rejected": 0, "confirmed": 0, "unreviewed": 0})} for sid in sorted(set(mins) | set(ev))]
    g = se.gate(videos, cams)
    r, f = g["recall"], g["fp"]
    print("Yiqilish darvozasi (6-bosqich)")
    print(f"  Mezon: recall ≥ {se.RECALL_MIN * 100:.0f}% (≥ {se.MIN_FALLS} ta yiqilish), xato ≤ {se.FP_MAX_WEEK:g} / kamera / "
          f"hafta (har kamera ≥ {se.MIN_DAYS} kun)")
    print(f"\n1) Belgilangan videolar: {len(videos)} ta, yiqilish {r['falls']} ta, topildi {r['tp']}, recall {pct(r['recall'])}"
          + (f", tahlil qilinmagan {r['pending']}" if r["pending"] else "") + f", videolardagi xato signal {r['fp']}")
    for v in r["videos"]:
        if v["analyzed"]:
            miss = f", topilmadi: {v['missed']}" if v.get("missed") else ""
            print(f"   - {v['filename']}: {v['tp']}/{v['falls']}, xato {v['fp']}{miss}")
        else:
            print(f"   - {v['filename']}: tahlil qilinmagan")
    print(f"\n2) Jonli kameralar ({len(f['cams'])} ta), {time.strftime('%d.%m.%Y %H:%M', time.localtime(t0))} dan:")
    for c in f["cams"]:
        rate = "—" if c["rate"] is None else f"{c['rate']:.2f}"
        mark = ("o'tdi" if c["ok"] else "o'tmadi") if c["enough"] else f"kam (≥ {se.MIN_DAYS} kun kerak)"
        print(f"   - {c['name']}: {c['days']} kun, ❌ {c['rejected']}, ✅ {c['confirmed']}, javobsiz {c['unreviewed']}, "
              f"haftasiga {rate} — {mark}")
    state = g["state"]
    print(f"\nNATIJA: {'OTDI' if state == 'otdi' else ('OTMADI' if state == 'otmadi' else 'MALUMOT YETARLI EMAS')}")
    return 0 if state == "otdi" else (1 if state == "otmadi" else 2)


if __name__ == "__main__":
    sys.exit(main())
