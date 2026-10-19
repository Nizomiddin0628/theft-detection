"""3.2.0 sinovi: dasturni to'liq ko'rib chiqishda topilgan kamchiliklar tuzatishlari.

Bo'limlar:
  1. Do'kon boti — signal yetkazilishi kafolati (tg_outbox): internet/Telegram ishlamasa, dastur qayta ishga tushsa ham
     xabar yo'qolmaydi; kechikkan xabar belgisi; matnli xabarlar; 403 (bloklangan); superguruhga ko'chish; HTML javob
     so'rovlar oqimini o'ldirmaydi; guruhdagi oddiy suhbatga javob yo'q.
  2. Do'kon paneli — Sozlamalar Telegram'ni o'chirmaydi, Host himoyasi (DNS rebinding), sessiyani bekor qilish,
     kursor bilan sahifalash, noto'g'ri JSON — 400, .env kodlashlari, hisoblagichlarda video sinovi yo'q.
  3. AI va xavfsizlik — polka zonasi signallarni o'chirmaydi, tungi kirish (tasodifiy aniqlashlar, statik buyum),
     kameralararo takror signal, oqim soati yopilishi, disk himoyasi, chala qolgan kliplar.
  4. Sinxronlash — bulut buyruqlari tartibi (✅ keyin ❌), hodisalar ko'rsatkichlardan oldin.
  5. Bulut — buzuq edge yozuvlari, video sinovi hodisalari hisoblagichda emas, bitta manzilga bitta xabar,
     superguruh, ikki xodim bir vaqtda (409), eski edge'ga summa, panel so'rov hajmi, klip chegarasi, siqilgan zaxira,
     taqqoslashda chala davr, /system kompaniya bo'yicha.

Ishga tushirish:  python -m tests.test_320
"""
from __future__ import annotations

import gzip
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


