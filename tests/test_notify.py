"""Bulut bildirishnoma xizmati (1.7.0) sinovi: Telegram v2 (bulut boti — bog'lash, xabar, klip, ✅/❌ tugmalari,
tahrir), email (SMTP), brauzer push (Web Push, shifrlangan yuk tekshiriladi), rollar bo'yicha kim nima oladi,
eskalatsiya, do'kon aloqasi uzilishi/tiklanishi, kamera holati, qayta urinish, bloklangan bot / bekor qilingan push,
eski (backfill) va video tahlili hodisalari uchun xabar yo'qligi, jonli oqim (SSE), cookie sessiya va CSRF.

Ishga tushirish:  python -m tests.test_notify
                  CLOUD_TEST_PG_URL=postgresql+psycopg://postgres@127.0.0.1:5433/hushyor_test python -m tests.test_notify
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import threading
import time

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_cloud17_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402

from tests import mock_telegram as tg  # noqa: E402

TG_PORT, SMTP_PORT, PUSH_PORT, CLOUD_PORT = 8771, 8772, 8773, 9301
tg.serve(TG_PORT)

from cloud import notify  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def wait(cond, timeout=15.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


# ----------------------------------------------------------------------------- SMTP qabul qiluvchi (soxta)
MAILS: list[dict] = []


async def _smtp_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    def send(line: str) -> None:
        writer.write((line + "\r\n").encode())
    send("220 sink ESMTP")
    await writer.drain()
    rcpt, mail_from = [], ""
    while True:
        line = await reader.readline()
        if not line:
            break
        cmd = line.decode(errors="ignore").strip()
        up = cmd.upper()
        if up.startswith("EHLO"):
            writer.write(b"250-sink\r\n250 OK\r\n")
        elif up.startswith("HELO") or up.startswith("RSET") or up.startswith("NOOP"):
            send("250 OK")
        elif up.startswith("MAIL FROM"):
            mail_from = cmd[10:]
            send("250 OK")
        elif up.startswith("RCPT TO"):
            rcpt.append(cmd[8:].strip("<> "))
            send("250 OK")
        elif up == "DATA":
            send("354 End data with <CR><LF>.<CR><LF>")
            await writer.drain()
            buf = b""
            while not buf.endswith(b"\r\n.\r\n"):
                chunk = await reader.readline()
                if not chunk:
                    break
                buf += chunk
            import email

            msg = email.message_from_bytes(buf[:-5])
            body = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
            MAILS.append({"to": list(rcpt), "from": mail_from, "subject": str(msg["Subject"]), "body": body})
            rcpt = []
            send("250 OK queued")
        elif up.startswith("QUIT"):
            send("221 Bye")
            await writer.drain()
            break
        else:
            send("250 OK")
        await writer.drain()
    writer.close()


def serve_smtp(port: int) -> None:
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        srv = loop.run_until_complete(asyncio.start_server(_smtp_session, "127.0.0.1", port))
        loop.run_until_complete(srv.serve_forever())
    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)


# ----------------------------------------------------------------------------- push xizmati (soxta)
PUSHES: list[dict] = []
PUSH_STATUS = {"code": 201}
push_app = FastAPI()


@push_app.post("/push/{sid}")
async def _push(sid: str, request: Request):
    PUSHES.append({"sid": sid, "headers": dict(request.headers), "body": await request.body()})
    from fastapi.responses import Response

    return Response(status_code=PUSH_STATUS["code"])


def serve_app(app, port: int):
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(80):
        if srv.started:
            break
        time.sleep(0.1)
    return srv


def push_keys():
    """Brauzer tomoni: ECDH kalit juftligi va auth sir — yukni yechib tekshirish uchun."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return priv, {"p256dh": b64(pub), "auth": b64(auth)}, auth


def decrypt_push(body: bytes, priv, auth: bytes) -> dict:
    import http_ece

    return json.loads(http_ece.decrypt(body, private_key=priv, auth_secret=auth, version="aes128gcm"))


# ----------------------------------------------------------------------------- yordamchilar
def login(base: str, email: str, pw: str) -> dict:
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def edge_event(base: str, dev: str, tok: str, lid: int, score: float, **kw) -> httpx.Response:
    now = time.time()
    it = {"key": f"ev:{lid}", "uid": f"{dev}:{lid}", "local_id": lid, "created_at": kw.pop("created_at", now),
          "updated_at": kw.pop("updated_at", now), "kind": "torso", "score": score, "level": "yuqori" if score >= 75 else "o'rta",
          "status": kw.pop("status", "yangi"), "source_name": "Kassa 1", "reasons": ["Qo'l kiyim ichiga kirdi", "Buyum yo'qoldi"]}
    it.update(kw)
    return httpx.post(f"{base}/api/v1/edge/events", json={"items": [it]},
                      headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev})


def outbox(where: str = "1=1", params: dict | None = None) -> list[dict]:
    return cloud.cdb.rows(f"SELECT * FROM notify_outbox WHERE {where} ORDER BY id", params or {})


def tg_calls(method: str, chat: int | None = None) -> list[dict]:
    out = [c for c in tg.calls(method) if c["token"] == "777:CLOUD"]
    if chat is not None:
        out = [c for c in out if str(c["data"].get("chat_id")) == str(chat)]
    return out


