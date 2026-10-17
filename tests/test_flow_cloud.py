"""2.1.0 oqim — bulut tomoni: hello ("features": flow, rejalar va bo'limlar), soatlik yig'indi qabul qilish (almashtirish,
eskisi yangisini buzmaydi, noto'g'risi rad), /flow API (bo'limlar, yo'nalishlar, heatmap, filial doirasi), bulut boti
/bugun, edge → bulut to'liq yo'l (SyncWorker). SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham.

    python -m tests.test_flow_cloud
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_fcloud_")
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


PLAN = {"id": 1, "name": "Zal", "w_m": 20.0, "h_m": 12.0, "cell": 0.5, "nx": 40, "ny": 24, "grid": "40x24@0.5",
        "sections": [{"id": 1, "name": "Sut", "color": "#2563eb", "pts": [[2, 2], [7, 2], [7, 6], [2, 6]]},
                     {"id": 2, "name": "Non <b>", "color": "red;x", "pts": [[13, 2], [18, 2], [18, 6], ["a", 1]]}]}


def item(hour: int, visits: dict, obs=3600.0, occ=None, pas=None, grid="40x24@0.5", moves=None) -> dict:
    return {"key": f"fh:1:{hour}", "plan_id": 1, "hour": hour, "grid": grid, "nx": 40, "ny": 24, "cell": 0.5, "obs": obs,
            "visits": visits, "moves": moves or [[1, 2, 3]], "occ": occ or {"100": 50.5, "101": 20, "5000": 9},
            "pas": pas or {"100": 7, "-1": 3}}


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
    a = cloud.bootstrap_owner("egasi@f.uz", "Parol12345", "F savdo")
    b1, t1 = cdb.create_branch(a["company_id"], "Chilonzor", "devF1")
    b2, t2 = cdb.create_branch(a["company_id"], "Yunusobod", "devF2")
    cdb.create_user(a["company_id"], "menejer@f.uz", "Parol12345", "Menejer", "manager", [b2])

    def edge(dev, tok, path, **kw):
        return c.post(f"/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev}, **kw)

    def login(email):
        return {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": email, "password": "Parol12345"}).json()["token"]}

    r = edge("devF1", t1, "hello", json={"time": time.time(), "version": "2.1.0", "cameras": [], "flow_plans": [PLAN]})
    ok("Hello: 'features' da 'flow' bor", r.status_code == 200 and "flow" in r.json()["features"], r.text[:120])
    fp = cdb.json_load(cdb.scalar("SELECT flow_plans FROM branches WHERE id=:b", {"b": b1}))
    s2 = fp["items"][0]["sections"][1]
    ok("Hello: reja va bo'limlar saqlandi, noto'g'ri qiymatlar tozalandi", len(fp["items"][0]["sections"]) == 2
       and s2["color"] == "#64748b" and len(s2["pts"]) == 3, str(s2))
    day0 = cloud._local_day0()
    h0 = int(min(time.time() - 60, day0 + 86400 - 3600) // 3600 * 3600)
    if h0 < day0:
        h0 = int(day0 // 3600 * 3600) + (3600 if day0 % 3600 else 0)
    hy = int((day0 - 86400 + 12 * 3600) // 3600 * 3600)
    rows = [item(h0, {"1": 5, "2": 3}), item(hy, {"1": 10}), item(h0 + 1, {"1": 1}),
            {"key": "fh:x", "plan_id": 1, "hour": h0, "grid": "bad", "nx": 40, "ny": 24}]
    r = edge("devF1", t1, "flow", json={"items": rows})
    j = r.json()
    ok("Soatlik yig'indi qabul qilindi; soat chegarasida bo'lmagan va noto'g'ri to'r rad etildi",
       r.status_code == 200 and len(j["accepted"]) == 2 and len(j["rejected"]) == 2, r.text[:200])
    d = cdb.json_load(cdb.scalar("SELECT data FROM flow_hour WHERE branch_id=:b AND hour=:h", {"b": b1, "h": h0}))
    ok("To'rdan tashqari kataklar tashlandi (5000, -1)", "5000" not in d["occ"] and "-1" not in d["pas"], str(d["occ"]))
    edge("devF1", t1, "flow", json={"items": [item(h0, {"1": 7, "2": 4}, obs=3700)]})
    ok("Qayta yuborilgan soat almashtiriladi",
       cdb.scalar("SELECT visits FROM flow_hour WHERE branch_id=:b AND hour=:h", {"b": b1, "h": h0}) == 11)
    edge("devF1", t1, "flow", json={"items": [item(h0, {"1": 5, "2": 3}, obs=3600)]})
    ok("Eski nusxa yangisidan keyin yetsa — ustiga yozilmaydi",
       cdb.scalar("SELECT visits FROM flow_hour WHERE branch_id=:b AND hour=:h", {"b": b1, "h": h0}) == 11)
    big = item(h0 + 3600, {"1": 1}, occ={str(i): 1.5 for i in range(960)} | {f"x{i}": 1 for i in range(30000)})
    r = edge("devF1", t1, "flow", json={"items": [big]})
    ok("Juda katta yozuv rad etiladi (qolgan navbat to'xtamaydi)", r.status_code == 200 and len(r.json()["rejected"]) == 1)
    A = login("egasi@f.uz")
    f = c.get("/api/v1/flow?days=7", headers=A).json()
    ok("/flow: reja, bo'limlar va jami tashrif (7 kun)", f["plan"] and f["plan"]["name"] == "Zal" and f["total"] == 21
       and f["sections"][0]["name"] == "Sut" and f["sections"][0]["visits"] == 17, str({k: f[k] for k in ("total", "sections")}))
    ok("/flow: yo'nalishlar va bugungi soatlar", f["moves"][0]["n"] == 6 and f["moves"][0]["from_name"] == "Sut"
       and sum(sum(h.values()) for h in f["today_hours"]) == 11, str(f["moves"][:1]))
    raw = base64.b64decode(f["heat"]["occ"]["data"])
    ok("/flow: heatmap (40 × 24 katak, eng ko'p katak 255)", len(raw) == 960 and max(raw) == 255 and raw[100] == 255
       and f["heat"]["pas"]["total"] == 14, str(f["heat"]["pas"]["total"]))
    f1 = c.get("/api/v1/flow?days=1", headers=A).json()
    ok("/flow?days=1: faqat bugun", f1["total"] == 11, str(f1["total"]))
    # Masshtab o'zgardi (yangi to'r): eski to'r kataklari heatmap'ga qo'shilmaydi, tashriflar qoladi
    plan2 = dict(PLAN, grid="20x12@0.5", nx=20, ny=12, w_m=10, h_m=6)
    edge("devF1", t1, "hello", json={"time": time.time(), "version": "2.1.0", "cameras": [], "flow_plans": [plan2]})
    f = c.get("/api/v1/flow?days=7", headers=A).json()
    ok("Reja to'ri o'zgarsa eski heatmap aralashmaydi, tashriflar saqlanadi",
       f["heat"]["occ"]["total"] == 0 and f["total"] == 21 and len(base64.b64decode(f["heat"]["occ"]["data"])) == 240)
    txt = notify.flow_today_text(cdb.row("SELECT * FROM branches WHERE id=:b", {"b": b1}), day0)
    ok("Bulut boti /bugun: eng ko'p tashrif qilingan bo'limlar", "Sut 7" in txt and "Non &lt;b&gt; 4" in txt, txt)
    # Ikkinchi filial rejasi: filial tanlanganda ham ro'yxatda hamma filiallar qoladi
    edge("devF2", t2, "hello", json={"time": time.time(), "version": "2.1.0", "cameras": [], "flow_plans": [PLAN]})
    edge("devF2", t2, "flow", json={"items": [item(h0, {"1": 2})]})
    fb = c.get(f"/api/v1/flow?days=7&branch_id={b2}&plan_id=1", headers=A).json()
    ok("Filial tanlanganda: o'sha filial ko'rsatiladi, ro'yxatda ikkala filial", fb["plan"]["branch_id"] == b2
       and fb["total"] == 2 and len(fb["plans"]) == 2, str(fb["plans"]))
    # Masshtab o'zgargan soat (yangi to'r, obs kamaygan) — baribir almashtiriladi
    edge("devF1", t1, "flow", json={"items": [dict(item(h0, {"1": 1}, obs=100), grid="20x12@0.5")]})
    ok("Yangi to'rdagi soat (kichikroq qiymatlar bilan) eskisini almashtiradi",
       cdb.scalar("SELECT grid FROM flow_hour WHERE branch_id=:b AND hour=:h", {"b": b1, "h": h0}) == "20x12@0.5")
    M = login("menejer@f.uz")
    fm = c.get("/api/v1/flow?days=7", headers=M).json()
    ok("Menejer faqat o'z filiali oqimini ko'radi", fm["plan"] and fm["plan"]["branch_id"] == b2
       and all(p["branch_id"] == b2 for p in fm["plans"]), str(fm["plans"]))
    ok("Menejerga begona filial so'rovi — 403", c.get(f"/api/v1/flow?branch_id={b1}", headers=M).status_code == 403)
    # Reja o'chirildi (edge bo'sh ro'yxat yuboradi) — bulutda ham o'chadi
    edge("devF2", t2, "hello", json={"time": time.time(), "version": "2.1.0", "cameras": [], "flow_plans": []})
    ok("Edge rejasi o'chirilsa — bulutda ham ro'yxatdan chiqadi", c.get("/api/v1/flow?days=7", headers=M).json()["plan"] is None)
    ok("Kirishsiz — 401", TestClient(cloud.app).get("/api/v1/flow").status_code == 401)
    ok("Edge tokeni boshqa qurilmaniki — 401/403", edge("devF2", t1, "flow", json={"items": []}).status_code in (401, 403))


def sync_e2e() -> None:
    """Edge (app/) → bulut (cloud/) to'liq yo'l: features → enqueue_flow_hour → SyncWorker → /edge/flow."""
    print("\n=== Edge → bulut (SyncWorker) ===")
    import importlib

    os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_fsync_")
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    a = cloud.bootstrap_owner("e2@f.uz", "Parol12345", "E2")
    bid, tok = cdb.create_branch(a["company_id"], "Markaz", "edgef01")
    os.environ["CLOUD_URL"] = "http://bulut.test"
    os.environ["EDGE_TOKEN"] = tok
    from app import config

    importlib.reload(config)
    from app import db, sync

    importlib.reload(sync)
    db.init_db()
    db.set_settings({"device_id": "edgef01"})
    tc = TestClient(cloud.app)

    class W(sync.SyncWorker):
        def _post(self, path, json_body=None, files=None, timeout=30.0):
            r = tc.post(path, json=json_body, headers={"Authorization": f"Bearer {self.token}", "X-Device-Id": "edgef01"})
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {}
    w = W(url="http://bulut.test", token=tok)
    sync.enqueue_flow_hour({"plan_id": 1, "hour": 3600 * 500000})
    ok("Bulut 'flow' ni e'lon qilmaguncha navbatga yozilmaydi (eski bulut navbatni to'smasin)",
       not db.scalar("SELECT COUNT(*) FROM outbox WHERE kind='flow'"))
    ok("Hello → features saqlandi", w.hello() and "flow" in db.get_setting("cloud_features", ""))
    h = int(time.time() // 3600 * 3600)
    sync.enqueue_flow_hour(dict(item(h, {"1": 2}), key=None))
    sync.enqueue_flow_hour(dict(item(h, {"1": 4}), key=None))
    ok("Bir soat yozuvi navbatda birlashadi (coalesce)", db.scalar("SELECT COUNT(*) FROM outbox WHERE kind='flow'") == 1)
    batch = w._take()
    ok("Yuborildi va bulutda saqlandi", batch and w._send_batch(batch) and
       cdb.scalar("SELECT visits FROM flow_hour WHERE branch_id=:b AND hour=:h", {"b": bid, "h": h}) == 4)
    for k in ("CLOUD_URL", "EDGE_TOKEN"):
        os.environ.pop(k, None)
    importlib.reload(config)
    importlib.reload(sync)


def main() -> int:
    run("SQLite")
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        run("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)
    sync_e2e()
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
