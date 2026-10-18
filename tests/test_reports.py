"""2.4.0 (8-bosqich) sinovi: kunlik/haftalik hisobot — davrlar, raqamlar (panel manbalari bilan bir xil), tavsiya
qoidalari, matn va PDF, Telegram'ga yuborish (matn + hujjat), tavsiyaga baho, /hisobot, rejalashtiruvchi (09:00,
dushanba, kechikib yoqilgan dastur, ikki marta yubormaslik), panel API, darvoza va buyruq qatori.

    python -m tests.test_reports
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_rep_")   # hech qachon jonli data/ emas
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, pdfmini, report_eval, reports  # noqa: E402
from tests import mock_telegram as tg  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def wait(cond, timeout=10.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


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


# ============================================================================ ma'lumot
def seed(t0: float, scale: int = 1, cam_gap: tuple[int, int] | None = None, sys_gap: tuple[int, int] | None = None,
         rejected: int = 2, confirmed: int = 3, unreviewed: int = 1, falls: int = 1) -> dict:
    """Bir kunlik ma'lumot (t0 — yarim tun): sanash, navbat, signallar, ko'rsatkichlar."""
    src = db.row("SELECT id FROM sources WHERE name='Kassa kamera'")
    if src:
        sid = src["id"]
    else:
        sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                         ("Kassa kamera", "rtsp", "rtsp://x/1", 1, 1, t0 - 30 * 86400))
        db.execute("INSERT INTO zones(source_id, kind, name, points, enabled) VALUES(?,?,?,?,1)",
                   (sid, "chiziq", "Eshik", json.dumps([[0, .7], [1, .7]])))
        db.execute("INSERT INTO zones(source_id, kind, name, points, enabled) VALUES(?,?,?,?,1)",
                   (sid, "navbat", "1-kassa", json.dumps([[.1, .4], [.9, .4], [.9, .9], [.1, .9]])))
    zq = db.row("SELECT id FROM zones WHERE kind='navbat'")["id"]
    rows = []
    for h in range(9, 22):                                  # 09–22: soatiga 10 × scale kirdi, 10 × scale chiqdi
        n = (30 if h == 18 else 10) * scale
        for i in range(n):
            t = t0 + h * 3600 + (i + 0.5) * 3600 / n
            rows.append((t, sid, "Kassa kamera", 1, "Eshik", "in", i, 0, 0))
            rows.append((t + 60, sid, "Kassa kamera", 1, "Eshik", "out", i, 0, 0))
    with db.tx() as c:
        c.executemany("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        q = []
        for h in range(9, 22):
            for m in range(60):
                minute = int(t0 + h * 3600 + m * 60)
                long_ = 40.0 if h in (17, 18) else 0.0          # 17–19 da navbat uzun
                q.append((minute, zq, "1-kassa", sid, "Kassa kamera", 60.0, 120.0 + long_ * 3, 6 if long_ else 2, long_,
                          40.0, 0.0, 0, 1, 90.0 if long_ else 60.0, 1, 200.0 if long_ else 80.0))
        c.executemany("INSERT OR REPLACE INTO queue_minute(minute, zone_id, zone_name, source_id, source_name, t_obs, "
                      "len_sum, len_max, long_sec, busy_sec, cashier_sec, cashier_known, served, wait_sum, wait_n, "
                      "wait_max) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", q)
        mm = []
        for m in range(1440):
            minute = int(t0 + m * 60)
            if sys_gap and sys_gap[0] <= m < sys_gap[1]:
                continue                                      # dastur o'chiq
            mm.append((minute, 0, "tizim", None, None, None, None, None, None, 10.0, 20.0, None))
            up = 0.0 if cam_gap and cam_gap[0] <= m < cam_gap[1] else 1.0
            mm.append((minute, sid, "Kassa kamera", 1.0, 2, 5.0, 0.5, up, 0, None, None, None))
        c.executemany("INSERT OR REPLACE INTO metrics_minute(minute, source_id, source_name, persons_avg, persons_max, "
                      "fps_ai, active_pct, up_pct, alerts, cpu, ram, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", mm)
    k = 0
    for st, n in (("rad_etilgan", rejected), ("tasdiqlangan", confirmed), ("yangi", unreviewed)):
        for _ in range(n):
            k += 1
            db.insert_event({"source_id": sid, "source_name": "Kassa kamera", "created_at": t0 + 10 * 3600 + k * 60,
                             "score": 70, "level": "o'rta", "kind": "pocket", "reasons": ["x"], "breakdown": [],
                             "track_id": k, "status": st})
    for _ in range(falls):
        db.insert_event({"source_id": sid, "source_name": "Kassa kamera", "created_at": t0 + 15 * 3600, "score": 100,
                         "level": "yuqori", "kind": "fall", "reasons": ["Odam yiqildi"], "breakdown": [], "track_id": 99,
                         "status": "yangi"})
    db.insert_event({"source_id": None, "source_name": "video.mp4", "created_at": t0 + 11 * 3600, "score": 90,
                     "level": "yuqori", "kind": "bag", "reasons": ["x"], "breakdown": [], "track_id": 1,
                     "status": "rad_etilgan", "job_id": 5})       # video tahlili — hisobotga kirmaydi
    return {"sid": sid}


def clear() -> None:
    for t in ("counts", "queue_minute", "metrics_minute", "events", "reports", "report_feedback", "report_sends",
              "queue_alerts"):
        db.execute(f"DELETE FROM {t}")


# ============================================================================ 1. davr va raqamlar
def period_suite() -> None:
    print("\n--- Davrlar ---")
    now = time.mktime((2026, 10, 21, 9, 5, 0, 0, 0, -1))           # chorshanba 09:05
    t0, t1 = reports.period("daily", now)
    ok("Kunlik: kecha 00:00–24:00", time.localtime(t0)[:4] == (2026, 10, 20, 0) and t1 - t0 == 86400)
    w0, w1 = reports.period("weekly", now)
    ok("Haftalik: o'tgan dushanba–yakshanba", time.localtime(w0)[:3] == (2026, 10, 12) and time.localtime(w0).tm_wday == 0
       and time.localtime(w1)[:3] == (2026, 10, 19) and w1 - w0 == 7 * 86400)
    p0, p1 = reports.prev_period(t0, t1)
    ok("Solishtirish: o'tgan haftaning shu kuni", t0 - p0 == 7 * 86400 and time.localtime(p0).tm_wday == time.localtime(t0).tm_wday)
    ok("Yorliq: sana va hafta kuni", reports.period_label("daily", t0, t1) == "20.10.2026, seshanba",
       reports.period_label("daily", t0, t1))
    ok("Soatlar oralig'i birlashtiriladi", reports._ranges([9, 10, 11, 17, 18]) == [(9, 12), (17, 19)])
    ok("Foiz o'zgarish: kichik bazada — yo'q", reports.pct_change(30, 10) is None and reports.pct_change(150, 100) == 50.0)
    d0 = reports.day_start(now)
    q0, q1 = reports.prev_period(d0, d0 + 15 * 3600)
    ok("Tugamagan kun (/hisobot, 15:00 gacha) — o'tgan haftaning shu kuni ham 15:00 gacha",
       d0 - q0 == 7 * 86400 and q1 - q0 == 15 * 3600)
    ok("Tavsiya turi: navbat soatlari oralig'i farqi — bitta tur", reports.rec_type("queue:3:17-19") == "queue:3"
       and reports.rec_type("queue:p90") == "queue:p90" and reports.rec_type("cam:2") == "cam:2"
       and report_eval.rec_type("queue:3:18-20") == "queue:3")


def numbers_suite() -> None:
    print("\n--- Raqamlar (panel manbalari bilan bir xil) ---")
    from app.ai import counting, queues

    clear()
    t0, t1 = reports.period("daily")
    db.set_settings({"work_hours": "09:00-22:00", "queue_max_len": "4", "store_name": "Test do'kon"})
    seed(t0, cam_gap=(10 * 60, 11 * 60 + 30), sys_gap=(20 * 60, 20 * 60 + 45))
    seed(t0 - 7 * 86400, scale=2)                                   # o'tgan haftaning shu kuni: 2 barobar ko'p
    d = reports.compute("daily", t0, t1)
    v = d["visitors"]
    s = counting.summary(t0, t1)
    ok("Tashriflar = counting.summary (Tashriflar sahifasi manbai)", v["in"] == s["in"] == 150 and v["out"] == s["out"],
       f"{v['in']} / {s['in']}")
    ok("O'tgan haftaga nisbatan −50%", v["prev_in"] == 300 and v["change_pct"] == -50.0, str(v["change_pct"]))
    ok("Eng gavjum soat 18:00 va soatlik taqsimot", time.localtime(v["busiest_hour"]).tm_hour == 18 and v["hours"][18] == 30
       and sum(v["hours"]) == 150)
    q = d["queues"]
    qs = queues.summary(t0, t1)
    ok("Navbat = queues.summary (Navbat sahifasi manbai)", q["has"] and q["served"] == qs["served"] and q["avg_wait"] == qs["avg_wait"])
    ok("Uzun navbat soatlari: 1-kassa 17:00–19:00", q["long_zones"] and q["long_zones"][0]["ranges"] == [(17, 19)],
       str(q["long_zones"]))
    th = d["theft"]
    ok("O'g'irlik signallari: 6 (✅3 ❌2 🟡1), video tahlili kirmaydi, yiqilish alohida",
       (th["total"], th["confirmed"], th["rejected"], th["unreviewed"]) == (6, 3, 2, 1)
       and d["safety"] and d["safety"][0]["kind"] == "fall" and d["safety_unreviewed"] == 1, str(th))
    cams = d["cameras"]
    c = cams["cams"][0]
    ok("Kamera ish vaqtida 90 daqiqa ishlamadi (dastur o'chiq 45 daqiqa alohida)", c["down_min"] == 90
       and cams["app_off_min"] == 45 and cams["work_min"] == 13 * 60, f"{c['down_min']} / {cams['app_off_min']}")
    keys = [r["key"] for r in d["recs"]]
    ok("Tavsiyalar: uzun navbat, kamera, dastur o'chiq, xavfsizlik javobsiz, tashriflar kamaydi",
       {"queue:%d:17-19" % q["long_zones"][0]["zone_id"], "cam:%d" % c["source_id"], "app_off", "safety_unrev",
        "visitors:drop"} <= set(keys), str(keys))
    ok("Avval ogohlantirishlar (warn), ko'pi bilan 6 ta", len(d["recs"]) <= 6
       and [r["level"] for r in d["recs"]] == sorted([r["level"] for r in d["recs"]], key=lambda x: x != "warn"))
    clear()
    seed(t0, rejected=8, confirmed=1, unreviewed=0, falls=0)
    keys = [r["key"] for r in reports.compute("daily", t0, t1)["recs"]]
    ok("Xato signal ulushi 89% (≥ 5 javob) → sezgirlik tavsiyasi; javobsiz kam — eslatma yo'q",
       "false" in keys and "unreviewed" not in keys, str(keys))
    clear()
    seed(t0, rejected=0, confirmed=1, unreviewed=4, falls=0)
    keys = [r["key"] for r in reports.compute("daily", t0, t1)["recs"]]
    ok("Javobsiz signallar ko'p → eslatma", "unreviewed" in keys and "false" not in keys, str(keys))
    w0, w1 = reports.period("weekly")
    clear()
    for i in range(7):
        seed(reports.day_start(w0 + i * 86400 + 43200), scale=1 + (i == 5))
    dw = reports.compute("weekly", w0, w1)
    ok("Haftalik: 7 kun, eng gavjum kun — shanba, uzun navbat soatlari kun soati bo'yicha",
       len(dw["visitors"]["days"]) == 7 and time.localtime(dw["visitors"]["busiest_day"]).tm_wday == 5
       and dw["queues"]["long_zones"][0]["ranges"] == [(17, 19)], str([x["in"] for x in dw["visitors"]["days"]]))


def render_suite() -> None:
    print("\n--- Matn va PDF ---")
    clear()
    t0, t1 = reports.period("daily")
    seed(t0, cam_gap=(10 * 60, 11 * 60 + 30))
    d = reports.compute("daily", t0, t1)
    s = reports.text(d, 7)
    ok("Telegram matni: do'kon, sana, tashrif, navbat, signallar, kamera, tavsiyalar; ≤ 4000 belgi",
       "Test do'kon" in s and "Tashriflar: <b>150</b>" in s and "Navbat:" in s and "✅ 3, ❌ 2, 🟡 1" in s
       and "Kassa kamera 1 soat 30 daq" in s and "Tavsiyalar" in s and len(s) <= 4000, s[:200])
    mk = reports.markup(d, 7)
    cb = mk["inline_keyboard"][0][0]["callback_data"] if mk else ""
    ok("Har tavsiyaga 👍/👎 tugmasi (tavsiya kaliti xeshi bilan, ≤ 64 bayt)", mk and len(mk["inline_keyboard"]) == len(d["recs"])
       and cb == f"rf:7:{reports.rec_ref(d['recs'][0]['key'])}:1" and all(
           len(b["callback_data"].encode()) <= 64 for row in mk["inline_keyboard"] for b in row), cb)
    b = reports.pdf(d)
    ok("PDF tuzilishi to'g'ri, ichida ustunli grafik va jadval", pdf_ok(b) and b.count(b" 0 obj") >= 5
       and b"/FlateDecode" in b, f"{len(b)} bayt")
    doc = pdfmini.Doc("t")
    doc.bars([0, 3, 10, 0], ["a", "b", "c", "d"], title="x")
    doc.table(["A", "B"], [["juda uzun nom " * 10, 1]], [120, 60])
    body = "\n".join(doc.pages[0])
    ok("pdfmini: ustunlar (re f) va qirqilgan jadval katagi", body.count(" re f") == 2 and "juda uzun nom juda" in body
       and "juda uzun nom " * 10 not in body)
    d0 = reports.compute("daily", t0 - 30 * 86400, t1 - 30 * 86400)
    ok("Dastur hali o'rnatilmagan kun — 'o'chiq' deyilmaydi (birinchi ishga tushishdan oldin)", not d0["recs"]
       and d0["cameras"]["app_off_min"] == 0, str([r["key"] for r in d0["recs"]]))
    db.execute("INSERT INTO metrics_minute(minute, source_id, source_name) VALUES(?, 0, 'tizim')", (int(t0 - 40 * 86400),))
    d0 = reports.compute("daily", t0 - 30 * 86400, t1 - 30 * 86400)
    ok("Bo'sh kun (dastur ishlamagan): faqat 'dastur o'chiq' tavsiyasi, matn va PDF baribir",
       [r["key"] for r in d0["recs"]] == ["app_off"] and "Tashriflar: <b>0</b>" in reports.text(d0)
       and pdf_ok(reports.pdf(d0)), str([r["key"] for r in d0["recs"]]))
    long = dict(d, recs=[{"key": f"k{i}", "level": "info", "text": "<b>" + "x" * 900} for i in range(6)])
    s = reports.text(long)
    ok("Juda uzun matn butun qatorlar bilan qisqaradi (HTML teg buzilmaydi)", len(s) <= 4000 and s.endswith("…")
       and "&lt;b&gt;" in s and s.count("<b>") == s.count("</b>"), str(len(s)))


# ============================================================================ 2. Telegram va rejalashtiruvchi
def telegram_suite() -> None:
    print("\n--- Telegram: yuborish, baho, /hisobot, rejalashtiruvchi ---")
    from app import telegram_bot as tb

    port = free_port()
    tg.serve(port)
    tg.CALLS.clear()
    db.execute("DELETE FROM subscribers")
    for cid, rep in ((5001, 1), (5002, 0)):
        db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active, kb_cleared, reports) "
                   "VALUES(?,?,?,?,1,1,?)", (cid, "Q", "q", time.time(), rep))
    bot = tb.TelegramBot(token="777:TEST", api_base=f"http://127.0.0.1:{port}")
    clear()
    t0, t1 = reports.period("daily")
    seed(t0)
    rid = reports.generate("daily", t0, t1, due_at=time.time() - 60)
    n = reports.send(rid, bot)
    msgs = [c for c in tg.calls("sendMessage") if c["data"].get("chat_id") == 5001]
    docs = tg.calls("sendDocument")
    ok("Hisobot faqat hisobot yoqilgan obunachiga: matn + PDF hujjat", n == 1 and len(msgs) == 1 and len(docs) == 1
       and not [c for c in tg.calls("sendMessage") if c["data"].get("chat_id") == 5002]
       and docs[0]["files"]["document"]["head"].startswith("255044462d"), str(n))
    r = reports.get(rid)
    ok("Yuborilgan vaqt va son saqlandi", r["sent_at"] and r["sent_n"] == 1)
    k0, k1 = r["data"]["recs"][0]["key"], r["data"]["recs"][1]["key"]
    h0, h1 = reports.rec_ref(k0), reports.rec_ref(k1)

    def cbk(cid, data):
        bot._on_callback({"id": cid, "data": data, "from": {"id": 5001, "first_name": "Ali"},
                          "message": {"message_id": 1, "chat": {"id": 5001}}})
    cbk("c1", f"rf:{rid}:{h0}:1")
    cbk("c2", f"rf:{rid}:{h0}:0")
    cbk("c3", f"rf:{rid}:{h1}:1")
    fb = reports.get(rid)["feedback"]
    ok("Baho: bir kishi fikrini o'zgartirsa — oxirgisi qoladi", fb[k0] == {"up": 0, "down": 1} and fb[k1]["up"] == 1, str(fb))
    tg.CALLS.clear()
    cbk("c3b", f"rf:{rid}:0123456789:1")
    ok("Noma'lum tavsiya belgisi — 'topilmadi', baho yozilmaydi",
       any("topilmadi" in (c["data"].get("text") or "") for c in tg.calls("answerCallbackQuery"))
       and len(reports.get(rid)["feedback"]) == 2)
    # Yuborilgandan keyin qayta yaratish: ma'lumot va tugmalar o'zgarmaydi
    data0 = json.dumps(reports.get(rid)["data"]["recs"])
    db.insert_event({"source_id": 1, "source_name": "x", "created_at": t0 + 20 * 3600, "score": 70, "level": "o'rta",
                     "kind": "fall", "reasons": [], "breakdown": [], "track_id": 1, "status": "yangi"})
    rid2 = reports.generate("daily", t0, t1)
    ok("Yuborilgan hisobot qayta hisoblanmaydi (Telegram'dagi xabar va 👍/👎 tugmalari o'sha nusxa bilan)",
       rid2 == rid and json.dumps(reports.get(rid)["data"]["recs"]) == data0)
    db.execute("DELETE FROM events WHERE created_at=? AND kind='fall'", (t0 + 20 * 3600,))
    bot._on_callback({"id": "c4", "data": "rp:off", "from": {"id": 5001, "first_name": "Ali"},
                      "message": {"message_id": 1, "chat": {"id": 5001}}})
    ok("Telegram'dan har kungi hisobotni o'chirish", db.scalar("SELECT reports FROM subscribers WHERE chat_id=5001") == 0)
    db.execute("UPDATE subscribers SET reports=1 WHERE chat_id=5001")
    tg.CALLS.clear()
    bot._on_message({"chat": {"id": 5001}, "text": "/hisobot", "from": {"id": 5001}})
    m = [c for c in tg.calls("sendMessage") if c["data"].get("chat_id") == 5001]
    ok("/hisobot: bugungi holat + tugmalar (kechagi, o'tgan hafta, o'chirish)", m and "Bugungi holat" in m[-1]["data"]["text"]
       and "rp:daily" in json.dumps(m[-1]["data"].get("reply_markup")), str(m[-1]["data"]["text"][:80]) if m else "")
    tg.CALLS.clear()
    sent0 = reports.get(rid)["sent_at"]
    bot._on_callback({"id": "c5", "data": "rp:daily", "from": {"id": 5001, "first_name": "Ali"},
                      "message": {"message_id": 1, "chat": {"id": 5001}}})
    ok("Kechagi PDF tugmasi — faqat so'ragan chatga (rejadagi yuborish hisobiga kirmaydi)",
       wait(lambda: tg.calls("sendDocument"), 10) and reports.get(rid)["sent_at"] == sent0
       and db.scalar("SELECT tries FROM report_sends WHERE report_id=? AND chat_id=5001", (rid,)) == 1)
    # Yetkazish: PDF yetmasa — matn qayta yuborilmaydi; bir chatga yetmasa — faqat unga qayta
    db.execute("UPDATE subscribers SET reports=1 WHERE chat_id=5002")
    clear()
    seed(t0)
    tg.CALLS.clear()
    tg.FAIL_NEXT["sendDocument"] = 50
    rid = reports.generate("daily", t0, t1)
    n = reports.send(rid, bot)
    tg.FAIL_NEXT.clear()
    rows = {x["chat_id"]: x for x in db.rows("SELECT * FROM report_sends WHERE report_id=?", (rid,))}
    ok("PDF yetmadi — matn yetgan chat yetkazilgan (PDF yo'q deb belgilanadi)", n == 2 and len(rows) == 2
       and all(x["sent_at"] and x["pdf"] == 0 and "PDF" in (x["error"] or "") for x in rows.values())
       and reports.get(rid)["sent_at"], str(rows))
    ok("... va rejalashtiruvchi matnni takror yubormaydi", reports.pending_chats(rid) == [])
    clear()
    seed(t0)
    tg.CALLS.clear()
    rid = reports.generate("daily", t0, t1)
    real_api = bot.api

    def flaky(method, data=None, *a, **kw):
        if method == "sendMessage" and int((data or {}).get("chat_id", 0)) == 5002:
            return {"ok": False, "description": "soxta xato"}
        return real_api(method, data, *a, **kw)
    bot.api = flaky
    n1 = reports.send(rid, bot)
    p1 = reports.pending_chats(rid)
    bot.api = real_api
    tg.CALLS.clear()
    n2 = reports.send(rid, bot)
    ok("Bir chatga yetmadi — keyingi urinish faqat unga (yetgan chatga takror yo'q)", n1 == 1 and p1 == [5002] and n2 == 1
       and [c["data"].get("chat_id") for c in tg.calls("sendMessage")] == [5002] and reports.pending_chats(rid) == [],
       f"{n1} {p1} {n2}")
    db.execute("UPDATE report_sends SET sent_at=NULL, tries=20, perm=0 WHERE report_id=? AND chat_id=5002", (rid,))
    ok("Tarmoq/Telegram xatosi — o'sha kun davomida qayta uriniladi (urinishlar soni cheklanmagan)",
       reports.pending_chats(rid) == [5002])
    db.execute("UPDATE report_sends SET tries=?, perm=1 WHERE report_id=? AND chat_id=5002", (reports.PERM_TRIES, rid))
    ok("Doimiy xato (bot bloklangan) — PERM_TRIES dan keyin urinilmaydi", reports.pending_chats(rid) == [])
    clear()
    seed(t0)
    tg.CALLS.clear()
    rid = reports.generate("daily", t0, t1)
    tg.FAIL_STATUS["sendMessage"] = 403
    tg.FAIL_NEXT["sendMessage"] = 1
    reports.send(rid, bot)
    row = db.row("SELECT * FROM report_sends WHERE report_id=? AND sent_at IS NULL", (rid,))
    tg.FAIL_NEXT.clear()
    tg.FAIL_STATUS.clear()
    ok("Telegram 403 (bloklangan) — doimiy xato deb belgilanadi", row and row["perm"] == 1 and row["tries"] == 1, str(row))
    clear()
    seed(t0)
    tg.CALLS.clear()
    rid = reports.generate("daily", t0, t1)
    db.execute("UPDATE reports SET due_at=? WHERE id=?", (time.time() - 3600, rid))      # rejadagi vaqt o'tgan
    n = reports.send(rid, bot, mark=True, only_missing=False)       # paneldan "Telegram'ga yuborish"
    ok("Rejadagi vaqtdan keyin paneldan yuborish — rejalashtiruvchi takror yubormaydi; darvoza uni sanamaydi (sent_at yo'q)",
       n == 2 and reports.pending_chats(rid) == [] and not reports.get(rid)["sent_at"]
       and db.scalar("SELECT COUNT(*) FROM report_sends WHERE report_id=? AND auto=0", (rid,)) == 2)
    db.execute("UPDATE reports SET due_at=? WHERE id=?", (time.time() + 3600, rid))      # qo'lda — rejadagi vaqtdan oldin
    tg.CALLS.clear()
    n = reports.send(rid, bot)
    ok("Rejadagi vaqtdan oldin qo'lda yuborilgan — rejadagi nusxa baribir yuboriladi va darvozada sanaladi", n == 2
       and reports.get(rid)["sent_at"] and db.scalar("SELECT COUNT(*) FROM report_sends WHERE report_id=? AND auto=1",
                                                      (rid,)) == 2 and reports.pending_chats(rid) == [], str(n))
    today0 = reports.day_start()
    rid_t = reports.generate("daily", today0, reports.day_start(today0 + 36 * 3600))
    tg.CALLS.clear()
    n = reports.send(rid_t, bot, mark=True, only_missing=False)
    rt = reports.get(rid_t)
    ok("Tugamagan kun (bugun) paneldan yuborilsa — belgilanmaydi va muzlatilmaydi (ertangi to'liq hisobot to'silmaydi)",
       n == 2 and not rt["sent_at"] and not rt["frozen"] and not db.scalar(
           "SELECT COUNT(*) FROM report_sends WHERE report_id=?", (rid_t,)), str(rt.get("frozen")))
    db.set_settings({"report_time": "09:00"})
    clear()
    seed(t0)
    rid = reports.generate("daily", t0, t1)
    d9 = reports.get(rid)["due_at"]
    db.set_settings({"report_time": "11:00"})
    reports.generate("daily", t0, t1, due_at=reports.scheduled_due("daily", t1))
    ok("report_time o'zgarsa — rejalashtiruvchi rejadagi vaqtni yangilaydi (yuborilmagan hisobotda)",
       reports.get(rid)["due_at"] - d9 == 2 * 3600)
    db.set_settings({"report_time": "09:00"})
    cbk("c9", f"rf:{rid}:0:1")
    ok("Eski ko'rinishdagi tugma (tartib raqami) ham ishlaydi", reports.get(rid)["feedback"])
    db.execute("UPDATE subscribers SET reports=0 WHERE chat_id=5002")
    # Rejalashtiruvchi
    clear()
    tg.CALLS.clear()
    today = reports.day_start()
    db.set_settings({"report_daily": "1", "report_weekly": "1", "report_time": "09:00",
                     "report_since": str(today - 30 * 86400)})
    sch = reports.Scheduler(bot)
    ok("09:00 dan oldin — yuborilmaydi", sch.tick(today + 8 * 3600 + 3540) == [])
    got = sch.tick(today + 9 * 3600 + 30)
    ok("09:00 dan keyin — kechagi hisobot yuborildi", len(got) == 1 and reports.get(got[0])["kind"] == "daily"
       and reports.get(got[0])["t0"] == reports.period("daily", today + 9 * 3600)[0])
    ok("Ikkinchi marta yuborilmaydi", sch.tick(today + 9 * 3600 + 90) == [])
    late = reports.Scheduler(bot)
    clear()
    ok("Dastur kechikib yoqildi (14:00) — o'sha kuni baribir yuboriladi", len(late.tick(today + 14 * 3600)) == 1)
    mon = reports.week_start(today)
    clear()
    s2 = reports.Scheduler(bot)
    got = s2.tick(mon + 9 * 3600 + 60)
    kinds = sorted(reports.get(i)["kind"] for i in got)
    ok("Dushanba 09:00 — kunlik va haftalik", kinds == ["daily", "weekly"], str(kinds))
    clear()
    db.set_settings({"report_since": str(today + 60)})
    ok("Davr hisobot yoqilishidan oldin tugagan — avtomatik yuborilmaydi",
       reports.Scheduler(bot).tick(today + 9 * 3600 + 30) == [])
    db.set_settings({"report_since": str(today - 30 * 86400), "report_daily": "0", "report_weekly": "0"})
    ok("O'chirilgan — yuborilmaydi", reports.Scheduler(bot).tick(mon + 9 * 3600 + 30) == [])
    db.set_settings({"report_daily": "1", "report_weekly": "1"})
    db.execute("UPDATE subscribers SET active=0")
    clear()
    s3 = reports.Scheduler(bot)
    ok("Obunachi yo'q — hisoblanmaydi ham", s3.tick(today + 9 * 3600 + 30) == []
       and not db.scalar("SELECT COUNT(*) FROM reports"))
    db.execute("UPDATE subscribers SET active=1")
    real_due = reports._due
    reports._due = lambda kind, now: (_ for _ in ()).throw(RuntimeError("database is locked"))
    s4 = reports.Scheduler(bot)
    try:
        got = s4.tick(today + 9 * 3600 + 30)
        survived = True
    except Exception:  # noqa: BLE001
        got, survived = None, False
    reports._due = real_due
    ok("Baza xatosi (database is locked) — rejalashtiruvchi to'xtamaydi, 10 daqiqadan keyin qayta",
       survived and got == [] and s4.next_try.get("daily", 0) >= today + 9 * 3600 + 600)


# ============================================================================ 3. panel API va darvoza
def api_suite() -> None:
    print("\n--- Panel API ---")
    from fastapi.testclient import TestClient

    from app import web

    c = TestClient(web.app)
    c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"}, follow_redirects=False)
    clear()
    t0, t1 = reports.period("daily")
    seed(t0)
    r = c.post("/api/reports/generate", json={"kind": "daily"})
    rid = r.json().get("id")
    ok("Hisobotni hozir yaratish", r.status_code == 200 and rid)
    j = c.get(f"/api/reports/{rid}").json()
    ok("Hisobotni o'qish (ma'lumot + baholar)", j["data"]["visitors"]["in"] == 150 and "feedback" in j)
    p = c.get(f"/api/reports/{rid}/pdf")
    ok("PDF yuklab olish", p.status_code == 200 and p.headers["content-type"] == "application/pdf" and pdf_ok(p.content))
    ok("Ro'yxat", any(x["id"] == rid for x in c.get("/api/reports/list").json()["reports"]))
    r = c.post("/api/reports/generate", json={"kind": "daily", "date": time.strftime("%Y-%m-%d", time.localtime(t0))})
    ok("Sana bo'yicha — o'sha hisobot yangilanadi (takror yaratilmaydi)", r.json().get("id") == rid)
    ok("Noto'g'ri sana / tur — 400", c.post("/api/reports/generate", json={"kind": "daily", "date": "17.10"}).status_code == 400
       and c.post("/api/reports/generate", json={"kind": "oylik"}).status_code == 400)
    key0 = j["data"]["recs"][0]["key"] if j["data"]["recs"] else "x"
    ok("Baho (panel — tavsiya kaliti bilan; eski idx ham)", c.post(f"/api/reports/{rid}/feedback",
       json={"key": key0, "useful": 1}).status_code == 200
       and c.post(f"/api/reports/{rid}/feedback", json={"idx": 0, "useful": 1}).status_code == 200
       and c.post(f"/api/reports/{rid}/feedback", json={"key": "yo'q", "useful": 1}).status_code == 404
       and c.post(f"/api/reports/{rid}/feedback", json={"idx": 99, "useful": 1}).status_code == 404)
    r = c.post("/api/reports/settings", json={"daily": False, "time": "08:30"})
    s = c.get("/api/reports/settings").json()
    ok("Sozlamalar: kunlik o'chirildi, vaqt 08:30", r.status_code == 200 and not s["daily"] and s["time"] == "08:30")
    ok("Noto'g'ri vaqt — 400", c.post("/api/reports/settings", json={"time": "25:00"}).status_code == 400)
    c.post("/api/reports/settings", json={"daily": True, "time": "09:00"})
    ok("Telegram ulanmagan — yuborish 400", c.post(f"/api/reports/{rid}/send").status_code == 400)
    ok("Yo'q hisobot — 404", c.get("/api/reports/99999").status_code == 404)
    pg = c.get("/reports")
    ok("Hisobotlar sahifasida kunlik/haftalik hisobot bo'limi", pg.status_code == 200 and "Kunlik va haftalik hisobot" in pg.text)
    ok("Eski signal tahlili API o'zgarmagan", c.get("/api/reports?days=7").json().get("days") == 7)
    from fastapi.testclient import TestClient as TC

    ok("Kirmagan foydalanuvchi — 401", TC(web.app).get(f"/api/reports/{rid}").status_code == 401)


def gate_suite() -> None:
    print("\n--- Darvoza ---")
    clear()
    now = time.time()
    today = reports.day_start(now)
    since = today - 20 * 86400
    db.set_settings({"report_since": str(since)})

    def q(sql, p=()):
        return db.rows(sql, p)
    g = report_eval.gate(q, today - 3 * 86400 + 3600, now)
    ok("Yoqilganiga 3 kun — 'yetarli emas'", g["state"] == "yetarli emas" and len(g["days"]) == 2, str(len(g["days"])))
    for i in range(1, 15):
        d0 = reports.day_start(today - i * 86400 + 43200)
        seed(d0, falls=0)
        rid = reports.generate("daily", d0, reports.day_start(d0 + 36 * 3600), due_at=d0 + 86400 + 9 * 3600)
        if i != 3:
            db.execute("UPDATE reports SET sent_at=? WHERE id=?", (d0 + 86400 + 9 * 3600 + (600 if i != 5 else 400), rid))
    g = report_eval.gate(q, since, now)
    ok("14 kun, 1 kun o'tkazilgan, foydali tavsiya yo'q — hali o'tmadi", g["on_time"] == 13 and g["missed"] == 1
       and not g["mismatch"] and not g["ok"] and g["state"] == "yetarli emas", f"{g['on_time']} {g['state']}")
    rid = db.row("SELECT id FROM reports ORDER BY id DESC LIMIT 1")["id"]
    unsent = db.row("SELECT id FROM reports WHERE sent_at IS NULL")["id"]

    def vote(r_id, key, useful, who="x"):
        db.execute("INSERT OR REPLACE INTO report_feedback(report_id, rec_key, useful, who, t) VALUES(?,?,?,?,?)",
                   (r_id, key, useful, who, now))
    for key in ("queue:1:17-19", "queue:1:17-20", "queue:1:18-19"):
        vote(rid, key, 1)
    vote(unsent, "cam:5", 1)
    vote(rid, "app_off", 1)
    vote(rid, "app_off", 0, "y")
    g = report_eval.gate(q, since, now)
    ok("Bitta kassaning turli soatlari — 1 tavsiya; yuborilmagan hisobot va 👍=👎 sanalmaydi", g["useful"] == 1
       and not g["ok"], str(g["useful"]))
    vote(rid, "cam:2", 1)
    vote(rid, "false", 1)
    g = report_eval.gate(q, since, now)
    ok("3 xil foydali tavsiya — o'tdi", g["ok"] and g["state"] == "otdi", f"{g['state']} {g['useful']}")
    d0 = reports.day_start(today - 2 * 86400 + 43200)
    db.execute("DELETE FROM events WHERE created_at >= ? AND created_at < ? AND status='rad_etilgan'", (d0, d0 + 86400))
    g = report_eval.gate(q, since, now)
    ok("Hisobotdan keyin ❌ hodisalar o'chirildi — darvoza buzilmaydi (tekshiruv yaratilgan paytga nisbatan)",
       not g["mismatch"] and g["state"] == "otdi", str(g["mismatch"][:1]))
    r2 = db.row("SELECT id, data FROM reports WHERE t0=?", (d0,))
    bad = json.loads(r2["data"])
    bad["visitors"]["in"] += 7
    db.execute("UPDATE reports SET data=? WHERE id=?", (json.dumps(bad), r2["id"]))
    g = report_eval.gate(q, since, now)
    ok("Hisobot raqami bazadagidan farq qilsa — farq ko'rsatiladi, o'tmadi", g["mismatch"] and g["state"] == "otmadi",
       str(g["mismatch"][:1]))
    bad["visitors"]["in"] -= 7
    db.execute("UPDATE reports SET data=? WHERE id=?", (json.dumps(bad), r2["id"]))
    r3 = db.row("SELECT id FROM reports WHERE sent_at IS NULL")
    db.execute("INSERT INTO report_sends(report_id, chat_id, sent_at, auto) VALUES(?, 1, ?, 0)", (r3["id"], now))
    g = report_eval.gate(q, since, now)
    ok("Paneldan qo'lda yuborilgan kun — 'qo'lda yuborilgan', avtomatik yetkazish sifatida sanalmaydi",
       any(x["state"] == "qo'lda yuborilgan" for x in g["days"]) and g["missed"] == 1)
    db.execute("UPDATE reports SET sent_at=NULL WHERE t0=?", (reports.day_start(today - 7 * 86400 + 43200),))
    g = report_eval.gate(q, since, now)
    ok("2 kun o'tkazilgan — o'tmadi", g["missed"] == 2 and g["state"] == "otmadi")
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "report_gate.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    ok("tests/report_gate.py: HUSHYOR_DATA_DIR dagi baza, o'tmadi — kod 1", r.returncode == 1 and "OTMADI" in r.stdout,
       f"kod {r.returncode}: {(r.stdout + r.stderr)[-200:]}")


def misc_suite() -> None:
    print("\n--- Boshqa ---")
    ok("Versiya 2.4.0", config.APP_VERSION == "2.4.0")
    ok("Sukut sozlamalar: kunlik va haftalik yoqiq, 09:00", config.DEFAULT_SETTINGS["report_daily"] == "1"
       and config.DEFAULT_SETTINGS["report_weekly"] == "1" and config.DEFAULT_SETTINGS["report_time"] == "09:00")
    from app import telegram_bot as tb

    ok("Bot buyrug'i /hisobot va yordam matni", any(c["command"] == "hisobot" for c in tb.TelegramBot.COMMANDS)
       and "/hisobot" in tb.HELP)


def main() -> int:
    t = time.time()
    db.init_db()
    period_suite()
    numbers_suite()
    render_suite()
    telegram_suite()
    api_suite()
    gate_suite()
    misc_suite()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
