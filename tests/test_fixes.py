"""1.4.1 tuzatishlari sinovi: login himoyasi, CSRF, sarlavhalar, CSV, RTSP paroli, model yuklash tekshiruvi,
Engine.reload (qat'iy), buyum modeli ixtiyoriy, statik partiyali model, buzuq model (.broken), kamera xato holatidan
tiklanishi, Telegram (haqiqiy ustuvorlik, tugmalar alohida oqimda, klip qayta urinish, "klip yaratilmadi", ulanish
kodi himoyasi, toza to'xtash), nazoratchi (toza to'xtatish buyrug'i, port tekshiruvi, proksisiz sog'liq tekshiruvi),
.env BOM, start_windows.bat belgisi, ffmpeg osilishi.

Ishga tushirish:  rm -rf data && python -m tests.test_fixes
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import uvicorn  # noqa: E402

from app import config, db  # noqa: E402
from tests import mock_telegram as tg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT, TG_PORT = 8791, 8792
BASE = f"http://127.0.0.1:{PORT}"
results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def wait(cond, timeout=10.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


def serve(app, port: int) -> uvicorn.Server:
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    return srv


REMOTE = {"X-Forwarded-For": "192.168.1.77"}     # 127.0.0.1 orqali kelgan "tarmoqdagi boshqa kompyuter"


def logged_in(headers: dict | None = None) -> httpx.Client:
    from app import web

    web._login_fails.clear()
    c = httpx.Client(base_url=BASE, follow_redirects=False, headers=headers or {}, timeout=30)
    r = c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"})
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    return c


# ----------------------------------------------------------------------------- web: kirish va CSRF
def web_suite() -> None:
    from app import web

    print("\n--- Web panel: kirish, CSRF, sarlavhalar ---")
    r = httpx.get(f"{BASE}/login")
    ok("Login sahifasi shu kompyuterdan — standart parol eslatmasi bor", "admin123" in r.text)
    r = httpx.get(f"{BASE}/login", headers=REMOTE)
    ok("Tarmoqdagi boshqa kompyuterdan — eslatma YO'Q", r.status_code == 200 and "admin123" not in r.text)
    # Blok IP bo'yicha (X-Forwarded-For ga ishonilmaydi — uni har kim yozishi mumkin): sinov shu kompyuterdan
    web._login_fails.clear()
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=30)
    for _ in range(web.LOGIN_MAX_FAILS):
        c.post("/login", data={"username": "admin", "password": "xato", "next": "/"})
    r = c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"})
    ok("5 xato paroldan keyin IP bloklanadi (to'g'ri parol ham)", r.headers.get("location") == "/login?error=2",
       r.headers.get("location", ""))
    page = c.get("/login?error=2").text
    ok("Blok haqida tushunarli xabar (necha soniya)", "soniyadan keyin" in page)
    saved = dict(web._login_fails)
    web._login_fails.clear()
    dpage = httpx.get(f"{BASE}/login?error=default").text
    web._login_fails.update(saved)
    ok("Standart parol bilan tarmoqdan kirish rad etilganda — aniq sabab (\"parol noto'g'ri\" emas)",
       'data-testid="login-default"' in dpage and "Login yoki parol noto'g'ri" not in dpage)
    ok("Boshqa IP bloklanmagan", web.login_blocked("192.168.1.78") == 0 and web.login_blocked("127.0.0.1") > 0)
    st = web._login_fails["127.0.0.1"]
    first = st["until"] - time.time()
    st["until"] = time.time() - 1
    for _ in range(web.LOGIN_MAX_FAILS):
        c.post("/login", data={"username": "admin", "password": "xato", "next": "/"})
    second = web._login_fails["127.0.0.1"]["until"] - time.time()
    ok("Takroriy blok uzoqroq (1 daqiqa → 2 daqiqa)", 50 < first <= 61 and 110 < second <= 121, f"{first:.0f}/{second:.0f}")
    web._login_fails.clear()
    bad = []
    for nxt in ("//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)", "/\t/evil.example"):
        r = httpx.post(f"{BASE}/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": nxt})
        if r.headers.get("location") != "/":
            bad.append(f"{nxt!r} → {r.headers.get('location')}")
    ok("Kirishdan keyin boshqa saytga yo'naltirilmaydi (next=...)", not bad, "; ".join(bad))
    r = httpx.post(f"{BASE}/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/events?status=yangi"})
    ok("Shu saytdagi sahifaga qaytadi", r.headers.get("location") == "/events?status=yangi")

    s = logged_in()
    good = s.post("/api/settings", json={"cooldown_sec": 15})
    evil = s.post("/api/settings", json={"cooldown_sec": 1}, headers={"Origin": "http://evil.example"})
    sfs = s.post("/api/settings", json={"cooldown_sec": 1}, headers={"Sec-Fetch-Site": "cross-site"})
    nul = s.post("/api/events/bulk", json={"action": "delete_rejected"}, headers={"Origin": "null"})
    ok("CSRF: o'z sahifasidan — 200; boshqa sayt (Origin / Sec-Fetch-Site / null) — 403",
       (good.status_code, evil.status_code, sfs.status_code, nul.status_code) == (200, 403, 403, 403),
       str((good.status_code, evil.status_code, sfs.status_code, nul.status_code)))
    ok("CSRF urinishi sozlamani o'zgartirmadi", db.get_setting("cooldown_sec") in ("15", "15.0"))
    r = httpx.post(f"{BASE}/login", data={"username": "admin", "password": config.ADMIN_PASSWORD},
                   headers={"Sec-Fetch-Site": "cross-site"})
    ok("Boshqa saytdan login (login CSRF) — 403", r.status_code == 403)
    r = s.get("/")
    need = ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy")
    ok("HTML sahifada xavfsizlik sarlavhalari (CSP, XFO, nosniff, Referrer)", all(h in r.headers for h in need)
       and "frame-ancestors" in r.headers["content-security-policy"], str([h for h in need if h not in r.headers]))
    ok("API javobida nosniff", s.get("/api/summary").headers.get("x-content-type-options") == "nosniff")

    old = (web.HOST, web.ADMIN_PASSWORD)
    web.HOST, web.ADMIN_PASSWORD = "0.0.0.0", "admin123"
    warn = 'data-testid="insecure-default"' in s.get("/").text
    web.HOST = "127.0.0.1"
    calm = 'data-testid="insecure-default"' not in s.get("/").text
    web.HOST, web.ADMIN_PASSWORD = old
    ok("Standart parol + tarmoqqa ochiq — qizil ogohlantirish; faqat shu kompyuter — yo'q", warn and calm)

    # CSV in'ektsiya
    eid = db.insert_event({"source_id": 1, "source_name": "=cmd|' /C calc'!A0", "created_at": time.time(), "score": 70,
                           "level": "o'rta", "kind": "pocket", "reasons": ["+SUM(1,2)"], "breakdown": {}, "track_id": 1,
                           "status": "yangi", "note": "@HYPERLINK(\"http://evil\")"})
    csv_text = s.get("/api/export/events.csv").content.decode("utf-8-sig")
    line = [ln for ln in csv_text.splitlines() if ln.startswith(f"{eid},")]
    ok("CSV eksport: formula sifatida bajariladigan matn neytrallangan (=, +, @)", line and "'=cmd" in line[0]
       and "'@HYPERLINK" in line[0] and "'+SUM" in line[0], line[0][:120] if line else "qator yo'q")
    db.execute("DELETE FROM events WHERE id=?", (eid,))

    # RTSP paroli
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Ombor", "rtsp", "rtsp://admin:Juda$ir1i@10.0.0.9:554/Streaming/101", 0, 1, time.time()))
    src = [x for x in s.get("/api/sources").json() if x["id"] == sid][0]
    ok("RTSP login/paroli panelga chiqmaydi (bazada saqlanadi)", "Juda$ir1i" not in src["uri"] and "admin" not in src["uri"]
       and src["uri"].startswith("rtsp://***@10.0.0.9") and "Juda$ir1i" in db.scalar("SELECT uri FROM sources WHERE id=?", (sid,)),
       src["uri"])
    ok("Kameralar sahifasida ham parol yo'q", "Juda$ir1i" not in s.get("/sources").text + s.get("/api/summary").text)
    db.execute("DELETE FROM sources WHERE id=?", (sid,))

    # Nazoratchining toza to'xtatish buyrug'i
    class FakeServer:
        should_exit = False
    fake = FakeServer()
    old_ref, old_tok = list(web.SERVER_REF), web.CTL_TOKEN
    web.SERVER_REF[:] = [fake]
    web.CTL_TOKEN = "sinov-token-123"           # run.py --supervise HUSHYOR_CTL_TOKEN orqali beradi
    r1 = httpx.post(f"{BASE}/healthz/shutdown")
    r2 = httpx.post(f"{BASE}/healthz/shutdown", headers={"X-Hushyor-Token": "xato"})
    r3 = httpx.post(f"{BASE}/healthz/shutdown", headers={"X-Hushyor-Token": "sinov-token-123", **REMOTE})
    ok("To'xtatish buyrug'i: tokensiz / xato token — 403, tarmoqdan — 404", (r1.status_code, r2.status_code,
       r3.status_code) == (403, 403, 404) and not fake.should_exit and not web.SHUTDOWN.is_set(),
       str((r1.status_code, r2.status_code, r3.status_code)))
    r4 = httpx.post(f"{BASE}/healthz/shutdown", headers={"X-Hushyor-Token": "sinov-token-123"})
    ok("To'g'ri token, shu kompyuterdan — server toza to'xtaydi", r4.status_code == 200 and fake.should_exit
       and web.SHUTDOWN.is_set())
    web.SHUTDOWN.clear()
    web.SERVER_REF[:] = old_ref
    web.CTL_TOKEN = old_tok
    models_upload(s)


def models_upload(s: httpx.Client) -> None:
    from app import web
    from app.ai import model_zoo

    print("\n--- Model yuklash (tekshiruvdan keyin) ---")
    dst = model_zoo.path("yolox-nano")
    before = model_zoo.sha256(dst)
    orig = dst.read_bytes()
    try:
        _models_upload(s, web, model_zoo, dst, before, orig)
    finally:
        dst.write_bytes(orig)              # asl model fayli qaytariladi (sinov nima bo'lsa ham)


def _models_upload(s, web, model_zoo, dst, before, orig) -> None:
    r = s.post("/api/models/upload", files={"file": ("yolox_nano.onnx", b"bu model emas" * 100, "application/octet-stream")})
    ok("Buzuq .onnx rad etiladi, mavjud model joyida qoladi", r.status_code == 400 and model_zoo.sha256(dst) == before,
       r.text[:100])
    r = s.post("/api/models/upload", files={"file": ("yolox_nano.onnx", model_zoo.path("yolo11n").read_bytes(), "x")})
    ok("Boshqa model fayli (shakli mos emas) — rad", r.status_code == 400 and model_zoo.sha256(dst) == before, r.text[:120])
    r = s.post("/api/models/upload", files={"file": ("nimadir.onnx", b"x", "x")})
    ok("Nomidan model aniqlanmasa — 400", r.status_code == 400)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("yolox_nano/end2end.onnx", orig)
    r = s.post("/api/models/upload", files={"file": ("yolox_nano.zip", buf.getvalue(), "application/zip")})
    ok("To'g'ri model (.zip ichida) qabul qilinadi", r.status_code == 200 and r.json().get("model") == "yolox-nano", r.text[:100])
    r = s.post("/api/models/upload", files={"file": ("yolox_nano.onnx", orig, "x")})
    ok("To'g'ri .onnx to'g'ridan-to'g'ri ham qabul qilinadi", r.status_code == 200)
    old = web.MODEL_UPLOAD_LIMIT
    web.MODEL_UPLOAD_LIMIT = 1000
    r = s.post("/api/models/upload", files={"file": ("yolox_nano.onnx", orig, "x")})
    web.MODEL_UPLOAD_LIMIT = old
    ok("Hajm chegarasidan katta — 413 (vaqtinchalik fayl qolmaydi)", r.status_code == 413
       and not list(config.UPLOADS_DIR.glob("_model_*")))


# ----------------------------------------------------------------------------- modellar
def models_suite() -> None:
    from app.ai import model_zoo, runtime
    from app.ai.engine import Engine

    print("\n--- Modellar: tekshiruv, qat'iy almashtirish, buzuq fayl, ixtiyoriy detektor, statik partiya ---")
    ok("validate: YOLO11n-pose va YOLOX-nano to'g'ri", model_zoo.validate("yolo11n-pose", model_zoo.path("yolo11n-pose"))
       and model_zoo.validate("yolox-nano", model_zoo.path("yolox-nano")))
    try:
        model_zoo.validate("rtmpose-t", model_zoo.path("yolox-nano"))
        ok("validate: RTMPose o'rnida YOLOX — xato", False)
    except ValueError as e:
        ok("validate: RTMPose o'rnida YOLOX — xato", "RTMPose" in str(e), str(e)[:80])
    db.set_settings({"model_set": "yolo11"})
    eng = Engine.get()
    rtm = model_zoo.path("rtmpose-t")
    had_rtm = rtm.exists()
    saved = rtm.read_bytes() if had_rtm else None
    rtm.write_bytes(b"buzuq fayl")
    try:
        Engine.reload(model_set="open")
        ok("Qat'iy almashtirish: 'open' yuklanmadi — xato qaytadi", False)
    except Exception as e:  # noqa: BLE001
        ok("Qat'iy almashtirish: 'open' yuklanmadi — xato qaytadi", True, e.__class__.__name__)
    ok("Eski to'plam (yolo11) joyida qoldi, sozlama o'zgarmadi", Engine.get() is eng and eng.model_set == "yolo11"
       and db.get_setting("model_set") == "yolo11")
    ok("Buzuq model fayli .broken nomiga olindi, to'plam 'yo'q' ko'rinadi", not rtm.exists()
       and rtm.with_name(rtm.name + ".broken").exists() and "rtmpose-t" in model_zoo.missing("open"))
    rtm.with_name(rtm.name + ".broken").unlink(missing_ok=True)
    if saved:
        rtm.write_bytes(saved)
    det = model_zoo.path("yolo11n")
    hidden = det.with_name("yolo11n.onnx.hidden")
    det.replace(hidden)
    try:
        e2 = Engine(model_set="yolo11", save_bench=False)
        frame = np.full((360, 640, 3), 120, np.uint8)
        ok("yolo11n.onnx yo'q — dastur to'xtamaydi, buyum detektori o'chiq", e2.det is None
           and e2.detect_objects(frame) == [] and isinstance(e2.detect_persons(frame), list))
    except Exception as e:  # noqa: BLE001
        ok("yolo11n.onnx yo'q — dastur to'xtamaydi, buyum detektori o'chiq", False, repr(e))
    finally:
        hidden.replace(det)
    # Statik partiyali (batch=1) model — bittadan yuritib birlashtiriladi
    import onnx
    from onnx import TensorProto, helper

    td = Path(tempfile.mkdtemp())
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 4, 4])
    y1 = helper.make_tensor_value_info("a", TensorProto.FLOAT, [1, 3, 4, 4])
    y2 = helper.make_tensor_value_info("b", TensorProto.FLOAT, [1, 3, 4, 4])
    g = helper.make_graph([helper.make_node("Identity", ["x"], ["a"]), helper.make_node("Neg", ["x"], ["b"])],
                          "g", [x], [y1, y2])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8
    onnx.save(m, str(td / "static.onnx"))
    rn = runtime.Runner(td / "static.onnx", "onnxruntime", 1)
    inp = np.random.rand(3, 3, 4, 4).astype(np.float32)
    a, b = rn.run_all(inp)
    ok("Statik batch=1 model (RTMPose rasmiy fayli kabi) 3 odam bilan ishlaydi", rn.fixed_batch == 1 and a.shape == (3, 3, 4, 4)
       and np.allclose(a, inp) and np.allclose(b, -inp))


# ----------------------------------------------------------------------------- kamera, ffmpeg
def camera_suite() -> None:
    from app.ai import clipper, worker

    print("\n--- Kamera xato holatidan tiklanadi, RTSP vaqt chegarasi, ffmpeg osilishi ---")
    w = worker.SourceWorker({"id": 77, "name": "Sinov", "kind": "file", "uri": "x.mp4", "loop": 1}, None)
    calls = {"n": 0}

    def step(frame, t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bitta kadrda xato")
        return None, ([], [], []), [], {}
    w.proc.step = step
    w.reader = threading.Thread(target=lambda: None)
    w.latest_frame, w.latest_t = np.zeros((10, 10, 3), np.uint8), time.time()
    th = threading.Thread(target=w.run, daemon=True)
    th.start()
    seen_err = wait(lambda: w.state == "xato", 5)

    def fresh():
        with w.frame_lock:
            w.latest_t = time.time()
        return w.state == "ishlayapti"
    recovered = wait(fresh, 6)
    w.stop()
    ok("Bitta kadrdagi AI xatosidan keyin kamera 'ishlayapti' ga qaytadi", seen_err and recovered and w.error == "",
       f"{w.state} {w.error}")
    ok("RTSP ochish/o'qish vaqt chegarasi 8 s", worker.RTSP_TIMEOUT_MS == 8000)
    # ffmpeg osilib qolsa — taymer to'xtatadi (soxta "ffmpeg": stdin o'qimaydi)
    fake = Path(tempfile.mkdtemp()) / "ffmpeg_hang.py"
    fake.write_text("import time\ntime.sleep(600)\n")
    orig_exe, orig_t = clipper.ffmpeg_exe, clipper.FFMPEG_TIMEOUT
    clipper.ffmpeg_exe = lambda: sys.executable
    clipper.FFMPEG_TIMEOUT = 2
    import subprocess

    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        return real_popen([sys.executable, str(fake)], **kw)
    subprocess.Popen = popen
    t0 = time.time()
    try:
        out = Path(tempfile.mkdtemp()) / "c.mp4"
        imgs = [np.zeros((360, 640, 3), np.uint8)] * 400
        clipper.write_mp4(imgs, out, 12)
    finally:
        subprocess.Popen = real_popen
        clipper.ffmpeg_exe, clipper.FFMPEG_TIMEOUT = orig_exe, orig_t
    took = time.time() - t0
    ok("ffmpeg osilib qolsa vaqt chegarasida to'xtatiladi (zaxira kodlovchiga o'tadi)", took < 30, f"{took:.1f} s")


# ----------------------------------------------------------------------------- Telegram
def telegram_suite() -> None:
    from app import telegram_bot as tb

    print("\n--- Telegram: ustuvorlik, tugmalar, klip qayta urinish, kod himoyasi, toza to'xtash ---")
    tg.CALLS.clear()
    tg.UPDATES.clear()
    db.set_settings({"telegram_enabled": "1", "telegram_send_video": "1", "telegram_min_score": "0"})
    db.execute("DELETE FROM subscribers")
    db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active, kb_cleared) VALUES(?,?,?,?,1,1)",
               (5001, "Qorovul", "q", time.time()))
    # Ustuvorlik: 2 bajaruvchi band (sekin vazifalar), navbatda 6 ta klip va keyin 1 ta signal → signal birinchi
    bot = tb.TelegramBot(token="", api_base="http://127.0.0.1:1")
    order: list[str] = []
    gate = threading.Event()

    def slow_clip(a, b, t):
        order.append(f"clip{a}")
        gate.wait(5)

    def alert(a, b, t):
        order.append(f"alert{a}")
    bot._send_clip, bot._send_alert = slow_clip, alert
    for i in range(8):
        bot._enqueue(tb.PRIO_CLIP, "clip", i)
    workers = [threading.Thread(target=bot._worker_loop, daemon=True) for _ in range(2)]
    for w_ in workers:
        w_.start()
    wait(lambda: len(order) >= 2, 3)
    bot._enqueue(tb.PRIO_ALERT, "alert", 99)
    time.sleep(0.2)
    gate.set()
    wait(lambda: "alert99" in order, 5)
    bot.stop_evt.set()
    ok("Navbat ustuvorligi: band bajaruvchi bo'shagach — avval yangi signal, keyin qolgan kliplar",
       "alert99" in order and order.index("alert99") == 2, str(order[:5]))

    bot = tb.TelegramBot(token="777:TEST", api_base=f"http://127.0.0.1:{TG_PORT}")
    bot.start()
    wait(lambda: bot.me is not None, 10)
    clip = config.CLIPS_DIR / "fix_clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(300_000))
    snap = config.CLIPS_DIR / "fix_snap.jpg"
    import cv2

    cv2.imwrite(str(snap), np.full((180, 320, 3), 90, np.uint8))
    eid = db.insert_event({"source_id": 1, "source_name": "Kassa", "created_at": time.time(), "t_detect": time.time(),
                           "score": 80, "level": "yuqori", "kind": "pocket", "reasons": ["sabab"], "breakdown": {},
                           "track_id": 1, "status": "yangi", "thumb_path": snap.name})
    bot.notify_alert(eid)
    wait(lambda: db.row("SELECT * FROM tg_messages WHERE event_id=?", (eid,)), 10)
    # Sekin video yuklash (2 MB/s ≈ 0.15 s) paytida tugma bosish — darhol javob
    os.environ["MOCK_TG_BW"] = "300000"
    db.update_event(eid, clip_path=clip.name)
    bot.notify_clip(eid)
    time.sleep(0.2)
    t_cb = time.time()
    tg.callback(5001, f"see:{eid}")
    answered = wait(lambda: any(c["method"] == "answerCallbackQuery" and c["t"] >= t_cb for c in tg.CALLS), 10)
    cb_lat = (answered and [c["t"] for c in tg.CALLS if c["method"] == "answerCallbackQuery" and c["t"] >= t_cb][0] - t_cb)
    upload_done = wait(lambda: db.scalar("SELECT kind FROM tg_messages WHERE event_id=?", (eid,)) == "video", 15)
    os.environ.pop("MOCK_TG_BW", None)
    ok("Video yuklanayotganda tugma bosilishiga javob kutmaydi (alohida oqim)", answered and upload_done and cb_lat < 0.8,
       f"{cb_lat:.2f} s")

    # Klip tarmoq xatosi: edit ham, javob-video ham 3 marta xato → 15 s (sinovda 0.5 s) dan keyin qayta → yetadi
    tb.CLIP_RETRY_SEC = 0.5
    e2 = db.insert_event({"source_id": 1, "source_name": "Kassa", "created_at": time.time(), "t_detect": time.time(),
                          "score": 80, "level": "yuqori", "kind": "torso", "reasons": ["sabab"], "breakdown": {},
                          "track_id": 2, "status": "yangi", "thumb_path": snap.name})
    bot.notify_alert(e2)
    wait(lambda: db.row("SELECT * FROM tg_messages WHERE event_id=?", (e2,)), 10)
    tg.FAIL_NEXT.update({"editMessageMedia": 3, "sendVideo": 3})
    db.update_event(e2, clip_path=clip.name)
    bot.notify_clip(e2)
    got = wait(lambda: db.scalar("SELECT kind FROM tg_messages WHERE event_id=?", (e2,)) in ("video", "photo_video"), 20)
    logs = db.rows("SELECT ok FROM notify_log WHERE event_id=? AND stage='clip' ORDER BY id", (e2,))
    ok("Klip tarmoq xatosi bilan yetmadi → avtomatik qayta yuborildi va yetdi", got and logs and logs[0]["ok"] == 0
       and logs[-1]["ok"] == 1, str([x["ok"] for x in logs]))
    tg.FAIL_NEXT.clear()

    # Klip yaratilmagan: "tayyorlanmoqda" o'rniga ogohlantirish
    e3 = db.insert_event({"source_id": 1, "source_name": "Kassa", "created_at": time.time(), "t_detect": time.time(),
                          "score": 80, "level": "yuqori", "kind": "bag", "reasons": ["sabab"], "breakdown": {},
                          "track_id": 3, "status": "yangi", "thumb_path": snap.name})
    bot.notify_alert(e3)
    wait(lambda: db.row("SELECT * FROM tg_messages WHERE event_id=?", (e3,)), 10)
    first_cap = [c for c in tg.calls("sendPhoto") if f"#{e3}" in str(c["data"].get("caption"))]
    db.update_event(e3, clip_path=None, clip_error=1)
    # Vaqt bo'yicha emas, ro'yxatdagi o'rin bo'yicha: Windows'da time.time() qadami ~16 ms — tez javob o'sha
    # "lahza"ga tushib qolardi (2.0.0)
    n_before = len(tg.calls("editMessageCaption"))
    bot.notify_clip(e3)
    ed = wait(lambda: [c for c in tg.calls("editMessageCaption")[n_before:] if f"#{e3}" in str(c["data"].get("caption"))],
              10)
    ok("Klip yaratilmasa xabarda '⚠️ klip yaratilmadi' (tayyorlanmoqda emas)", first_cap and "tayyorlanmoqda" in
       first_cap[0]["data"]["caption"] and ed and "yaratilmadi" in ed[-1]["data"]["caption"]
       and "tayyorlanmoqda" not in ed[-1]["data"]["caption"],
       f"birinchi: {bool(first_cap)}, tahrir: {[str(c['data'].get('caption'))[:60] for c in (ed or [])]}")

    # Ulanish kodini tavakkal qilish
    code = db.get_setting("connect_code")
    for i in range(tb.CODE_TRIES):
        tg.text_message(6001, f"/start {100000 + i if str(100000 + i) != code else 999999}")
    wait(lambda: len([c for c in tg.calls("sendMessage") if c["data"].get("chat_id") == 6001]) >= tb.CODE_TRIES, 10)
    tg.text_message(6001, f"/start {code}")
    blocked = wait(lambda: any("Ko'p noto'g'ri" in str(c["data"].get("text")) for c in tg.calls("sendMessage")
                               if c["data"].get("chat_id") == 6001), 10)
    ok("5 xato koddan keyin shu chat bloklanadi (to'g'ri kod ham o'tmaydi)", blocked and not db.scalar(
        "SELECT 1 FROM subscribers WHERE chat_id=6001"))
    tg.text_message(6002, code)
    ok("Boshqa chat to'g'ri kod bilan ulanadi", wait(lambda: db.scalar("SELECT 1 FROM subscribers WHERE chat_id=6002"), 10))
    for i in range(tb.CODE_ROTATE_AFTER):
        tg.text_message(7000 + i, f"{200000 + i}")
    rotated = wait(lambda: db.get_setting("connect_code") != code, 15)
    ok("Ko'p chatdan tavakkal (30 xato) — ulanish kodi o'zi almashadi", rotated)

    bot.stop()
    # Toza to'xtash: katta video yuklanayotganda dastur (jarayon) darhol yopiladi — alohida jarayonda o'lchanadi
    os.environ["MOCK_TG_BW"] = "20000"          # 300 KB → ~15 s yuklash
    script = f"""
