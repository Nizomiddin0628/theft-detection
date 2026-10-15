"""Uzoq sinov (tests/soak.py) uchun edge: `run.py` bilan aynan bir xil dastur, faqat kamera oqimiga har
SOAK_EVENT_SEC soniyada bitta soxta "shubhali harakat" signali qo'shiladi.

Signal haqiqiy yo'ldan o'tadi: kamera ishchisi → hodisa yozuvi → rasm → klip (halqa buferdan) → Telegram →
bulut navbati. Shunday qilib 72 soat davomida yuzlab hodisa va klip yetkazilishi tekshiriladi, do'konda
o'g'irlik bo'lishini kutmasdan. Jonli dasturda bu fayl ishlatilmaydi.

    python tests/soak_edge.py --host 127.0.0.1 --port 8010   (odatda soak.py o'zi ishga tushiradi)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not os.environ.get("HUSHYOR_DATA_DIR"):
    # Xavfsizlik: sinov hech qachon jonli data/ papkasiga yozmasin
    sys.exit("HUSHYOR_DATA_DIR berilmagan — soak_edge.py faqat tests/soak.py orqali ishga tushadi")

EVERY = float(os.environ.get("SOAK_EVENT_SEC", "120") or 0)


def _patch() -> None:
    from app.ai import worker as W

    orig = W.Processor.step
    last: dict = {}
    lock = threading.Lock()
    t0 = time.time()

    def step(self, frame, t):
        res = orig(self, frame, t)
        if EVERY <= 0 or frame is None:
            return res
        key = getattr(self, "source_id", None) or id(self)
        now = time.time()
        with lock:
            if now - last.get(key, t0) < EVERY:
                return res
            last[key] = now
        h, w = frame.shape[:2]
        box = [w * 0.35, h * 0.2, w * 0.6, h * 0.95]
        alert = {"track_id": 9000 + int(now) % 1000, "t": t, "score": 82.0, "level": "yuqori", "kind": "pocket",
                 "kind_label": "Cho'ntakka solish", "side": "R",
                 "reasons": ["SINOV: soxta signal (tests/soak.py)"], "breakdown": [["Sinov signali", 82]],
                 "ml_prob": None, "box": box, "susp_start": float(t) - 2.0, "susp_end": float(t), "pose_seq": []}
        out = list(res)
        out[2] = list(out[2]) + [alert]
        return tuple(out)

    W.Processor.step = step


def _watch_parent() -> None:
    """Orkestrator (tests/soak.py) o'lsa — edge ham chiqadi (yetim jarayon qolmasin)."""
    ppid = int(os.environ.get("SOAK_PARENT_PID", "0") or 0)
    if not ppid:
        return
    import psutil

    def loop():
        while True:
            time.sleep(5)
            if not psutil.pid_exists(ppid):
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="soak-parent").start()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    a = ap.parse_args()
    _patch()
    _watch_parent()
    import uvicorn

    from app import web

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            ev = getattr(web, "SHUTDOWN", None)      # jonli oqimlar (MJPEG, SSE) darhol yopilsin
            if ev is not None:
                ev.set()
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(web.app, host=a.host, port=a.port, log_level="warning", access_log=False,
                                   timeout_graceful_shutdown=5))
    # Toza to'xtatish (/healthz/shutdown) uchun serverni dasturga beramiz — versiyaga qarab nomi boshqacha
    if isinstance(getattr(web, "SERVER_REF", None), list):
        web.SERVER_REF.append(server)
    if isinstance(getattr(web, "SERVER", None), dict):
        web.SERVER["server"] = server
    server.run()


if __name__ == "__main__":
    main()
