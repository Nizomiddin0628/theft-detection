"""2.0.0 kassa navbati — bulut tomoni: hello (kassalar holati, "features"), daqiqalik yig'indi, qoida signallari →
bildirishnomalar (rol sozlamalari), 5-qoida (tarmoq signali), /queues API, filial doirasi, bulut boti /navbat.
SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham.

    python -m tests.test_queues_cloud
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_qcloud_")
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
    a = cloud.bootstrap_owner("egasi@q.uz", "Parol12345", "Q savdo")
    b1, t1 = cdb.create_branch(a["company_id"], "Chilonzor", "devQ1")
    b2, t2 = cdb.create_branch(a["company_id"], "Yunusobod", "devQ2")
    b3, t3 = cdb.create_branch(a["company_id"], "Sergeli", "devQ3")
    mid = cdb.create_user(a["company_id"], "menejer@q.uz", "Parol12345", "Menejer", "manager", [b1])
    gid = cdb.create_user(a["company_id"], "qorovul@q.uz", "Parol12345", "Qorovul", "guard", [b1])
    owner_id = a["user_id"] if "user_id" in a else cdb.scalar("SELECT id FROM users WHERE email='egasi@q.uz'")
    for uid_, chat in ((owner_id, "9001"), (mid, "9002"), (gid, "9003")):
        notify.add_target(int(uid_), "telegram", chat, {"name": "x"})

    def edge(dev, tok, path, **kw):
        return c.post(f"/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev}, **kw)

    def login(email):
        return {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": email, "password": "Parol12345"}).json()["token"]}

    def outbox(kind="queue"):
        return cdb.rows("SELECT n.user_id, n.ref, n.payload FROM notify_outbox n WHERE n.kind=:k ORDER BY n.id", {"k": kind})

    # 1) hello
    r = edge("devQ1", t1, "hello", json={"time": time.time(), "version": "1.9.1", "cameras": []})
    ok("Hello javobida 'features': ['queues'] (edge shundan keyin navbat yozuvlarini yuboradi)",
       r.status_code == 200 and "queues" in r.json().get("features", []), r.text[:160])
    q_items = [{"zone_id": 11, "name": "1-kassa", "source_name": "Kassa", "length": 5, "max_wait": 190, "cashier": True,
                "state": "navbat", "live": True},
               {"zone_id": 12, "name": "2-kassa <b>x</b>", "length": "abc", "max_wait": -5, "cashier": "ha",
                "state": "<script>", "live": 1}] + [{"zone_id": 100 + i, "name": f"k{i}"} for i in range(80)]
    edge("devQ1", t1, "hello", json={"time": time.time(), "version": "2.0.0", "cameras": [], "queues": q_items})
    st = cdb.json_load(cdb.scalar("SELECT queues FROM branches WHERE id=:b", {"b": b1}))
    items = st["items"]
    ok("Hello: kassalar holati saqlandi (≤ 64 ta, noto'g'ri qiymatlar tozalandi)", len(items) == 64
       and items[0]["state"] == "navbat" and items[1]["state"] == "nomalum" and items[1]["length"] == 0
       and items[1]["cashier"] is None and items[1]["max_wait"] == 0, str(items[:2]))
    edge("devQ1", t1, "hello", json={"time": time.time(), "version": "2.0.0", "cameras": [], "queues": q_items[:2]})

    # 2) daqiqalik
    day0 = cloud._local_day0()
    now = time.time()
    m_now = int(min(now, day0 + 86400 - 180) // 60 * 60)
    rows = []
    for k in range(5):
        m = m_now - k * 60
        if m < day0:
            break
        rows.append({"key": f"qm:{m}:11", "minute": m, "zone_id": 11, "zone_name": "1-kassa", "source_id": 3,
                     "t_obs": 60, "len_sum": 180, "len_max": 5, "long_sec": 30, "busy_sec": 60, "cashier_sec": 60,
                     "cashier_known": 1, "served": 2, "wait_sum": 240, "wait_n": 2, "wait_max": 150})
    n_min = len(rows)
    y = day0 - 86400 + 13 * 3600
    rows.append({"key": f"qm:{y}:11", "minute": y, "zone_id": 11, "zone_name": "1-kassa", "t_obs": 60, "served": 9,
                 "wait_sum": 900, "wait_n": 9, "wait_max": 200, "len_max": 7})
    rows.append({"key": "qm:bad", "minute": "x", "zone_id": 11})
    r = edge("devQ1", t1, "queues", json={"items": rows})
    j = r.json()
    ok("Navbat daqiqalari qabul qilindi, noto'g'risi rad etildi", r.status_code == 200 and len(j["accepted"]) == n_min + 1
       and len(j["rejected"]) == 1, r.text[:160])
    rows[0]["served"] = 3
    rows[0]["wait_sum"] = 360
    rows[0]["wait_n"] = 3
    edge("devQ1", t1, "queues", json={"items": [rows[0]]})
    ok("Qayta yuborilgan daqiqa almashtiriladi (edge kechikkan qismni qo'shib yuboradi)",
       cdb.scalar("SELECT served FROM queue_minute WHERE branch_id=:b AND minute=:m AND zone_id=11",
                  {"b": b1, "m": rows[0]["minute"]}) == 3)
    old_row = dict(rows[0], served=2, wait_sum=240, wait_n=2)
    edge("devQ1", t1, "queues", json={"items": [old_row]})
    ok("Eski nusxa yangisidan keyin yetsa — ustiga yozilmaydi",
       cdb.scalar("SELECT served FROM queue_minute WHERE branch_id=:b AND minute=:m AND zone_id=11",
                  {"b": b1, "m": rows[0]["minute"]}) == 3)
    A = login("egasi@q.uz")
    q = c.get("/api/v1/queues?days=7", headers=A).json()
    br = {x["name"]: x for x in q["branches"]}
    ch = br.get("Chilonzor")
    served_today = 2 * n_min + 1
    ok("/queues: bugun navbatdan o'tdi, o'rtacha kutish, eng uzun navbat", ch and ch["today"]["served"] == served_today
       and ch["today"]["avg_wait"] == round((240 * n_min + 120) / served_today, 1) and ch["today"]["max_len"] == 5
       and ch["today"]["avg_len"] == 3.0, str(ch and ch["today"]))
    ok("/queues: kassalar hozir (hello'dan), 24 soatlik grafiklar", ch["live"] and ch["live"][0]["length"] == 5
       and not ch["stale"] and len(ch["hours_len"]) == 24 and sum(ch["hours_served"]) == served_today)
    ok("/queues: kunlar (7), kecha alohida (9 kishi)", len(q["days"]) == 7 and q["days"][-2]["served"] == 9
       and q["days"][-2]["len_max"] == 7, str(q["days"][-2])[:160])
    ok("Ma'lumotsiz filial ro'yxatda yo'q", "Sergeli" not in br, str(list(br)))
    edge("devQ3", t3, "queues", json={"items": [dict(rows[0], key="qm:x")]})
    b3row = {x["name"]: x for x in c.get("/api/v1/queues?days=7", headers=A).json()["branches"]}.get("Sergeli")
    ok("Faqat tarixi bor filial 'aloqa yo'q' deb belgilanmaydi", b3row and b3row["stale"] is False and b3row["live"] is None,
       str(b3row and (b3row["stale"], b3row["live"])))

    # 3) qoida signallari → bildirishnomalar
    def alert(dev, tok, lid, rule, t=None, text="1-kassa: navbatda <b>5 kishi</b>", zone="1-kassa"):
        return edge(dev, tok, "queue_alerts", json={"items": [{
            "key": f"qa:{lid}", "local_id": lid, "t": t or time.time(), "rule": rule, "level": "warning",
            "source_name": "Kassa", "zone_name": zone, "title": "Qo'shimcha kassa oching" if rule == "R1" else "X",
            "text": text, "data": {"length": 5}}]})
    r = alert("devQ1", t1, 1, "R1", text="1-kassa &lt;i&gt;: navbatda <b>5 kishi</b><script>alert(1)</script>")
    ok("1-qoida signali qabul qilindi", r.status_code == 200 and r.json()["accepted"] == ["qa:1"], r.text[:120])
    row = cdb.row("SELECT * FROM queue_alerts WHERE key='devQ1:1'")
    ok("Signal matni oddiy matnga aylantirildi (teglar yo'q)", row and "<" not in row["text"].replace("<i>", "")
       and "<b>" not in row["text"] and "5 kishi" in row["text"], str(row and row["text"]))
    ob = outbox()
    users = sorted(x["user_id"] for x in ob)
    ok("Bildirishnoma: egasi va menejer (qo'riqchiga sukut bo'yicha yo'q)", users == sorted([owner_id, mid]), str(users))
    title, text_, path = notify.render("queue", cdb.json_load(ob[0]["payload"]))
    ok("Telegram matni ekranlangan, havola /queues/", "<script>" not in text_ and "&lt;script&gt;" not in text_
       and "Chilonzor" in text_ and path == "/queues/", text_[:200])
    alert("devQ1", t1, 1, "R1")
    ok("Takror yuborilgan signal — bitta (idempotent)", cdb.scalar("SELECT COUNT(*) FROM queue_alerts") == 1
       and len(outbox()) == 2)
    alert("devQ1", t1, 2, "R3", text="2 ta kassa ochiq")
    ok("3-qoida — hech kimga sukut bo'yicha yuborilmaydi (queue_low)", len(outbox()) == 2)
    alert("devQ1", t1, 3, "R1", t=time.time() - 3600)
    ok("Eskirgan signal (1 soat oldin) saqlanadi, lekin xabar yuborilmaydi", len(outbox()) == 2
       and cdb.scalar("SELECT COUNT(*) FROM queue_alerts") == 3)
    r = edge("devQ1", t1, "queue_alerts", json={"items": [{"key": "qa:9", "local_id": 9, "rule": "R9"},
                                                          {"key": "qa:10", "rule": "R1"}]})
    ok("Noma'lum qoida / local_id yo'q — rad etildi", len(r.json()["rejected"]) == 2, r.text[:160])
    G = login("qorovul@q.uz")
    c.put("/api/v1/me/notifications", headers=G, json={"prefs": {"queue": True}})
    p = notify.get_prefs(cdb.row("SELECT * FROM users WHERE id=:u", {"u": gid}))
    ok("Qo'riqchi navbat signallarini o'zi yoqa oladi (sozlama)", p["queue"] is True and p["queue_low"] is False, str(p))
    alert("devQ1", t1, 4, "R2", text="kassir yo'q")
    ok("Endi qo'riqchiga ham keladi", gid in [x["user_id"] for x in outbox()[2:]], str(outbox()[2:]))

    # 4) 5-qoida — tarmoq
    n0 = len(outbox())
    alert("devQ2", t2, 1, "R1", zone="Kassa A")
    r5 = cdb.rows("SELECT * FROM queue_alerts WHERE rule='R5'")
    new = outbox()[n0:]
    ok("5-qoida: 15 daqiqada 2 filialda navbat → tarmoq signali (bitta)", len(r5) == 1 and r5[0]["branch_id"] is None
       and "Chilonzor" in r5[0]["text"] and "Yunusobod" in r5[0]["text"], str([x["text"][:80] for x in r5]))
    net = [x for x in new if str(x["ref"]).startswith("net:")]
    ok("Tarmoq signali faqat egaga", [x["user_id"] for x in net] == [owner_id], str(net))
    alert("devQ3", t3, 1, "R2", zone="K")
    ok("30 daqiqa ichida ikkinchi tarmoq signali yo'q", cdb.scalar("SELECT COUNT(*) FROM queue_alerts WHERE rule='R5'") == 1)
    ok("Boshqa kompaniyaga ta'sir yo'q (kompaniya bo'yicha)",
       cdb.scalar("SELECT COUNT(DISTINCT company_id) FROM queue_alerts") == 1)

    # 5) doira
    qa = c.get("/api/v1/queues", headers=A).json()["alerts"]
    ok("Egasi barcha signallarni (tarmoq signali bilan) ko'radi", any(x["rule"] == "R5" for x in qa)
       and {x["branch_name"] for x in qa if x["branch_id"]} == {"Chilonzor", "Yunusobod", "Sergeli"})
    many = [{"key": f"qa:{100 + k}", "local_id": 100 + k, "t": time.time() - 100, "rule": "R3", "title": "x", "text": "y"}
            for k in range(260)]
    edge("devQ2", t2, "queue_alerts", json={"items": many})
    qg = c.get("/api/v1/queues", headers=G).json()
    ok("Qo'riqchi faqat o'z filialini ko'radi (tarmoq signalisiz; boshqa filialda 260 ta yangi signal bo'lsa ham)",
       [x["name"] for x in qg["branches"]] == ["Chilonzor"] and qg["alerts"]
       and all(x["branch_id"] == b1 for x in qg["alerts"]), str([(x["branch_id"], x["rule"]) for x in qg["alerts"]][:5]))
    ok("Boshqa filialga so'rov — 403", c.get(f"/api/v1/queues?branch_id={b2}", headers=G).status_code == 403)
    ok("Kirishsiz — 401", TestClient(cloud.app).get("/api/v1/queues").status_code == 401)
    qb = c.get(f"/api/v1/queues?branch_id={b2}", headers=A).json()
    ok("Bitta filial tanlansa — tarmoq signali ko'rinmaydi", all(x["branch_id"] == b2 for x in qb["alerts"]))
    nt = c.get("/api/v1/me/notifications", headers=A).json()
    ok("Sozlamalar API navbat bandlari bilan", "queue" in nt["prefs"] and "queue_low" in nt["prefs"], str(nt["prefs"]))

    # 6) bulut boti /navbat
    sent = []
    old = notify.TelegramPoller.reply
    notify.TelegramPoller.reply = staticmethod(lambda chat, txt: sent.append((chat, txt)))
    try:
        bot = notify.TelegramPoller.__new__(notify.TelegramPoller)
        bot.on_message({"chat": {"id": 9002}, "text": "/navbat"})
        txt = sent[-1][1] if sent else ""
        ok("Bulut boti /navbat: menejer o'z filiali kassalarini ko'radi", "1-kassa" in txt and "5 kishi" in txt
           and "Yunusobod" not in txt and "&lt;b&gt;" in txt, txt[:200])
        cdb.execute("UPDATE branches SET queues=:q WHERE id=:b", {"q": '{"at": 1, "items": []}', "b": b1})
        bot.on_message({"chat": {"id": 9002}, "text": "/navbat"})
        ok("Eskirgan holat — 'aloqa yo'q'", "eskirgan" in sent[-1][1], sent[-1][1][:120])
    finally:
        notify.TelegramPoller.reply = old


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