def run(label: str) -> None:
    print(f"\n--- {label} ---")
    cloud.cdb.reset_engine()
    cloud.cdb.meta.drop_all(cloud.cdb.engine())
    cloud.init_db()
    for p in cloud.cdb.DATA_DIR.glob("vapid_private.pem"):
        p.unlink()
    notify._vapid_pub.clear()
    notify.state.update(tg_bot=None, tg_error="", push_error="", tg_conflict=False)
    notify.cfg = notify.Config(tg_token="777:CLOUD", tg_api=f"http://127.0.0.1:{TG_PORT}", public_url="https://panel.dokon.uz",
                               smtp_host="127.0.0.1", smtp_port=SMTP_PORT, smtp_from="hushyor@dokon.uz", smtp_tls="none",
                               tz_offset=5, offline_sec=0, escalate_sec=0, push_allow_any=True)
    notify.RETRY = (0.3, 0.6, 1.0, 1.0, 1.0)
    notify.Worker.MONITOR_SEC = 0.5
    notify.TEST_EVERY = 0.0
    notify.MAX_TARGETS = 10
    cloud._state.update(tokens=[], admin="admin-sinov", fail=False, fail_paths=(), notify_configured=True, no_notify=False)
    cloud._login_fails.clear()
    tg.CALLS.clear()
    tg.UPDATES.clear()
    tg.FAIL_NEXT.clear()
    tg.FAIL_STATUS.clear()
    MAILS.clear()
    PUSHES.clear()
    srv = serve_app(cloud.app, CLOUD_PORT)
    base = f"http://127.0.0.1:{CLOUD_PORT}"
    try:
        _suite(base)
    finally:
        srv.should_exit = True
        time.sleep(0.8)


