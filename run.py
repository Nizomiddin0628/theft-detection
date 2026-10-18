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
import secrets
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
    # Jurnal (app.log) konsol fayliga ikkinchi marta yozilmasin (app/logutil.py shu belgini ko'radi)
    os.environ["HUSHYOR_NO_CONSOLE_LOG"] = "1"


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


# Tizim proksisi (Windows sozlamasi) chetlab o'tiladi: 127.0.0.1 ga so'rov proksiga ketib "javob yo'q" bo'lmasin
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _healthy(port: int) -> bool:
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def port_busy(host: str, port: int) -> bool:
    """Port band (boshqa Hushyor AI nusxasi yoki boshqa dastur) — ikkinchi nusxa bazaga, kameralarga va botga
    TEGMASDAN chiqishi uchun (uvicorn avval ilovani ishga tushirib, keyin portni ochadi)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        # Linux/macOS: yaqinda yopilgan ulanishlar (TIME_WAIT) "band" deb ko'rinmasin (uvicorn ham shunday ochadi)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host if host not in ("", "localhost") else "127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def stop_child(proc, port: int, token: str, wait_s: float = 20.0) -> None:
    """Bola jarayonni toza to'xtatish: HTTP buyruq → kameralar, kliplar, bot yopiladi.
    TerminateProcess (Windows'da darhol o'ldirish) — faqat zaxira."""
    if proc.poll() is not None:
        return
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz/shutdown", data=b"", method="POST",
                                     headers={"X-Hushyor-Token": token})
        _LOCAL.open(req, timeout=5).close()
        proc.wait(wait_s)
        _slog("dastur toza to'xtatildi")
        return
    except Exception as e:  # noqa: BLE001
        _slog(f"toza to'xtatib bo'lmadi ({e.__class__.__name__}) — majburiy to'xtatiladi")
    try:
        proc.terminate()
        proc.wait(15)
    except Exception:  # noqa: BLE001
        proc.kill()
        proc.wait(10)


def supervise(a) -> None:
    """Asosiy dasturni bola jarayon sifatida ishga tushiradi va kuzatadi.

    * jarayon to'xtasa (xato bilan yoki kutilmaganda) — qayta ishga tushiradi (3 s dan 60 s gacha kutib);
    * /healthz ketma-ket 3 marta javob bermasa (dastur qotib qolgan) — to'xtatib, qayta ishga tushiradi.
    """
    if port_busy(a.host, a.port):
        _slog(f"{a.port}-port band — Hushyor AI allaqachon ishlayapti (ikkinchi nazoratchi ishga tushmaydi)")
        return
    cmd = [sys.executable, str(ROOT / "run.py"), "--child", "--no-browser", "--host", a.host, "--port", str(a.port)]
    token = secrets.token_hex(16)
    # Bola jarayon (app/web.py) shu kalitni HUSHYOR_CTL_TOKEN dan o'qiydi; /healthz/shutdown — X-Hushyor-Token bilan
    env = dict(os.environ, HUSHYOR_PARENT_PID=str(os.getpid()), HUSHYOR_CTL_TOKEN=token)
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
                        stop_child(proc, a.port, token, wait_s=10)
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
            stop_child(proc, a.port, token)
        _slog("nazoratchi to'xtadi (kod 0)")


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
                # Nazoratchi o'ldirildi (Windows vazifasi to'xtatildi): toza to'xtash, 20 s da bo'lmasa — majburan
                try:
                    from app import web

                    web.SHUTDOWN.set()
                    for srv in _servers(web):
                        srv.should_exit = True
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(20)
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def _servers(web) -> list:
    """uvicorn Server(lar)i: app/web.py dagi SERVER_REF ro'yxati (1.4.1+)."""
    ref = getattr(web, "SERVER_REF", None)
    return list(ref) if isinstance(ref, list) else []


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
    if port_busy(a.host, a.port):
        # Ilova import qilinmasdan oldin: ikkinchi nusxa bazani, kameralarni va Telegram botini band qilmaydi
        print(f"\n{a.port}-port band: Hushyor AI allaqachon ishlayapti (yoki boshqa dastur shu portda).")
        print(f"Panel: http://localhost:{a.port}   Boshqa port: python run.py --port {a.port + 1}\n")
        sys.exit(2)
    if a.child:
        _watch_parent()

    import uvicorn

    url = f"http://localhost:{a.port}"
    if os.environ.get("HUSHYOR_DEMO") != "1":          # demo o'z manzili va login/parolini scripts/demo.py da chiqaradi
        print("\n" + "=" * 62)
        print("  Hushyor AI ishga tushmoqda...")
        print(f"  Web panel:        {url}")
        print(f"  Tarmoqdan:        http://{local_ip()}:{a.port}")
        print("  Login / parol:    .env faylida (standart: admin / admin123)")
        print("  To'xtatish:       Ctrl+C")
        print("=" * 62 + "\n")
    if not a.no_browser and os.environ.get("HUSHYOR_NO_BROWSER") != "1":
        threading.Thread(target=lambda: (time.sleep(2.5), webbrowser.open(url)), daemon=True).start()
    from app import web

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # Ochiq jonli oqimlar (brauzer sahifalari) darhol yopilsin — aks holda server ularni kutib qoladi
            # va ikkinchi Ctrl+C dagi majburiy to'xtash xato matnlarini chiqaradi
            web.SHUTDOWN.set()
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(web.app, host=a.host, port=a.port, log_level="warning", access_log=False,
                                   timeout_graceful_shutdown=5))
    if isinstance(getattr(web, "SERVER_REF", None), list):
        web.SERVER_REF.append(server)         # /healthz/shutdown va nazoratchi shu orqali toza to'xtatadi
    try:
        server.run()
    except KeyboardInterrupt:
        pass          # uvicorn to'xtagach Ctrl+C signalini qayta ko'taradi — bu xato emas
    print("Hushyor AI to'xtatildi.")


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


if __name__ == "__main__":
    main()