TG_PORT = _free_port()
EDGE_TG = "321:EDGE"
CLOUD_TG = "322:CLOUD"
os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_320_edge_")
os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_320_cloud_")
os.environ["TELEGRAM_BOT_TOKEN"] = EDGE_TG
os.environ["TELEGRAM_API_BASE"] = f"http://127.0.0.1:{TG_PORT}"
os.environ["CLOUD_TELEGRAM_BOT_TOKEN"] = ""
os.environ.pop("CLOUD_DB_URL", None)
os.environ.pop("CLOUD_URL", None)
os.environ.pop("EDGE_TOKEN", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import mock_telegram as tg  # noqa: E402

tg.serve(TG_PORT)

import numpy as np  # noqa: E402

from app import db  # noqa: E402
from app.config import CLIPS_DIR  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def wait(cond, timeout=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def tg_calls(method: str, token: str = EDGE_TG) -> list[dict]:
    return [c for c in tg.CALLS if c["method"] == method and c["token"] == token]


JPG = b"\xff\xd8\xff\xe0" + b"J" * 2000 + b"\xff\xd9"


def _event(kind="pocket", job_id=None, created_at=None, source_id=3) -> int:
    t = created_at or time.time()
    ev = db.insert_event({"source_id": source_id, "source_name": "Kassa", "job_id": job_id, "created_at": t,
                          "score": 80.0, "level": "yuqori", "kind": kind, "reasons": ["sinov"], "breakdown": {},
                          "track_id": 1, "status": "yangi", "t_detect": t})
    stem = f"ev{ev:05d}_t320"
    (CLIPS_DIR / f"{stem}.jpg").write_bytes(JPG)
    db.update_event(ev, thumb_path=f"{stem}.jpg", clip_error=1)        # klipsiz (faqat rasm) — oddiyroq
    return ev


# ============================================================================= 1. do'kon boti
def bot_suite() -> None:
    from app import telegram_bot as tb

    print("\n--- Do'kon boti: signal yetkazilishi kafolati ---")
    tb.OB_TICK = 0.3
    tb.OB_BACKOFF = (0.5, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0)
    tb.OB_LEASE = 3.0          # _outbox_tick ijarasi (_ob_add ning birinchi ijarasi — sukut, 300 s)
    db.set_settings({"telegram_enabled": "1", "telegram_send_video": "1", "telegram_min_score": "0",
                     "store_name": "Sinov 3.2"})
    # kb_cleared=1: bot ishga tushganda "menyu yangilandi" xabari yubormasin (u FAIL_NEXT ni yeb qo'yardi — beqaror sinov)
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active, kb_cleared) "
               "VALUES(7101,'Q','q',?,1,1)", (time.time(),))
    api = f"http://127.0.0.1:{TG_PORT}"
    bot = tb.TelegramBot(EDGE_TG, api)
    bot.start()
    wait(lambda: bot.me, 10)

    tg.FAIL_NEXT.update(sendPhoto=3, sendMessage=3)        # birinchi urinish (ichki 2 ta qayta urinish bilan) yiqiladi
    ev = _event()
    bot.notify_alert(ev)
    def snap():
        r_ = db.row("SELECT * FROM tg_outbox WHERE kind='alert' AND event_id=? AND attempts>=1", (ev,))
        return (r_, db.get_event(ev).get("telegram_sent")) if r_ else None
    row = wait(snap, 15, 0.02)
    ok("Telegram ishlamadi — xabar navbatda qoldi, 'yuborilgan' deb belgilanmadi", row and not row[1], str(row))
    got = wait(lambda: db.row("SELECT * FROM tg_messages WHERE event_id=? AND chat_id=7101", (ev,)), 20)
    ok("Aloqa tiklangach o'zi qayta yubordi, navbat bo'shadi, telegram_sent=1",
       got and not db.row("SELECT 1 FROM tg_outbox WHERE event_id=?", (ev,)) and db.get_event(ev).get("telegram_sent") == 1)

    # dastur qayta ishga tushdi — yetmagan xabar bazadan tiklanadi (qayta urinish vaqti uzoq: faqat qayta ishga tushish)
    tb.OB_BACKOFF = (600.0,) * 7
    tg.FAIL_NEXT.update(sendPhoto=3, sendMessage=3)
    ev2 = _event()
    bot.notify_alert(ev2)
    wait(lambda: db.row("SELECT 1 FROM tg_outbox WHERE event_id=? AND attempts>=1", (ev2,)), 15)
    bot.stop()
    time.sleep(0.5)
    ok("To'xtashda xabar yetmagan — navbatda (bazada) turibdi", db.row("SELECT 1 FROM tg_outbox WHERE event_id=?", (ev2,))
       and not db.row("SELECT 1 FROM tg_messages WHERE event_id=?", (ev2,)))
    bot2 = tb.TelegramBot(EDGE_TG, api)
    bot2.start()
    wait(lambda: bot2.me, 10)
    got2 = wait(lambda: db.row("SELECT * FROM tg_messages WHERE event_id=?", (ev2,)), 20)
    ok("Dastur qayta ishga tushgach yetmagan signal yuborildi", got2, str(got2))
    tb.OB_BACKOFF = (0.5, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0)

    # kechikkan xabar (aloqa uzoq yo'q edi)
    now = time.time()
    ev3 = _event(created_at=now - 3600)
    db.execute("INSERT INTO tg_outbox(kind, event_id, chat_id, attempts, next_try, created_at, expires_at) "
               "VALUES('alert', ?, 7101, 1, ?, ?, ?)", (ev3, now, now - 3600, now + 3600))
    late = wait(lambda: [c for c in tg_calls("sendPhoto") + tg_calls("sendMessage")
                         if "Kechikkan xabar" in str(c["data"].get("caption") or c["data"].get("text") or "")
                         and f"#{ev3}" in str(c["data"].get("caption") or c["data"].get("text") or "")], 15)
    ok("1 soat kech yetgan signal — '⏰ Kechikkan xabar' belgisi bilan", late)
    ev4 = _event(created_at=now - 3 * 86400)
    db.execute("INSERT INTO tg_outbox(kind, event_id, chat_id, attempts, next_try, created_at, expires_at) "
               "VALUES('alert', ?, 7101, 5, ?, ?, ?)", (ev4, now, now - 3 * 86400, now - 1))
    time.sleep(2)
    ok("Muddati o'tgan (24 soatdan eski) signal — yuborilmaydi, navbatdan chiqadi",
       not db.row("SELECT 1 FROM tg_outbox WHERE event_id=?", (ev4,))
       and not db.row("SELECT 1 FROM tg_messages WHERE event_id=?", (ev4,)))

    # birinchi yuborish hali navbatda (bajaruvchilar band) — ijara tugasa ham parallel ikkinchi nusxa qo'yilmaydi;
    # navbatda kutayotgan qayta urinish takror qo'yilmaydi
    import threading as th_

    bot2._outbox_tick = lambda: None          # ishlab turgan bot bu qatorlarni o'zi yuborib qo'ymasin (beqaror sinov)
    b3 = tb.TelegramBot(EDGE_TG, api)
    rec = []
    b3._enqueue = lambda prio, kind, a, b=None: rec.append((kind, a))
    ev5 = _event()
    b3._alert_done[ev5] = th_.Event()
    t_ = time.time()
    db.execute("INSERT INTO tg_outbox(kind, event_id, chat_id, attempts, next_try, created_at, expires_at) "
               "VALUES('alert', ?, 7101, 0, ?, ?, ?)", (ev5, t_ - 1, t_, t_ + 3600))
    tid = db.execute("INSERT INTO tg_outbox(kind, chat_id, text, attempts, next_try, created_at, expires_at) "
                     "VALUES('text', 7101, 'x', 1, ?, ?, ?)", (t_ - 1, t_, t_ + 3600))
    b3._outbox_tick()
    first_skip = [x for x in rec if x == ("alert", ev5)] == []
    b3._alert_done[ev5].set()
    db.execute("UPDATE tg_outbox SET next_try=? WHERE event_id=? OR id=?", (t_ - 1, ev5, tid))
    b3._outbox_tick()
    db.execute("UPDATE tg_outbox SET next_try=? WHERE event_id=? OR id=?", (t_ - 1, ev5, tid))
    b3._outbox_tick()                                  # avvalgisi hali navbatda — takror qo'yilmaydi
    ok("Birinchi yuborish tugamaguncha qayta urinish yo'q; navbatdagi vazifa takror qo'yilmaydi",
       first_skip and rec.count(("alert", ev5)) == 1 and rec.count(("otext", tid)) == 1, str(rec))
    db.execute("DELETE FROM tg_outbox WHERE event_id=? OR id=?", (ev5, tid))
    del bot2._outbox_tick

    # matnli xabar (kamera uzildi) — bir marta xato bo'lsa ham yetadi
    n0 = len([c for c in tg_calls("sendMessage") if "sinov matni 320" in str(c["data"].get("text"))])
    tg.FAIL_NEXT.update(sendMessage=3)
    bot2.send_text_all("📷 sinov matni 320", ttl=60)
    sent = wait(lambda: len([c for c in tg_calls("sendMessage") if "sinov matni 320" in str(c["data"].get("text"))])
                >= n0 + 4 and not db.row("SELECT 1 FROM tg_outbox WHERE kind='text'"), 15)
    ok("Kamera xabari Telegram xatosidan keyin qayta yuborildi (yo'qolmadi)", sent)

    # 403 — bot bloklangan: obunachi o'chiriladi
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active, kb_cleared) VALUES(7102,'B','b',?,1,1)",
               (time.time(),))
    db.execute("UPDATE subscribers SET active=0 WHERE chat_id=7101")
    tg.FAIL_STATUS["sendMessage"] = 403
    tg.FAIL_NEXT.update(sendMessage=1)
    bot2.send_text_all("blok sinovi")
    off = wait(lambda: db.scalar("SELECT active FROM subscribers WHERE chat_id=7102") == 0, 10)
    tg.FAIL_STATUS.pop("sendMessage", None)
    ok("Bot bloklangan (403) — obunachi o'chirildi, navbatda qolmaydi",
       off and not db.row("SELECT 1 FROM tg_outbox WHERE chat_id=7102"))
    # 403 "not enough rights" (guruhda huquq olib qo'yilgan) — vaqtinchalik holat, obunachi saqlanadi
    db.execute("UPDATE subscribers SET active=1 WHERE chat_id=7102")
    tg.FAIL_STATUS["sendMessage"] = 403
    tg.FAIL_DESC["sendMessage"] = "Forbidden: not enough rights to send text messages to the chat"
    tg.FAIL_NEXT.update(sendMessage=1)
    n_r = len(tg_calls("sendMessage"))
    bot2.send_text_all("huquq sinovi")
    wait(lambda: len(tg_calls("sendMessage")) > n_r, 10)
    time.sleep(0.3)
    tg.FAIL_STATUS.pop("sendMessage", None)
    tg.FAIL_DESC.clear()
    ok("403 'not enough rights' — obunachi o'chirilmaydi (admin huquqni qaytarishi mumkin)",
       db.scalar("SELECT active FROM subscribers WHERE chat_id=7102") == 1)
    db.execute("UPDATE subscribers SET active=0 WHERE chat_id=7102")
    db.execute("UPDATE subscribers SET active=1 WHERE chat_id=7101")

    # superguruh
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active, kb_cleared) VALUES(-500,'G','',?,1,1)",
               (time.time(),))
    tg.MIGRATE["-500"] = -100500
    bot2.send_text_all("superguruh sinovi")
    mig = wait(lambda: [c for c in tg_calls("sendMessage") if str(c["data"].get("chat_id")) == "-100500"
                        and "superguruh sinovi" in str(c["data"].get("text"))], 10)
    tg.MIGRATE.clear()
    ok("Guruh superguruhga aylangan — obunachi yangi raqamga ko'chdi va xabar yetdi",
       mig and db.scalar("SELECT active FROM subscribers WHERE chat_id=-100500") == 1
       and not db.row("SELECT 1 FROM subscribers WHERE chat_id=-500"))

    # HTML javob (nginx 502) so'rovlar oqimini o'ldirmaydi; guruhdagi suhbatga javob yo'q
    tg.HTML_NEXT["getUpdates"] = 2
    time.sleep(1.5)
    n_help = len(tg_calls("sendMessage"))
    tg.text_message(7101, "/yordam")
    rep = wait(lambda: len(tg_calls("sendMessage")) > n_help, 10)
    poll = next((t for t in bot2._threads if t.name == "tg-poll"), None)
    ok("Telegram HTML (502) qaytarsa ham buyruqlar ishlaydi (oqim tirik)", rep and poll is not None and poll.is_alive())
    n1 = len(tg_calls("sendMessage"))
    tg.push_update({"message": {"message_id": 9001, "date": int(time.time()), "text": "salom hammaga",
                                "chat": {"id": -100500, "type": "supergroup"}, "from": {"id": 55, "first_name": "A"}}})
    tg.push_update({"message": {"message_id": 9002, "date": int(time.time()), "new_chat_members": [{"id": 56}],
                                "chat": {"id": -100500, "type": "supergroup"}, "from": {"id": 55, "first_name": "A"}}})
    time.sleep(2.0)
    quiet = len([c for c in tg_calls("sendMessage")[n1:] if str(c["data"].get("chat_id")) == "-100500"]) == 0
    tg.push_update({"message": {"message_id": 9003, "date": int(time.time()), "text": "/yordam",
                                "chat": {"id": -100500, "type": "supergroup"}, "from": {"id": 55, "first_name": "A"}}})
    ans = wait(lambda: [c for c in tg_calls("sendMessage")[n1:] if str(c["data"].get("chat_id")) == "-100500"], 10)
    ok("Guruhda oddiy suhbat va xizmat xabarlariga javob yo'q, buyruqqa — javob", quiet and ans)
    bot2.stop()


