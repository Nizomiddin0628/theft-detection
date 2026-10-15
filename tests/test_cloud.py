"""Bulut serveri (1.6.0) sinovi: kompaniya → filial → kamera, rollar (owner/manager/guard), qurilma tokeni, edge
agent ulanishi, bulutda tekshirish → edge'ga buyruq, ko'rsatkichlar, kliplar, token almashtirish.

Bir xil to'plam SQLite'da va (CLOUD_TEST_PG_URL berilsa) PostgreSQL'da yuritiladi.

Ishga tushirish:  rm -rf data && python -m tests.test_cloud
                  CLOUD_TEST_PG_URL=postgresql+psycopg://postgres@127.0.0.1:5433/hushyor_test python -m tests.test_cloud
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_cloud16_")
os.environ["CLOUD_URL"] = "http://127.0.0.1:9200"      # app.config uchun (sync.configured) — har run'da worker URL'i alohida
os.environ["EDGE_TOKEN"] = "boshlangich"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import httpx  # noqa: E402

from app import db, sync  # noqa: E402
from app.config import CLIPS_DIR  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def wait(cond, timeout=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


def serve(port: int):
    import uvicorn

    cfg = uvicorn.Config(cloud.app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    return srv


def new_event(**kw) -> int:
    base = {"source_id": 1, "source_name": "Kassa 1", "created_at": time.time(), "t_detect": time.time(), "score": 70,
            "level": "yuqori", "kind": "pocket", "reasons": ["sabab"], "breakdown": {}, "track_id": 1, "status": "yangi"}
    base.update(kw)
    return db.insert_event(base)


def run_suite(label: str, port: int) -> None:
    print(f"\n--- {label} ---")
    cloud.cdb.reset_engine()
    cloud.cdb.meta.drop_all(cloud.cdb.engine())
    info = cloud.init_db()
    cloud._state.update(tokens=[], admin="admin-sinov", fail=False, fail_paths=())
    cloud._login_fails.clear()
    srv = serve(port)
    base = f"http://127.0.0.1:{port}"
    ok(f"{label}: baza ishga tushdi", info["dialect"] in ("sqlite", "postgresql"), str(info))
    hz = httpx.get(f"{base}/healthz").json()
    ok(f"{label}: /healthz bazani ko'rsatadi", hz["ok"] and hz["db"] == info["dialect"])

    # --- egasi va login
    r = cloud.bootstrap_owner("egasi@dokon.uz", "Parol12345", "Korzinka Test")
    ok("Birinchi kompaniya va egasi yaratildi", r["company_id"] >= 1 and r["user_id"] >= 1)
    try:
        cloud.bootstrap_owner("egasi@dokon.uz", "Parol12345", "X")
        ok("Takroriy egasi rad etiladi", False)
    except ValueError:
        ok("Takroriy egasi rad etiladi", True)
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "egasi@dokon.uz", "password": "xato"})
    ok("Noto'g'ri parol — 401", r.status_code == 401)
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "Egasi@dokon.uz ", "password": "Parol12345"})
    ok("Login (email katta-kichik harfga bog'liq emas)", r.status_code == 200 and r.json()["user"]["role"] == "owner",
       r.text[:100])
    owner = {"Authorization": f"Bearer {r.json()['token']}"}
    r = httpx.get(f"{base}/api/v1/me", headers=owner).json()
    ok("/me: kompaniya va rol", r["company"]["name"] == "Korzinka Test" and r["user"]["role"] == "owner")
    ok("Tokensiz /me — 401", httpx.get(f"{base}/api/v1/me").status_code == 401)

    # --- filiallar va qurilma tokeni
    db.init_db()
    dev = sync.device_id()
    r = httpx.post(f"{base}/api/v1/branches", json={"name": "Chilonzor", "device_id": dev, "address": "Toshkent"},
                   headers=owner)
    ok("Filial yaratildi, qurilma tokeni bir marta ko'rsatildi", r.status_code == 200 and len(r.json()["device_token"]) == 32,
       r.text[:120])
    bA, tokA = r.json()["branch"]["id"], r.json()["device_token"]
    r = httpx.post(f"{base}/api/v1/branches", json={"name": "Yunusobod", "device_id": "devB"}, headers=owner)
    bB = r.json()["branch"]["id"]
    r = httpx.post(f"{base}/api/v1/branches", json={"name": "Takror", "device_id": dev}, headers=owner)
    ok("Bir qurilma ikki filialga biriktirilmaydi — 409", r.status_code == 409)
    r = httpx.post(f"{base}/api/v1/branches", json={"name": "X", "device_id": "yomon id!"}, headers=owner)
    ok("Noto'g'ri device_id — 400", r.status_code == 400)

    # --- foydalanuvchilar va rollar
    r = httpx.post(f"{base}/api/v1/users", json={"email": "menejer@dokon.uz", "password": "Menejer123", "name": "Menejer",
                                                   "role": "manager", "branch_ids": [bA, bB]}, headers=owner)
    ok("Menejer (ikki filial) yaratildi", r.status_code == 200, r.text[:100])
    r = httpx.post(f"{base}/api/v1/users", json={"email": "qorovul@dokon.uz", "password": "Qorovul123", "name": "Qorovul",
                                                   "role": "guard", "branch_ids": [bA]}, headers=owner)
    ok("Qo'riqchi (faqat A filiali) yaratildi", r.status_code == 200)
    r = httpx.post(f"{base}/api/v1/users", json={"email": "q2@dokon.uz", "password": "123", "role": "guard",
                                                   "branch_ids": [bA]}, headers=owner)
    ok("Qisqa parol — 400", r.status_code == 400)
    r = httpx.post(f"{base}/api/v1/users", json={"email": "q3@dokon.uz", "password": "Qorovul123", "role": "guard"},
                   headers=owner)
    ok("Qo'riqchi filialsiz — 400", r.status_code == 400)
    guard = {"Authorization": "Bearer " + httpx.post(f"{base}/api/v1/auth/login",
                                                    json={"email": "qorovul@dokon.uz", "password": "Qorovul123"}).json()["token"]}
    manager = {"Authorization": "Bearer " + httpx.post(f"{base}/api/v1/auth/login",
                                                      json={"email": "menejer@dokon.uz", "password": "Menejer123"}).json()["token"]}
    r = httpx.post(f"{base}/api/v1/users", json={"email": "x@dokon.uz", "password": "Parol12345", "role": "guard",
                                                   "branch_ids": [bA]}, headers=guard)
    ok("Qo'riqchi foydalanuvchi yarata olmaydi — 403", r.status_code == 403)
    ok("Qo'riqchi faqat o'z filialini ko'radi",
       [b["id"] for b in httpx.get(f"{base}/api/v1/branches", headers=guard).json()["branches"]] == [bA])
    ok("Menejer ikkala filialni ko'radi",
       sorted(b["id"] for b in httpx.get(f"{base}/api/v1/branches", headers=manager).json()["branches"]) == sorted([bA, bB]))
    ok("Qo'riqchi B filiali kameralarini ko'rolmaydi — 403",
       httpx.get(f"{base}/api/v1/branches/{bB}/cameras", headers=guard).status_code == 403)
    ok("Foydalanuvchilar ro'yxati faqat egasiga", httpx.get(f"{base}/api/v1/users", headers=manager).status_code == 403
       and len(httpx.get(f"{base}/api/v1/users", headers=owner).json()["users"]) == 3)

    # --- edge agent shu filial tokeni bilan ulanadi
    sync.BACKOFF_MIN, sync.BACKOFF_MAX, sync.HELLO_SEC = 0.5, 2.0, 1.0
    sync.install_hooks()
    w = sync.SyncWorker(url=base, token=tokA)
    sync._worker = w
    w.start()
    e1 = new_event(score=81)
    name = f"ev{e1:05d}_bulut.mp4"
    (CLIPS_DIR / name).write_bytes(os.urandom(6000))
    db.update_event(e1, clip_path=name)
    got = wait(lambda: (db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=0") or 0) == 0 and w.state == "ishlayapti", 30)
    ev_cloud = cloud.cdb.row("SELECT * FROM events WHERE uid=:u", {"u": sync.event_uid(e1)})
    ok("Edge filial tokeni bilan ulandi, hodisa va klip filialga bog'landi", bool(got) and ev_cloud
       and ev_cloud["branch_id"] == bA and ev_cloud["clip_file"], f"{w.state} {w.last_error}")
    br = [b for b in httpx.get(f"{base}/api/v1/branches", headers=owner).json()["branches"] if b["id"] == bA][0]
    ok("Filial onlayn, versiya va 24 soatlik hodisa soni", br["online"] and br["version"] and br["events_24h"] >= 1
       and br["pending_review"] >= 1)
    sync.enqueue_metrics([{"minute": int(time.time() // 60) * 60 - 60, "source_id": 1, "source_name": "Kassa 1",
                           "persons_avg": 2.0, "persons_max": 3, "fps_ai": 7.0, "active_pct": 0.5, "up_pct": 1.0,
                           "alerts": 1, "cpu": None, "ram": None, "extra": None},
                          {"minute": int(time.time() // 60) * 60 - 60, "source_id": 0, "source_name": "tizim",
                           "persons_avg": None, "persons_max": None, "fps_ai": None, "active_pct": None, "up_pct": None,
                           "alerts": None, "cpu": 40.0, "ram": 60.0, "extra": None}])
    sync.enqueue_health({"id": 1, "name": "Kassa 1"}, "down", "kadr kelmayapti", time.time() - 100)
    wait(lambda: (db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=0") or 0) == 0, 20)
    m = httpx.get(f"{base}/api/v1/branches/{bA}/metrics?hours=24", headers=guard).json()
    ok("Soatlik ko'rsatkichlar (kamera + tizim)", m["ok"] and len(m["rows"]) == 2 and
       any(r["source_id"] == 1 and r["persons_max"] == 3 for r in m["rows"]) and
       any(r["source_id"] == 0 and r["cpu"] == 40.0 for r in m["rows"]), str(m["rows"])[:160])
    c = httpx.get(f"{base}/api/v1/branches/{bA}/cameras", headers=guard).json()
    ok("Kamera holati jurnali filialda", c["ok"] and len(c["health"]) == 1 and c["health"][0]["state"] == "down")

    # --- hodisalar: ko'rish va tekshirish (rollar bo'yicha)
    evs = httpx.get(f"{base}/api/v1/events?branch_id={bA}", headers=guard).json()["events"]
    ok("Qo'riqchi A filiali hodisalarini ko'radi", any(e["uid"] == sync.event_uid(e1) for e in evs))
    ok("Qo'riqchi B filiali hodisalarini so'rasa — 403",
       httpx.get(f"{base}/api/v1/events?branch_id={bB}", headers=guard).status_code == 403)
    ok("Holat filtri noto'g'ri — 400", httpx.get(f"{base}/api/v1/events?status=xato", headers=owner).status_code == 400)
    r = httpx.get(f"{base}/api/v1/events/{sync.event_uid(e1)}", headers=manager).json()
    ok("Hodisa tafsiloti (data JSON bilan)", r["ok"] and r["event"]["data"]["reasons"] == ["sabab"])
    clip_file = ev_cloud["clip_file"]
    r = httpx.get(f"{base}/api/v1/clips/{clip_file}", headers=guard)
    ok("Qo'riqchi o'z filiali klipini oladi", r.status_code == 200 and len(r.content) == 6000)
    r = httpx.get(f"{base}/api/v1/clips/devB/x.mp4", headers=guard)
    ok("Begona filial klipi — 403", r.status_code == 403)

    # Bulutda tekshirish → edge'ga buyruq → lokal hodisa yangilanadi → ack
    r = httpx.post(f"{base}/api/v1/events/{sync.event_uid(e1)}/review",
                   json={"status": "rad_etilgan", "note": "oddiy xaridor"}, headers=guard)
    ok("Bulutda tekshirish qabul qilindi", r.status_code == 200 and r.json()["event"]["status"] == "rad_etilgan")
    got = wait(lambda: (db.get_event(e1) or {}).get("status") == "rad_etilgan", 15)
    lev = db.get_event(e1)
    ok("Edge buyruqni oldi: lokal hodisa rad etildi, kim tekshirgani bulutdan", bool(got)
       and str(lev.get("reviewed_by", "")).startswith("Bulut: Qorovul") and lev.get("note") == "oddiy xaridor")
    acked = wait(lambda: cloud.cdb.scalar("SELECT COUNT(*) FROM commands WHERE acked_at IS NOT NULL") == 1, 10)
    ok("Buyruq ack qilindi (qayta yuborilmaydi)", bool(acked))
    wait(lambda: (db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=0") or 0) == 0, 15)
    ok("Edge qayta yuborgan hodisa bulutdagi holatni buzmaydi",
       cloud.cdb.row("SELECT status FROM events WHERE uid=:u", {"u": sync.event_uid(e1)})["status"] == "rad_etilgan")
    r = httpx.post(f"{base}/api/v1/events/{sync.event_uid(e1)}/review", json={"status": "yoq"}, headers=guard)
    ok("Noto'g'ri holat — 400", r.status_code == 400)

    # --- token almashtirish va o'chirish
    r = httpx.post(f"{base}/api/v1/branches/{bA}/token", headers=manager)
    ok("Menejer tokenni almashtira olmaydi — 403", r.status_code == 403)
    r = httpx.post(f"{base}/api/v1/branches/{bA}/token", headers=owner)
    tokA2 = r.json()["device_token"]
    e2 = new_event()
    got = wait(lambda: w.state == "token xato", 15)
    ok("Eski qurilma tokeni endi ishlamaydi (edge: token xato, qator saqlanadi)", bool(got)
       and (db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=0") or 0) >= 1)
    w.stop()
    w2 = sync.SyncWorker(url=base, token=tokA2)
    sync._worker = w2
    w2.start()
    got = wait(lambda: cloud.cdb.row("SELECT uid FROM events WHERE uid=:u", {"u": sync.event_uid(e2)}) is not None, 20)
    ok("Yangi token bilan navbat yetkazildi", bool(got), f"{w2.state} {w2.last_error}")
    w2.stop()
    users = httpx.get(f"{base}/api/v1/users", headers=owner).json()["users"]
    gid = [u["id"] for u in users if u["email"] == "qorovul@dokon.uz"][0]
    r = httpx.post(f"{base}/api/v1/users/{gid}/deactivate", headers=owner)
    ok("Qo'riqchi o'chirildi — sessiyasi ham", r.json()["updated"] == 1
       and httpx.get(f"{base}/api/v1/me", headers=guard).status_code == 401)
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "qorovul@dokon.uz", "password": "Qorovul123"})
    ok("O'chirilgan foydalanuvchi kira olmaydi", r.status_code == 401)
    for _ in range(10):
        httpx.post(f"{base}/api/v1/auth/login", json={"email": "egasi@dokon.uz", "password": "xato"})
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": "egasi@dokon.uz", "password": "Parol12345"})
    ok("10 xato logindan keyin IP vaqtincha bloklanadi — 429", r.status_code == 429)
    cloud._login_fails.clear()

    # --- muhit tokeni bazadagi filialni egallay olmaydi
    cloud._state["tokens"] = [(None, "umumiy-muhit-tokeni")]
    r = httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time()},
                   headers={"Authorization": "Bearer umumiy-muhit-tokeni", "X-Device-Id": dev})
    ok("Muhit tokeni bazada o'z tokeni bor filial nomidan o'tmaydi — 401", r.status_code == 401)
    r = httpx.post(f"{base}/api/v1/edge/hello", json={"time": time.time(), "name": "Eski do'kon"},
                   headers={"Authorization": "Bearer umumiy-muhit-tokeni", "X-Device-Id": "eskiqurilma"})
    ok("Muhit tokeni yangi qurilma uchun avtomatik filial yaratadi (eski usul)", r.status_code == 200
       and cloud.cdb.row("SELECT token_hash, name FROM branches WHERE device_id='eskiqurilma'")["token_hash"] == "env")
    cloud._state["tokens"] = []

    # --- superadmin
    r = httpx.get(f"{base}/api/v1/admin/summary", headers={"Authorization": "Bearer admin-sinov"}).json()
    ok("Superadmin: barcha filiallar (A, B va muhit tokenli), baza turi", r["ok"] and len(r["devices"]) == 3
       and r["db"] == info["dialect"] and any(d["events"] >= 2 for d in r["devices"]), f"{len(r['devices'])} filial")
    srv.should_exit = True
    time.sleep(0.5)


def test_legacy_sqlite() -> None:
    """1.5.0 bulut bazasi (filialsiz sxema) topilsa chetga olinadi, toza baza ochiladi."""
    import sqlite3

    cloud.cdb.reset_engine()
    path = cloud.cdb.DATA_DIR / "cloud.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    for p in path.parent.glob("cloud.db*"):
        p.unlink()
    c = sqlite3.connect(str(path))
    c.executescript("CREATE TABLE devices(id TEXT PRIMARY KEY); CREATE TABLE events(uid TEXT PRIMARY KEY, device_id TEXT, data TEXT);")
    c.commit()
    c.close()
    os.environ.pop("CLOUD_DB_URL", None)
    info = cloud.init_db()
    moved = list(path.parent.glob("cloud.db.v15-*"))
    ok("Eski 1.5.0 bulut bazasi chetga olinadi, yangi sxema yaratiladi", bool(info.get("legacy_db")) and len(moved) == 1
       and "branch_id" in [r["name"] for r in cloud.cdb.rows("PRAGMA table_info(events)")], str(info.get("legacy_db")))
    cloud.cdb.reset_engine()


def main() -> int:
    db.init_db()
    test_legacy_sqlite()
    run_suite("SQLite", 9201)
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        run_suite("PostgreSQL", 9202)
        os.environ.pop("CLOUD_DB_URL", None)
    else:
        print("\n  (PostgreSQL sinovi o'tkazilmadi: CLOUD_TEST_PG_URL berilmagan)")
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)      # AI/OpenVINO va server oqimlari ochiq turganda interpretator yopilishida segfault bo'lmasin
