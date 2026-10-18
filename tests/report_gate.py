"""8-bosqich darvozasi: "hisobot 2 hafta har kuni o'z vaqtida, raqamlari to'g'ri, ≥ 3 ta tavsiya foydali".

Avval: Telegram bot ulangan, Sozlamalar/Hisobotlar sahifasida kunlik hisobot yoqilgan; 14 kun ishlating va har
hisobotdagi tavsiyalarga 👍 / 👎 bosing. Keyin:

    python tests/report_gate.py                  # data/hushyor.db (HUSHYOR_DATA_DIR bo'lsa — o'sha papkadagi)
    python tests/report_gate.py --db boshqa.db

Mezonlar: app/report_eval.py (panel ham aynan shularni ishlatadi). Bazani faqat o'qiydi. Chiqish kodi: 0 — o'tdi,
1 — o'tmadi, 2 — ma'lumot yetarli emas.
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

from app import report_eval as re_  # noqa: E402  (toza modul)


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Hisobot darvozasi (bazani faqat o'qiydi)")
    data_dir = Path(os.environ.get("HUSHYOR_DATA_DIR") or ROOT / "data")
    ap.add_argument("--db", default=str(data_dir / "hushyor.db"))
    a = ap.parse_args()
    p = Path(a.db)
    if not p.exists():
        print(f"Baza topilmadi: {p}")
        return 2
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row

    def q(sql, params=()):
        try:
            return [dict(r) for r in con.execute(sql, tuple(params))]
        except sqlite3.OperationalError:          # 2.4.0 dan oldingi baza — jadval yo'q
            return []
    sets = {r["key"]: r["value"] for r in q("SELECT key, value FROM settings")}
    g = re_.gate(q, float(sets.get("report_since", 0) or 0), time.time())
    con.close()
    print("Hisobot darvozasi (8-bosqich)")
    print(f"  Mezon: {re_.DAYS} kun, har kuni o'z vaqtida (≤ {re_.ON_TIME_SEC // 60} daq kechikish; ko'pi bilan "
          f"{re_.MAX_MISS} kun o'tkazib), raqamlar bazaga mos, ≥ {re_.MIN_USEFUL} xil foydali tavsiya")
    for d in g["days"]:
        late = "" if d["late_sec"] is None else f" (+{d['late_sec'] // 60} daq)"
        print(f"   - {time.strftime('%d.%m.%Y', time.localtime(d['day']))}: {d['state']}{late}")
    if not g["days"]:
        print("   (hali tugagan kun yo'q)")
    print(f"  O'z vaqtida: {g['on_time']}/{len(g['days'])}, o'tkazilgan: {g['missed']}; raqam farqi: {len(g['mismatch'])}; "
          f"foydali tavsiyalar: {g['useful']} (foydasiz: {g['useless']})")
    st = g["state"]
    print(f"\nNATIJA: {'OTDI' if st == 'otdi' else ('OTMADI' if st == 'otmadi' else 'MALUMOT YETARLI EMAS')}")
    return 0 if st == "otdi" else (1 if st == "otmadi" else 2)


if __name__ == "__main__":
    sys.exit(main())
