"""run.py (ishga tushiruvchi) — haqiqiy jarayon sifatida: start_windows.bat va Windows avtomatik starti aynan shuni
ishlatadi. Boshqa sinovlar ilovani to'g'ridan-to'g'ri chaqiradi, shuning uchun bu yo'l alohida tekshiriladi.

    python -m tests.test_run

Tekshiradi: oddiy ishga tushish (/healthz), ikkinchi nusxa band portda bazaga tegmasdan chiqadi, toza to'xtatish
(/healthz/shutdown: noto'g'ri kalit — 403, to'g'ri — jarayon chiqadi), nazoratchi rejimi (--supervise): dastur
ishga tushadi, nazoratchi o'ldirilsa bola jarayon ham o'zi chiqadi; POSIX'da Ctrl+C — toza to'xtatish.
Ma'lumot vaqtinchalik papkada (jonli data/ ga tegmaydi).
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def healthy(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=3, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait(fn, timeout: float, step: float = 0.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return bool(fn())


def start(args: list[str], env: dict, log: Path) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    return subprocess.Popen([sys.executable, str(ROOT / "run.py"), *args], cwd=str(ROOT), env=env,
                            stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT, creationflags=flags)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hushyor_run_"))
    base_env = dict(os.environ, HUSHYOR_DATA_DIR=str(tmp / "data"), HUSHYOR_NO_BROWSER="1", PYTHONUNBUFFERED="1",
                    PYTHONIOENCODING="utf-8")
    for k in ("HUSHYOR_CTL_TOKEN", "HUSHYOR_SHUTDOWN_TOKEN", "HUSHYOR_PARENT_PID", "EDGE_TOKEN", "CLOUD_URL",
              "TELEGRAM_BOT_TOKEN"):
        base_env.pop(k, None)

    print("--- Oddiy ishga tushish: python run.py ---")
    port = free_port()
    env = dict(base_env, HUSHYOR_CTL_TOKEN="sinov-kaliti-123456")
    p = start(["--no-browser", "--host", "127.0.0.1", "--port", str(port)], env, tmp / "run1.log")
    up = wait(lambda: healthy(port) or p.poll() is not None, 180)
    out = (tmp / "run1.log").read_text(encoding="utf-8", errors="replace")
    ok("run.py ishga tushdi, /healthz javob beradi", up and p.poll() is None, out[-600:] if not up or p.poll() else "")
    if p.poll() is not None:
        print(out[-3000:])
        return 1
    p2 = start(["--no-browser", "--host", "127.0.0.1", "--port", str(port)], env, tmp / "run2.log")
    try:
        code2 = p2.wait(60)
    except subprocess.TimeoutExpired:
        p2.kill()
        code2 = None
    out2 = (tmp / "run2.log").read_text(encoding="utf-8", errors="replace")
    ok("Ikkinchi nusxa band portda darhol chiqadi (kod 2), birinchisi ishlashda davom etadi",
       code2 == 2 and "band" in out2 and healthy(port), f"kod {code2}")
    r = httpx.post(f"http://127.0.0.1:{port}/healthz/shutdown", headers={"X-Hushyor-Token": "xato"}, timeout=5,
                   trust_env=False)
    ok("To'xtatish buyrug'i noto'g'ri kalit bilan — 403, dastur ishlayapti", r.status_code == 403 and healthy(port))
    r = httpx.post(f"http://127.0.0.1:{port}/healthz/shutdown", headers={"X-Hushyor-Token": "sinov-kaliti-123456"},
                   timeout=5, trust_env=False)
    try:
        code = p.wait(40)
    except subprocess.TimeoutExpired:
        p.kill()
        code = None
    out = (tmp / "run1.log").read_text(encoding="utf-8", errors="replace")
    ok("To'g'ri kalit bilan toza to'xtadi (kod 0, 'to'xtatildi')", r.status_code == 200 and code == 0
       and "to'xtatildi" in out and "Traceback" not in out, f"kod {code}; {out[-400:]}")

    print("\n--- Nazoratchi rejimi: python run.py --supervise (Windows avtomatik starti) ---")
    port = free_port()
    sup = start(["--supervise", "--no-browser", "--host", "127.0.0.1", "--port", str(port)], base_env, tmp / "sup.log")
    up = wait(lambda: healthy(port) or sup.poll() is not None, 180)
    ok("Nazoratchi dasturni ishga tushirdi", up and sup.poll() is None,
       (tmp / "sup.log").read_text(encoding="utf-8", errors="replace")[-600:] if not up else "")
    if os.name != "nt" and up:
        sup.send_signal(signal.SIGINT)               # Ctrl+C: nazoratchi bola jarayonni HTTP buyruq bilan to'xtatadi
        try:
            sup.wait(60)
        except subprocess.TimeoutExpired:
            sup.kill()
        slog = (tmp / "sup.log").read_text(encoding="utf-8", errors="replace")
        ok("Ctrl+C — dastur toza to'xtatildi (majburiy emas), port bo'shadi",
           "toza to'xtatildi" in slog and "majburiy" not in slog and wait(lambda: not port_open(port), 30),
           slog[-500:])
        port = free_port()
        sup = start(["--supervise", "--no-browser", "--host", "127.0.0.1", "--port", str(port)], base_env,
                    tmp / "sup2.log")
        up = wait(lambda: healthy(port) or sup.poll() is not None, 180)
    else:
        ok("Ctrl+C sinovi (Windows'da konsol signalini yuborib bo'lmaydi — o'tkazib yuborildi)", True)
    if up:
        sup.kill()                                   # nazoratchi o'ldirildi (masalan, vazifa to'xtatildi)
        sup.wait(10)
        gone = wait(lambda: not port_open(port), 45, 1.0)
        ok("Nazoratchi o'ldirilsa bola jarayon ham o'zi chiqadi (port bo'shadi)", gone)
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
