"""Signal tezligi testi (0-bosqich darvozasi): 100 ta hodisa — Telegram rasm, klip va web panel (SSE).

Haqiqiy server (uvicorn) va soxta Telegram API ishga tushiriladi. Tarmoq taqlid qilinadi:
har so'rovga 150 ms kechikish va 1.5 MB/s yuklash tezligi. Ikkita obunachi.

Ishga tushirish (loyiha papkasidan, ALOHIDA nusxada — bazaga sinov ma'lumoti yoziladi):
    python -m tests.test_latency [hodisalar_soni]

Qabul mezonlari: birinchi xabar (rasm) p95 ≤ 5 s, klip p95 ≤ 30 s, web panel p95 ≤ 1 s, xatosiz.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["TELEGRAM_API_BASE"] = "http://127.0.0.1:8766"
os.environ.setdefault("MOCK_TG_DELAY", "0.15")
os.environ.setdefault("MOCK_TG_BW", "1500000")

from tests import mock_telegram as tg  # noqa: E402

tg.serve(8766)

import cv2  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402
import uvicorn  # noqa: E402

from app import db  # noqa: E402
from app.ai.clipper import BufFrame  # noqa: E402
from app.ai.manager import get_manager  # noqa: E402
from app.ai.worker import alert_banner, make_pending  # noqa: E402

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


def pct(vals, p):
    if not vals:
        return float("nan")
    v = sorted(vals)
    return v[max(0, min(len(v) - 1, int(round(p / 100 * (len(v) - 1)))))]


def sample_frames(n=240):
    cap = cv2.VideoCapture("samples/dokon_namuna_20s.mp4")
    out = []
    while len(out) < n:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
        for _ in range(4):
            cap.read()
    return out


def main(n_events: int = 100) -> int:
    from app.web import app

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)

    results = []

    def ok(name, cond, info=""):
        results.append((name, bool(cond)))
        print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)

    c = httpx.Client(base_url=BASE, timeout=30)
    c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"})
    # Telegram obunachilari
    t0 = time.time()
    while not tg.calls("getMe") and time.time() - t0 < 20:
        time.sleep(0.2)
    code = db.get_setting("connect_code")
    for chat in (111, 222):
        tg.text_message(chat, f"/start {code}")
    t0 = time.time()
    while (db.scalar("SELECT COUNT(*) FROM subscribers") or 0) < 2 and time.time() - t0 < 20:
        time.sleep(0.2)
    ok("2 ta obunachi ulandi", (db.scalar("SELECT COUNT(*) FROM subscribers") or 0) == 2)
    db.set_settings({"telegram_enabled": "1", "telegram_send_video": "1", "telegram_min_score": "0",
                     "pre_sec": 10, "post_sec": 10})

    # Web panel jonli oqimi (SSE)
    sse_recv: dict[int, float] = {}
    stop = threading.Event()

    def sse_reader():
        with httpx.Client(base_url=BASE, cookies=c.cookies, timeout=None) as sc:
            with sc.stream("GET", "/api/sse") as r:
                for line in r.iter_lines():
                    if stop.is_set():
                        break
                    if line.startswith("data: "):
                        d = json.loads(line[6:])
                        if d["kind"] == "new":
                            sse_recv.setdefault(int(d["event"]["id"]), time.time())
    threading.Thread(target=sse_reader, daemon=True).start()
    time.sleep(1.0)

    frames = sample_frames()
    h, w = frames[0].shape[:2]
    jpegs = [cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tobytes() for f in frames]
    mgr = get_manager()
    box = [w * 0.1, h * 0.1, w * 0.3, h * 0.9]
    persons = [(1, np.array(box, dtype=np.float32), np.zeros((17, 3), dtype=np.float32))]
    src = {"id": None, "name": "Sinov kamerasi", "kind": "rtsp"}
    ev_ids = []
    print(f"  {n_events} ta hodisa yaratilmoqda (har 1 s da bittasi)...", flush=True)
    for i in range(n_events):
        t = time.time()
        alert = {"track_id": 1, "t": t, "score": 72, "level": "o'rta", "kind": "pocket",
                 "kind_label": "Cho'ntakka solish", "side": "R", "reasons": ["Sinov hodisasi"],
                 "breakdown": [["Sinov", 72]], "ml_prob": None, "box": box, "susp_start": t - 1.2, "susp_end": t,
                 "pose_seq": []}
        snap = {"frame": frames[i % len(frames)], "ann": (persons, [], {}), "banner": alert_banner(alert, True),
                "time_text": "sinov"}
        ev_id = mgr.create_event(alert, source=src, job_id=None, video_time=None, snapshot=snap)
        ev_ids.append(ev_id)
        if i % 10 == 0:
            # Klip: haqiqiy holatdagidek 10 s kutib, keyin yoziladi va yuboriladi
            p = make_pending(ev_id, alert, t, 10, 10, live=True)
            bufs = [BufFrame(p.t_start + k / 12.0, p.t_start + k / 12.0, jpegs[k % len(jpegs)], 1.0, None)
                    for k in range(int((p.t_end - p.t_start) * 12))]
            threading.Timer(10.0, mgr.finish_clip, args=(p, bufs)).start()
        time.sleep(1.0)

    # Hammasi yetib kelishini kutish
    deadline = time.time() + 90
    n_clips = len(range(0, n_events, 10))
    while time.time() < deadline:
        a_n = db.scalar("SELECT COUNT(*) FROM notify_log WHERE stage='alert'") or 0
        c_n = db.scalar("SELECT COUNT(*) FROM notify_log WHERE stage='clip'") or 0
        if a_n >= 2 * n_events and c_n >= 2 * n_clips:
            break
        time.sleep(1)
    stop.set()

    rows = db.rows("SELECT * FROM notify_log")
    alert = [r for r in rows if r["stage"] == "alert"]
    clip = [r for r in rows if r["stage"] == "clip"]
    a_lat = [r["t_sent"] - r["t_event"] for r in alert if r["ok"]]
    c_lat = [r["t_sent"] - r["t_event"] for r in clip if r["ok"]]
    t_det = {e["id"]: e["t_detect"] for e in db.rows("SELECT id, t_detect FROM events")}
    s_lat = [sse_recv[i] - t_det[i] for i in ev_ids if i in sse_recv]
    errors = [r for r in rows if not r["ok"]]

    print()
    print(f"  Telegram rasm:  {len(a_lat)}/{2 * n_events} yetdi, median {pct(a_lat, 50):.2f} s, "
          f"p95 {pct(a_lat, 95):.2f} s, max {max(a_lat or [0]):.2f} s")
    print(f"  Telegram klip:  {len(c_lat)}/{2 * n_clips} yetdi, median {pct(c_lat, 50):.2f} s, "
          f"p95 {pct(c_lat, 95):.2f} s (10 s kutish + yozish + yuklash)")
    print(f"  Web panel (SSE): {len(s_lat)}/{n_events} yetdi, median {pct(s_lat, 50) * 1000:.0f} ms, "
          f"p95 {pct(s_lat, 95) * 1000:.0f} ms")
    ok("Rasm hammaga yetdi", len(a_lat) == 2 * n_events, f"{len(a_lat)}/{2 * n_events}")
    ok("Birinchi xabar p95 ≤ 5 s", pct(a_lat, 95) <= 5.0, f"{pct(a_lat, 95):.2f} s")
    ok("Klip hammaga yetdi", len(c_lat) == 2 * n_clips, f"{len(c_lat)}/{2 * n_clips}")
    ok("Klip p95 ≤ 30 s", pct(c_lat, 95) <= 30.0, f"{pct(c_lat, 95):.2f} s")
    ok("Web panel p95 ≤ 1 s", len(s_lat) == n_events and pct(s_lat, 95) <= 1.0, f"{pct(s_lat, 95) * 1000:.0f} ms")
    ok("Yuborishda xato yo'q", not errors, f"{len(errors)} ta xato")
    api = c.get("/api/latency").json()
    ok("Panel API ko'rsatkichlari", api["stats"].get("telegram/alert", {}).get("ok", 0) == len(a_lat))
    srv.should_exit = True
    bad = [r for r in results if not r[1]]
    print(f"\nNatija: {len(results) - len(bad)}/{len(results)} sinov o'tdi")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100))
