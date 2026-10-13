"""Hushyor AI — ishga tushirish.

    python run.py              → web panel: http://localhost:8000
    python run.py --port 9000
    python run.py --no-browser
    python run.py --supervise  → nazoratchi rejimi: dastur to'xtab qolsa yoki javob bermasa,
                                 avtomatik qayta ishga tushiradi (Windows avtomatik starti shuni ishlatadi)
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "data" / "logs"

# pythonw.exe (oynasiz) bilan ishga tushganda konsol yo'q: chiqishlar faylga yoziladi
if sys.stdout is None or sys.stderr is None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _console = open(LOG_DIR / "console.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _console
    sys.stderr = sys.stderr or _console


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _slog(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} supervisor: {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "supervisor.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def supervise(a) -> None:
    """Asosiy dasturni bola jarayon sifatida ishga tushiradi va kuzatadi.

    * jarayon to'xtasa (xato bilan yoki kutilmaganda) — qayta ishga tushiradi (3 s dan 60 s gacha kutib);
    * /healthz ketma-ket 3 marta javob bermasa (dastur qotib qolgan) — to'xtatib, qayta ishga tushiradi.
    """
    cmd = [sys.executable, str(ROOT / "run.py"), "--child", "--no-browser", "--host", a.host, "--port", str(a.port)]
    env = dict(os.environ, HUSHYOR_PARENT_PID=str(os.getpid()))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    backoff = 3.0
    opened = False
    proc = None
    _slog(f"nazoratchi ishga tushdi (pid {os.getpid()}), port {a.port}")
    try:
        while True:
            t0 = time.time()
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, creationflags=flags)
            _slog(f"dastur ishga tushirildi (pid {proc.pid})")
            fails, last_check = 0, 0.0
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - t0 < 90:      # modellar yuklanishi uchun vaqt
                    if not opened and not a.no_browser and _healthy(a.port):
                        opened = True
                        webbrowser.open(f"http://localhost:{a.port}")
                    continue
                if time.time() - last_check < 20:
                    continue
                last_check = time.time()
                if _healthy(a.port):
                    fails = 0
                else:
                    fails += 1
                    _slog(f"dastur javob bermayapti ({fails}/3)")
                    if fails >= 3:
                        _slog("dastur qotib qoldi — qayta ishga tushirilmoqda")
                        proc.terminate()
                        try:
                            proc.wait(15)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(10)
                        break
            code = proc.poll()
            ran = time.time() - t0
            _slog(f"dastur to'xtadi (kod {code}, {ran:.0f} s ishladi)")
            backoff = 3.0 if ran > 120 else min(backoff * 2, 60.0)
            _slog(f"{backoff:.0f} s dan keyin qayta ishga tushiriladi")
            time.sleep(backoff)
    except KeyboardInterrupt:
        _slog("to'xtatildi (Ctrl+C)")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(15)
            except Exception:
                proc.kill()


def _watch_parent() -> None:
    """Nazoratchi o'ldirilsa (masalan, Windows vazifasi to'xtatilsa) — bola jarayon ham o'zi chiqadi."""
    ppid = int(os.environ.get("HUSHYOR_PARENT_PID", "0") or 0)
    if not ppid:
        return
    try:
        import psutil
    except ImportError:
        return

    def loop():
        while True:
            time.sleep(5)
            if not psutil.pid_exists(ppid):
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main() -> None:
    from app.config import HOST, PORT

    ap = argparse.ArgumentParser(description="Hushyor AI web panel")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--supervise", action="store_true", help="to'xtasa yoki qotsa avtomatik qayta ishga tushirish")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    missing = [m for m in ("fastapi", "uvicorn", "cv2", "onnxruntime", "sklearn", "httpx", "jinja2", "multipart")
               if not _has(m)]
    if missing:
        print("Quyidagi kutubxonalar o'rnatilmagan:", ", ".join(missing))
        print("O'rnatish:  pip install -r requirements.txt")
        sys.exit(1)

    if a.supervise:
        supervise(a)
        return
    if a.child:
        _watch_parent()

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