# ============================================================================= 2. do'kon paneli
def web_suite() -> None:
    from fastapi.testclient import TestClient

    from app import config, web

    print("\n--- Do'kon paneli ---")
    db.set_settings({"telegram_enabled": "1", "telegram_send_video": "1", "camera_alerts": "1"})
    c = TestClient(web.app)
    c.post("/login", data={"username": config.ADMIN_USER, "password": config.ADMIN_PASSWORD, "next": "/"})
    r = c.post("/api/settings", data={"_form": "full", "threshold": "60", "store_name": "Do'kon", "show_objects": "on"})
    s = db.get_settings(force=True)
    ok("Sozlamalar formasini saqlash Telegram, video va kamera xabarlarini O'CHIRMAYDI",
       r.status_code in (200, 303) and s.get("telegram_enabled") == "1" and s.get("telegram_send_video") == "1"
       and s.get("camera_alerts") == "1" and s.get("signal_pocket") == "0", str({k: s.get(k) for k in (
           "telegram_enabled", "telegram_send_video", "camera_alerts", "signal_pocket")}))
    db.set_settings({"signal_pocket": "1"})
    ok("Noto'g'ri JSON (ro'yxat) — 400, 500 emas", c.post("/api/settings", json=[1, 2]).status_code == 400)
    ok("Son o'rniga null/ro'yxat — 400", c.post("/api/settings", json={"threshold": [1]}).status_code == 400)

    bad = c.get("/api/summary", headers={"Host": "evil-rebind.example.com"})
    good = c.get("/api/summary", headers={"Host": "192.168.1.20:8000"})
    loc = c.get("/api/summary", headers={"Host": "localhost:8000"})
    ok("Begona domen (DNS rebinding) — 400; IP va localhost — ishlaydi",
       bad.status_code == 400 and good.status_code == 200 and loc.status_code == 200,
       f"{bad.status_code} {good.status_code} {loc.status_code}")
    from app import web as web_mod

    lan = c.get("/api/summary", headers={"Host": "dokon-pc.lan:8000"})
    old_ah = list(web_mod.ALLOWED_HOSTS)
    web_mod.ALLOWED_HOSTS[:] = ["panel.dokon.uz:8443"]
    web_mod._HOSTS_OK.clear()
    try:
        prox = c.get("/api/summary", headers={"Host": "panel.dokon.uz:8443"})
    finally:
        web_mod.ALLOWED_HOSTS[:] = old_ah
        web_mod._HOSTS_OK.clear()
    ok("Router nomi (.lan) va ALLOWED_HOSTS (port bilan yozilgan) — ochiladi; rad etishda qanday yoqish yozilgan",
       lan.status_code == 200 and prox.status_code == 200 and "ALLOWED_HOSTS=evil-rebind.example.com" in bad.text,
       f"{lan.status_code} {prox.status_code}")

    cookie = c.cookies.get("hushyor_session")
    c.get("/logout")
    c2 = TestClient(web.app)
    c2.cookies.set("hushyor_session", cookie)
    ok("Chiqqandan keyin eski sessiya cookie'si yaroqsiz (o'g'irlangan cookie ham)",
       c2.get("/api/summary").status_code == 401)
    c.post("/login", data={"username": config.ADMIN_USER, "password": config.ADMIN_PASSWORD, "next": "/"})
    old_pw = web.ADMIN_PASSWORD
    web.ADMIN_PASSWORD = old_pw + "-yangi"
    try:
        ok("Parol almashgach eski sessiyalar yaroqsiz", c.get("/api/summary").status_code == 401)
    finally:
        web.ADMIN_PASSWORD = old_pw
    c.post("/login", data={"username": config.ADMIN_USER, "password": config.ADMIN_PASSWORD, "next": "/"})

    for t in ("events", "jobs", "tg_outbox", "tg_messages"):
        db.execute(f"DELETE FROM {t}")
    ids = [_event() for _ in range(30)]
    p1 = c.get("/api/events?status=yangi&per=24").json()
    for i in [x["id"] for x in p1["items"]][:5]:
        db.review_event(i, "tasdiqlangan", "sinov")
    p2 = c.get(f"/api/events?status=yangi&per=24&before={p1['next_before']}").json()
    seen = {x["id"] for x in p1["items"]} | {x["id"] for x in p2["items"]}
    ok("'Yana ko'rsatish' — orada tekshirilgan hodisalar tufayli birortasi tushib qolmaydi",
       set(ids) <= seen and p2["next_before"] is None, f"{len(seen)}/{len(ids)}")

    jid = db.execute("INSERT INTO jobs(filename, path, status, created_at) VALUES('v.mp4','/x','tayyor',?)", (time.time(),))
    for _ in range(4):
        _event(job_id=jid)
    sm = c.get("/api/summary").json()
    ok("Bosh sahifa: bugungi va tekshirilmagan hodisalar sonida video tahlili yo'q",
       sm["today"] == 30 and sm["pending"] == 25, f"{sm['today']} {sm['pending']}")

    from app import config as cfg

    d = Path(tempfile.mkdtemp())
    for name, data in (("u16", "Z_A1=bir\r\nZ_PORT=9000\r\n".encode("utf-16")),
                       ("ansi", "Z_A2=Привет\nZ_B2=8000  # izoh\nZ_C2=\"q # z\" # izoh\n".encode("cp1251"))):
        (d / name).write_bytes(data)
        cfg._load_dotenv(d / name)
    ok(".env: UTF-16 (Notepad 'Unicode') va ANSI fayllar o'qiladi, qatordagi izoh olib tashlanadi",
       os.environ.get("Z_A1") == "bir" and os.environ.get("Z_A2") == "Привет" and os.environ.get("Z_B2") == "8000"
       and os.environ.get("Z_C2") == "q # z", str([os.environ.get(k) for k in ("Z_A1", "Z_A2", "Z_B2", "Z_C2")]))
    (d / "pw").write_text("Z_ADMIN_PASSWORD=Dokon # 2024\nZ_D2=abc #2024\n", encoding="utf-8")
    cfg._load_dotenv(d / "pw")
    ok(".env: parol ichidagi '#' saqlanadi (3.1.0 dagidek), '#2024' kabi qiymat izoh emas",
       os.environ.get("Z_ADMIN_PASSWORD") == "Dokon # 2024" and os.environ.get("Z_D2") == "abc #2024",
       str([os.environ.get(k) for k in ("Z_ADMIN_PASSWORD", "Z_D2")]))


