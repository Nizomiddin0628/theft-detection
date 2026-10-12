"""Hushyor AI — ishga tushirish.

    python run.py            → web panel: http://localhost:8000
    python run.py --port 9000
    python run.py --no-browser
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> None:
    from app.config import HOST, PORT

    ap = argparse.ArgumentParser(description="Hushyor AI web panel")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    missing = [m for m in ("fastapi", "uvicorn", "cv2", "onnxruntime", "sklearn", "httpx", "jinja2", "multipart")
               if not _has(m)]
    if missing:
        print("Quyidagi kutubxonalar o'rnatilmagan:", ", ".join(missing))
        print("O'rnatish:  pip install -r requirements.txt")
        sys.exit(1)

    import uvicorn

    url = f"http://localhost:{a.port}"
    print("\n" + "=" * 62)
    print("  Hushyor AI ishga tushmoqda...")
    print(f"  Web panel:        {url}")
    print(f"  Tarmoqdan:        http://{local_ip()}:{a.port}")
    print("  Login / parol:    .env faylida (standart: admin / admin123)")
    print("  To'xtatish:       Ctrl+C")
    print("=" * 62 + "\n")
    if not a.no_browser and os.environ.get("HUSHYOR_NO_BROWSER") != "1":
        threading.Thread(target=lambda: (time.sleep(2.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("app.web:app", host=a.host, port=a.port, log_level="warning", access_log=False)


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


if __name__ == "__main__":
    main()
