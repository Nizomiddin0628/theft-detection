"""2.0.0 kassa navbati — haqiqiy videoda (sintetik emas): to'liq quvur (YOLO11n → kuzatuvchi → kamera rejimlari →
LineCounter.update → QueueMonitor), xuddi jonli kameradagidek.

    python -m tests.test_queue_video

samples/navbat_namuna.mp4 — "xizmat joyi": 6 kishi birin-ketin stol oldiga keladi, 10–26 s turib ketadi. Har biri
qachon kelib-ketgani kadrlar bo'yicha qo'lda belgilangan (±0.5 s): kutish vaqti xatosi va uzunlik (0/1) tekshiriladi.
samples/navbat_guruh.mp4 — kameraga 1, 2, 3 kishilik guruhlar kelib yonma-yon turadi: uzunlik ±1 va aniq.

Eslatma: bu ochiq videolar — do'koningizdagi aniqlik faqat Navbat sahifasidagi darvoza bilan (tests/queue_gate.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_qvideo_")   # hech qachon jonli data/ emas
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, evaluation, queue_eval as qe  # noqa: E402
from app.ai import counting, zones as zones_mod  # noqa: E402

results: list[bool] = []

ONE = ROOT / "samples" / "navbat_namuna.mp4"
ONE_ZONE = [[0.40, 0.70], [0.78, 0.70], [0.84, 1.0], [0.34, 1.0]]
# (keldi, ketdi) — oyog'i zonaga kirgan va chiqqan payt (kadrlar bo'yicha, ±0.5 s)
ONE_WAITS = [(7.0, 19.3), (23.75, 44.3), (51.5, 69.3), (75.75, 85.9), (93.75, 119.3), (125.0, 135.3)]
GROUP = ROOT / "samples" / "navbat_guruh.mp4"
GROUP_ZONE = [[0.0, 0.75], [1.0, 0.75], [1.0, 1.0], [0.0, 1.0]]
# (boshi, oxiri, kadrdagi odamlar) — barqaror oraliqlar (o'tish paytlari tashqarida)
GROUP_LEN = [(0.0, 3.0, 0), (8.0, 18.0, 1), (21.0, 27.0, 0), (32.0, 42.0, 2), (45.0, 54.0, 0), (59.0, 66.0, 3),
             (70.0, 74.0, 0), (79.0, 85.0, 1), (88.0, 90.0, 0)]


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def run(video: Path, zone: list) -> dict:
    db.set_settings({"ai_fps": "8", "calm_fps": "4"})
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Kassa', 'file', ?, 0, ?)",
                     (str(video), time.time()))
    db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
               (sid, "1-kassa", "navbat", json.dumps(zone), "", 1, time.time()))
    zones_mod.invalidate(sid)
    t0 = time.time()
    r = evaluation.analyze(str(video), zone_source_id=sid)
    r["sec"] = round(time.time() - t0, 1)
    return r


def at(trace: list, t: float):
    best = None
    for x in trace:
        if x["t"] <= t:
            best = x
        else:
            break
    return best["length"] if best else None


def main() -> int:
    for v in (ONE, GROUP):
        if not v.exists():
            print(f"Video yo'q: {v}")
            return 1
    db.init_db()
    if not counting.hook_ok():
        print("Sanash/navbat worker.py ga ulanmagan: python scripts/apply_counting_hook.py")
        return 1

    print("--- Haqiqiy video 1: xizmat joyi, 6 kishi birin-ketin ---")
    r = run(ONE, ONE_ZONE)
    q = r["queue"]
    print(f"       {r['duration']} s video, tahlil {r['sec']} s; avtomatik kutishlar: "
          + ", ".join(f"{w['t_in']:.1f}–{w['t_out']:.1f} ({w['wait']:.1f} s, ID {w['ids']})" for w in q["waits"]))
    tims = [{"id": i, "zone_id": q["waits"][0]["zone_id"] if q["waits"] else 0, "t0": a, "t1": b}
            for i, (a, b) in enumerate(ONE_WAITS)]
    m = qe.match_timings(tims, q["waits"], now=1e12)
    g = qe.timings_gate(m)
    for x in m:
        err = "—" if x["err"] is None else f"{x['err'] * 100:.1f}%"
        auto = "—" if x["auto"] is None else f"{x['auto']:.1f} s"
        print(f"       qo'lda {x['manual']:5.1f} s  avto {auto}  xato {err}")
    ok("6 kishining hammasi topildi va juftlandi (ortiqcha yozuv yo'q)", g["matched"] == 6 and len(q["waits"]) == 6,
       f"juftlangan {g['matched']}, avto {len(q['waits'])}")
    ok("Kutish vaqti o'rtacha xatosi ≤ 20% (darvoza mezoni)", g["mean_err"] is not None and g["mean_err"] <= 0.20,
       f"o'rtacha {g['mean_err']}, mediana {g['median_err']}")
    stable = []
    for a, b in ONE_WAITS:
        stable.append((a + 1.5, b - 1.5, 1))
    gaps = [(0.0, ONE_WAITS[0][0] - 1.5)] + [(ONE_WAITS[i][1] + 1.5, ONE_WAITS[i + 1][0] - 1.5)
                                            for i in range(len(ONE_WAITS) - 1)]
    stable += [(a, b, 0) for a, b in gaps if b > a]
    pairs = [(n, at(q["trace"], x["t"])) for a, b, n in stable for x in q["trace"] if a <= x["t"] <= b]
    exact = sum(1 for n, v in pairs if v == n)
    ok("Uzunlik (0/1) barqaror paytlarda aniq ≥ 95%", pairs and exact / len(pairs) >= 0.95,
       f"{exact}/{len(pairs)} = {exact / max(len(pairs), 1):.3f}")

    print("\n--- Haqiqiy video 2: 1, 2, 3 kishilik guruhlar ---")
    r = run(GROUP, GROUP_ZONE)
    q = r["queue"]
    print(f"       {r['duration']} s video, tahlil {r['sec']} s")
    pairs = []
    for a, b, n in GROUP_LEN:
        vals = [x["length"] for x in q["trace"] if a <= x["t"] <= b]
        pairs += [(n, v) for v in vals]
        print(f"       {a:4.0f}–{b:4.0f} s: haqiqat {n}, tizim {sorted(set(vals))}")
    good = sum(1 for n, v in pairs if abs(n - v) <= 1)
    exact = sum(1 for n, v in pairs if n == v)
    ok("Uzunlik ±1 ichida ≥ 90% (darvoza mezoni)", pairs and good / len(pairs) >= 0.90, f"{good}/{len(pairs)}")
    ok("Uzunlik aniq ≥ 85%", pairs and exact / len(pairs) >= 0.85, f"{exact}/{len(pairs)} = {exact / max(len(pairs), 1):.3f}")
    peak = max((x["length"] for x in q["trace"] if 59 <= x["t"] <= 66), default=0)
    ok("3 kishilik guruh — 3 deb ko'rindi", peak == 3, f"eng ko'pi {peak}")
    ok("Video tahlili bazaga yozmaydi (kutishlar, daqiqalar, signallar)",
       db.scalar("SELECT COUNT(*) FROM queue_waits") == 0 and db.scalar("SELECT COUNT(*) FROM queue_alerts") == 0)
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