# ============================================================================= 3. AI, xavfsizlik, oqim, disk
class Pp:
    def __init__(self, tid, box):
        self.track_id = tid
        self.box = np.asarray(box, dtype=np.float32)
        self.kps = np.zeros((17, 3), np.float32)


def ai_suite() -> None:
    from app.ai import flow, safety

    print("\n--- AI va xavfsizlik ---")
    lt = time.localtime()
    night = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 2, 30, 0, 0, 0, -1))
    db.set_settings({"work_hours": "09:00-21:00", "safety_night_hours": "", "safety_night": "1",
                     "safety_cooldown_min": "10"})
    mon = safety.SafetyMonitor(901, "Zal")
    out = []
    for k in range(3):                                    # 100 s oraliqda 3 ta bir lahzalik "odam"
        out += mon._night([Pp(1, [100, 100, 200, 400])], night + k * 100, db.get_settings())
        out += mon._night([], night + k * 100 + 0.3, db.get_settings())
    ok("Tungi kirish: tasodifiy (bir-biridan uzoq) 3 ta bir lahzalik aniqlash — signal yo'q", not out, str(out[:1]))
    mon = safety.SafetyMonitor(902, "Zal")
    t0 = night + 3600
    out = []
    for i in range(40):
        out += mon._night([Pp(1, [300, 100, 400, 400])], t0 + i * 0.25, db.get_settings())
    ok("Uzluksiz ko'ringan odam — bitta signal", len(out) == 1 and out[0]["kind"] == "night")
    out = []
    for i in range(int(700 / 4)):                         # maneken: 11 daqiqa joyida "ko'rinib" turadi
        out += mon._night([Pp(1, [300, 100, 400, 400])], t0 + 20 + i * 4, db.get_settings())
    ok("Joyidan qimirlamagan 'odam' (maneken) qayta-qayta signal bermaydi", not out)
    out = []
    for i in range(20):                                   # boshqa joyda yangi odam (sovish o'tgan)
        out += mon._night([Pp(1, [300, 100, 400, 400]), Pp(2, [900, 120, 1000, 420])], t0 + 740 + i * 0.5,
                          db.get_settings())
    ok("Maneken turgan paytda boshqa joyda yangi odam — yana signal (avval tun bo'yi jim edi)", len(out) == 1)

    # bitta odam ikki kamera oldida 15 daqiqa yuradi: do'kon bo'yicha sovish davriga bitta signal (avval ikkinchi
    # kamera 2 daqiqadan keyin qayta qurollanib, o'sha odam uchun yana signal va SMS berardi)
    safety._store_last.clear()
    ca, cb = safety.SafetyMonitor(903, "A"), safety.SafetyMonitor(904, "B")
    tw = night + 7200
    walk = []
    for i in range(15 * 60 * 2):
        x = float((i * 4) % 600)
        for nm, mm in (("A", ca), ("B", cb)):
            for a_ in mm._night([Pp(1, [x, 100, x + 80, 300])], tw + i * 0.5, db.get_settings()):
                walk.append((nm, round(a_["t"] - tw) if "t" in a_ else round(i * 0.5)))
    ok("Bitta odam ikki kamera oldida 15 daqiqa — 2 ta signal (10 daqiqalik sovish davriga bitta)",
       len(walk) == 2 and walk[1][1] - walk[0][1] >= 590, str(walk))

    # bazadagi "kelajakdagi" signal (soat oldinga ketgan edi, keyin to'g'rilandi) signallarni o'chirib qo'ymaydi
    fut = db.insert_event({"source_id": 905, "source_name": "C", "created_at": time.time() + 3600, "score": 90,
                           "level": "yuqori", "kind": "fire", "reasons": [], "breakdown": {}, "track_id": -1,
                           "status": "yangi", "t_detect": time.time() + 3600})
    mf = safety.SafetyMonitor(905, "C")
    ok("Soat orqaga to'g'rilangandan keyin: kelajakdagi oxirgi signal sovishni soatlab cho'zmaydi (≤ 10 daqiqa)",
       mf.last.get("fire", 0) <= time.time() + 1 and mf._cool("fire", time.time() + 601, db.get_settings())
       and mf._cool("fire", time.time() - 7200, db.get_settings()), str(mf.last.get("fire")))
    db.execute("DELETE FROM events WHERE id=?", (fut,))

    safety._store_last.clear()
    a = safety._alert("fall", 1, night, [1, 1, 2, 2], ["x"])
    m1, m2 = safety.SafetyMonitor(None, "A"), safety.SafetyMonitor(None, "B")
    keep = [x for x in [a, dict(a)] if x.get("kind") not in safety.STORE_DUP_SEC
            or safety.store_once(x["kind"], night, safety.STORE_DUP_SEC[x["kind"]])]
    ok("Bitta yiqilishni ikki kamera ko'rsa — bitta signal (va bitta SMS)", len(keep) == 1)
    del m1, m2

    sent = []
    import app.sync as sync_mod

    orig_sup, orig_enq = sync_mod.cloud_supports, sync_mod.enqueue_flow_hour
    orig_flush, orig_item, orig_bf, orig_prune = flow.flush, flow.hour_item, flow.backfill, flow.prune
    h0 = int(time.time() // 3600) * 3600 - 7200
    try:
        sync_mod.cloud_supports = lambda f: True
        sync_mod.enqueue_flow_hour = lambda it: sent.append(it["hour"])
        flow.flush = lambda: ({(1, h0)}, set())
        flow.hour_item = lambda pid, hour: {"plan_id": pid, "hour": hour}
        flow.backfill = lambda *a, **k: 0
        flow.prune = lambda *a, **k: None
        flow.on_minute(h0 + 59 * 60 + 10 * 0)              # HH:59 daqiqasi yozilmoqda
    finally:
        sync_mod.cloud_supports, sync_mod.enqueue_flow_hour = orig_sup, orig_enq
        flow.flush, flow.hour_item, flow.backfill, flow.prune = orig_flush, orig_item, orig_bf, orig_prune
    ok("Oqim: soatning oxirgi daqiqasi (HH:59) yozilganda o'sha soat to'liq bulutga yuboriladi", sent == [h0], str(sent))

    from app.ai import manager as mg

    m = mg.Manager.__new__(mg.Manager)
    m._started = time.time()
    db.execute("DELETE FROM events")
    e1 = _event(created_at=time.time() - 60)
    db.update_event(e1, clip_error=0, clip_path=None)
    e2 = _event(created_at=time.time() - 60)
    db.update_event(e2, clip_error=0, clip_path="bor.mp4")
    ids = m.reconcile_pending_clips()
    ok("Oldingi ishga tushishdan klipi qolib ketgan hodisa — 'klip yaratilmadi' (Telegram'da 'tayyorlanmoqda' qolmaydi)",
       ids == [e1] and db.get_event(e1)["clip_error"] == 1 and not db.get_event(e2).get("clip_error"), str(ids))

    # disk himoyasi: bo'sh joy kam — eski kliplar o'chiriladi, yangilanish zaxiralari 5 tagacha
    import shutil as sh

    from app.config import DATA_DIR

    bdir = DATA_DIR / "backups"
    for i in range(8):
        (bdir / f"update_2026010{i}_000000").mkdir(parents=True, exist_ok=True)
    db.execute("DELETE FROM events")
    olds = []
    for i in range(3):
        e = _event(created_at=time.time() - 86400 * (10 - i))
        (CLIPS_DIR / f"old{i}.mp4").write_bytes(b"0" * 1000)
        db.update_event(e, clip_path=f"old{i}.mp4")
        olds.append(e)
    m.notifier, m._disk_warned = None, 0.0
    real_du = sh.disk_usage
    calls = {"n": 0}

    def fake_du(p):
        calls["n"] += 1
        return real_du(p)._replace(free=1 * 1024 ** 3)
    mg.shutil.disk_usage = fake_du
    try:
        m._disk_guard()
    finally:
        mg.shutil.disk_usage = real_du
    left = sorted(d.name for d in bdir.iterdir())
    ok("Disk to'lsa: eski kliplar o'chirildi (hodisa qoladi), yangilanish zaxiralaridan eng yangi 5 tasi qoldi",
       len(left) == 5 and left[0].endswith("3_000000") and all(not (CLIPS_DIR / f"old{i}.mp4").exists() for i in range(3))
       and all(db.get_event(e) and db.get_event(e)["clip_path"] is None for e in olds), str(left[:2]))
    # yangi kliplar (oxirgi sutka) va tasdiqlangan o'g'irlik (kliplar diskni to'ldirmagan) — tegilmaydi; o'chirish joy
    # bermasa (disk boshqa narsa bilan to'lgan) — bitta bo'lakdan keyin to'xtaydi
    db.execute("DELETE FROM events")
    keep = []
    for k, (age, st) in enumerate(((60, "yangi"), (3600, "tasdiqlangan"), (10 * 86400, "tasdiqlangan"))):
        e = _event(created_at=time.time() - age)
        (CLIPS_DIR / f"keep{k}.mp4").write_bytes(b"0" * 1000)
        db.update_event(e, clip_path=f"keep{k}.mp4", status=st)
        keep.append(e)
    many = []
    for k in range(120):
        e = _event(created_at=time.time() - 3 * 86400 - k)
        (CLIPS_DIR / f"many{k}.mp4").write_bytes(b"0" * 100)
        db.update_event(e, clip_path=f"many{k}.mp4")
        many.append(e)
    mg.shutil.disk_usage = fake_du
    try:
        m._disk_guard()
    finally:
        mg.shutil.disk_usage = real_du
    gone = sum(1 for e in many if db.get_event(e)["clip_path"] is None)
    ok("Disk himoyasi: oxirgi sutkadagi va tasdiqlangan kliplar qoldi; joy bo'shamasa — bitta bo'lakdan keyin to'xtadi",
       all((CLIPS_DIR / f"keep{k}.mp4").exists() for k in range(3)) and gone == 50, f"o'chdi {gone}/120")

    def mk(prefix, k, age, st, real=True):
        e = _event(created_at=time.time() - age)
        if real:
            (CLIPS_DIR / f"{prefix}{k}.mp4").write_bytes(b"0" * 100)
        db.update_event(e, clip_path=f"{prefix}{k}.mp4", status=st)
        return e

    m.DISK_CLIPS_SIGNIFICANT = 0               # kliplar diskni to'ldirgan deb olamiz (tasdiqlanganlar bosqichi yoqiladi)
    try:
        # A: eng eski kliplar qo'lda o'chirilgan (fayl yo'q) — tasdiqlanganlarga o'tib ketmaydi, keyingi tekshirilmaganlar
        db.execute("DELETE FROM events")
        gone_rows = [mk("yoq", k, 20 * 86400, "yangi", real=False) for k in range(60)]
        unrev = [mk("eski", k, 10 * 86400, "yangi") for k in range(10)]
        conf = [mk("tasd", k, 10 * 86400, "tasdiqlangan") for k in range(10)]
        grow = {"n": 0}

        def growing_du(p):
            grow["n"] += 1
            return real_du(p)._replace(free=1 * 1024 ** 3 + grow["n"] * 10 * 1024 ** 2)
        mg.shutil.disk_usage = growing_du
        try:
            m._disk_guard()
        finally:
            mg.shutil.disk_usage = real_du
        ok("Disk himoyasi: fayli yo'q yozuvlar tozalanib, avval hamma eski tekshirilmagan kliplar o'chadi",
           all(db.get_event(e)["clip_path"] is None for e in gone_rows + unrev), str(grow))
        # B: o'chirish joy bermaydi (disk boshqa narsa bilan to'lmoqda) — tasdiqlangan o'g'irliklarga tegilmaydi
        db.execute("DELETE FROM events")
        unrev = [mk("b_eski", k, 10 * 86400, "yangi") for k in range(60)]
        conf = [mk("b_tasd", k, 10 * 86400, "tasdiqlangan") for k in range(10)]
        mg.shutil.disk_usage = fake_du
        try:
            m._disk_guard()
        finally:
            mg.shutil.disk_usage = real_du
        ok("Disk himoyasi: joy bo'shamasa to'xtaydi — tasdiqlangan o'g'irlik videolari qoladi",
           all((CLIPS_DIR / f"b_tasd{k}.mp4").exists() for k in range(10))
           and sum(1 for e in unrev if db.get_event(e)["clip_path"] is None) == 50)
    finally:
        del m.DISK_CLIPS_SIGNIFICANT


# ============================================================================= 4. sinxronlash
def sync_suite() -> None:
    from app import sync

    print("\n--- Sinxronlash ---")
    db.execute("DELETE FROM events")
    ev = _event()

    class W(sync.SyncWorker):
        def __init__(self):
            self.server_time_skew = 0.0
            self.manager = None

        def _post(self, *a, **k):
            return 200, {"ok": True}
    w = W()
    t = time.time()
    w._apply_commands([{"id": 1, "type": "review", "local_id": ev, "status": "tasdiqlangan", "by": "Bulut: A", "t": t - 5},
                       {"id": 2, "type": "review", "local_id": ev, "status": "rad_etilgan", "by": "Bulut: A", "t": t - 2}])
    ok("Bulutda ✅ keyin darhol ❌ (bitta partiyada) — do'konda oxirgi qaror (❌) qoladi",
       db.get_event(ev)["status"] == "rad_etilgan", db.get_event(ev)["status"])
    db.execute("DELETE FROM outbox")
    os.environ["CLOUD_URL"], os.environ["EDGE_TOKEN"] = "http://127.0.0.1:9", "x" * 24
    sync.CLOUD_URL, sync.EDGE_TOKEN = "http://127.0.0.1:9", "x" * 24
    for i in range(50):
        sync.enqueue("metrics", f"m:{i}", {"minute": i})
    sync.enqueue("event", "ev:999", {"uid": "d:999"})
    b = W.__new__(W)
    b.batch_size = 20
    batch = sync.SyncWorker._take(b)
    ok("Uzoq aloqasizlikdan keyin: hodisa minglab daqiqalik ko'rsatkichlardan OLDIN yuboriladi",
       batch and batch[0]["kind"] == "event", str([r["kind"] for r in batch][:3]))


# ============================================================================= 5. bulut
def cloud_suite() -> None:
    from fastapi.testclient import TestClient

    from cloud import maintenance, notify, report, service
    from cloud import server as cloud

    print("\n--- Bulut ---")
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    comp = cdb.create_company("3.2 MChJ")
    owner = cdb.create_user(comp, "egasi@t320.uz", "T320Parol-2026x", "Egasi", "owner")
    mgr = cdb.create_user(comp, "menejer@t320.uz", "T320Parol-2026y", "Menejer", "manager")
    bid, tok = cdb.create_branch(comp, "Chilonzor", "t320dokon")
    cdb.execute("UPDATE branches SET version='3.2.0' WHERE id=:b", {"b": bid})
    c = TestClient(cloud.app)
    H = {"Authorization": f"Bearer {tok}", "X-Device-Id": "t320dokon"}
    now = time.time()
    items = [{"key": "ev:1", "uid": "t320dokon:1", "local_id": 1, "created_at": now, "updated_at": now, "kind": "pocket",
              "score": 80, "status": "yangi", "reasons": 5},
             {"key": "ev:2", "uid": "t320dokon:2", "local_id": 10 ** 19, "created_at": now, "updated_at": now,
              "kind": "pocket", "score": float("inf") if False else 70, "status": "yangi", "reasons": ["a"]}]
    raw = json.dumps({"items": items}).replace('"score": 70', '"score": Infinity')
    r = c.post("/api/v1/edge/events", content=raw, headers={**H, "Content-Type": "application/json"})
    acc = r.json().get("accepted", []) if r.status_code == 200 else []
    ok("Buzuq edge yozuvlari (Infinity, juda katta son, reasons=5) — 500 emas, qabul qilindi", r.status_code == 200
       and set(acc) == {"ev:1", "ev:2"}, f"{r.status_code} {r.text[:120]}")
    rm = c.post("/api/v1/edge/metrics", content=json.dumps({"items": [{"key": "m:1", "minute": 1}]}).replace(
        '"minute": 1', '"minute": Infinity'), headers={**H, "Content-Type": "application/json"})
    ok("Daqiqa = Infinity — shu yozuv rad etiladi, so'rov 200", rm.status_code == 200 and rm.json()["rejected"])
    c.post("/api/v1/auth/login", json={"email": "egasi@t320.uz", "password": "T320Parol-2026x"})
    lst = c.get("/api/v1/events?limit=10")
    ok("Hodisalar ro'yxati buzuq 'reasons' bilan ham ochiladi", lst.status_code == 200 and len(lst.json()["events"]) == 2)

    items = [{"key": "ev:3", "uid": "t320dokon:3", "local_id": 3, "created_at": now, "updated_at": now, "kind": "pocket",
              "score": 80, "status": "yangi", "job_id": 77, "reasons": []}]
    c.post("/api/v1/edge/events", json={"items": items}, headers=H)
    br = [b for b in c.get("/api/v1/branches").json()["branches"] if b["id"] == bid][0]
    yangi = c.get("/api/v1/events?status=yangi").json()["events"]
    vid = c.get("/api/v1/events?status=yangi&video=1").json()["events"]
    ok("Video sinovi hodisasi 'tekshirish kerak' soniga va navbatga kirmaydi (video=1 bilan — bor)",
       br["pending_review"] == 2 and len(yangi) == 2 and len(vid) == 3, f"{br['pending_review']} {len(yangi)} {len(vid)}")

    # bitta guruhni ikki xodim ulagan — bitta xabar
    notify.add_target(owner, "telegram", "-777", {"name": "Guruh"})
    notify.add_target(mgr, "telegram", "-777", {"name": "Guruh"})
    for uid_ in (owner, mgr):
        notify.set_prefs(cdb.row("SELECT * FROM users WHERE id=:u", {"u": uid_}), {"min_score": 0})
    n_before = cdb.scalar("SELECT COUNT(*) FROM notify_outbox WHERE kind='camera'") or 0
    notify.notify_branch(bid, "camera", "x", {"branch_id": bid}, "camera")
    n_after = cdb.scalar("SELECT COUNT(*) FROM notify_outbox WHERE kind='camera' AND channel='telegram'") or 0
    ok("Ikki xodim ulagan bitta guruhga — bitta xabar", n_after - n_before == 1, str(n_after - n_before))
    ok("Superguruhga ko'chish: ulangan manzillar yangi raqamga", notify.migrate_chat("-777", "-100777") == 2
       and cdb.scalar("SELECT COUNT(*) FROM notify_targets WHERE address='-100777'") == 2)

    r1 = c.post("/api/v1/events/t320dokon:1/review", json={"status": "tasdiqlangan", "expect": "yangi"})
    r2 = c.post("/api/v1/events/t320dokon:1/review", json={"status": "rad_etilgan", "expect": "yangi"})
    st = cdb.scalar("SELECT status FROM events WHERE uid='t320dokon:1'")
    ok("Ikki xodim bir vaqtda: ikkinchisi 409 oladi, birinchining qarori ustidan yozilmaydi",
       r1.status_code == 200 and r2.status_code == 409 and "boshqa xodim" in r2.text and st == "tasdiqlangan",
       f"{r1.status_code} {r2.status_code} {st}")
    # poyga: tekshiruvdan keyin, yozishdan oldin boshqa xodim ulgurdi — shartli UPDATE ushlaydi
    from cloud import service as csvc

    stale = dict(cdb.row("SELECT * FROM events WHERE uid='t320dokon:3'"))
    cdb.execute("UPDATE events SET status='rad_etilgan' WHERE uid='t320dokon:3'")
    real_ef = csvc.event_for
    calls = []
    csvc.event_for = lambda u, x: (calls.append(1), stale if len(calls) == 1 else real_ef(u, x))[1]
    try:
        u_owner = cdb.row("SELECT * FROM users WHERE id=:u", {"u": owner})
        csvc.review(u_owner, "t320dokon:3", "tasdiqlangan", expect="yangi")
        raced = "o'tib ketdi"
    except csvc.Conflict as e:
        raced = e.current
    finally:
        csvc.event_for = real_ef
    n_cmd = sum(1 for r in cdb.rows("SELECT payload FROM commands WHERE type='review'")
                if (cdb.json_load(r["payload"]) or {}).get("uid") == "t320dokon:3")
    ok("Poyga (tekshiruv va yozish orasida): Conflict, holat va edge buyrug'i yozilmadi",
       raced == "rad_etilgan" and cdb.scalar("SELECT status FROM events WHERE uid='t320dokon:3'") == "rad_etilgan"
       and n_cmd == 0, f"{raced} {n_cmd}")
    cdb.execute("UPDATE branches SET version='3.0.0' WHERE id=:b", {"b": bid})
    r3 = c.post("/api/v1/events/t320dokon:2/review", json={"status": "tasdiqlangan", "loss_sum": "100000"})
    st2 = cdb.scalar("SELECT status FROM events WHERE uid='t320dokon:2'")
    ev2 = c.get("/api/v1/events/t320dokon:2").json()["event"]
    ok("Eski (3.0.0) do'kon: summa bilan tasdiq — 409 va holat o'zgarmaydi; panelga loss_ok=false",
       r3.status_code == 409 and st2 == "yangi" and ev2.get("loss_ok") is False, f"{r3.status_code} {st2}")
    cdb.execute("UPDATE branches SET version='3.2.0' WHERE id=:b", {"b": bid})
    ok("Panel so'rovi 256 KB dan katta — kirishdan oldin 413",
       c.post("/api/v1/events/t320dokon:2/review", content=b"{" + b" " * (300 * 1024) + b"}",
              headers={"Content-Type": "application/json"}).status_code == 413)

    old_lim = cloud.CLIPS_PER_BRANCH_DAY
    cloud.CLIPS_PER_BRANCH_DAY = 1
    cloud._clip_day.clear()
    try:
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 500
        a1 = c.post("/api/v1/edge/events/t320dokon:1/clip", headers=H, files={"clip": ("a.mp4", mp4, "video/mp4")},
                    data={"meta": "{}"})
        a2 = c.post("/api/v1/edge/events/t320dokon:2/clip", headers=H, files={"clip": ("b.mp4", mp4, "video/mp4")},
                    data={"meta": "{}"})
    finally:
        cloud.CLIPS_PER_BRANCH_DAY = old_lim
    ok("Filialning kunlik klip chegarasi — keyingisi 429 (diskni to'ldirib, boshqalarnikini o'chirtira olmaydi)",
       a1.status_code == 200 and a2.status_code == 429, f"{a1.status_code} {a2.status_code}")

    out = maintenance.backup()
    ok("Zaxira siqilgan (.db.gz) va ochiladi", out.name.endswith(".db.gz") and gzip.open(out).read(16)[:15] == b"SQLite format 3")
    bd = Path(maintenance.settings()["backup_dir"])
    for i in range(9):
        (bd / f"cloud-2026010{i}-000000.db").write_bytes(b"x")
    maintenance._rotate(bd, 3)
    mine = sorted(p.name for p in bd.glob("cloud-*") if maintenance._BACKUP_RX.fullmatch(p.name))
    ok("Zaxira aylanishi .db va .db.gz bo'yicha birga (eng yangi 3 tasi)", len(mine) == 3 and out.name in mine, str(mine))

    t1 = time.time()
    t0 = t1 - 3600
    cmp_ = report.compare([bid], t0, t1 + 6 * 86400, 0, now=t1)
    ok("Taqqoslash: chala davr (joriy hafta) xatosiz hisoblanadi", "branches" in cmp_)

    comp2 = cdb.create_company("Boshqa MChJ")
    cdb.create_user(comp2, "boshqa@t320.uz", "T320Parol-2026z", "B", "owner")
    sysd = c.get("/api/v1/system").json()
    ok("/system: serverda bir nechta kompaniya — kliplar soni faqat o'z kompaniyasi bo'yicha",
       sysd.get("ok") and "clips" in sysd and isinstance(sysd.get("sse"), int))
    del service


def main() -> int:
    t0 = time.time()
    db.init_db()
    bot_suite()
    web_suite()
    ai_suite()
    sync_suite()
    cloud_suite()
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi  ({time.time() - t0:.0f} s)")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