import os, sys, time
sys.path.insert(0, {str(ROOT)!r})
os.environ["TELEGRAM_BOT_TOKEN"] = ""
from app import db, telegram_bot as tb
db.init_db()
b = tb.TelegramBot(token="777:TEST", api_base="http://127.0.0.1:{TG_PORT}")
b.start()
while b.me is None: time.sleep(0.05)
b.notify_event({e3})
db.update_event({e3}, clip_path={clip.name!r}, clip_error=0)
time.sleep(1.5)
t0 = time.time(); b.stop(); print("STOP", round(time.time() - t0, 2), flush=True)
"""
    db.update_event(e3, clip_path=clip.name, clip_error=0)
    t0 = time.time()
    import subprocess

    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    took = time.time() - t0
    os.environ.pop("MOCK_TG_BW", None)
    ok("Dastur to'xtatilganda bot uzun video yuklashni kutmaydi (jarayon darhol yopiladi)",
       "STOP" in out.stdout and took < 8, f"{took:.1f} s, {out.stdout.strip()[-30:]} {out.stderr.strip()[-120:]}")


# ----------------------------------------------------------------------------- nazoratchi, .env, bat
def runner_suite() -> None:
    print("\n--- Nazoratchi, port, .env BOM, start_windows.bat ---")
    import socket

    sys.path.insert(0, str(ROOT))
    import run

    ok("Port band — ikkinchi nusxa ishga tushmaydi (bazaga tegmaydi)", run.port_busy("127.0.0.1", PORT))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    ok("Bo'sh port — band emas", not run.port_busy("127.0.0.1", free))
    os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"   # ishlamaydigan proksi
    try:
        healthy = run._healthy(PORT)
    finally:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("http_proxy", None)
    ok("Sog'liq tekshiruvi tizim proksisidan o'tmaydi", healthy)

    class P:
        def __init__(self):
            self.alive, self.waited = True, []

        def poll(self):
            return None if self.alive else 0

        def wait(self, t=None):
            self.waited.append(t)
            self.alive = False
            return 0

        def terminate(self):
            self.terminated = True
    from app import web

    class FS:
        should_exit = False
    fs = FS()
    old_ref, old_tok = list(web.SERVER_REF), web.CTL_TOKEN
    web.SERVER_REF[:] = [fs]
    web.CTL_TOKEN = "tok-xyz"
    p = P()
    run.stop_child(p, PORT, "tok-xyz")
    ok("Nazoratchi bolani HTTP buyruq bilan toza to'xtatadi (TerminateProcess emas)", fs.should_exit
       and not getattr(p, "terminated", False))
    web.SERVER_REF[:] = old_ref
    web.CTL_TOKEN = old_tok
    web.SHUTDOWN.clear()

    td = Path(tempfile.mkdtemp())
    (td / ".env").write_bytes("﻿TELEGRAM_BOT_TOKEN=123:ABC\r\nexport X_BOM_TEST=\"qiymat\"\r\n".encode("utf-8"))
    os.environ.pop("X_BOM_TEST", None)
    saved = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    config._load_dotenv(td / ".env")
    got = (os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("X_BOM_TEST"))
    os.environ["TELEGRAM_BOT_TOKEN"] = saved or ""
    ok(".env Notepad BOM bilan saqlansa ham birinchi kalit o'qiladi", got == ("123:ABC", "qiymat"), str(got))
    bat = (ROOT / "start_windows.bat").read_text(encoding="utf-8", errors="ignore")
    ok("start_windows.bat: requirements.txt o'zgarsa kutubxonalar qayta o'rnatiladi (xesh belgisi)",
       "certutil -hashfile requirements.txt" in bat and ".installed" in bat and "REQHASH" in bat)


def main() -> int:
    db.init_db()
    from app import web

    tg.serve(TG_PORT)
    srv = serve(web.app, PORT)
    try:
        web_suite()
        models_suite()
        camera_suite()
        telegram_suite()
        runner_suite()
    finally:
        srv.should_exit = True
    shutil.rmtree(config.UPLOADS_DIR / "x", ignore_errors=True)
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
