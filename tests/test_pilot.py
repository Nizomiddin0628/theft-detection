"""2.5.0 (9-bosqich) sinovi: pilotga tayyorgarlik — tayyorlik ro'yxati, darvozalar jadvali, pilot haftalari va
darvozasi (28 kun, ishlash, kameralar, xato signal, javobsiz, hisobotlar, egasining bahosi), haftalik baho so'rovi
(Telegram: dushanba, 1–5, izoh), /pilot, PDF, diagnostika paketi (maxfiy qiymatlar yashirilgan), panel API, edge →
bulut (hello'da tayyorlik, bulut filiallar ro'yxatida; SQLite, CLOUD_TEST_PG_URL bilan PostgreSQL ham).

    python -m tests.test_pilot
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_pilot_")   # hech qachon jonli data/ emas
os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_pilot_cloud_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN",
          "CLOUD_URL", "EDGE_TOKEN"):
    os.environ.pop(k, None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, pilot, reports  # noqa: E402
from tests import mock_telegram as tg  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def pdf_ok(b: bytes) -> bool:
    if not b.startswith(b"%PDF-1.") or not b.rstrip().endswith(b"%%EOF"):
        return False
    m = re.search(rb"startxref\s+(\d+)", b[-200:])
    return bool(m) and b[int(m.group(1)):int(m.group(1)) + 4] == b"xref"


def clear() -> None:
    for t in ("counts", "metrics_minute", "events", "reports", "report_sends", "report_feedback", "pilot_feedback",
              "pilot_weeks", "zones", "sources", "subscribers"):
        db.execute(f"DELETE FROM {t}")
    db.set_settings({"pilot_since": "", "pilot_until": "", "pilot_poll_week": "", "pilot_poll_state": "",
                     "work_hours": "09:00-22:00", "store_name": "", "retention_days": "0", "report_daily": "1",
                     "report_time": "09:00", "sms_phones": ""})
    for f in config.FEEDBACK_DIR.glob("ev*.json"):
        f.unlink()
    pilot._reset_caches()


def add_camera(name: str = "Kassa kamera", zones: bool = True) -> int:
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     (name, "rtsp", "rtsp://admin:parol@10.0.0.9/1", 1, 1, time.time() - 60 * 86400))
    if zones:
        db.execute("INSERT INTO zones(source_id, kind, name, points, enabled) VALUES(?,?,?,?,1)",
                   (sid, "xodim", "Kassa", json.dumps([[0, 0], [.3, 0], [.3, .3], [0, .3]])))
    return sid


def seed_days(sid: int, day0: float, n: int, off_days: tuple[int, ...] = (), rejected: int = 1, confirmed: int = 2,
              unreviewed: int = 0, reports_ok: bool = True) -> None:
    """n kun: har daqiqa tizim va kamera qatori (off_days — dastur butun kun o'chiq), signallar (javob 2 daqiqada),
    kunlik hisobot (rejadagi vaqtdan 1 daqiqa keyin yetgan)."""
    mm, d = [], day0
    days = []
    for i in range(n):
        days.append(d)
        if i not in off_days:
            for m in range(1440):
                minute = int(d + m * 60)
                mm.append((minute, 0, "tizim", None, None, None, None, None, None, 10.0, 20.0, None))
                mm.append((minute, sid, "Kassa kamera", 1.0, 2, 5.0, 0.5, 1.0, 0, None, None, None))
        d = reports.day_start(d + 36 * 3600)
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO metrics_minute(minute, source_id, source_name, persons_avg, persons_max, "
                      "fps_ai, active_pct, up_pct, alerts, cpu, ram, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", mm)
    for d in days:
        k = 0
        for st, cnt in (("rad_etilgan", rejected), ("tasdiqlangan", confirmed), ("yangi", unreviewed)):
            for _ in range(cnt):
                k += 1
                t = d + 12 * 3600 + k * 60
                eid = db.insert_event({"source_id": sid, "source_name": "Kassa kamera", "created_at": t, "score": 70,
                                       "level": "o'rta", "kind": "pocket", "reasons": ["x"], "breakdown": [],
                                       "track_id": k, "status": st})
                if st != "yangi":
                    db.execute("UPDATE events SET reviewed_at=? WHERE id=?", (t + 120, eid))
        d1 = reports.day_start(d + 36 * 3600)
        due = reports.scheduled_due("daily", d1)
        if due + 1800 <= time.time():
            db.execute("INSERT OR REPLACE INTO reports(kind, t0, t1, created_at, due_at, data, sent_at, sent_n) "
                       "VALUES('daily',?,?,?,?,'{}',?,1)", (d, d1, due, due, (due + 60) if reports_ok else None))


# ============================================================================ 1. tayyorlik
def readiness_suite() -> None:
    print("\n--- Tayyorlik ro'yxati ---")
    clear()
    db.set_settings({"work_hours": ""})
    r = pilot.readiness(force=True)
    st = {x["key"]: x["state"] for x in r["items"]}
    ok("Bo'sh o'rnatish: parol, Telegram, kameralar — ❌; do'kon nomi, ish vaqti — ⚠️",
       st["password"] == "bad" and st["telegram"] == "bad" and st["cameras"] == "bad" and st["store"] == "warn"
       and st["work_hours"] == "warn" and not r["ready"], str(st))
    ok("Ixtiyoriy bandlar (sanash chizig'i, bulut, Windows'dan boshqa tizimda avtomatik start) jami hisobiga kirmaydi",
       all(x["optional"] for x in r["items"] if x["key"] in ("line", "cloud"))
       and r["total"] == sum(1 for x in r["items"] if not x["optional"]))
    ok("Har ❌/⚠️ bandda izoh va (kerak bo'lsa) tuzatish sahifasi", all(x["detail"] for x in r["items"])
       and next(x for x in r["items"] if x["key"] == "cameras")["link"] == "/sources")
    sid = add_camera(zones=False)
    db.set_settings({"store_name": "Test do'kon", "work_hours": "09:00-22:00", "retention_days": "7"})
    r2 = pilot.readiness()
    ok("10 daqiqa kesh: o'zgarish darhol ko'rinmaydi, force=1 bilan — ko'rinadi", r2 is r)
    r = pilot.readiness(force=True)
    st = {x["key"]: x for x in r["items"]}
    ok("Kamera qo'shildi, lekin ishlamayapti — ❌ nomi bilan; zonasiz — ⚠️", st["cameras"]["state"] == "bad"
       and "Kassa kamera" in st["cameras"]["detail"] and st["zones"]["state"] == "warn", st["cameras"]["detail"])
    ok("Saqlash muddati 7 kun — ⚠️ (pilot uchun ≥ 30); do'kon nomi va ish vaqti — ✅", st["retention"]["state"] == "warn"
       and st["store"]["state"] == "ok" and st["work_hours"]["state"] == "ok")
    now = time.time()
    seed_days(sid, reports.day_start(now - 7 * 86400 + 43200), 8, off_days=(2,))
    r = pilot.readiness(force=True)
    a = next(x for x in r["items"] if x["key"] == "app_off")
    ok("Oxirgi 7 kunda dastur ish vaqtida o'chiq bo'lgan (bir kun) — ❌ va maslahat", a["state"] == "bad"
       and "uyqu" in a["detail"], a["detail"])
    ok("Avtomatik ishga tushish: Windows'dan boshqa tizimda — 'faqat Windows'da'",
       next(x for x in r["items"] if x["key"] == "autostart")["state"] == ("warn" if os.name != "nt" else
                                                                         next(x for x in r["items"] if x["key"] == "autostart")["state"]))
    s = pilot.summary()
    ok("Bulutga qisqasi: ok/total/bad/warn (pilot hali yo'q)", set(s) >= {"ok", "total", "bad", "warn", "at"}
       and "pilot" not in s and "Panel paroli" in s["bad"])
    db.set_settings({"retention_days": "30"})
    pilot.start(now - 86400)
    rr = {x["key"]: x for x in pilot.readiness(force=True)["items"]}
    ok("Pilot davomida saqlash muddati 30 kun — ❌ (kamida 60, eski hodisalar o'chsa haftalar kamayadi)",
       rr["retention"]["state"] == "bad" and "60" in rr["retention"]["detail"], rr["retention"]["detail"])
    db.set_settings({"retention_days": "0", "pilot_since": ""})


# ============================================================================ 2. pilot darvozasi
def pilot_suite() -> None:
    print("\n--- Pilot: haftalar va darvoza ---")
    clear()
    g = pilot.pilot_gate()
    ok("Boshlanmagan", g["state"] == "boshlanmagan" and not pilot.weeks())
    sid = add_camera()
    now = time.time()
    start = reports.day_start(now - 30 * 86400 + 43200)
    seed_days(sid, start, 30)
    pilot.start(start)
    for i, r in enumerate((5, 4, 4)):
        w = pilot.week_start(start + (i + 1) * 7 * 86400 + 43200)
        pilot.save_feedback(w, f"tg:{100 + i}", r, "yaxshi" if i == 0 else None, 100 + i)
    g = pilot.pilot_gate()
    chk = {c["key"]: c for c in g["checks"]}
    ok("30 kun, hammasi me'yorida — pilot darvozasi o'tdi", g["state"] == "otdi" and g["days"] >= 28,
       json.dumps({k: (v["text"], v["ok"]) for k, v in chk.items()}, ensure_ascii=False))
    ok("Dastur va kameralar 100%, xato 1 / kamera / kun, javobsiz 0%", chk["uptime"]["value"] == 1.0
       and chk["cam_up"]["value"] == 1.0 and abs(chk["false"]["value"] - 1.0) < 0.05 and chk["unreviewed"]["value"] == 0)
    ok("Baho: o'rtacha 4.3 (3 ta)", abs(chk["rating"]["value"] - 4.33) < 0.01, chk["rating"]["text"])
    sid_b = add_camera("Yangi kamera")
    db.execute("UPDATE sources SET created_at=? WHERE id=?", (now - 3600, sid_b))
    db.execute("INSERT INTO metrics_minute(minute, source_id, source_name, up_pct) VALUES(?,?,?,1.0)",
               (int(now // 60 * 60) - 3600, sid_b, "Yangi kamera"))
    db.insert_event({"source_id": sid_b, "source_name": "Yangi kamera", "created_at": now - 1800, "score": 70,
                     "level": "o'rta", "kind": "pocket", "reasons": ["x"], "breakdown": [], "track_id": 1,
                     "status": "rad_etilgan"})
    g2 = pilot.pilot_gate(time.time())
    ok("1 soat ishlagan yangi kamerada bitta ❌ — pilotni yiqitmaydi (faqat ≥ 7 kun ishlagan kameralar)",
       g2["state"] == "otdi", {c["key"]: c["text"] for c in g2["checks"]}.get("false"))
    for k in range(25):                              # 7 kun to'lmagan, lekin 25 ta ❌ — chegaradan (21) oshgan
        db.insert_event({"source_id": sid_b, "source_name": "Yangi kamera", "created_at": now - 1700 + k, "score": 70,
                         "level": "o'rta", "kind": "pocket", "reasons": ["x"], "breakdown": [], "track_id": k,
                         "status": "rad_etilgan"})
    g4 = pilot.pilot_gate(time.time())
    ok("... lekin 7 kun to'lmasdan 25 ta ❌ bergan kamera — xato signal mezoni o'tmaydi (7-bosqich bilan bir xil)",
       not {c["key"]: c for c in g4["checks"]}["false"]["ok"] and "Yangi kamera" in
       {c["key"]: c for c in g4["checks"]}["false"]["text"], {c["key"]: c["text"] for c in g4["checks"]}["false"])
    db.execute("DELETE FROM events WHERE source_id=?", (sid_b,))
    ws_f = pilot.weeks(time.time() + 30 * 86400)
    ok("Kelajakdagi 'now' bilan hisob — haftalar saqlanmaydi", len(ws_f) > 5
       and db.scalar("SELECT COUNT(*) FROM pilot_weeks WHERE week_t0 >= ?", (pilot.week_start(now),)) == 0)
    ws0 = pilot.weeks(time.time())
    w1 = next(w for w in ws0 if not w["partial"] and w["t0"] == w["week"])
    from app.ai.manager import archive_feedback
    old = db.rows("SELECT * FROM events WHERE created_at >= ? AND created_at < ? AND status != 'tasdiqlangan'",
                  (w1["t0"], w1["t1"]))
    for e in old:                                    # saqlash muddati tozalashi kabi: arxivga yozib o'chirish
        archive_feedback(e)
        db.execute("DELETE FROM events WHERE id=?", (e["id"],))
    ws1 = pilot.weeks(time.time())
    g3 = pilot.pilot_gate(time.time())
    ok("Eski ❌ hodisalar o'chirildi — yopilgan hafta raqamlari va pilot darvozasi o'zgarmaydi (saqlangan hafta, arxiv)",
       len(old) == 7 and ws1[ws0.index(w1)]["theft"] == w1["theft"] and g3["state"] == "otdi"
       and {c["key"]: c["value"] for c in g3["checks"]}["false"] == {c["key"]: c["value"] for c in g2["checks"]}["false"],
       f"{len(old)} {ws1[ws0.index(w1)]['theft']}")
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "pilot_gate.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    ok("tests/pilot_gate.py: bazani faqat o'qib, jadval va 'OTDI' — kod 0", r.returncode == 0 and "OTDI" in r.stdout
       and "Darvozalar jadvali" in r.stdout and "9. Pilot" in r.stdout, f"kod {r.returncode}: {(r.stdout + r.stderr)[-900:]}")
    ws = pilot.weeks()
    ok("Kalendar haftalari (dushanba–yakshanba), birinchisi pilot boshidan, oxirgisi — davom etyapti",
       len(ws) >= 5 and all(time.localtime(w["week"]).tm_wday == 0 for w in ws) and ws[0]["t0"] == start
       and ws[-1]["partial"], str([w["label"] for w in ws]))
    full = [w for w in ws if not w["partial"] and w["t0"] == w["week"]]
    ok("To'liq hafta: 7 kun × (1 ❌ + 2 ✅), javob vaqti 2 daqiqa, hisobot 7/7",
       full and full[0]["theft"] == {"total": 21, "confirmed": 14, "rejected": 7, "unreviewed": 0}
       and full[0]["react_med"] == 120 and full[0]["report_days"] == 7 and full[0]["report_on_time"] == 7,
       str(full[0] if full else ws))
    ok("Hafta bahosi o'sha haftaga yoziladi", any(w["rating"] == 5.0 for w in ws))
    wk = pilot.weeks()[1]["week"]
    pilot.save_feedback(wk, "panel:admin", 2)
    ok("Yangi baho darhol ko'rinadi (kesh tozalanadi)", next(w for w in pilot.weeks() if w["week"] == wk)["ratings"] == 2)
    db.execute("DELETE FROM pilot_feedback WHERE who='panel:admin'")
    pilot._reset_caches()
    # Bir kun dastur o'chiq (ish vaqtining ~3,4%) — 98% dan kam
    clear()
    sid = add_camera()
    seed_days(sid, start, 30, off_days=(10,))
    pilot.start(start)
    for i in range(3):
        pilot.save_feedback(pilot.week_start(start + (i + 1) * 7 * 86400 + 43200), f"tg:{i}", 5)
    g = pilot.pilot_gate()
    chk = {c["key"]: c for c in g["checks"]}
    ok("Bir kun kompyuter o'chiq — 'dastur ishladi' < 98%, darvoza o'tmadi", g["state"] == "otmadi"
       and not chk["uptime"]["ok"] and chk["cam_up"]["ok"], chk["uptime"]["text"])
    clear()
    sid = add_camera()
    seed_days(sid, start, 30, rejected=5, unreviewed=1, reports_ok=False)
    pilot.start(start)
    g = pilot.pilot_gate()
    chk = {c["key"]: c for c in g["checks"]}
    ok("Xato 5 / kun, javobsiz 12,5%, hisobot yetmagan, baho yo'q — to'rt mezon o'tmadi",
       not chk["false"]["ok"] and not chk["unreviewed"]["ok"] and not chk["reports"]["ok"] and not chk["rating"]["ok"]
       and g["state"] == "otmadi", json.dumps({k: v["text"] for k, v in chk.items()}, ensure_ascii=False))
    clear()
    sid = add_camera()
    seed_days(sid, reports.day_start(now - 10 * 86400 + 43200), 10)
    pilot.start(reports.day_start(now - 10 * 86400 + 43200))
    g = pilot.pilot_gate()
    ok("10 kun — 'yetarli emas' (28 kundan kam)", g["state"] == "yetarli emas" and g["days"] == 10, g["detail"])
    pilot.stop(now - 2 * 86400)
    g = pilot.pilot_gate()
    db.insert_event({"source_id": sid, "source_name": "Kassa kamera", "created_at": now - 3600, "score": 70,
                     "level": "o'rta", "kind": "pocket", "reasons": ["x"], "breakdown": [], "track_id": 1,
                     "status": "rad_etilgan"})
    pilot._reset_caches()
    ok("Yakunlangan pilot: oyna tugash kunigacha, keyingi hodisalar natijani o'zgartirmaydi",
       g["days"] == 8 and pilot.summary()["pilot"]["until"]
       and pilot.pilot_gate()["stats"]["theft"] == g["stats"]["theft"])
    try:
        pilot.stop()
        again = False
    except ValueError:
        again = True
    ok("Yakunlangan pilotni qayta yakunlab bo'lmaydi (oyna cho'zilmaydi)", again)
    gs = pilot.gates()
    ok("Darvozalar jadvali: 3–9-bosqich, har birida holat va izoh", [x["stage"] for x in gs] == [3, 4, 5, 6, 7, 8, 9]
       and all(x["state"] and x["detail"] and x["link"] for x in gs), str([(x["stage"], x["state"]) for x in gs]))
    orig = pilot.GATES[0]
    pilot.GATES[0] = (3, "Odamlarni sanash", "/visits", lambda: 1 / 0)
    try:
        gs = pilot.gates()
    finally:
        pilot.GATES[0] = orig
    ok("Bitta darvoza xatosi jadvalni buzmaydi ('xato')", gs[0]["state"] == "xato" and gs[1]["state"])
    real = pilot._pilot_gate
    pilot._pilot_gate = lambda *a: 1 / 0
    pilot._reset_caches()
    try:
        gs = pilot.gates()
    finally:
        pilot._pilot_gate = real
    ok("Pilot darvozasi xatosi ham jadvalni buzmaydi", gs[-1]["stage"] == 9 and gs[-1]["state"] == "xato")
    b = pilot.pdf()
    ok("Pilot hisoboti PDF (darvoza, haftalar, darvozalar jadvali, tayyorlik)", pdf_ok(b) and len(b) > 1500, f"{len(b)} bayt")


# ============================================================================ 3. Telegram: baho so'rovi
def telegram_suite() -> None:
    print("\n--- Telegram: haftalik baho, izoh, /pilot ---")
    from app import telegram_bot as tb

    port = free_port()
    tg.serve(port)
    clear()
    for cid, rep in ((5001, 1), (5002, 0)):
        db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active, kb_cleared, reports) "
                   "VALUES(?,?,?,?,1,1,?)", (cid, "Q", "q", time.time(), rep))
    bot = tb.TelegramBot(token="777:TEST", api_base=f"http://127.0.0.1:{port}")
    now = time.time()
    mon = pilot.week_start(now)
    pilot.start(mon - 20 * 86400)
    at = mon + 9 * 3600 + 11 * 60
    w0 = pilot.week_start(mon - 7 * 86400 + 43200)
    ok("Dushanba 09:05 — hali erta (hisobotdan 10 daqiqa keyin)", pilot.poll_week(mon + 9 * 3600 + 300) is None)
    ok("Dushanba 09:11 — o'tgan hafta so'raladi", pilot.poll_week(at) == w0)
    ok("Seshanba — so'ralmaydi", pilot.poll_week(at + 86400) is None)
    db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active, kb_cleared, reports) "
               "VALUES(5003, 'Q3', 'q3', ?, 1, 1, 1)", (time.time(),))
    tg.CALLS.clear()
    tg.FAIL_STATUS["sendMessage"] = 403
    tg.FAIL_NEXT["sendMessage"] = 1                  # 5001 ga yetmaydi
    n1 = pilot.poll_tick(bot, at)
    tg.FAIL_NEXT.clear()
    tg.FAIL_STATUS.clear()
    ok("So'rov bir chatga yetmadi — hafta yopilmaydi, keyingi urinish faqat unga", n1 == 1
       and pilot.poll_week(at + 60) == w0, str(n1))
    tg.CALLS.clear()
    n = pilot.poll_tick(bot, at + 600)
    ok("... qayta urinishda yetdi, hafta yopildi", n == 1 and pilot.poll_week(at + 1200) is None
       and [c["data"]["chat_id"] for c in tg.calls("sendMessage")] == [5001])
    db.execute("DELETE FROM subscribers WHERE chat_id=5003")
    db.execute("UPDATE subscribers SET active=0")
    db.set_settings({"pilot_poll_week": "", "pilot_poll_state": ""})
    ok("Obunachi yo'q — hafta yopilmaydi (kun davomida qo'shilgan obunachi oladi)", pilot.poll_tick(bot, at) == 0
       and pilot.poll_week(at + 60) == w0)
    db.execute("UPDATE subscribers SET active=1")
    tg.CALLS.clear()
    n = pilot.poll_tick(bot, at)
    m = [c for c in tg.calls("sendMessage")]
    ok("So'rov faqat hisobot oladigan obunachiga, 1–5 tugmalari bilan", n == 1 and len(m) == 1
       and m[0]["data"]["chat_id"] == 5001 and "haftalik baho" in m[0]["data"]["text"]
       and [b["callback_data"] for b in m[0]["data"]["reply_markup"]["inline_keyboard"][0]]
       == [f"pv:{int(w0)}:{i}" for i in range(1, 6)], str(m[:1])[:200])
    ok("Haftasiga bir marta", pilot.poll_tick(bot, at + 60) == 0)

    def cbk(data, cid=5001):
        bot._on_callback({"id": "c1", "data": data, "from": {"id": cid, "first_name": "Ali"},
                          "message": {"message_id": 1, "chat": {"id": cid}}})
    cbk(f"pv:{int(w0)}:4")
    r = db.row("SELECT * FROM pilot_feedback WHERE who='tg:5001'")
    ok("Baho saqlandi (4)", r and r["rating"] == 4 and r["week_t0"] == w0, str(r))
    tg.CALLS.clear()
    bot._on_message({"chat": {"id": 5001}, "text": "Kassa kamerasi ko'p xato beryapti", "from": {"id": 5001}})
    r = db.row("SELECT * FROM pilot_feedback WHERE who='tg:5001'")
    ok("Bahodan keyingi oddiy xabar — izoh (baho saqlanib qoladi)", r["comment"] == "Kassa kamerasi ko'p xato beryapti"
       and r["rating"] == 4 and any("Izoh saqlandi" in (c["data"].get("text") or "") for c in tg.calls("sendMessage")))
    tg.CALLS.clear()
    bot._on_message({"chat": {"id": 5001}, "text": "salom", "from": {"id": 5001}})
    ok("Keyingi xabar endi izoh emas", db.row("SELECT comment FROM pilot_feedback WHERE who='tg:5001'")["comment"]
       != "salom" and any("tushunmadim" in (c["data"].get("text") or "") for c in tg.calls("sendMessage")))
    cbk(f"pv:{int(w0)}:9")
    cbk("pv:abc:3")
    cbk("pv:nan:5")
    cbk("pv:1e300:5")
    cbk(f"pv:{int(mon + 7 * 86400)}:5")
    cbk(f"pv:{int(w0) + 3600}:5")
    ok("Noto'g'ri / soxta callback (9, abc, nan, 1e300, kelajak hafta, dushanba emas) — yozilmaydi",
       db.scalar("SELECT COUNT(*) FROM pilot_feedback") == 1)
    cbk(f"pv:{int(w0)}:5", cid=7777)
    ok("Obuna bo'lmagan chat — ruxsat yo'q", db.scalar("SELECT COUNT(*) FROM pilot_feedback") == 1)
    cbk(f"pv:{int(w0)}:2")
    bot._on_message({"chat": {"id": 5001}, "photo": [{}], "from": {"id": 5001}})              # rasm — izoh emas
    bot._on_message({"chat": {"id": 5001}, "text": "boshqa a'zo", "from": {"id": 9999}})    # guruhda boshqa odam
    ok("Rasm va boshqa odamning xabari izohni 'yemaydi'", 5001 in bot._pilot_wait
       and db.row("SELECT comment FROM pilot_feedback WHERE who='tg:5001'")["comment"] != "boshqa a'zo")
    bot._pilot_wait[5001] = (w0, time.time() - 1, 5001)
    bot._on_message({"chat": {"id": 5001}, "text": "kech qolgan izoh", "from": {"id": 5001}})
    r = db.row("SELECT * FROM pilot_feedback WHERE who='tg:5001'")
    ok("Fikr o'zgardi (2); 10 daqiqadan keyingi matn izoh emas", r["rating"] == 2 and r["comment"] != "kech qolgan izoh")
    tg.CALLS.clear()
    bot._on_message({"chat": {"id": 5001}, "text": "/pilot", "from": {"id": 5001}})
    t_end = time.time() + 15
    while time.time() < t_end and not any("Pilot holati" in (c["data"].get("text") or "") for c in tg.calls("sendMessage")):
        time.sleep(0.05)                             # /pilot — fon oqimida (so'rovlarni qabul qiluvchini to'xtatmaydi)
    m = [c for c in tg.calls("sendMessage") if c["data"].get("chat_id") == 5001]
    ok("/pilot — tayyorlik va mezonlar", m and "Pilot holati" in m[-1]["data"]["text"] and "kun" in m[-1]["data"]["text"])
    ok("Bot buyrug'i /pilot va yordam matni", any(c["command"] == "pilot" for c in tb.TelegramBot.COMMANDS)
       and "/pilot" in tb.HELP)
    db.set_settings({"pilot_poll_week": "", "pilot_poll_state": ""})
    tg.CALLS.clear()
    reports.Scheduler(bot).tick(at)
    ok("Rejalashtiruvchi dushanba so'rovni o'zi yuboradi", any("haftalik baho" in (c["data"].get("text") or "")
                                                             for c in tg.calls("sendMessage")))


# ============================================================================ 4. diagnostika va API
def diag_api_suite() -> None:
    print("\n--- Diagnostika paketi va panel API ---")
    from fastapi.testclient import TestClient

    from app import diagnostics, web

    clear()
    add_camera()
    db.set_settings({"store_name": "Do'kon 1", "connect_code": "482913", "sms_phones": "+998901234567, 998935554433"})
    db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active) VALUES(77001234, 'Sardor Karimov', "
               "'sardor_k', ?, 1)", (time.time(),))
    db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active) VALUES(-1001234567, 'Kassa', "
               "'admin', ?, 1)", (time.time(),))
    db.set_settings({"pilot_poll_state": json.dumps({"week": 1, "chats": [77001234], "tries": 1})})
    secret_tok = ":".join(["1234567890", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"])     # soxta; repoda butun holda yo'q
    (config.LOGS_DIR / "app.log").write_text(
        f"INFO boshlandi\nPOST https://api.telegram.org/bot{secret_tok}/sendMessage\n"
        f"kamera rtsp://admin:juda_maxfiy@10.0.0.5:554/s ulandi\nCLOUD password=hunter2 token: abcdef123456\n"
        f"Yangi obunachi: Sardor Karimov (77001234) @sardor_k\nSMS +998901234567 ga yuborildi\n"
        f"kamera rtsp://admin:Admin@123@10.0.0.6/s\nAlisher Kassa kamera admin paneli -1001234567\n"
        f"2026-10-18 INFO hushyor.telegram: Hodisa #12: tasdiqlangan (Akmaljon)\n"
        f"2026-10-18 INFO hushyor.telegram: Hodisa #13: qaror bekor qilindi (Akmaljon)\n"
        f"2026-10-18 INFO hushyor.telegram: Navbat signallari o'chirildi: Akmaljon\n", encoding="utf-8")
    data, name = diagnostics.build()
    z = zipfile.ZipFile(io.BytesIO(data))
    allt = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "replace")
    info = json.loads(z.read("info.json"))
    ok("ZIP: info.json, README.txt, jurnallar", {"info.json", "README.txt", "logs/app.log"} <= set(z.namelist())
       and name.endswith(".zip") and "Do_kon_1" in name, str(z.namelist()))
    ok("Maxfiy qiymatlar yo'q: bot tokeni, RTSP paroli, password=, ulanish kodi",
       secret_tok not in allt and "juda_maxfiy" not in allt and "hunter2" not in allt and "482913" not in allt
       and "abcdef123456" not in allt and "<TELEGRAM_TOKEN>" in allt and "rtsp://***@10.0.0.5" in allt
       and "Admin@123" not in allt and "@123@" not in allt)
    ok("Obunachilar chat raqami, ismi, username'i va SMS telefonlari yo'q", "77001234" not in allt
       and "Sardor Karimov" not in allt and "sardor_k" not in allt and "998901234567" not in allt
       and "998935554433" not in allt and "<chat>" in allt and "-1001234567" not in allt and "Akmaljon" not in allt
       and "Hodisa #12: tasdiqlangan (<ism>)" in allt)
    lg = z.read("logs/app.log").decode()
    ok("Bitta so'zli ism / guruh nomi va oddiy so'zlar buzilmaydi (Alisher, Kassa kamera, admin)",
       "Alisher Kassa kamera admin paneli" in lg and info["settings"]["pilot_poll_state"] == "<yashirilgan>", lg[-200:])
    ok("info.json: versiya, tizim, kameralar, tayyorlik, darvozalar, oxirgi 7 kun",
       info["app_version"] == config.APP_VERSION and info["cameras"] and info["readiness"]["items"]
       and len(info["gates"]) == 7 and "last_7_days" in info and info["settings"]["connect_code"] == "<berilgan>"
       and info["settings"]["store_name"] == "Do'kon 1")
    ok("redact(): Bearer va ulanish manzili", diagnostics.redact("Authorization: Bearer abc.def-123456") ==
       "Authorization: Bearer <TOKEN>" and diagnostics.redact("http://u:p@h/x") == "http://***@h/x")
    c = TestClient(web.app)
    ok("Kirmagan foydalanuvchi — 401", TestClient(web.app).get("/api/pilot/summary").status_code == 401
       and TestClient(web.app).get("/api/diagnostics.zip").status_code == 401)
    c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"}, follow_redirects=False)
    p = c.get("/pilot")
    ok("Pilot sahifasi va menyuda 'Pilot'", p.status_code == 200 and "Tayyorlik ro'yxati" in p.text
       and 'href="/pilot"' in p.text)
    ok("API: tayyorlik, darvozalar, xulosa", c.get("/api/pilot/readiness?force=1").json()["items"]
       and len(c.get("/api/pilot/gates").json()["gates"]) == 7
       and c.get("/api/pilot/summary").json()["gate"]["state"] == "boshlanmagan")
    ok("Yakunlash — boshlanmagan pilot 400; kelajakdagi / juda eski / noto'g'ri sana 400",
       c.post("/api/pilot/stop").status_code == 400
       and c.post("/api/pilot/start", json={"date": "2099-01-01"}).status_code == 400
       and c.post("/api/pilot/start", json={"date": "2000-01-01"}).status_code == 400
       and c.post("/api/pilot/start", json={"date": "19.10.2026"}).status_code == 400)
    d = time.strftime("%Y-%m-%d", time.localtime(time.time() - 14 * 86400))
    r = c.post("/api/pilot/start", json={"date": d})
    s = c.get("/api/pilot/summary").json()
    ok("Sana bilan boshlash: o'sha kun yarim tunidan, haftalar bor", r.status_code == 200
       and abs(s["since"] - time.mktime(time.strptime(d, "%Y-%m-%d"))) < 1 and s["weeks"], str(r.json()))
    w = s["weeks"][0]["week"]
    ok("Panelda baho: noto'g'ri baho / hafta — 400, to'g'risi saqlanadi",
       c.post("/api/pilot/feedback", json={"week": w, "rating": 7}).status_code == 400
       and c.post("/api/pilot/feedback", json={"week": w + 3600, "rating": 4}).status_code == 400
       and c.post("/api/pilot/feedback", json={"week": w}).status_code == 400
       and c.post("/api/pilot/feedback", json={"week": "nan", "rating": 4}).status_code == 400
       and c.post("/api/pilot/feedback", json={"week": w + 70 * 86400, "rating": 4}).status_code == 400
       and c.post("/api/pilot/feedback", json={"week": w, "rating": 5, "comment": "zo'r"}).status_code == 200
       and db.row("SELECT rating, comment FROM pilot_feedback WHERE who LIKE 'panel:%'")["comment"] == "zo'r")
    r = c.get("/api/pilot/report.pdf")
    ok("PDF yuklab olish", r.status_code == 200 and r.headers["content-type"] == "application/pdf" and pdf_ok(r.content))
    r = c.get("/api/diagnostics.zip")
    ok("Diagnostika ZIP yuklab olish", r.status_code == 200 and r.headers["content-type"] == "application/zip"
       and zipfile.ZipFile(io.BytesIO(r.content)).read("info.json"))
    ok("Yakunlash; qayta yakunlash — 400", c.post("/api/pilot/stop").status_code == 200 and pilot.since_until()[1]
       and c.post("/api/pilot/stop").status_code == 400)


# ============================================================================ 5. edge → bulut
def cloud_suite() -> None:
    print("\n--- Edge → bulut: tayyorlik filiallar ro'yxatida ---")
    from app import sync

    clear()
    pilot.start(time.time() - 5 * 86400)
    w = sync.SyncWorker(url="http://127.0.0.1:1", token="x")
    sent = []
    w._post = lambda path, payload=None, files=None, timeout=30.0: (sent.append(payload) or (200, {"ok": True}))
    w.hello()
    rd = (sent[0] if sent else {}).get("readiness") or {}
    ok("Edge hello: tayyorlik (ok/total/bad) va pilot kuni", rd.get("total") and "Panel paroli" in rd.get("bad", [])
       and rd.get("pilot", {}).get("days") == 5, str(rd)[:200])
    from fastapi.testclient import TestClient

    from cloud import notify
    from cloud import server as cloud

    def run(label):
        cdb = cloud.cdb
        cdb.reset_engine()
        cdb.meta.drop_all(cdb.engine())
        cloud.init_db()
        notify.cfg = notify.Config(tz_offset=5, offline_sec=0, escalate_sec=0)
        cloud._state.update(tokens=[], admin="superadmin-sinov-tokeni-uzunroq-1234567", fail=False, fail_paths=(),
                            notify_configured=True, no_notify=True)
        cloud._login_fails.clear()
        c = TestClient(cloud.app)
        a = cloud.bootstrap_owner("egasi@p.uz", "Parol12345", "P savdo")
        b1, t1 = cdb.create_branch(a["company_id"], "Chilonzor", "devP1")
        b2, _t2 = cdb.create_branch(a["company_id"], "Yunusobod", "devP2")
        junk = {**rd, "bad": ["<b>x</b>" * 30] + list(range(20)), "ok": "abc", "pilot": {"since": "x"}}
        r = c.post("/api/v1/edge/hello", json={"time": time.time(), "version": config.APP_VERSION, "cameras": [],
                                               "readiness": rd}, headers={"Authorization": f"Bearer {t1}",
                                                                          "X-Device-Id": "devP1"})
        tok = c.post("/api/v1/auth/login", json={"email": "egasi@p.uz", "password": "Parol12345"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        bs = {x["name"]: x for x in c.get("/api/v1/branches", headers=h).json()["branches"]}
        x = bs["Chilonzor"]["readiness"]
        ok(f"{label}: bulut tayyorlikni saqladi va filiallar ro'yxatida beradi", r.status_code == 200 and x
           and x["total"] == rd["total"] and x["bad"] == rd["bad"][:8] and x["pilot"]["days"] == 5 and x["at"]
           and bs["Yunusobod"]["readiness"] is None, str(x)[:200])
        c.post("/api/v1/edge/hello", json={"time": time.time(), "cameras": [], "readiness": junk},
               headers={"Authorization": f"Bearer {t1}", "X-Device-Id": "devP1"})
        x = {b["name"]: b for b in c.get("/api/v1/branches", headers=h).json()["branches"]}["Chilonzor"]["readiness"]
        ok(f"{label}: noto'g'ri qiymatlar tozalanadi (≤ 8 ta, ≤ 80 belgi, son, pilot yo'q)", len(x["bad"]) == 8
           and all(len(s) <= 80 for s in x["bad"]) and x["ok"] == 0 and x["pilot"] is None)
        c.post("/api/v1/edge/hello", json={"time": time.time(), "cameras": []},
               headers={"Authorization": f"Bearer {t1}", "X-Device-Id": "devP1"})
        x = {b["name"]: b for b in c.get("/api/v1/branches", headers=h).json()["branches"]}["Chilonzor"]["readiness"]
        ok(f"{label}: eski edge (tayyorliksiz hello) — oxirgi holat saqlanadi", x is not None)
        ok(f"{label}: bulut va edge versiyasi bir xil", cloud.VERSION == config.APP_VERSION)
    run("SQLite")
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        run("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)


def main() -> int:
    t = time.time()
    db.init_db()
    readiness_suite()
    pilot_suite()
    telegram_suite()
    diag_api_suite()
    cloud_suite()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
