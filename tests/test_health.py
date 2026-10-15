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
    # 1.9.0: kamera ishlamay turganda dastur qayta ishga tushdi — "ishlamayapti" takrorlanmaydi, tiklansa xabar keladi
    m2 = Manager()
    m2.notifier = FakeNotifier()
    w2 = FakeWorker("Kassa oldi")
    w2.state, w2.error = "qayta ulanmoqda", "oqim uzildi"
    m2.workers[1] = w2
    m2._health_check()
    ok("Qayta ishga tushgach (kamera hali ishlamayapti) — takroriy 'ishlamayapti' yo'q", not m2.notifier.texts
       and m2.health.get(1, {}).get("alerted"), str(m2.notifier.texts))
    w2.state, w2.error, w2.latest_t = "ishlayapti", "", time.time()
    m2._health_check()
    ok("Qayta ishga tushgandan keyin tiklansa — 'tiklandi' xabari keladi", len(m2.notifier.texts) == 1
       and "tiklandi" in m2.notifier.texts[0], str(m2.notifier.texts))
    m3 = Manager()
    ok("Tiklangach eslab qolingan holat tozalandi", 1 not in m3.health)
    # Qayta ishga tushgach kamera ulanadi, lekin bitta ham kadr bermaydi (NVR kanali o'lik) — soxta "tiklandi" yo'q
    m2.health[1] = {"since": time.time() - 600, "alerted": True, "reason": "x", "name": "Kassa oldi"}
    m2._save_health()
    m4 = Manager()
    m4.notifier = FakeNotifier()
    w4 = FakeWorker("Kassa oldi")
    w4.started_at, w4.latest_t = time.time() - 30, 0.0
    m4.workers[1] = w4
    m4._health_check()
    ok("Qayta ishga tushgach kadr kelmayapti — soxta 'tiklandi' ham, takroriy 'ishlamayapti' ham yo'q",
       not m4.notifier.texts and m4.health.get(1, {}).get("alerted"), str(m4.notifier.texts))
    w4.latest_t = time.time()
    m4._health_check()
    ok("Birinchi yangi kadr kelgach — 'tiklandi'", len(m4.notifier.texts) == 1 and "tiklandi" in m4.notifier.texts[0])
    m.health[1]["alerted"] = True
    m._save_health()
    # Foydalanuvchi o'zi to'xtatdi — holat tozalanadi, xabar yo'q
    m.workers.pop(1)
    m._health_check()
    ok("O'zi to'xtatilgan kamera kuzatilmaydi", 1 not in m.health and len(m.notifier.texts) == 3)
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
