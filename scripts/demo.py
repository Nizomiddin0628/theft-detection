"""Hushyor AI demo rejimi (2.6.0): namuna do'kon — 30 kunlik ma'lumot, kameralar o'rnida samples/ videolari.

    python scripts/demo.py                 # http://127.0.0.1:8090  (login: demo / demo12345)
    python scripts/demo.py --reset         # demo ma'lumotini qaytadan yaratish
    python scripts/demo.py --port 8091 --no-browser
    python scripts/demo.py --seed-only     # faqat ma'lumot yaratish (sinov / rasm olish uchun)

Alohida papka: data/demo (haqiqiy data/ ga, .env dagi Telegram bot, bulut va SMS ga TEGMAYDI — ular bo'sh qilinadi).
Ishlab turgan Hushyor AI (8000/8010) bilan bir vaqtda ishlaydi. Panel faqat shu kompyuterdan ochiladi.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = Path(os.environ.get("HUSHYOR_DEMO_DIR") or ROOT / "data" / "demo")     # sinovlar o'z papkasini beradi
USER, PASSWORD = "demo", "demo12345"


def demo_env(port: int) -> dict:
    env = dict(os.environ)
    env.update({"HUSHYOR_DEMO": "1", "HUSHYOR_DATA_DIR": str(DEMO_DIR), "ADMIN_USER": USER, "ADMIN_PASSWORD": PASSWORD,
                "HOST": "127.0.0.1", "PORT": str(port), "TELEGRAM_BOT_TOKEN": "", "CLOUD_URL": "", "EDGE_TOKEN": "",
                "SMS_ESKIZ_EMAIL": "", "SMS_ESKIZ_PASSWORD": "", "SMS_HTTP_URL": "", "SMS_HTTP_TOKEN": "",
                "PUBLIC_URL": "", "SECRET_KEY": "", "PYTHONIOENCODING": "utf-8"})
    return env


MARK = DEMO_DIR / ".seeded"              # ma'lumot yaratilgan kun — to'liq yaratilgandan keyin yoziladi


def _stale() -> str | None:
    """Qayta yaratish kerakmi: papka bor, lekin belgi yo'q (yarim yo'lda to'xtatilgan) yoki boshqa kunda yaratilgan
    (bugungi raqamlar va oxirgi kunlar bo'sh ko'rinmasin)."""
    if not DEMO_DIR.exists() or not any(DEMO_DIR.iterdir()):
        return None
    try:
        day = MARK.read_text(encoding="utf-8").strip()
    except OSError:
        return "oldingi yaratish tugallanmagan"
    return None if day == time.strftime("%Y-%m-%d") else f"{day} kuni yaratilgan"


def _busy(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Hushyor AI demo (namuna do'kon)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--reset", action="store_true", help="demo ma'lumotini o'chirib qaytadan yaratish")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    a.days = max(7, min(a.days, 90))
    if DEMO_DIR.name != "demo":                # --reset papkani o'chiradi — faqat "demo" nomli papka (ehtiyot)
        print(f"Demo papkasi 'demo' deb nomlanishi kerak: {DEMO_DIR}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{a.port}"
    if not a.seed_only and _busy(a.port):
        # Ilova (va baza) ochilmasdan oldin: demo allaqachon ishlayapti yoki port boshqa dasturda
        print(f"\n{a.port}-port band: demo allaqachon ochiq bo'lishi mumkin — brauzerda {url} ni oching.")
        print(f"Boshqa portda: demo_windows.bat --port {a.port + 1}\n", flush=True)
        if not a.no_browser:
            webbrowser.open(url)
        return 2
    why = "--reset" if a.reset and DEMO_DIR.exists() else _stale()
    if why:
        print(f"Demo ma'lumoti qayta yaratiladi ({why})", flush=True)
        try:
            # Avval nomini o'zgartirish: baza boshqa oynada ochiq bo'lsa (Windows) — darhol xato, yarim o'chirilmaydi
            tmp = DEMO_DIR.with_name(f"demo_old_{int(time.time())}")
            DEMO_DIR.rename(tmp)
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            print("Demo boshqa oynada ochiq — avval uni yoping (Ctrl+C), keyin qayta urinib ko'ring", file=sys.stderr)
            return 2
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    env = demo_env(a.port)
    os.environ.update(env)                     # shu jarayonda ma'lumot yaratish uchun (app keyin import qilinadi)
    sys.path.insert(0, str(ROOT))
    from app import db, demo  # noqa: E402

    db.init_db()
    if demo.is_empty():
        print("Demo ma'lumot yaratilmoqda (bir marta, ~1 daqiqa)...", flush=True)
        t0 = time.time()
        st = demo.seed(days=a.days)
        MARK.write_text(time.strftime("%Y-%m-%d"), encoding="utf-8")
        print(f"Tayyor ({time.time() - t0:.0f} s): {st}", flush=True)
    else:
        print("Demo ma'lumot bor (qaytadan yaratish: --reset)", flush=True)
    if a.seed_only:
        return 0
    print("\n" + "=" * 60)
    print(f"  Hushyor AI DEMO:  {url}")
    print(f"  Login / parol:    {USER} / {PASSWORD}")
    print("  To'xtatish:       Ctrl+C")
    print("=" * 60 + "\n", flush=True)
    if not a.no_browser:
        def opener():
            for _ in range(60):
                try:
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "/healthz", timeout=2):
                        webbrowser.open(url)
                        return
                except Exception:  # noqa: BLE001
                    time.sleep(1)
        threading.Thread(target=opener, daemon=True).start()
    cmd = [sys.executable, str(ROOT / "run.py"), "--no-browser", "--host", "127.0.0.1", "--port", str(a.port)]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)

    def stop(*_a) -> None:
        # Shu jarayon to'xtatilsa (vazifa, terminal yopildi) — panel ham to'xtasin (yetim bo'lib portni band qilmasin)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, stop)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C panelga ham boradi (bitta konsol) — u o'zi toza to'xtaydi; 20 s da to'xtamasa majburan
        try:
            return proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.kill()
            return 0


if __name__ == "__main__":
    sys.exit(main())
