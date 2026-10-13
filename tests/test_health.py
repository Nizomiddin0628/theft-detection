"""Kamera holati monitori testi: kamera uzilsa xabar, tiklansa yana xabar (soxta kamera bilan).

Ishga tushirish:  python -m tests.test_health
"""
from __future__ import annotations

import sys
import time

from app import db
from app.ai.manager import Manager


class FakeWorker:
    def __init__(self, name):
        self.src = {"id": 1, "name": name, "kind": "rtsp"}
        self.state = "ishlayapti"
        self.error = ""
        self.started_at = time.time() - 100
        self.latest_t = time.time()
        self.alive = True

    def is_alive(self):
        return self.alive


class FakeNotifier:
    def __init__(self):
        self.texts = []

    def send_text_all(self, text):
        self.texts.append(text)
        return 1


def main() -> int:
    db.init_db()
    db.set_settings({"camera_alerts": "1", "camera_alert_sec": 15})
    m = Manager()
    m.notifier = FakeNotifier()
    w = FakeWorker("Kassa oldi")
    m.workers[1] = w
    results = []

    def ok(name, cond, info=""):
        results.append(bool(cond))
        print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))

    m._health_check()
    ok("Ishlayotgan kamera — xabar yo'q", not m.notifier.texts)
    # Kamera uzildi
    w.state, w.error = "qayta ulanmoqda", "oqim uzildi"
    m._health_check()
    ok("Darhol xabar bermaydi (15 s kutadi)", not m.notifier.texts)
    m.health[1]["since"] -= 16
    m._health_check()
    ok("15 s dan keyin xabar", len(m.notifier.texts) == 1 and "ishlamayapti" in m.notifier.texts[0],
       m.notifier.texts[0].replace("\n", " | ") if m.notifier.texts else "")
    m._health_check()
    ok("Takroriy xabar yo'q", len(m.notifier.texts) == 1)
    # Tiklandi
    w.state, w.error, w.latest_t = "ishlayapti", "", time.time()
    m._health_check()
    ok("Tiklanganda xabar", len(m.notifier.texts) == 2 and "tiklandi" in m.notifier.texts[1],
       m.notifier.texts[-1] if m.notifier.texts else "")
    # Kadr kelmay qoldi (holat "ishlayapti", lekin kamera qotgan)
    w.latest_t = time.time() - 30
    m._health_check()
    m.health[1]["since"] -= 16
    m._health_check()
    ok("Kadr kelmasa ham xabar", len(m.notifier.texts) == 3 and "kadr kelmayapti" in m.notifier.texts[2])
    # Foydalanuvchi o'zi to'xtatdi — holat tozalanadi, xabar yo'q
    m.workers.pop(1)
    m._health_check()
    ok("O'zi to'xtatilgan kamera kuzatilmaydi", 1 not in m.health and len(m.notifier.texts) == 3)
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
