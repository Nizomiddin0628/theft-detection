"""1.9.0 odamlarni sanash — bulut tomoni: edge daqiqalik kirdi/chiqdi → /visits, filiallar ro'yxati, soatlik
ko'rsatkichlar; filial doirasi (qo'riqchi faqat o'z filiali). SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham.

    python -m tests.test_counting_cloud
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_vcloud_")
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from cloud import notify  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def run(label: str) -> None:
    print(f"\n=== {label} ===")
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    notify.cfg = notify.Config(tz_offset=5, offline_sec=0, escalate_sec=0)
    cloud._state.update(tokens=[], admin="superadmin-sinov-tokeni-uzunroq-1234567", fail=False, fail_paths=(),
                        notify_configured=True, no_notify=True)
    cloud._login_fails.clear()
    c = TestClient(cloud.app)
    a = cloud.bootstrap_owner("egasi@v.uz", "Parol12345", "V savdo")
    b1, t1 = cdb.create_branch(a["company_id"], "Chilonzor", "devV1")
    b2, t2 = cdb.create_branch(a["company_id"], "Yunusobod", "devV2")
    cdb.create_user(a["company_id"], "qorovul@v.uz", "Parol12345", "Qorovul", "guard", [b2])

    def edge(dev, tok, path, **kw):
        return c.post(f"/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev}, **kw)

    def login(email):
        return {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": email, "password": "Parol12345"}).json()["token"]}

    day0 = cloud._local_day0()
    now = time.time()
    m_now = int(min(now, day0 + 86400 - 120) // 60 * 60)
    items = []
    # Chilonzor: bugun 2 kamera; kecha ham; sanashsiz eski ko'rsatkich (in/out yo'q) — e'tiborsiz
    for k in range(10):
        m = m_now - k * 60
        if m < day0:
            break
        items.append({"key": f"m:{m}:1", "minute": m, "source_id": 1, "source_name": "Eshik", "persons_avg": 1,
                      "in": 3, "out": 1, "staff_in": 1, "staff_out": 0})
        items.append({"key": f"m:{m}:2", "minute": m, "source_id": 2, "source_name": "Zal", "persons_avg": 2})
    n_today = len(items) // 2
    y = day0 - 86400 + 12 * 3600
    items.append({"key": f"m:{y}:1", "minute": y, "source_id": 1, "source_name": "Eshik", "in": 50, "out": 49})
    items.append({"key": f"m:{m_now}:0", "minute": m_now, "source_id": 0, "source_name": "tizim", "cpu": 20})
    r = edge("devV1", t1, "metrics", json={"items": items})
    ok("Edge daqiqalik ko'rsatkichlari (kirdi/chiqdi bilan) qabul qilindi", r.status_code == 200
       and len(r.json()["accepted"]) == len(items), r.text[:120])
    m2 = m_now
    edge("devV2", t2, "metrics", json={"items": [{"key": f"m:{m2}:5", "minute": m2, "source_id": 5, "in": 7, "out": 2}]})
    A = login("egasi@v.uz")
    v = c.get("/api/v1/visits?days=7", headers=A).json()
    br = {x["name"]: x for x in v["branches"]}
    ok("Tashriflar: filial bo'yicha bugun kirdi/chiqdi va ichkarida",
       br["Chilonzor"]["today_in"] == 3 * n_today and br["Chilonzor"]["today_out"] == n_today
       and br["Chilonzor"]["inside"] == 2 * n_today and br["Yunusobod"]["today_in"] == 7, str(br)[:200])
    ok("Kunlar: 7 ta, oxirgisi bugun; kecha (50) alohida kunda", len(v["days"]) == 7 and v["days"][-1]["day"] == day0
       and v["days"][-2]["branches"].get(str(b1), {}).get("in") == 50, str(v["days"][-2])[:160])
    ok("Bugun soatlar bo'yicha (24 ta, yig'indi = bugun kirdi)", len(br["Chilonzor"]["hourly"]) == 24
       and sum(br["Chilonzor"]["hourly"]) == 3 * n_today)
    bl = {x["name"]: x for x in c.get("/api/v1/branches", headers=A).json()["branches"]}
    ok("Filiallar ro'yxatida bugungi tashrif", bl["Chilonzor"]["visits_today"] == {"in": 3 * n_today, "out": n_today,
                                                                                  "inside": 2 * n_today}, str(bl["Chilonzor"].get("visits_today")))
    met = c.get(f"/api/v1/branches/{b1}/metrics?hours=48", headers=A).json()["rows"]
    ok("Soatlik ko'rsatkichlarda kirdi/chiqdi", sum(r_["in"] for r_ in met) == 3 * n_today + 50, str(sum(r_["in"] for r_ in met)))
    # Edge "hozir ichkarida" ni hello bilan yuboradi (qo'lda "0 qilish" hisobga olingan) — bulut shuni ko'rsatadi
    r = edge("devV1", t1, "hello", json={"time": time.time(), "version": "1.9.0", "cameras": [],
                                         "occupancy": {"inside": 1, "since": day0, "day0": day0, "in": 3, "out": 2}})
    ok("Hello: sanash holati qabul qilindi", r.status_code == 200, r.text[:100])
    bl = {x["name"]: x for x in c.get("/api/v1/branches", headers=A).json()["branches"]}
    v2 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Hozir ichkarida — edge'niki (1), kirdi/chiqdi — bulut yig'indisi", bl["Chilonzor"]["visits_today"]["inside"] == 1
       and v2["Chilonzor"]["inside"] == 1 and v2["Chilonzor"]["today_in"] == 3 * n_today
       and "occupancy" not in bl["Chilonzor"], str(bl["Chilonzor"].get("visits_today")))
    with cdb.engine().begin() as cn:
        cn.execute(cdb.branches.update().where(cdb.branches.c.id == b1).values(
            occupancy={"inside": 1, "since": day0, "day0": day0, "in": 3, "out": 2, "at": time.time() - 3600}))
    v3 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Eski (1 soat) holat — bulut yig'indisi (kirdi − chiqdi)", v3["Chilonzor"]["inside"] == 2 * n_today,
       str(v3["Chilonzor"]["inside"]))
    edge("devV1", t1, "hello", json={"time": time.time(), "version": "1.8.0", "cameras": []})
    v4 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Eski edge (occupancy yo'q) — bulut yig'indisi", v4["Chilonzor"]["inside"] == 2 * n_today)
    # Ish vaqtidan keyin chiqqanlar "ichkarida" dan ayiriladi (zaxira hisobda ham): 22:00 dan keyin 2 kishi chiqdi
    m3 = m_now - 60 * 11 if m_now - 60 * 11 >= day0 else m_now
    edge("devV2", t2, "metrics", json={"items": [{"key": f"m:{m3}:5", "minute": m3, "source_id": 5, "in": 0, "out": 0,
                                                  "off_in": 0, "off_out": 2}]})
    v5 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Zaxira hisob: ichkarida = (kirdi + tashqari kirdi) − (chiqdi + tashqari chiqdi)",
       v5["Yunusobod"]["inside"] == 7 - 2 - 2 and v5["Yunusobod"]["today_in"] == 7, str(v5["Yunusobod"])[:160])
    # Edge daqiqani qayta yubordi, endi sanashsiz (masalan, o'tishlar o'chirildi) — bulutdagi yig'indidan ham chiqadi
    edge("devV2", t2, "metrics", json={"items": [{"key": f"m:{m2}:5", "minute": m2, "source_id": 5, "persons_avg": 1}]})
    v6 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Sanashsiz qayta yuborilgan daqiqa yig'indidan chiqdi", v6["Yunusobod"]["today_in"] == 0, str(v6["Yunusobod"])[:120])
    # Yangilanishdan oldin (eski bulut) kelgan sanashli qatorlar — init'da bir marta ko'chiriladi
    cdb.upsert(cdb.metrics_minute, {"branch_id": b2, "minute": m2 - 120, "source_id": 6,
                                    "data": {"source_id": 6, "in": 4, "out": 1}}, ["branch_id", "minute", "source_id"], ["data"])
    cdb.kv_set("visits_minute_v1", "")
    info = cloud.init_db()
    v7 = {x["name"]: x for x in c.get("/api/v1/visits?days=7", headers=A).json()["branches"]}
    ok("Eski sanashli qatorlar ko'chirildi (bir marta)", info.get("visits_backfilled", 0) >= 1
       and v7["Yunusobod"]["today_in"] == (4 if m2 - 120 >= day0 else 0) and cloud.init_db().get("visits_backfilled") is None,
       str(info))
    # Tezlik: 90 kun × 2 filial × 2 kamera, ish vaqtida (13 soat) har daqiqa — 280 ming qator; so'rov SQL'da yig'iladi
    rows = []
    for bid in (b1, b2):
        for d in range(1, 91):
            base = day0 - d * 86400
            for mi in range(9 * 60, 22 * 60, 1):
                for sid in (11, 12):
                    rows.append({"branch_id": bid, "minute": int(base + mi * 60), "source_id": sid, "n_in": 1, "n_out": 1,
                                 "n_staff": 0, "n_off_in": 0, "n_off_out": 0})
    with cdb.engine().begin() as cn:
        for i in range(0, len(rows), 5000):
            cn.execute(cdb.visits_minute.insert(), rows[i:i + 5000])
    t0_ = time.perf_counter()
    v8 = c.get("/api/v1/visits?days=90", headers=A).json()
    dt = time.perf_counter() - t0_
    t1_ = time.perf_counter()
    c.get("/api/v1/branches", headers=A)
    dt2 = time.perf_counter() - t1_
    ok(f"Tezlik: {len(rows)} qator, 90 kunlik /visits {dt:.2f} s, /branches {dt2:.2f} s (< 3 s)",
       dt < 3 and dt2 < 3 and v8["days"][-2]["in"] == 2 * 2 * 13 * 60 + 50, f"{v8['days'][-2]['in']}")   # +50 — kechagi
    # Bugun o'tish yo'q, lekin edge "ichkarida 3" ni yubormoqda (masalan, 00:30) — filial kartasida ko'rinadi
    b3, t3 = cdb.create_branch(a["company_id"], "Sergeli", "devV3")
    edge("devV3", t3, "hello", json={"time": time.time(), "version": "1.9.0", "cameras": [],
                                     "occupancy": {"inside": 3, "since": day0, "day0": day0, "in": 0, "out": 0}})
    bl3 = {x["name"]: x for x in c.get("/api/v1/branches", headers=A).json()["branches"]}
    ok("O'tishsiz, lekin yangi 'ichkarida' — kartada ko'rinadi", bl3["Sergeli"]["visits_today"] == {"in": 0, "out": 0,
                                                                                                    "inside": 3},
       str(bl3["Sergeli"].get("visits_today")))
    # Yarim soatli vaqt mintaqasi (+5:30): mahalliy 00:15 dagi o'tish bugungi 00:00 soatiga tushadi (kechaga emas)
    notify.cfg = notify.Config(tz_offset=5.5, offline_sec=0, escalate_sec=0)
    d55 = cloud._local_day0()
    m55 = int(d55 + 15 * 60)
    if m55 < time.time() - 60:
        cdb.upsert(cdb.visits_minute, {"branch_id": b3, "minute": m55, "source_id": 9, "n_in": 2, "n_out": 0,
                                       "n_staff": 0, "n_off_in": 0, "n_off_out": 0}, ["branch_id", "minute", "source_id"],
                   ["n_in"])
        v55 = {x["name"]: x for x in c.get("/api/v1/visits?days=2", headers=A).json()["branches"]}
        ok("+5:30 mintaqa: 00:15 dagi o'tish bugun, 00:00 soatida", v55["Sergeli"]["today_in"] == 2
           and v55["Sergeli"]["hourly"][0] == 2, str(v55["Sergeli"])[:140])
    else:
        ok("+5:30 mintaqa (hozir mahalliy 00:16 dan oldin — tekshirib bo'lmaydi)", True)
    notify.cfg = notify.Config(tz_offset=5, offline_sec=0, escalate_sec=0)
    G = login("qorovul@v.uz")
    vg = c.get("/api/v1/visits", headers=G).json()
    ok("Qo'riqchi faqat o'z filialini ko'radi", [x["name"] for x in vg["branches"]] == ["Yunusobod"])
    ok("Boshqa filialga so'rov — 403", c.get(f"/api/v1/visits?branch_id={b1}", headers=G).status_code == 403)
    ok("Kirishsiz — 401", TestClient(cloud.app).get("/api/v1/visits").status_code == 401)
    bad = c.get("/api/v1/visits?days=abc", headers=A).status_code
    ok("Noto'g'ri parametr — 422", bad == 422)


def main() -> int:
    run("SQLite")
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        run("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
