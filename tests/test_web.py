"""To'liq integratsion sinov: web panel + AI pipeline + Telegram bot (soxta API bilan).

Ishga tushirish (loyiha papkasidan):  python -m tests.test_web path/to/video.mp4
"""
from __future__ import annotations

import os
import sys
import time

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["TELEGRAM_API_BASE"] = "http://127.0.0.1:8765"

from tests import mock_telegram as tg  # noqa: E402

tg.serve(8765)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.web import app  # noqa: E402

PAGES = ["/", "/live", "/upload", "/events", "/sources", "/model", "/telegram", "/reports", "/settings",
         "/system", "/help"]


def wait(cond, timeout=60, step=0.5, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError(f"Kutish vaqti tugadi: {what}")


def main(video: str):
    results = []

    def ok(name, cond=True, info=""):
        results.append((name, bool(cond), info))
        print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))

    with TestClient(app) as c:
        r = c.get("/", follow_redirects=False)
        ok("Kirishsiz panel yopiq", r.status_code == 303 and "/login" in r.headers["location"])
        r = c.post("/login", data={"username": "admin", "password": "xato", "next": "/"}, follow_redirects=False)
        ok("Noto'g'ri parol rad etiladi", "error" in r.headers.get("location", ""))
        r = c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"}, follow_redirects=False)
        ok("Login ishlaydi", r.status_code == 303 and r.headers["location"] == "/")
        for p in PAGES:
            r = c.get(p)
            ok(f"Sahifa {p}", r.status_code == 200 and "Hushyor" in r.text, f"{r.status_code}")

        # --- Telegram ulanish
        wait(lambda: tg.calls("getMe"), 20, what="getMe")
        code = db.get_setting("connect_code")
        tg.text_message(111, "salom")
        wait(lambda: any("Ulanish uchun" in (x["data"].get("text") or "") for x in tg.calls("sendMessage")), 20,
             what="ulanmagan foydalanuvchiga ko'rsatma")
        ok("Ulanmagan foydalanuvchiga ko'rsatma beriladi")
        tg.text_message(111, f"/start {code}")
        wait(lambda: db.scalar("SELECT COUNT(*) FROM subscribers WHERE chat_id=111"), 20, what="obuna")
        ok("Kod bilan obuna bo'lish", True)
        tg.text_message(111, "/holat")
        wait(lambda: any("Bugun" in (x["data"].get("text") or "") for x in tg.calls("sendMessage")), 20, what="/holat")
        ok("/holat buyrug'i")
        api = c.get("/api/telegram").json()
        ok("Telegram API holati", api["bot"]["status"] == "ishlayapti" and api["bot"]["username"] == "hushyor_test_bot",
           api["bot"]["status"])

        # --- Sozlamalar (demo rejim — sinov videoda signal chiqishi uchun)
        r = c.post("/api/settings", json={"sensitivity": "demo"})
        ok("Sozlamani saqlash (JSON)", r.status_code == 200 and r.json()["settings"]["threshold"] in ("35", "35.0"),
           r.json()["settings"]["threshold"])
        r = c.post("/api/settings", data={"_form": "full", "sensitivity": "demo", "apply_preset": "1",
                                          "threshold": "35", "use_ml": "on", "signal_pocket": "on",
                                          "signal_torso": "on", "signal_bag": "on", "signal_vanish": "on",
                                          "signal_looking": "on", "signal_offscreen": "on",
                                          "telegram_enabled": "on", "telegram_send_video": "on",
                                          "store_name": "Sinov do'koni"}, follow_redirects=False)
        ok("Sozlamalar formasi", r.status_code == 303)

        # --- Video yuklash va tahlil
        with open(video, "rb") as f:
            r = c.post("/api/upload", files={"file": ("sinov.mp4", f, "video/mp4")}, data={"mode": "fast"})
        ok("Video yuklash", r.status_code == 200, r.text[:120])
        jid = r.json()["job_id"]
        job = wait(lambda: (lambda j: j if j["status"] in ("tayyor", "xato") else None)(
            db.row("SELECT * FROM jobs WHERE id=?", (jid,))), 900, 2, what="video tahlili")
        ok("Video tahlili tugadi", job["status"] == "tayyor", f"{job['events_count']} ta hodisa, {job['duration']} s")
        evs = c.get(f"/api/events?job={jid}").json()
        ok("Hodisalar ro'yxati", evs["total"] >= 1, f"{evs['total']} ta")
        if evs["total"]:
            ev = evs["items"][-1]
            ok("Klip va rasm yaratildi", bool(ev["clip_url"] and ev["thumb_url"]))
            r = c.get(ev["clip_url"])
            ok("Klip yuklanadi (MP4)", r.status_code == 200 and r.content[4:8] == b"ftyp", f"{len(r.content)} bayt")
            r = c.get(f"/events/{ev['id']}")
            ok("Hodisa sahifasi", r.status_code == 200 and "Ball tarkibi" in r.text)
            wait(lambda: tg.calls("sendVideo"), 30, what="sendVideo")
            sv = tg.calls("sendVideo")[0]
            ok("Telegramga video yuborildi", sv["files"].get("video", {}).get("size", 0) > 1000,
               f"{sv['files']['video']['size']} bayt, thumb={'thumbnail' in sv['files']}")
            ok("Xabarda tugmalar bor", "Tasdiqlash" in sv["data"].get("reply_markup", ""))
            ok("Xabarda sabablar bor", "Sabablar" in sv["data"].get("caption", ""))
            first_ev = int(sv["data"]["reply_markup"].split("ok:")[1].split('"')[0])
            # Telegram tugmasi orqali tasdiqlash
            tg.callback(111, f"ok:{first_ev}")
            wait(lambda: db.get_event(first_ev)["status"] == "tasdiqlangan", 20, what="callback")
            ok("Telegram tugmasi — tasdiqlash", True, db.get_event(first_ev)["reviewed_by"])
            wait(lambda: tg.calls("editMessageCaption"), 20, what="xabarni yangilash")
            ok("Telegram xabari yangilandi", "TASDIQLANDI" in tg.calls("editMessageCaption")[-1]["data"]["caption"])
            # Web orqali rad etish
            if evs["total"] > 1:
                other = [e for e in evs["items"] if e["id"] != first_ev][0]["id"]
                r = c.post(f"/api/events/{other}/review", json={"status": "rad_etilgan", "note": "telefon"})
                ok("Web orqali xato deb belgilash", r.json()["event"]["status"] == "rad_etilgan")

        # --- Jonli rejim (video kamera kabi) va MJPEG
        with open(video, "rb") as f:
            r = c.post("/api/upload", files={"file": ("jonli.mp4", f, "video/mp4")}, data={"mode": "live"})
        sid = r.json().get("source_id")
        ok("Jonli rejimda video manba", bool(sid))
        wait(lambda: c.get(f"/api/sources/{sid}/snapshot.jpg").content[:2] == b"\xff\xd8"
             and any(s["id"] == sid and s["fps_ai"] > 0 for s in c.get("/api/sources").json()), 60, 1,
             what="jonli oqim")
        info = [s for s in c.get("/api/sources").json() if s["id"] == sid][0]
        ok("Jonli AI tahlil ishlayapti", info["running"], f"AI {info['fps_ai']} kadr/s, kamera {info['fps_cam']}")
        c.post(f"/api/sources/{sid}/stop")
        ok("Manbani to'xtatish", True)

        # --- Model o'qitish (kichik)
        r = c.post("/api/model/train", json={"synthetic": 400, "feedback": "1"})
        ok("O'qitishni boshlash", r.status_code == 200)
        st = wait(lambda: (lambda s: s if not s["running"] else None)(c.get("/api/model/train/status").json()),
                  300, 1, what="o'qitish")
        ok("O'qitish tugadi", st["result"] is not None, (st["error"] or "") + (st["logs"][-1] if st["logs"] else ""))
        m = c.get("/api/model").json()
        ok("Model versiyalari", len(m["versions"]) >= 1 and any(v["is_active"] for v in m["versions"]))

        # --- Boshqa API lar
        for path in ("/api/summary", "/api/reports?days=7", "/api/system", "/api/jobs", "/api/sources"):
            r = c.get(path)
            ok(f"API {path}", r.status_code == 200)
        r = c.get("/api/export/events.csv")
        ok("CSV eksport", r.status_code == 200 and "Kamera" in r.content.decode("utf-8-sig"))
        r = c.post("/api/telegram/test")
        ok("Telegram sinov xabari", r.status_code == 200)
        r = c.post("/api/sources", json={"kind": "rtsp", "uri": "rtsp://user:pw@10.0.0.9:554/s1", "name": "IP test"})
        ok("RTSP manba qo'shish", r.status_code == 200)

    bad = [r for r in results if not r[1]]
    print(f"\nNatija: {len(results) - len(bad)}/{len(results)} sinov o'tdi")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "../testdata/store-aisle-detection.mp4"))
