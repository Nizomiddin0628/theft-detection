"""9-bosqich darvozasi: "pilot do'konda (1–3 do'kon, 4–6 hafta)" va barcha bosqichlar darvozalari jadvali.

Avval panelda: **Pilot** → tayyorlik ro'yxatidagi ❌ larni tuzating → **Pilotni boshlash**; har dushanba Telegram'dagi
"1–5" so'roviga javob bering. Kamida 28 kundan keyin:

    python tests/pilot_gate.py                   # data/hushyor.db (HUSHYOR_DATA_DIR bo'lsa — o'sha papkadagi)
    python tests/pilot_gate.py --db boshqa.db

Bazani faqat o'qiydi (jonli dasturga xalaqit bermaydi). Mezonlar: app/pilot.py (panel ham aynan shularni ishlatadi).
Chiqish kodi: 0 — o'tdi, 1 — o'tmadi, 2 — ma'lumot yetarli emas yoki pilot boshlanmagan.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Pilot darvozasi (bazani faqat o'qiydi)")
    data_dir = Path(os.environ.get("HUSHYOR_DATA_DIR") or ROOT / "data")
    ap.add_argument("--db", default=str(data_dir / "hushyor.db"))
    a = ap.parse_args()
    p = Path(a.db).resolve()
    if not p.exists():
        print(f"Baza topilmadi: {p}")
        return 2
    os.environ["HUSHYOR_DATA_DIR"] = str(p.parent)        # jurnal va fikr-mulohaza papkalari — o'sha bazaniki
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
    sys.path.insert(0, str(ROOT))
    from app import db  # noqa: E402

    def ro() -> sqlite3.Connection:                      # faqat o'qish (PRAGMA yozuvisiz)
        c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    db._connect = ro
    from app import pilot  # noqa: E402

    try:
        g = pilot.pilot_gate()
        table = pilot.gates()
    except sqlite3.OperationalError as e:
        print(f"Bazani o'qib bo'lmadi ({e}). Dastur 2.5.0 da kamida bir marta ishga tushganmi?")
        return 2
    print("Darvozalar jadvali")
    for x in table:
        print(f"  {x['stage']}. {x['name']:<22} {x['state']:<14} {x['detail']}")
    print("\nPilot darvozasi (9-bosqich)")
    if g.get("since"):
        print(f"  Boshlangan: {time.strftime('%d.%m.%Y', time.localtime(g['since']))}"
              + (f", yakunlangan: {time.strftime('%d.%m.%Y', time.localtime(g['until']))}" if g.get("until") else ""))
    for c in g["checks"]:
        print(f"  [{'+' if c['ok'] else ' '}] {c['title']:<38} {c['text']:<18} kerak: {c['need']}")
    st = g["state"]
    print(f"\nNATIJA: {'OTDI' if st == 'otdi' else ('OTMADI' if st == 'otmadi' else st.upper())}")
    return 0 if st == "otdi" else (1 if st == "otmadi" else 2)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