def _suite(base: str) -> None:
    ok("Bulut boti ishga tushdi (getMe)", wait(lambda: notify.state["tg_bot"] is not None, 10),
       str(notify.state["tg_error"]))
    cloud.bootstrap_owner("egasi@dokon.uz", "Parol12345", "Test savdo")
    owner = login(base, "egasi@dokon.uz", "Parol12345")
    bA = httpx.post(f"{base}/api/v1/branches", json={"name": "Chilonzor", "device_id": "devA"}, headers=owner).json()
    bB = httpx.post(f"{base}/api/v1/branches", json={"name": "Yunusobod", "device_id": "devB"}, headers=owner).json()
    tokA, tokB = bA["device_token"], bB["device_token"]
    idA, idB = bA["branch"]["id"], bB["branch"]["id"]
    for em, role, bids in (("qorovul@dokon.uz", "guard", [idA]), ("menejer@dokon.uz", "manager", [idA, idB])):
        r = httpx.post(f"{base}/api/v1/users", json={"email": em, "password": "Parol12345", "name": em.split("@")[0].title(),
                                                     "role": role, "branch_ids": bids}, headers=owner)
        assert r.status_code == 200, r.text
    guard = login(base, "qorovul@dokon.uz", "Parol12345")
    manager = login(base, "menejer@dokon.uz", "Parol12345")

    # --- kanallar holati va sukut sozlamalar
    n = httpx.get(f"{base}/api/v1/me/notifications", headers=owner).json()
    ok("Kanallar: Telegram (bot nomi), email, push sozlangan", n["channels"]["telegram"]["enabled"]
       and n["channels"]["telegram"]["bot"] == "hushyor_test_bot" and n["channels"]["email"]["enabled"]
       and n["channels"]["push"]["enabled"], str(n["channels"]))
    ok("Egasining sukut sozlamasi: ball ≥ 75, kamera, aloqa, eskalatsiya", n["prefs"] == notify.DEFAULT_PREFS["owner"])
    g = httpx.get(f"{base}/api/v1/me/notifications", headers=guard).json()
    ok("Qo'riqchi sukut: faqat hodisalar", g["prefs"]["event"] and not g["prefs"]["camera"] and not g["prefs"]["offline"])

    # --- Telegram bog'lash
    r = httpx.post(f"{base}/api/v1/me/telegram/link", headers=guard).json()
    ok("Bog'lash havolasi t.me/<bot>?start=<kod>", r["link"] == f"https://t.me/hushyor_test_bot?start={r['code']}"
       and r["expires_in"] == 600)
    tg.text_message(5001, "/start notogri-kod")
    ok("Noto'g'ri kod — javob, ulanmaydi", wait(lambda: any("noto'g'ri" in (c["data"].get("text") or "")
                                                             for c in tg_calls("sendMessage", 5001))))
    tg.text_message(5001, f"/start {r['code']}", first_name="Qorovul Ali")
    ok("Qo'riqchi Telegram'ga ulandi", wait(lambda: any("Ulandingiz" in (c["data"].get("text") or "")
                                                        for c in tg_calls("sendMessage", 5001))))
    tg.text_message(5009, f"/start {r['code']}")
    ok("Kod bir martalik — ikkinchi chat ulanmaydi", wait(lambda: any("eskirgan" in (c["data"].get("text") or "")
                                                                      for c in tg_calls("sendMessage", 5009))))
    code_m = httpx.post(f"{base}/api/v1/me/telegram/link", headers=manager).json()["code"]
    tg.text_message(5002, f"/start {code_m}", first_name="Menejer Vali")
    wait(lambda: any("Ulandingiz" in (c["data"].get("text") or "") for c in tg_calls("sendMessage", 5002)))
    t = httpx.get(f"{base}/api/v1/me/notifications", headers=guard).json()["targets"]
    ok("Panelda Telegram manzili ism bilan", len(t) == 1 and t[0]["channel"] == "telegram" and t[0]["address"] == "Qorovul Ali")

    # --- email va push
    ok("Menejer email'ni yoqdi", httpx.post(f"{base}/api/v1/me/email", json={"enabled": True}, headers=manager).json()["ok"])
    httpx.post(f"{base}/api/v1/me/email", json={"enabled": True}, headers=owner)
    key = httpx.get(f"{base}/api/v1/push/key", headers=owner).json()["public_key"]
    ok("VAPID ochiq kaliti (65 bayt, base64url)", len(base64.urlsafe_b64decode(key + "==")) == 65)
    priv, keys, auth = push_keys()
    r = httpx.post(f"{base}/api/v1/me/push", json={"endpoint": f"http://127.0.0.1:{PUSH_PORT}/push/owner", "keys": keys},
                   headers=owner)
    ok("Egasi brauzer push'ga obuna bo'ldi", r.status_code == 200, r.text[:80])
    notify.cfg.push_allow_any = False
    r = httpx.post(f"{base}/api/v1/me/push", json={"endpoint": "https://ichki-server.local/x", "keys": keys}, headers=owner)
    ok("Begona push manzili rad etiladi (SSRF himoyasi)", r.status_code == 400)
    notify.cfg.push_allow_any = True

    # --- hodisa: kim nima oladi
    r = edge_event(base, "devA", tokA, 1, 70)
    ok("Edge hodisasi qabul qilindi", r.status_code == 200 and r.json()["accepted"] == ["ev:1"])
    got = wait(lambda: tg_calls("sendMessage", 5001) and any("Shubhali harakat" in (c["data"].get("text") or "")
                                                             for c in tg_calls("sendMessage", 5001)))
    ok("Qo'riqchi Telegram xabari oldi (tugmalar bilan)", bool(got))
    msg_g = [c for c in tg_calls("sendMessage", 5001) if "Shubhali harakat" in (c["data"].get("text") or "")][0]
    kb = msg_g["data"]["reply_markup"]["inline_keyboard"]
    ok("Tugmalar: ✅/❌ + Panelda ochish havolasi", kb[0][0]["callback_data"].startswith("r:")
       and kb[1][0]["url"] == "https://panel.dokon.uz/events/?uid=devA:1")
    ok("Matnda filial, kamera, tur, ball, sabablar", all(x in msg_g["data"]["text"] for x in
                                                          ("Chilonzor", "Kassa 1", "Kiyim ichiga yashirish", "70", "Buyum yo'qoldi")))
    ok("Menejer Telegram + email oldi", wait(lambda: tg_calls("sendMessage", 5002) and any(
        "Chilonzor, ball 70" in m["subject"] and "menejer@dokon.uz" in m["to"] for m in MAILS)))
    time.sleep(0.6)
    ok("Egasi (ball 70 < 75) hech narsa olmadi", not PUSHES and not any("egasi@dokon.uz" in m["to"] for m in MAILS))
    edge_event(base, "devA", tokA, 2, 88)
    got = wait(lambda: PUSHES)
    ok("Egasi ball 88 uchun push oldi", bool(got))
    if PUSHES:
        p0 = PUSHES[0]
        payload = decrypt_push(p0["body"], priv, auth)
        ok("Push yuki shifrlangan va to'g'ri yechildi (aes128gcm, VAPID imzo)",
           payload["title"].startswith("Shubhali harakat: Chilonzor, ball 88") and payload["url"] == "/events/?uid=devA:2"
           and p0["headers"].get("content-encoding") == "aes128gcm" and p0["headers"].get("authorization", "").startswith("vapid "),
           str(payload)[:100])
    ok("Egasi ball 88 uchun email ham oldi", wait(lambda: any("egasi@dokon.uz" in m["to"] for m in MAILS)))

    # --- xabar yuborilmaydigan hodisalar
    n0 = len(outbox())
    edge_event(base, "devA", tokA, 3, 90, created_at=time.time() - 7200, updated_at=time.time() - 7200)
    edge_event(base, "devA", tokA, 4, 95, job_id=12)
    edge_event(base, "devA", tokA, 5, 92, status="tasdiqlangan")
    time.sleep(0.5)
    ok("Eski (backfill), video tahlili va allaqachon tekshirilgan hodisa uchun xabar yo'q", len(outbox()) == n0)

    # --- klip: Telegram javob sifatida, file_id qayta ishlatiladi
    clip = os.urandom(5000)
    r = httpx.post(f"{base}/api/v1/edge/events/devA:1/clip", data={"meta": "{}"},
                   files={"clip": ("c.mp4", clip, "video/mp4"), "thumb": ("t.jpg", b"\xff\xd8\xff" + b"0" * 50, "image/jpeg")},
                   headers={"Authorization": f"Bearer {tokA}", "X-Device-Id": "devA"})
    ok("Klip qabul qilindi", r.status_code == 200)
    got = wait(lambda: len(tg_calls("sendVideo")) >= 2)
    vids = tg_calls("sendVideo")
    ok("Klip ikkala chatga javob sifatida, ikkinchisiga file_id bilan (bir marta yuklanadi)", bool(got)
       and sum(1 for v in vids if v["files"]) == 1 and sum(1 for v in vids if v["data"].get("video")) == 1
       and all(str(v["data"].get("reply_to_message_id")) for v in vids), f"{len(vids)} ta")

    # --- ✅ tugmasi (Telegram) → bulutda holat, edge buyrug'i, xabarlar tahrirlanadi
    sent_id = int(kb[0][0]["callback_data"].split(":")[1])
    tg.callback(5009, f"r:{sent_id}:ok", message_id=1)
    ok("Begona chatdan tugma — ruxsat yo'q", wait(lambda: any(c["data"].get("text") == "Ruxsat yo'q"
                                                              for c in tg_calls("answerCallbackQuery"))))
    ok("Begona chat holatni o'zgartirmadi", cloud.cdb.scalar("SELECT status FROM events WHERE uid='devA:1'") == "yangi")
    tg.callback(5001, f"r:{sent_id}:ok", message_id=1)
    ok("Qo'riqchi ✅ bosdi — bulutda tasdiqlandi", wait(lambda: cloud.cdb.scalar("SELECT status FROM events WHERE uid='devA:1'")
                                                       == "tasdiqlangan"))
    cmd = cloud.cdb.row("SELECT * FROM commands WHERE type='review' ORDER BY id DESC LIMIT 1")
    ok("Do'kon (edge) uchun review buyrug'i yaratildi", cmd and cloud.cdb.json_load(cmd["payload"])["status"] == "tasdiqlangan"
       and "Telegram: Qorovul" in cloud.cdb.json_load(cmd["payload"])["by"])
    got = wait(lambda: len([c for c in tg_calls("editMessageText") if "TASDIQLANDI" in c["data"].get("text", "")]) >= 2)
    ok("Ikkala chatdagi xabar tahrirlandi (TASDIQLANDI, tugmalar olindi)", bool(got) and all(
        c["data"]["reply_markup"] == {"inline_keyboard": []} for c in tg_calls("editMessageText")
        if "TASDIQLANDI" in c["data"].get("text", "")))

    # --- panel orqali tekshirish ham Telegram'ni tahrirlaydi; edge'dan kelgan o'zgarish ham
    r = httpx.post(f"{base}/api/v1/events/devA:2/review", json={"status": "rad_etilgan"}, headers=manager)
    ok("Panelda rad etildi", r.status_code == 200 and r.json()["event"]["status"] == "rad_etilgan")
    ok("Panel qarori Telegram xabarlarida ko'rindi", wait(lambda: any("XATO SIGNAL" in c["data"].get("text", "")
                                                                      for c in tg_calls("editMessageText"))))
    n_edit = len(tg_calls("editMessageText"))
    edge_event(base, "devA", tokA, 2, 88, status="tasdiqlangan", updated_at=time.time() + 5, reviewed_by="Telegram: Do'kon")
    ok("Do'kondan kelgan qaror ham bulut botida tahrirlandi", wait(lambda: len(tg_calls("editMessageText")) > n_edit))

    # --- kamera holati: menejer va egasi oladi, qo'riqchi emas
    n_g = len(tg_calls("sendMessage", 5001))
    r = httpx.post(f"{base}/api/v1/edge/health", json={"items": [{"key": "h:1:down:1", "t": time.time(), "source_id": 1,
                   "source_name": "Kassa 1", "state": "down", "reason": "kadr kelmayapti", "since": time.time() - 70}]},
                   headers={"Authorization": f"Bearer {tokA}", "X-Device-Id": "devA"})
    ok("Kamera uzildi: menejer Telegram'da oldi", wait(lambda: any("Kamera ishlamayapti" in (c["data"].get("text") or "")
                                                                   for c in tg_calls("sendMessage", 5002))))
    ok("Kamera uzildi: egasi push oldi", wait(lambda: any(decrypt_push(p["body"], priv, auth)["title"].startswith("Kamera")
                                                          for p in PUSHES)))
    time.sleep(0.3)
    ok("Qo'riqchi kamera xabarini olmadi (sozlamasi o'chiq)", len(tg_calls("sendMessage", 5001)) == n_g)
    httpx.post(f"{base}/api/v1/edge/health", json={"items": [{"key": "h:1:down:1", "t": time.time(), "state": "down",
               "source_id": 1, "source_name": "Kassa 1"}]}, headers={"Authorization": f"Bearer {tokA}", "X-Device-Id": "devA"})
    time.sleep(0.4)
    ok("Takroriy holat yozuvi takroriy xabar bermaydi",
       len([o for o in outbox("kind='camera'")]) == len({(o["user_id"], o["target_id"]) for o in outbox("kind='camera'")}))

    # --- do'kon aloqasi uzilishi va tiklanishi
    httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time()}, headers={"Authorization": f"Bearer {tokB}", "X-Device-Id": "devB"})
    cloud.cdb.execute("UPDATE branches SET last_seen=:t WHERE device_id='devB'", {"t": time.time() - 400})
    notify._started[0] = time.time()
    notify.cfg.offline_sec = 300
    time.sleep(1.2)
    ok("Server yangi ishga tushgan — do'kon darhol 'uzilgan' deyilmaydi (edge qayta ulanib ulgursin)",
       cloud.cdb.scalar("SELECT offline_since FROM branches WHERE device_id='devB'") is None)
    notify._started[0] = 0.0
    ok("Aloqa uzildi: menejer xabar oldi", wait(lambda: any("aloqa uzildi" in (c["data"].get("text") or "")
                                                            for c in tg_calls("sendMessage", 5002)), 10))
    ok("Aloqa uzildi: bir marta (offline_since belgilandi)", cloud.cdb.scalar(
        "SELECT offline_since FROM branches WHERE device_id='devB'") is not None
       and len(outbox("kind='offline'")) == len({o["target_id"] for o in outbox("kind='offline'")}))
    time.sleep(1.2)
    ok("Takroriy tekshiruv yana xabar bermaydi", len(outbox("kind='offline'")) == len({o["target_id"] for o in outbox("kind='offline'")}))
    httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time(), "pending": 7},
               headers={"Authorization": f"Bearer {tokB}", "X-Device-Id": "devB"})
    ok("Aloqa tiklandi: davomiylik va navbat soni bilan", wait(lambda: any(
        "Aloqa tiklandi" in (c["data"].get("text") or "") and "7" in c["data"]["text"] for c in tg_calls("sendMessage", 5002))))
    # Navbatni bo'shatayotgan edge hello yubormasa ham (hodisa/klip yuboradi) — aloqada; tiklanish xabari bitta
    t_old = time.time() - 900
    cloud.cdb.execute("UPDATE branches SET last_seen=:t, offline_since=:t WHERE device_id='devB'", {"t": t_old})
    n_on = len(outbox("kind='online'"))
    ths = [threading.Thread(target=lambda: httpx.post(f"{base}/api/v1/edge/metrics", json={"items": []},
                                                       headers={"Authorization": f"Bearer {tokB}", "X-Device-Id": "devB"}))
           for _ in range(3)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    new_on = outbox("kind='online'")[n_on:]
    ok("Hello'siz so'rov ham aloqani tiklaydi; bir vaqtda 3 so'rov — bitta xabar", new_on
       and len(new_on) == len({o["target_id"] for o in new_on})
       and cloud.cdb.scalar("SELECT offline_since FROM branches WHERE device_id='devB'") is None
       and time.time() - cloud.cdb.scalar("SELECT last_seen FROM branches WHERE device_id='devB'") < 5, str(len(new_on)))
    # Katta klip 5 daqiqadan uzoq yuklanayotganda (so'rov tugamagan) — "uzildi" deyilmaydi
    notify.inflight(idB, 1)
    cloud.cdb.execute("UPDATE branches SET last_seen=:t WHERE device_id='devB'", {"t": time.time() - 900})
    time.sleep(1.2)
    ok("Uzoq davom etayotgan yuklash paytida do'kon 'uzilgan' deyilmaydi",
       cloud.cdb.scalar("SELECT offline_since FROM branches WHERE device_id='devB'") is None)
    notify.inflight(idB, -1)
    cloud.cdb.execute("UPDATE branches SET last_seen=:t WHERE device_id='devB'", {"t": time.time()})
    notify.cfg.offline_sec = 0

    # --- eskalatsiya: javobsiz hodisa menejer va egasiga, qo'riqchiga emas, bir marta
    edge_event(base, "devA", tokA, 6, 60)
    wait(lambda: any("devA:6" in str(o["ref"]) for o in outbox("kind='event' AND status='sent'")))
    cloud.cdb.execute("UPDATE events SET received_at=:t WHERE uid='devA:6'", {"t": time.time() - 30})
    uid_of = {r["email"]: r["id"] for r in cloud.cdb.rows("SELECT id, email FROM users")}
    notify.cfg.escalate_sec = 10
    ok("Eskalatsiya: menejer va egasiga", wait(lambda: {o["user_id"] for o in outbox("kind='escalation'")}
                                              >= {uid_of["menejer@dokon.uz"], uid_of["egasi@dokon.uz"]}, 10)
       and not any(o["user_id"] == uid_of["qorovul@dokon.uz"] for o in outbox("kind='escalation'")),
       str({o["user_id"] for o in outbox("kind='escalation'")}))
    time.sleep(1.2)
    ok("Eskalatsiya bir marta", len(outbox("kind='escalation' AND channel='telegram'")) == 1)
    notify.cfg.escalate_sec = 0
    esc = [c for c in tg_calls("sendMessage", 5002) if "Javobsiz hodisa" in (c["data"].get("text") or "")]
    ok("Eskalatsiya xabarida ham ✅/❌ tugmalari", esc and esc[0]["data"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("r:"))

    # --- qayta urinish va doimiy xato
    tg.FAIL_NEXT["sendMessage"] = 2
    edge_event(base, "devA", tokA, 7, 65)
    ok("Telegram 2 marta 500 qaytarsa ham xabar yetadi (qayta urinish)", wait(lambda: (
        lambda rs: rs and all(o["status"] == "sent" for o in rs) and max(o["attempts"] for o in rs) >= 2)(
        outbox("ref='devA:7' AND channel='telegram'")), 10),
       str([(o["status"], o["attempts"]) for o in outbox("ref='devA:7' AND channel='telegram'")]))
    tg.FAIL_STATUS["sendMessage"] = 403
    tg.FAIL_NEXT["sendMessage"] = 1
    edge_event(base, "devB", tokB, 8, 66)
    ok("Bot bloklangan (403) — manzil o'chirildi, xabar failed", wait(lambda: cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE channel='telegram' AND enabled=:f", {"f": False}) == 1))
    tg.FAIL_STATUS.clear()
    code_m = httpx.post(f"{base}/api/v1/me/telegram/link", headers=manager).json()["code"]
    tg.text_message(5002, f"/start {code_m}", first_name="Menejer Vali")
    ok("Qayta ulanish o'chirilgan Telegram manzilini yoqadi", wait(lambda: cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE channel='telegram' AND enabled=:f", {"f": False}) == 0))
    PUSH_STATUS["code"] = 410
    edge_event(base, "devA", tokA, 9, 99)
    ok("Push obunasi bekor (410) — manzil o'chirildi", wait(lambda: cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE channel='push' AND enabled=:f", {"f": False}) == 1))
    PUSH_STATUS["code"] = 201

    # --- xabar navbatda turganida: hodisa tekshirildi / klip keldi
    notify.RETRY = (1.5, 1.5, 1.5, 1.5, 1.5)
    tg.FAIL_NEXT["sendMessage"] = 1000        # Telegram vaqtincha ishlamayapti → xabarlar navbatda (1.5 s dan keyin qayta)
    edge_event(base, "devA", tokA, 12, 66)
    wait(lambda: len([o for o in outbox("ref='devA:12' AND channel='telegram'") if o["attempts"] >= 1]) == 2, 5)
    httpx.post(f"{base}/api/v1/events/devA:12/review", json={"status": "tasdiqlangan"}, headers=manager)
    tg.FAIL_NEXT.pop("sendMessage", None)
    ok("Navbatda turganida tekshirilgan hodisa — eskirgan tugmali xabar yuborilmaydi", wait(lambda: (
        lambda rs: len(rs) == 2 and all(o["status"] == "skipped" and "tekshirildi" in (o["error"] or "") for o in rs))(
        outbox("ref='devA:12' AND channel='telegram' AND kind='event'")), 6),
       str([(o["status"], o["error"]) for o in outbox("ref='devA:12' AND channel='telegram'")]))
    tg.FAIL_NEXT["sendMessage"] = 1000
    edge_event(base, "devA", tokA, 13, 67)
    wait(lambda: len([o for o in outbox("ref='devA:13' AND channel='telegram'") if o["attempts"] >= 1]) == 2, 5)
    httpx.post(f"{base}/api/v1/edge/events/devA:13/clip", data={"meta": "{}"},
               files={"clip": ("c.mp4", os.urandom(3000), "video/mp4")},
               headers={"Authorization": f"Bearer {tokA}", "X-Device-Id": "devA"})
    tg.FAIL_NEXT.pop("sendMessage", None)
    ok("Klip xabardan oldin kelsa — xabar yuborilgach video javob bo'lib keladi", wait(lambda: len(
        [v for v in tg_calls("sendVideo") if "#13" in str(v["data"].get("caption")) and v["data"].get("reply_to_message_id")]) == 2, 8),
       str(len([v for v in tg_calls("sendVideo") if "#13" in str(v["data"].get("caption"))])))
    notify.RETRY = (0.3, 0.6, 1.0, 1.0, 1.0)

    # --- o'chirilgan xodim: manzillari o'chadi, navbatdagisi yuborilmaydi
    r = httpx.post(f"{base}/api/v1/users", json={"email": "vaqtincha@dokon.uz", "password": "Parol12345", "role": "guard",
                                                 "branch_ids": [idA]}, headers=owner).json()
    tmp_id = r["user"]["id"]
    tmp = login(base, "vaqtincha@dokon.uz", "Parol12345")
    code_t = httpx.post(f"{base}/api/v1/me/telegram/link", headers=tmp).json()["code"]
    tg.text_message(5010, f"/start {code_t}")
    wait(lambda: cloud.cdb.scalar("SELECT COUNT(*) FROM notify_targets WHERE user_id=:u", {"u": tmp_id}))
    httpx.post(f"{base}/api/v1/users/{tmp_id}/deactivate", headers=owner)
    ok("Xodim o'chirildi — Telegram manzili ham o'chdi", cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE user_id=:u AND enabled=:e", {"u": tmp_id, "e": True}) == 0)
    cloud.cdb.execute("UPDATE notify_targets SET enabled=:e WHERE user_id=:u", {"e": True, "u": tmp_id})
    tt = notify.targets(tmp_id)[0]
    notify.enqueue(tmp_id, tt, "test", "x", {"t_event": time.time()})
    ok("Faol bo'lmagan xodimga navbatdagi xabar yuborilmaydi", wait(lambda: any(
        o["status"] == "skipped" and o["error"] == "xodim faol emas" for o in outbox("user_id=:u", {"u": tmp_id})), 5)
       and not any("Sinov" in (c["data"].get("text") or "") for c in tg_calls("sendMessage", 5010)))

    # --- cheklovlar: sinov xabari tez-tez emas, manzillar soni
    notify.TEST_EVERY = 20.0
    r1 = httpx.post(f"{base}/api/v1/me/notifications/test", headers=guard)
    r2 = httpx.post(f"{base}/api/v1/me/notifications/test", headers=guard)
    ok("Sinov xabari 20 s da bir marta (navbatni to'ldirib bo'lmaydi)", r1.status_code == 200 and r2.status_code == 429,
       f"{r1.status_code} {r2.status_code}")
    notify.TEST_EVERY = 0.0
    notify.MAX_TARGETS = 2
    rs = [httpx.post(f"{base}/api/v1/me/push", json={"endpoint": f"http://127.0.0.1:{PUSH_PORT}/push/g{i}", "keys": keys},
                     headers=guard).status_code for i in range(3)]
    ok("Bitta kanalda manzillar soni cheklangan", rs == [200, 200, 400], str(rs))
    notify.MAX_TARGETS = 10
    httpx.post(f"{base}/api/v1/me/push", json={"endpoint": f"http://127.0.0.1:{PUSH_PORT}/push/g0", "keys": keys},
               headers=manager)
    ok("Bitta brauzer — bitta xodim: boshqa xodim ulasa, oldingisidan uziladi", cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE address=:a", {"a": f"http://127.0.0.1:{PUSH_PORT}/push/g0"}) == 1
       and cloud.cdb.scalar("SELECT user_id FROM notify_targets WHERE address=:a",
                            {"a": f"http://127.0.0.1:{PUSH_PORT}/push/g0"}) == uid_of["menejer@dokon.uz"])
    ok("Jonli toast qoidasi: eski, video tahlili — yo'q; soati orqada do'kon — bor",
       not notify.should_alert({"created_at": time.time() - 7200}, {})
       and not notify.should_alert({"created_at": time.time(), "job_id": 3}, {})
       and not notify.should_alert({"created_at": time.time(), "status": "tasdiqlangan"}, {})
       and notify.should_alert({"created_at": time.time() - 700}, {"clock_skew": 700})
       and notify.should_alert({"created_at": time.time()}, {}))

    # --- sinov xabari, statistika, manzilni o'chirish
    r = httpx.post(f"{base}/api/v1/me/notifications/test", headers=manager).json()
    ok("Sinov xabari barcha faol manzillarga", r["ok"] and r["count"] >= 1 and wait(lambda: any(
        "Sinov xabari" in (c["data"].get("text") or "") for c in tg_calls("sendMessage", 5002))))
    st = httpx.get(f"{base}/api/v1/notify/stats", headers=owner).json()["channels"]
    ok("Statistika: Telegram yuborilgan, kechikish p95 ≤ 5 s", st["telegram"]["sent"] >= 5 and st["telegram"]["event_p95"] is not None
       and st["telegram"]["event_p95"] <= 5.0 and st["telegram"]["failed"] >= 1, json.dumps(st["telegram"]))
    ok("Qo'riqchi statistikani ko'rolmaydi", httpx.get(f"{base}/api/v1/notify/stats", headers=guard).status_code == 403)
    mt = httpx.get(f"{base}/api/v1/me/notifications", headers=manager).json()["targets"]
    em = [x for x in mt if x["channel"] == "email"][0]
    ok("Boshqaning manzilini o'chirib bo'lmaydi", httpx.post(f"{base}/api/v1/me/targets/{em['id']}/delete",
                                                              headers=guard).status_code == 404)
    ok("O'z manzilini o'chirish", httpx.post(f"{base}/api/v1/me/targets/{em['id']}/delete", headers=manager).json()["ok"])
    r = httpx.put(f"{base}/api/v1/me/notifications", json={"prefs": {"min_score": "abc"}}, headers=guard)
    ok("Noto'g'ri sozlama — 400", r.status_code == 400)
    r = httpx.put(f"{base}/api/v1/me/notifications", json={"prefs": {"min_score": 150, "camera": True}}, headers=guard).json()
    ok("Sozlama saqlandi (ball 0..100 oralig'iga keltirildi)", r["prefs"]["min_score"] == 100 and r["prefs"]["camera"] is True)

    # --- bot buyruqlari
    tg.text_message(5002, "/holat")
    ok("/holat — filiallar holati", wait(lambda: any("Do'konlar" in (c["data"].get("text") or "") and "Yunusobod" in c["data"]["text"]
                                                     for c in tg_calls("sendMessage", 5002))))
    tg.text_message(5002, "/bugun")
    ok("/bugun — bugungi hodisalar", wait(lambda: any("Bugun" in (c["data"].get("text") or "") for c in tg_calls("sendMessage", 5002))))

    # --- cookie sessiya, CSRF, jonli oqim
    c = httpx.Client(base_url=base)
    r = c.post("/api/v1/auth/login", json={"email": "menejer@dokon.uz", "password": "Parol12345"})
    ok("Login cookie o'rnatadi (HttpOnly, SameSite=Strict)", "hc_session" in c.cookies and
       "httponly" in r.headers.get("set-cookie", "").lower() and "samesite=strict" in r.headers.get("set-cookie", "").lower())
    ok("Cookie bilan /me", c.get("/api/v1/me").json()["user"]["email"] == "menejer@dokon.uz")
    r = c.post("/api/v1/me/notifications/test", headers={"Origin": "https://yomon.example"})
    ok("Cookie + boshqa sayt (Origin) — 403 CSRF", r.status_code == 403)
    r = c.post("/api/v1/me/notifications/test", headers={"Origin": base, "Sec-Fetch-Site": "same-origin"})
    ok("Cookie + o'z sayti — o'tadi", r.status_code == 200)
    r = c.post("/api/v1/me/notifications/test", headers={"Origin": "null"})
    ok("Cookie + Origin: null — 403", r.status_code == 403)
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "menejer@dokon.uz", "password": "Parol12345"},
                   headers={"Origin": "https://yomon.example"})
    ok("Boshqa saytdan login — 403 (login CSRF)", r.status_code == 403)
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "menejer@dokon.uz", "password": "Parol12345", "session": "cookie"})
    ok("Panel rejimida login javobida token yo'q (faqat cookie)", r.status_code == 200 and "token" not in r.json()
       and "hc_session" in r.cookies)
    got_sse: list[str] = []

    def listen():
        try:
            with c.stream("GET", "/api/v1/stream", timeout=10) as resp:
                for line in resp.iter_lines():
                    if line.startswith("event:") or (got_sse and line.startswith("data:")):
                        got_sse.append(line)
                        if line.startswith("data:") and "event_new" in got_sse[-2]:
                            return
        except Exception as e:  # noqa: BLE001
            got_sse.append(f"xato {e}")
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(0.5)
    t0 = time.time()
    edge_event(base, "devB", tokB, 10, 50)
    th.join(5)
    ok("Jonli oqim (SSE): yangi hodisa 1 s ichida", any("event_new" in x for x in got_sse) and time.time() - t0 < 2,
       f"{got_sse[:3]}, {time.time() - t0:.2f} s")
    data_new = next((json.loads(x[5:]) for x in got_sse if x.startswith("data:") and "devB:10" in x), {})
    ok("SSE event_new: filial, kamera, ball va 'alert' belgisi", data_new.get("alert") is True
       and data_new.get("branch_name") == "Yunusobod" and data_new.get("score") == 50, str(data_new)[:160])
    gc = httpx.Client(base_url=base)
    gc.post("/api/v1/auth/login", json={"email": "qorovul@dokon.uz", "password": "Parol12345"})
    got_g: list[str] = []

    def listen_g():
        try:
            with gc.stream("GET", "/api/v1/stream", timeout=3) as resp:
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        got_g.append(line)
        except Exception:  # noqa: BLE001
            pass
    th = threading.Thread(target=listen_g, daemon=True)
    th.start()
    time.sleep(0.5)
    edge_event(base, "devB", tokB, 11, 51)
    th.join(4)
    ok("Qo'riqchi boshqa filial hodisasini jonli oqimda ko'rmaydi", not any("event_new" in x for x in got_g))
    c.post("/api/v1/me/push", json={"endpoint": f"http://127.0.0.1:{PUSH_PORT}/push/shared", "keys": keys},
           headers={"Origin": base})
    c.post("/api/v1/auth/logout", json={"push_endpoint": f"http://127.0.0.1:{PUSH_PORT}/push/shared"}, headers={"Origin": base})
    ok("Chiqishdan keyin cookie ishlamaydi", c.get("/api/v1/me").status_code == 401)
    ok("Chiqishda shu brauzer push obunasi uzildi (umumiy kompyuter)", cloud.cdb.scalar(
        "SELECT COUNT(*) FROM notify_targets WHERE address=:a", {"a": f"http://127.0.0.1:{PUSH_PORT}/push/shared"}) == 0)

    # --- kamera holati: do'kon soati orqada bo'lsa ham xabar keladi
    httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time() - 700}, headers={"Authorization": f"Bearer {tokA}",
                                                                                        "X-Device-Id": "devA"})
    n_cam = len(outbox("kind='camera'"))
    httpx.post(f"{base}/api/v1/edge/health", json={"items": [{"key": "h:2:down:9", "t": time.time() - 700, "state": "down",
               "source_id": 2, "source_name": "Kirish", "since": time.time() - 760}]},
               headers={"Authorization": f"Bearer {tokA}", "X-Device-Id": "devA"})
    ok("Do'kon soati 12 daqiqa orqada — kamera xabari baribir keladi", wait(lambda: len(outbox("kind='camera'")) > n_cam, 5))
    httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time()}, headers={"Authorization": f"Bearer {tokA}",
                                                                                 "X-Device-Id": "devA"})


def main() -> int:
    serve_smtp(SMTP_PORT)
    serve_app(push_app, PUSH_PORT)
    run("SQLite")
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        run("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)
    else:
        print("\n  (PostgreSQL sinovi o'tkazilmadi: CLOUD_TEST_PG_URL berilmagan)")
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
