"""1.9.0 odam sanash — haqiqiy videoda (sintetik emas).

samples/sanash_namuna.mp4 — Intel "sample-videos" people-detection.mp4 (CC BY 4.0, samples/MANBA.txt). Videoda 7 kishi
kadr pastidagi gorizontal chiziqni (y = 0.72) pastdan yuqoriga — kameradan uzoqlashib — kesib o'tadi (qo'lda sanalgan).
To'liq quvur ishlaydi: detektor (YOLO11n) → kuzatuvchi → kamera rejimlari → LineCounter, xuddi jonli kameradagidek.

    python -m tests.test_count_video

Eslatma: bu bitta ochiq video; do'koningiz eshigidagi aniqlik — faqat qo'lda sanash sinovi bilan (tests/count_gate.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_cvideo_")   # hech qachon jonli data/ emas
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, evaluation  # noqa: E402
from app.ai import counting, zones as zones_mod  # noqa: E402

VIDEO = ROOT / "samples" / "sanash_namuna.mp4"
TRUTH = 7
results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def run(line: list, **settings) -> dict:
    db.set_settings({"ai_fps": "8", "calm_fps": "4", **{k: str(v) for k, v in settings.items()}})
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Eshik', 'file', ?, 0, ?)",
                     (str(VIDEO), time.time()))
    db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
               (sid, "Eshik", counting.LINE_KIND, json.dumps(line), "", 1, time.time()))
    zones_mod.invalidate(sid)
    t0 = time.time()
    r = evaluation.analyze(str(VIDEO), zone_source_id=sid)
    c = r["crossings"]
    return {"in": sum(1 for x in c if x["dir"] == "in"), "out": sum(1 for x in c if x["dir"] == "out"),
            "staff": sum(1 for x in c if x["staff"]), "sec": round(time.time() - t0, 1), "dur": r["duration"],
            "n_db": db.scalar("SELECT COUNT(*) FROM counts")}


def main() -> int:
    if not VIDEO.exists():
        print(f"Video yo'q: {VIDEO}")
        return 1
    db.init_db()
    if not counting.hook_ok():
        print("Sanash worker.py ga ulanmagan: python scripts/apply_counting_hook.py")
        return 1
    print("--- Haqiqiy video: 7 kishi chiziqni kesib o'tadi ---")
    a = run([[0.0, 0.72], [1.0, 0.72]])
    ok(f"Chiziq chapdan o'ngga (ichkari — pastda): 7 chiqdi, 0 kirdi", a["out"] == TRUTH and a["in"] == 0,
       f"{a} — {a['dur']} s video {a['sec']} s da")
    b = run([[1.0, 0.72], [0.0, 0.72]])
    ok("Chiziq teskari (strelka yuqoriga): 7 kirdi, 0 chiqdi", b["in"] == TRUTH and b["out"] == 0, str(b))
    c = run([[0.0, 0.72], [1.0, 0.72]], ai_fps=4, calm_fps=2)
    ok("Kuchsiz kompyuter rejimi (AI 4 kadr/s): baribir 7", c["out"] == TRUTH and c["in"] == 0, str(c))
    ok("Video tahlili jonli bazaga yozmaydi (persist=False)", a["n_db"] == 0 and c["n_db"] == 0, str(c["n_db"]))
    ok("Xodim deb belgilangan yo'q (xodim zonasi yo'q)", a["staff"] == 0 and b["staff"] == 0)
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
