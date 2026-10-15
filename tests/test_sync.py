"""Edge agent (bulutga yuborish) sinovi: navbat, tartib, takrorlanmaslik, internet uzilishi, token xatosi,
qayta ishga tushganda saqlanish, rad etilgan qatorlar, ko'rsatkichlar, panel API.

Ishga tushirish:  rm -rf data && python -m tests.test_sync
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

CLOUD_PORT = 9100
_tmp = tempfile.mkdtemp(prefix="hushyor_cloud_")
os.environ["CLOUD_DATA_DIR"] = _tmp
os.environ["CLOUD_URL"] = f"http://127.0.0.1:{CLOUD_PORT}"
os.environ["EDGE_TOKEN"] = "sinov-token-123"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import db, metrics, sync, web  # noqa: E402
from app.config import CLIPS_DIR  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def wait(cond, timeout=30.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


def serve_cloud() -> None:
    import uvicorn

    cloud._state["tokens"] = [(None, "sinov-token-123"), ("bogliq", "tok-bogliq")]
    cloud._state["admin"] = "admin-sinov"
    cfg = uvicorn.Config(cloud.app, host="127.0.0.1", port=CLOUD_PORT, log_level="error")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)


def cloud_q(sql: str, params=()) -> list[dict]:
    """Bulut bazasiga so'rov: `?` o'rniga nomli parametrlar (SQLite va PostgreSQL uchun bir xil)."""
    named = {}
    for i, v in enumerate(params):
        sql = sql.replace("?", f":p{i}", 1)
        named[f"p{i}"] = v
    return cloud.cdb.rows(sql, named)


def outbox_n(kind: str | None = None) -> int:
    if kind:
        return db.scalar("SELECT COUNT(*) FROM outbox WHERE kind=?", (kind,)) or 0
    return db.scalar("SELECT COUNT(*) FROM outbox") or 0


def outbox_live() -> int:
    """Rad etilgan (failed) qatorlarsiz navbat."""
    return db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=0") or 0


def new_event(**kw) -> int:
    base = {"source_id": 1, "source_name": "Kassa 1", "created_at": time.time(), "t_detect": time.time(), "score": 70,
            "level": "yuqori", "kind": "pocket", "reasons": ["sabab"], "breakdown": {"a": 1}, "track_id": 1,
            "status": "yangi"}
    base.update(kw)
    return db.insert_event(base)


def fake_clip(ev_id: int, size: int = 20000) -> str:
    name = f"ev{ev_id:05d}_sinov.mp4"
    (CLIPS_DIR / name).write_bytes(os.urandom(size))
    thumb = f"ev{ev_id:05d}_sinov.jpg"
    (CLIPS_DIR / thumb).write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    db.update_event(ev_id, clip_path=name, thumb_path=thumb, clip_error=0)
    return name


def main() -> int:
    db.init_db()
    cloud.init_db()
    serve_cloud()
    sync.BACKOFF_MIN, sync.BACKOFF_MAX = 0.5, 2.0       # sinov tez o'tsin
    sync.HELLO_SEC = 2.0
    sync.install_hooks()
    dev = sync.device_id()
    ok("Sozlangan: CLOUD_URL + EDGE_TOKEN", sync.configured() and len(dev) >= 8, dev)

    # ------------------------------------------------------------ navbat (worker ishlamayapti)
    e1 = new_event()
    ok("Yangi hodisa navbatga tushdi", outbox_n("event") == 1)
    db.update_event(e1, status="tasdiqlangan", reviewed_by="Qorovul", reviewed_at=time.time())
    db.update_event(e1, note="izoh")
    row = db.row("SELECT * FROM outbox WHERE key=?", (f"ev:{e1}",))
    ok("Yangilanishlar bitta qatorga birlashadi (coalesce), oxirgi holat bilan",
       outbox_n("event") == 1 and json.loads(row["payload"])["status"] == "tasdiqlangan"
       and json.loads(row["payload"])["note"] == "izoh")
    db.update_event(e1, telegram_sent=1)
    db.update_event(e1, thumb_path=None)
    ok("Ahamiyatsiz ustun (clip_start yo'q) ham sinxron ro'yxatda — 1 qator", outbox_n("event") == 1)
    fake_clip(e1)
    ok("Klip tayyor → navbatda hodisa + klip, hodisa oldinda", outbox_n("clip") == 1 and
       db.scalar("SELECT MIN(id) FROM outbox WHERE kind='event'") < db.scalar("SELECT MIN(id) FROM outbox WHERE kind='clip'"))

    # ------------------------------------------------------------ yuborish
    w = sync.SyncWorker()
    sync._worker = w
    w.start()
    got = wait(lambda: outbox_n() == 0, 20)
    ok("Navbat bo'shadi (hodisa va klip yetdi)", bool(got), f"qoldi {outbox_n()}, holat {w.state}, xato {w.last_error}")
    ev_cloud = cloud_q("SELECT * FROM events WHERE uid=?", (f"{dev}:{e1}",))
    ok("Bulutda hodisa to'g'ri (status, izoh, qurilma)", ev_cloud and ev_cloud[0]["status"] == "tasdiqlangan"
       and json.loads(ev_cloud[0]["data"])["note"] == "izoh" and ev_cloud[0]["device_id"] == dev)
    cf = ev_cloud[0]["clip_file"] if ev_cloud else None
    ok("Bulutda klip fayli saqlandi va hajmi mos", cf and (cloud.CLIPS_DIR / cf).stat().st_size == 20000
       and (cloud.CLIPS_DIR / cf.replace(".mp4", ".jpg")).exists(), str(cf))
    ok("Worker holati: ishlayapti, soat farqi o'lchandi", w.state == "ishlayapti" and w.server_time_skew is not None,
       f"{w.state}, farq {w.server_time_skew}")
    devs = cloud_q("SELECT * FROM branches")
    ok("Hello: qurilma (filial) ro'yxatda, versiya bilan", len(devs) == 1 and devs[0]["device_id"] == dev and devs[0]["version"])

    # ------------------------------------------------------------ 2.0.0: kassa navbati (bulut "queues" ni e'lon qiladi)
    from app.ai import queues

    ok("Hello: bulut imkoniyatlari saqlandi (queues)", sync.cloud_supports("queues"), db.get_setting("cloud_features", ""))
    sid_q = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Kassa', 'rtsp', 'rtsp://q', 0, ?)",
                       (time.time(),))
    zid_q = db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
                       (sid_q, "1-kassa", "navbat", "[[0.1,0.5],[0.9,0.5],[0.9,1],[0.1,1]]", "", 1, time.time()))
    aid = queues.raise_alert({"t": time.time(), "rule": "R1", "level": "warning", "source_id": sid_q, "source_name": "Kassa",
                              "zone_id": zid_q, "zone_name": "1-kassa", "title": "Qo'shimcha kassa oching",
                              "text": "1-kassa: navbatda <b>5 kishi</b>", "data": {"length": 5}})
    m_q = int(time.time() // 60) * 60 - 120
    with queues._acc_lock:
        acc = queues._Acc(m_q, zid_q, "1-kassa", sid_q, "Kassa")
        acc.t_obs, acc.len_sum, acc.len_max, acc.served, acc.wait_sum, acc.wait_n = 60, 120, 3, 2, 150, 2
        queues._acc[(m_q, zid_q)] = acc
    queues.flush_minutes(m_q)
    got = wait(lambda: cloud_q("SELECT COUNT(*) AS n FROM queue_alerts WHERE key=?", (f"{dev}:{aid}",))[0]["n"] == 1
               and cloud_q("SELECT COUNT(*) AS n FROM queue_minute WHERE minute=? AND zone_id=?", (m_q, zid_q))[0]["n"] == 1, 20)
    qa = cloud_q("SELECT * FROM queue_alerts WHERE key=?", (f"{dev}:{aid}",))
    ok("Navbat signali va daqiqasi bulutga yetdi (matn oddiy matnga aylangan)", bool(got) and qa
       and qa[0]["rule"] == "R1" and "<b>" not in qa[0]["text"], f"{outbox_n()} qator qoldi, {w.last_error}")
    got = wait(lambda: (cloud.cdb.json_load(cloud_q("SELECT queues FROM branches")[0]["queues"]) or {}).get("items"), 15)
    ok("Hello: kassalar holati bulutda (kamera o'chiq — 'ma'lumot yo'q')", bool(got) and got[0]["name"] == "1-kassa"
       and got[0]["live"] is False, str(got))
    db.execute("DELETE FROM zones WHERE id=?", (zid_q,))
    db.execute("DELETE FROM sources WHERE id=?", (sid_q,))

    # ------------------------------------------------------------ takror
    sync.enqueue("event", f"ev:{e1}", sync.event_payload(db.get_event(e1)))
    wait(lambda: outbox_n() == 0, 10)
    ok("Takroriy yuborish bulutda ikkinchi hodisa yaratmaydi",
       cloud_q("SELECT COUNT(*) AS n FROM events")[0]["n"] == 1)
    old_payload = sync.event_payload(db.get_event(e1))
    old_payload["updated_at"] -= 1000
    old_payload["status"] = "yangi"
    sync.enqueue("event", f"ev:{e1}", old_payload)
    wait(lambda: outbox_n() == 0, 10)
    ok("Eskiroq yangilanish bulutdagi yangisini buzmaydi",
       cloud_q("SELECT status FROM events WHERE uid=?", (f"{dev}:{e1}",))[0]["status"] == "tasdiqlangan")

    # ------------------------------------------------------------ internet uzilishi
    cloud._state["fail"] = True
    n_ev0 = cloud_q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    ids = [new_event(score=50 + i) for i in range(20)]
    for i in ids[:5]:
        fake_clip(i, size=5000 + i)
    for i in ids[5:10]:
        db.update_event(i, status="rad_etilgan", reviewed_by="Qorovul", reviewed_at=time.time())
    sync.enqueue_health({"id": 1, "name": "Kassa 1"}, "down", "kadr kelmayapti", time.time() - 70)
    sync.enqueue_health({"id": 1, "name": "Kassa 1"}, "up", "1 daqiqa ishlamadi", time.time() - 70)

    class FakeMgr:
        def source_info(self):
            return [{"id": 1, "name": "Kassa 1", "kind": "rtsp", "state": "ishlayapti", "running": True, "persons": 2,
                     "fps_ai": 6.5, "mode": "faol", "alerts": 3},
                    {"id": 2, "name": "Eshik", "kind": "rtsp", "state": "xato", "running": False, "persons": 0,
                     "fps_ai": 0, "mode": "bo'sh", "alerts": 0}]
    ms = metrics.MetricsSampler(FakeMgr())
    for _ in range(6):
        ms.sample()
    minute = int(time.time() // 60) * 60 - 60
    mrows = ms.flush(minute)
    ok("Ko'rsatkichlar: 2 kamera + tizim qatori, lokal jadvalga yozildi", len(mrows) == 3 and
       (db.scalar("SELECT COUNT(*) FROM metrics_minute WHERE minute=?", (minute,)) or 0) == 3 and
       mrows[0]["persons_avg"] == 2.0 and mrows[0]["active_pct"] == 1.0 and mrows[1]["up_pct"] == 0.0)
    time.sleep(3)
    pend = sync.pending()
    ok("Uzilish paytida hech narsa yo'qolmadi: 20 hodisa, 5 klip, 2 holat, 3 ko'rsatkich navbatda",
       pend["by_kind"].get("event") == 20 and pend["by_kind"].get("clip") == 5 and pend["by_kind"].get("health") == 2
       and pend["by_kind"].get("metrics") == 3 and pend["failed"] == 0, str(pend["by_kind"]))
    ok("Uzilishda bulutga hech narsa o'tmadi, worker kutish rejimida",
       cloud_q("SELECT COUNT(*) AS n FROM events")[0]["n"] == n_ev0 and w.state == "kutilmoqda", w.state)
    st = w.status()
    ok("Status API: navbat va klip hajmi ko'rinadi", st["pending"]["total"] == 30 and st["pending"]["clip_bytes"] > 25000
       and st["backoff"] > 0, f"{st['pending']['clip_bytes']} B, kutish {st['backoff']} s")

    # ------------------------------------------------------------ tiklanish
    cloud._state["fail"] = False
    w.kick()
    got = wait(lambda: outbox_n() == 0, 40)
    ok("Ulanish tiklangach navbat to'liq yetkazildi", bool(got), f"qoldi {outbox_n()}, holat {w.state}, xato {w.last_error}")
    n_ev = cloud_q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    n_clip = cloud_q("SELECT COUNT(*) AS n FROM events WHERE clip_file IS NOT NULL")[0]["n"]
    n_rej = cloud_q("SELECT COUNT(*) AS n FROM events WHERE status='rad_etilgan'")[0]["n"]
    ok("Bulutda aynan 21 hodisa (takror yo'q), 6 klip, 5 rad etilgan holat bilan",
       n_ev == 21 and n_clip == 6 and n_rej == 5, f"hodisa {n_ev}, klip {n_clip}, rad {n_rej}")
    ok("Kamera holati va ko'rsatkichlar bulutda",
       cloud_q("SELECT COUNT(*) AS n FROM health_log")[0]["n"] == 2 and
       cloud_q("SELECT COUNT(*) AS n FROM metrics_minute")[0]["n"] == 3)
    rec = cloud_q("SELECT key, t FROM received WHERE key LIKE ? ORDER BY t", (f"{dev}:%:{ids[0]}",))
    kinds = [r["key"].split(":")[1] for r in rec]
    ok("Tartib: hodisa yozuvi klipidan oldin yetdi", kinds[:1] == ["event"] and "clip" in kinds, str(kinds))

    # ------------------------------------------------------------ 409: klip hodisadan oldin
    e9 = new_event()
    wait(lambda: outbox_n() == 0, 10)
    cloud.cdb.execute("DELETE FROM events WHERE uid=:u", {"u": f"{dev}:{e9}"})
    db.EVENT_HOOKS.clear()                 # klip qatorini hodisasiz navbatga qo'yish uchun hook'siz
    name9 = fake_clip(e9)
    sync.enqueue("clip", f"clip:{e9}", {"uid": sync.event_uid(e9), "local_id": e9, "clip": name9}, coalesce=False)
    sync.install_hooks()
    got = wait(lambda: outbox_n() == 0, 20)
    ok("Klip hodisadan oldin kelsa (409): hodisa qayta yuboriladi, klip orqasidan yetadi",
       bool(got) and cloud_q("SELECT clip_file FROM events WHERE uid=?", (f"{dev}:{e9}",))[0]["clip_file"] is not None)

    # ------------------------------------------------------------ rad etilgan qator navbatni to'smaydi
    sync.enqueue("clip", "clip:777", {"uid": f"{dev}:777", "local_id": 777, "clip": "yoq_fayl.mp4"}, coalesce=False)
    e10 = new_event()
    got = wait(lambda: cloud_q("SELECT COUNT(*) AS n FROM events WHERE uid=?", (f"{dev}:{e10}",))[0]["n"] == 1, 15)
    failed = db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=1")
    ok("Fayli yo'q klip 'rad etilgan' bo'ladi, keyingi hodisa o'tadi", bool(got) and failed == 1)

    # ------------------------------------------------------------ klip o'z hodisasini kutsa ham navbat turmaydi
    db.EVENT_HOOKS.clear()
    evs = [new_event() for _ in range(3)]
    names = [fake_clip(i, size=3000) for i in evs]
    for i, nm in zip(evs, names):       # klip qatorlari OLDIN (hodisa qatorlari oynadan tashqarida qoladi)
        sync.enqueue("clip", f"clip:{i}", {"uid": sync.event_uid(i), "local_id": i, "clip": nm}, coalesce=False)
    for i in evs:
        sync.enqueue("event", f"ev:{i}", sync.event_payload(db.get_event(i)))
    sync.install_hooks()
    w.batch_size = 2
    got = wait(lambda: outbox_live() == 0, 20)
    w.batch_size = sync.BATCH
    ok("Oyna faqat kutayotgan kliplardan iborat bo'lsa ham hodisalar yetib, kliplar orqasidan ketadi", bool(got)
       and all(cloud_q("SELECT clip_file FROM events WHERE uid=?", (sync.event_uid(i),))[0]["clip_file"] for i in evs),
       f"qoldi {outbox_live()}")

    # ------------------------------------------------------------ klip xatosi boshqalarni to'smaydi
    cloud._state["fail_paths"] = ("/clip",)
    e_c = new_event()
    fake_clip(e_c, size=4000)
    e_after = new_event()
    got = wait(lambda: cloud_q("SELECT COUNT(*) AS n FROM events WHERE uid=?", (sync.event_uid(e_after),))[0]["n"] == 1, 15)
    row_c = db.row("SELECT * FROM outbox WHERE key=?", (f"clip:{e_c}",))
    ok("Klip serverda xato bersa, undan keyingi hodisa baribir yetadi; klip o'z navbatida kutadi",
       bool(got) and row_c is not None and row_c["attempts"] >= 1 and row_c["failed"] == 0 and row_c["next_try"] > time.time() - 1,
       f"klip urinish {row_c and row_c['attempts']}")
    cloud._state["fail_paths"] = ()
    got = wait(lambda: outbox_live() == 0, 20)
    ok("Klip yo'li tiklangach klip ham yetadi", bool(got)
       and cloud_q("SELECT clip_file FROM events WHERE uid=?", (sync.event_uid(e_c),))[0]["clip_file"] is not None)

    # ------------------------------------------------------------ panel API
    with TestClient(web.app) as c:
        c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"}, follow_redirects=False)
        st = c.get("/api/sync/status").json()
        ok("Panel: /api/sync/status", st["configured"] and st["device_id"] == dev and st["pending"]["failed"] == 1)
        r = c.post("/api/sync/test").json()
        ok("Panel: ulanishni tekshirish", r["ok"] is True)
        r = c.post("/api/sync/retry").json()
        ok("Panel: rad etilganni qayta navbatga", r["ok"] and r["count"] == 1)
        wait(lambda: (db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=1") or 0) == 1, 10)
        ok("Qayta urinishda ham fayl yo'q — yana rad etiladi, navbat to'silmaydi",
           db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=1") == 1)
        page = c.get("/system")
        ok("Tizim holati sahifasida Bulut kartasi", page.status_code == 200 and "Bulut (edge agent)" in page.text)
        m = c.get("/api/metrics?hours=48").json()
        ok("Ko'rsatkichlar API", len(m["rows"]) >= 3)
        sysinfo = c.get("/api/system").json()
        ok("/api/system ichida sync holati", sysinfo["sync"]["configured"] is True)
    # TestClient web.app lifespan'ni ishga tushirgan — uning worker/metrics'ini to'xtatamiz
    web._shutdown()

    # ------------------------------------------------------------ token xato
    w2 = sync.SyncWorker(token="notogri")
    sync._worker = w2
    db.execute("DELETE FROM outbox")
    e11 = new_event()
    w2.start()
    got = wait(lambda: w2.state == "token xato", 15)
    ok("Token xato: yuborish to'xtaydi, qator saqlanadi", bool(got) and outbox_n() == 1 and w2.auth_failed
       and (db.scalar("SELECT failed FROM outbox") or 0) == 0, w2.last_error)
    w2.stop()

    # ------------------------------------------------------------ qayta ishga tushish
    db.execute("UPDATE outbox SET sending=1")
    db.init_db()
    ok("Qayta ishga tushganda yarim qolgan 'sending' qatorlar qayta ochiladi",
       (db.scalar("SELECT COUNT(*) FROM outbox WHERE sending=1") or 0) == 0)
    w3 = sync.SyncWorker()
    sync._worker = w3
    w3.start()
    got = wait(lambda: outbox_n() == 0, 20)
    ok("Yangi worker eski navbatni yetkazadi (jarayon qayta ishga tushganda yo'qolmaydi)", bool(got) and
       cloud_q("SELECT COUNT(*) AS n FROM events WHERE uid=?", (f"{dev}:{e11}",))[0]["n"] == 1)
    w3.stop()
    w.stop()

    # ------------------------------------------------------------ bulut API: begona qurilma, katta so'rov
    import httpx

    h = {"Authorization": "Bearer sinov-token-123", "X-Device-Id": "boshqa"}
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/events",
                   json={"items": [{"key": "ev:1", "uid": f"{dev}:1", "updated_at": time.time()}]}, headers=h)
    ok("Bulut: boshqa qurilma nomidan hodisa rad etiladi", r.status_code == 200 and r.json()["rejected"])
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/events", json={"items": [{}] * 501}, headers=h)
    ok("Bulut: 500 dan ko'p yozuv — 413", r.status_code == 413)
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/hello", json={}, headers={"Authorization": "Bearer x",
                                                                                       "X-Device-Id": "a"})
    ok("Bulut: noto'g'ri token — 401", r.status_code == 401)
    r = httpx.get(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/admin/summary", headers=h)
    ok("Bulut: do'kon tokeni bilan admin API ochilmaydi", r.status_code == 401)
    hb = {"Authorization": "Bearer tok-bogliq", "X-Device-Id": dev}
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/hello", json={"time": time.time()}, headers=hb)
    ok("Bulut: qurilmaga bog'langan token boshqa qurilma nomidan o'tmaydi", r.status_code == 401)
    hb["X-Device-Id"] = "bogliq"
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/hello", json={"time": time.time()}, headers=hb)
    ok("Bulut: bog'langan token o'z qurilmasi bilan o'tadi", r.status_code == 200)
    r = httpx.post(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/edge/events", content=b"{bu json emas",
                   headers={**h, "Content-Type": "application/json"})
    ok("Bulut: buzuq JSON — 400 (500 emas)", r.status_code == 400)
    with TestClient(cloud.app) as cc:
        r = cc.post("/api/v1/edge/events", content=b"{}", headers={**h, "Content-Length": str(50 * 1024 * 1024)})
        ok("Bulut: juda katta so'rov (Content-Length) — 413", r.status_code == 413)
        r = cc.post("/api/v1/edge/events", content=b"{}", headers={**h, "Transfer-Encoding": "chunked"})
        ok("Bulut: Content-Length'siz (chunked) tana — 411", r.status_code == 411)
        r = cc.post(f"/api/v1/edge/events/{dev}:1/clip", files={"clip": ("a.mp4", b"x" * 100, "video/mp4")},
                    headers={"X-Device-Id": dev})
        ok("Bulut: tokensiz klip yuklash tana o'qilmasdan 401", r.status_code == 401)
    r = httpx.get(f"http://127.0.0.1:{CLOUD_PORT}/api/v1/admin/summary", headers={"Authorization": "Bearer admin-sinov"})
    s = r.json()
    me = next((d for d in s.get("devices", []) if d["id"] == dev), {"events": 0, "clips": 0})
    ok("Bulut: admin summary", r.status_code == 200 and me["events"] >= 22 and me["clips"] >= 7,
       f"hodisa {me['events']}, klip {me['clips']}, qurilmalar {len(s.get('devices', []))}")

    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)      # AI/OpenVINO va server oqimlari ochiq turganda interpretator yopilishida segfault bo'lmasin
