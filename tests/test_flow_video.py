"""2.1.0 oqim — haqiqiy videoda (sintetik emas): to'liq quvur detektor (YOLO11n) → kuzatuvchi → LineCounter → oqim.

samples/sanash_namuna.mp4 — Intel "sample-videos" people-detection.mp4 (CC BY 4.0, samples/MANBA.txt): xonaga (keng
burchakli kamera, yuqoridan) 7 kishi kirib, to'xtamay o'tib ketadi. Xona o'lchami noma'lum — kalibrlash taxminiy (orqa
devor 8 m, pol-devor chizig'i bo'yicha), shuning uchun bu ANIQLIK sinovi emas, quyidagilarni tekshiradi:
  * quvur haqiqiy detektor va kuzatuvchi bilan ishlaydi, oqim kamera tahlilini sekinlashtirmaydi;
  * joylar reja ichida va silliq (sakrashlar yo'q — haqiqiy yurish tezligi);
  * to'xtamay o'tib ketganlar "tashrif" emas, lekin "o'tganlar" heatmap'ida bor;
  * video tahlili jonli bazaga yozmaydi.

    python -m tests.test_flow_video
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_fvideo_")   # hech qachon jonli data/ emas
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app import db, evaluation  # noqa: E402
from app.ai import counting, flow  # noqa: E402

VIDEO = ROOT / "samples" / "sanash_namuna.mp4"
W, H = 768, 432
MPP = 0.02                      # reja: 1 piksel = 2 sm
# Kadrda pol va devor uchrashgan nuqtalar (piksel) → rejada (m): orqa devor y = 0, chap devor x = 0, o'ng x = 8
PAIRS = [((90, 212), (0.0, 0.0)), ((435, 211), (4.2, 0.0)), ((745, 201), (8.0, 0.0)), ((250, 211), (1.95, 0.0)),
         ((22, 318), (0.0, 2.4)), ((702, 310), (8.0, 2.4))]
results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def setup() -> int:
    db.init_db()
    db.set_settings({"ai_fps": "8", "calm_fps": "4"})
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Zal', 'file', ?, 0, ?)",
                     (str(VIDEO), time.time()))
    pid = db.execute("INSERT INTO floor_plans(name, img, w_px, h_px, m_per_px, blank, created_at, updated_at) "
                     "VALUES('Xona', '', 450, 400, ?, 1, ?, ?)", (MPP, time.time(), time.time()))
    pts = [[0, 0], [400, 0], [400, 350], [0, 350]]          # butun pol (8 × 7 m) — bitta bo'lim
    db.execute("INSERT INTO plan_sections(plan_id, name, color, points, enabled, created_at) VALUES(?,?,?,?,1,?)",
               (pid, "Zal", "#2563eb", json.dumps(pts), time.time()))
    pairs = [{"img": [u / W, v / H], "plan": [x / MPP, y / MPP], "check": 0} for (u, v), (x, y) in PAIRS]
    db.execute("INSERT INTO cam_calib(source_id, plan_id, pairs, model, stats, frame_w, frame_h, enabled, updated_at) "
               "VALUES(?,?,?,NULL,NULL,?,?,1,?)", (sid, pid, json.dumps(pairs), W, H, time.time()))
    flow.bump()
    return sid


def main() -> int:
    if not VIDEO.exists():
        print(f"Video yo'q: {VIDEO}")
        return 1
    if not counting.hook_ok():
        print("Sanash worker.py ga ulanmagan: python scripts/apply_counting_hook.py")
        return 1
    sid = setup()
    lay = flow.layout(force=True)
    ok("Taxminiy kalibrlash qabul qilindi (6 nuqta)", sid in lay.cams, str(list(lay.cams)))
    print("--- Haqiqiy video: 7 kishi xonadan to'xtamay o'tadi ---")
    t0 = time.time()
    r = evaluation.analyze(str(VIDEO), zone_source_id=sid)
    sec = time.time() - t0
    f = r.get("flow")
    ok("Oqim natijasi bor (LineCounter → FlowMonitor)", f is not None)
    if not f:
        return 1
    tr = f["trace"]
    ids = {p["tid"] for p in tr}
    ok("Odamlar rejaga tushdi", len(tr) > 50 and len(ids) >= 5, f"{len(tr)} nuqta, {len(ids)} iz")
    xs = np.array([p["x"] for p in tr])
    ys = np.array([p["y"] for p in tr])
    inside = float(np.mean((xs >= -0.5) & (xs <= 8.5) & (ys >= -0.5) & (ys <= 7.5)))
    ok("Joylar xona ichida (≥ 95%)", inside >= 0.95, f"{inside:.1%}")
    speeds = []
    by: dict = {}
    for p in tr:
        by.setdefault(p["tid"], []).append(p)
    for arr in by.values():
        for a, b in zip(arr, arr[1:]):
            dt = b["t"] - a["t"]
            if 0.05 < dt <= 0.6:
                speeds.append(np.hypot(b["x"] - a["x"], b["y"] - a["y"]) / dt)
    sp = np.array(speeds)
    p50, p95 = (float(np.percentile(sp, 50)), float(np.percentile(sp, 95))) if len(sp) else (0, 0)
    ok("Harakat silliq: tezlik mediana 0,3–2,5 m/s, 95% < 4 m/s (sakrash yo'q)", 0.3 <= p50 <= 2.5 and p95 < 4.0,
       f"mediana {p50:.2f}, 95% {p95:.2f} m/s, {len(sp)} qadam")
    ok("To'xtamay o'tganlar — tashrif emas (≤ 2 ta 7 kishidan)", len(f["visits"]) <= 2, f"{len(f['visits'])} tashrif")
    ok("'O'tganlar' heatmap'i to'ldi", f["pas"] > 30 and f["occ"] > 5, f"pas {f['pas']}, occ {f['occ']} odam·s")
    ok("Oqim har AI qadamida < 2 ms (kamera tahlilini sekinlashtirmaydi)", f["update_ms"] < 2.0, f"{f['update_ms']} ms")
    ok("Video tahlili jonli bazaga yozmaydi", (db.scalar("SELECT COUNT(*) FROM section_visits") or 0) == 0 and
       (db.scalar("SELECT COUNT(*) FROM flow_hour") or 0) == 0)
    print(f"  ({r['duration']} s video {sec:.0f} s da tahlil qilindi)")
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
