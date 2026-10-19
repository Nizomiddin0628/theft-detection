"""2.4.0 (8-bosqich) bulut tomoni: filiallarni taqqoslash (/api/v1/compare — kun/hafta, o'tgan davr bilan
solishtirish, navbat, signallar, xavfsizlik, ishlagan vaqt, tavsiyalar, rol doirasi) va haftalik xulosa
(notify.weekly_digest — dushanba 09:00 dan keyin, haftasiga bir marta, egasi va menejer, sozlama "digest").
SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham.

    python -m tests.test_reports_cloud
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_rcloud_")
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from cloud import notify  # noqa: E402
from cloud import report  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def unit() -> None:
    print("\n=== Davrlar va tavsiya qoidalari ===")
    import calendar

    off = 5 * 3600

    def at(s: str) -> float:
        return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M")) - off

    lbl = lambda k, now=None, d=None: report.label(k, *report.period(k, off, now, d), off)  # noqa: E731
    ok("Hafta (sukut): dushanba — o'tgan hafta", lbl("week", at("2026-10-19 10:00")) == "12.10–18.10.2026")
    ok("Hafta (sukut): seshanba ham o'tgan hafta (joriy emas)", lbl("week", at("2026-10-20 12:00")) == "12.10–18.10.2026")
    ok("Hafta (sukut): yakshanba kechqurun — undan oldingi hafta", lbl("week", at("2026-10-18 23:00")) == "05.10–11.10.2026")
    ok("Kun (sukut): kecha", lbl("day", at("2026-10-20 00:30")) == "19.10.2026, dushanba")
    d = at("2026-10-14 12:00")
    ok("Sana berilsa — o'sha kun / o'sha hafta", lbl("day", d=d) == "14.10.2026, chorshanba"
       and lbl("week", d=d) == "12.10–18.10.2026")

    def br(i, name, served=0, wait=None, conf=0, rej=0, unrev=0, saf_un=0, up=1.0, vin=0, vprev=0):
        return {"id": i, "name": name, "queue": {"served": served, "avg_wait": wait, "max_wait": 0, "long_sec": 0} if served else None,
                "theft": {"total": conf + rej + unrev, "confirmed": conf, "rejected": rej, "unreviewed": unrev,
                          "false_share": rej / (conf + rej) if conf + rej else None},
                "safety": {"total": saf_un, "unreviewed": saf_un}, "uptime": up,
                "visits": {"in": vin, "prev_in": vprev, "change_pct": report._pct(vin, vprev)}}
    keys = lambda rows: {r["key"] for r in report.recommend(rows)}  # noqa: E731
    ok("Kutish: boshqalardan 1.5× va ≥ 60 s — tavsiya (2 filialda ham)",
       keys([br(1, "A", 40, 150), br(2, "B", 40, 60)]) == {"wait:1"})
    ok("Kutish: 45 s — me'yorda (60 s dan kam), kam xizmat (< 30) — hisobga olinmaydi",
       not keys([br(1, "A", 40, 45), br(2, "B", 40, 20)]) and not keys([br(1, "A", 10, 300), br(2, "B", 40, 60)]))
    ok("Yolg'iz filialda kutish solishtirilmaydi", not keys([br(1, "A", 40, 300)]))
    ok("Xato ulushi ≥ 60% va ≥ 5 javob", keys([br(1, "A", conf=2, rej=4)]) == {"false:1"}
       and not keys([br(1, "A", conf=1, rej=3)]) and not keys([br(1, "A", conf=3, rej=3)]))
    ok("Javobsiz: ≥ 5 va ≥ 30%", keys([br(1, "A", conf=10, unrev=5)]) == {"unrev:1"}
       and not keys([br(1, "A", conf=20, unrev=5)]) and not keys([br(1, "A", unrev=4)]))
    ok("Xavfsizlik javobsiz va ishlagan vaqt < 90%", keys([br(1, "A", saf_un=1, up=0.85)]) == {"safety:1", "uptime:1"}
       and not keys([br(1, "A", up=None)]) and not keys([br(1, "A", up=0.95)]))
    ok("Tashrif −20% (asos ≥ 100)", keys([br(1, "A", vin=79, vprev=100)]) == {"visits:1"}
       and not keys([br(1, "A", vin=50, vprev=90)]) and not keys([br(1, "A", vin=85, vprev=100)]))
    ok("Kompyuter o'chiq bo'lgan bo'lsa — tashrif kamayishi alohida aytilmaydi (sabab — o'sha)",
       keys([br(1, "A", vin=10, vprev=300, up=0.4)]) == {"uptime:1"})
    r = report.recommend([br(1, "A", unrev=6), br(2, "B", saf_un=1)])
    ok("Ogohlantirishlar birinchi", [x["level"] for x in r] == ["warn", "info"], str(r))
    big = {"total": {"visits": 1}, "recs": [{"text": "x" * 500}] * 6,
           "branches": [dict(br(i, f"<Filial {i}>" + "y" * 400, vin=1), theft={"total": 0}) for i in range(10)]}
    t = report.digest_text(big, "12.10–18.10.2026", "A & B")
    ok("Xulosa matni ≤ 3900, butun qatorlar bilan qisqaradi, HTML ekranlangan", len(t) <= 3900 and t.endswith("…")
       and "&lt;Filial 0&gt;" in t and "A &amp; B" in t and t.count("<b>") == t.count("</b>"), str(len(t)))


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
    a = cloud.bootstrap_owner("egasi@r.uz", "Parol12345", "R savdo")
    b1, t1 = cdb.create_branch(a["company_id"], "Chilonzor", "devR1")
    b2, t2 = cdb.create_branch(a["company_id"], "Yunusobod", "devR2")
    b3, _t3 = cdb.create_branch(a["company_id"], "Sergeli", "devR3")
    mid = int(cdb.create_user(a["company_id"], "menejer@r.uz", "Parol12345", "Menejer", "manager", [b1]))
    gid = int(cdb.create_user(a["company_id"], "qorovul@r.uz", "Parol12345", "Qorovul", "guard", [b1]))
    oid = int(cdb.scalar("SELECT id FROM users WHERE email='egasi@r.uz'"))
    for uid_, ch, addr in ((oid, "telegram", "9101"), (oid, "email", "egasi@r.uz"), (mid, "telegram", "9102"),
                           (gid, "telegram", "9103")):
        notify.add_target(uid_, ch, addr, {"name": "x"})

    def edge(dev, tok, path, **kw):
        return c.post(f"/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev}, **kw)

    def login(email):
        return {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": email, "password": "Parol12345"}).json()["token"]}

    off = 5 * 3600
    w0, w1 = report.period("week", off)          # o'tgan (tugagan) hafta
    tue, wed = w0 + 86400, w0 + 2 * 86400
    pw = -7 * 86400                               # bir hafta oldin

    # --- tashriflar (edge daqiqalik qatorlari)
    vis = [("devR1", t1, tue + 10 * 3600, 100, 3), ("devR1", t1, tue + pw + 10 * 3600, 100, 4),
           ("devR2", t2, wed + 11 * 3600, 50, 3), ("devR2", t2, wed + pw + 11 * 3600, 50, 2)]
    for dev, tok, m0, n, k in vis:
        items = [{"key": f"m:{int(m0) + i * 60}:1", "minute": int(m0) + i * 60, "source_id": 1, "source_name": "Eshik",
                  "in": n, "out": n} for i in range(k)]
        r = edge(dev, tok, "metrics", json={"items": items})
        assert r.status_code == 200 and len(r.json()["accepted"]) == k, r.text
    # --- ishlagan vaqt: tizim qatorlari (source_id=0) — Chilonzor yarim hafta, Yunusobod to'liq, Sergeli — shanbadan
    # (shu hafta ulangan: hisob birinchi ko'rsatkichdan)
    with cdb.engine().begin() as conn:
        for bid, start, n in ((b1, w0, 7 * 1440 // 2), (b2, w0, 7 * 1440), (b3, w0 + 5 * 86400, 2 * 1440)):
            conn.execute(cdb.metrics_minute.insert(), [{"branch_id": bid, "minute": int(start) + i * 60, "source_id": 0,
                                                        "data": {"source_id": 0}} for i in range(n)])
    # --- navbat
    for dev, tok, wsum in (("devR1", t1, 40 * 150), ("devR2", t2, 40 * 60)):
        m = int(tue + 12 * 3600)
        r = edge(dev, tok, "queues", json={"items": [{"key": f"qm:{m}:1", "minute": m, "zone_id": 1, "zone_name": "1-kassa",
                                                      "t_obs": 60, "served": 40, "wait_sum": wsum, "wait_n": 40,
                                                      "wait_max": 300, "len_max": 6}]})
        assert r.status_code == 200, r.text
    # --- signallar
    evs = []
    sts = ["rad_etilgan"] * 4 + ["tasdiqlangan"] * 2 + ["yangi"] * 6
    for i, st in enumerate(sts, 1):
        evs.append(("devR1", t1, {"local_id": i, "status": st}))
    evs.append(("devR1", t1, {"local_id": 13, "kind": "fall", "status": "yangi"}))
    evs.append(("devR1", t1, {"local_id": 14, "status": "rad_etilgan", "job_id": 5}))          # video tahlili
    evs.append(("devR1", t1, {"local_id": 15, "status": "yangi", "created_at": tue + pw}))   # o'tgan hafta
    evs.append(("devR2", t2, {"local_id": 1, "status": "tasdiqlangan"}))
    evs.append(("devR2", t2, {"local_id": 2, "status": "tasdiqlangan", "created_at": wed + 3600}))
    for dev, tok, kw in evs:
        ts = kw.pop("created_at", tue + 15 * 3600 + kw["local_id"])
        it = {"key": f"ev:{kw['local_id']}", "uid": f"{dev}:{kw['local_id']}", "created_at": ts, "updated_at": ts,
              "kind": "torso", "score": 80, "source_name": "Kassa", **kw}
        r = edge(dev, tok, "events", json={"items": [it]})
        assert r.status_code == 200 and r.json()["accepted"], r.text

    O, M, G = login("egasi@r.uz"), login("menejer@r.uz"), login("qorovul@r.uz")
    r = c.get("/api/v1/compare", headers=O)
    j = r.json()
    ok("/compare (sukut — o'tgan hafta): davr, yorliq, tugagan", r.status_code == 200 and j["period"] == "week"
       and j["t0"] == w0 and j["t1"] == w1 and j["partial"] is False and j["label"] == report.label("week", w0, w1, off),
       r.text[:200])
    bs = {x["name"]: x for x in j.get("branches", [])}
    ok("Barcha 3 filial, tashrif bo'yicha tartiblangan", [x["name"] for x in j["branches"]] == ["Chilonzor", "Yunusobod", "Sergeli"])
    ch, yu, se = bs["Chilonzor"], bs["Yunusobod"], bs["Sergeli"]
    ok("Tashriflar va o'tgan hafta bilan farq", ch["visits"] == {"in": 300, "prev_in": 400, "change_pct": -25.0}
       and yu["visits"] == {"in": 150, "prev_in": 100, "change_pct": 50.0}, str((ch["visits"], yu["visits"])))
    ok("Navbat: o'rtacha kutish, xizmat", ch["queue"]["served"] == 40 and ch["queue"]["avg_wait"] == 150.0
       and yu["queue"]["avg_wait"] == 60.0 and se["queue"] is None, str(ch["queue"]))
    ok("Signallar: ✅/❌/javobsiz, xato ulushi (video tahlili va boshqa hafta kirmaydi)",
       ch["theft"] == {"total": 12, "confirmed": 2, "rejected": 4, "unreviewed": 6, "false_share": 0.667,
                       "loss": 0, "loss_n": 0}                                   # 3.1.0: oldini olingan zarar
       and yu["theft"]["total"] == 2, str(ch["theft"]))
    ok("Xavfsizlik signallari alohida", ch["safety"] == {"total": 1, "unreviewed": 1} and yu["safety"]["total"] == 0)
    ok("Ishlagan vaqt (tizim qatorlari; yangi ulangan — birinchi ko'rsatkichdan)", ch["uptime"] == 0.5 and yu["uptime"] == 1.0
       and se["uptime"] == 1.0,
       str((ch["uptime"], yu["uptime"], se["uptime"])))
    t = j["total"]
    ok("Jami: tashrif, kutish (xizmat bo'yicha o'rtacha), signallar", t == {"visits": 450, "prev_visits": 500, "change_pct": -10.0,
       "served": 80, "avg_wait": 105.0, "theft": 14, "safety": 1, "unreviewed": 6, "loss": 0, "loss_n": 0}, str(t))
    keys = [x["key"] for x in j["recs"]]
    ok("Tavsiyalar: kutish, xato, javobsiz, xavfsizlik, ishlagan vaqt (Chilonzor; tashrif kamayishi — o'chiq vaqt sababli emas)",
       set(keys) == {f"{k}:{b1}" for k in ("wait", "false", "unrev", "safety", "uptime")}, str(keys))
    ok("Tavsiyalarda ogohlantirishlar birinchi", [x["level"] for x in j["recs"]] == sorted(
        (x["level"] for x in j["recs"]), key=lambda v: v != "warn"))
    day = time.strftime("%Y-%m-%d", time.gmtime(tue + off))
    jd = c.get(f"/api/v1/compare?period=day&date={day}", headers=O).json()
    bd = {x["name"]: x for x in jd["branches"]}
    ok("Kun (sana bilan): faqat o'sha kun, o'tgan haftaning o'sha kuni bilan", jd["period"] == "day" and jd["t0"] == tue
       and bd["Chilonzor"]["visits"] == {"in": 300, "prev_in": 400, "change_pct": -25.0}
       and bd["Yunusobod"]["visits"]["in"] == 0 and bd["Yunusobod"]["theft"]["total"] == 1
       and bd["Chilonzor"]["uptime"] == 1.0 and bd["Sergeli"]["uptime"] is None, str(bd["Yunusobod"]["visits"]))
    jm = c.get("/api/v1/compare", headers=M).json()
    ok("Menejer — faqat o'z filiali", [x["name"] for x in jm["branches"]] == ["Chilonzor"] and jm["total"]["visits"] == 300)
    ok("Qo'riqchiga ruxsat yo'q (403), kirmaganga 401", c.get("/api/v1/compare", headers=G).status_code == 403
       and TestClient(cloud.app).get("/api/v1/compare").status_code == 401)       # c — login cookie'si bor
    ok("Noto'g'ri / kelajakdagi sana — 400", c.get("/api/v1/compare?date=17.10.2026", headers=O).status_code == 400
       and c.get("/api/v1/compare?period=day&date=2099-01-01", headers=O).status_code == 400)
    cur = c.get("/api/v1/compare?period=week&date=" + time.strftime("%Y-%m-%d", time.gmtime(time.time() + off)), headers=O).json()
    ok("Joriy hafta — 'hali tugamagan'", cur["partial"] is True and cur["t0"] == w1)
    co2 = cdb.create_company("Boshqa")
    cdb.create_branch(co2, "Begona", "devX")
    ok("Boshqa kompaniya filiallari ko'rinmaydi", [x["name"] for x in c.get("/api/v1/compare", headers=O).json()["branches"]]
       == ["Chilonzor", "Yunusobod", "Sergeli"])

    # --- sozlama
    p_m = c.get("/api/v1/me/notifications", headers=M).json()["prefs"]
    p_g = c.get("/api/v1/me/notifications", headers=G).json()["prefs"]
    p_o = c.get("/api/v1/me/notifications", headers=O).json()["prefs"]
    ok("Sukut: egasi va menejer — haftalik xulosa yoqilgan, qo'riqchi — yo'q", p_o["digest"] and p_m["digest"] and not p_g["digest"])

    # --- haftalik xulosa
    def digests():
        return cdb.rows("SELECT user_id, channel, ref, payload FROM notify_outbox WHERE kind='digest' ORDER BY id")
    ok("Dushanba 08:00 — hali erta", notify.weekly_digest(w1 + 8 * 3600) == 0 and not digests())
    n = notify.weekly_digest(w1 + 10 * 3600)
    rows = digests()
    ok("Dushanba 10:00: egasi (Telegram + email) va menejer — 3 xabar, qo'riqchi yo'q", n == 3 and len(rows) == 3
       and sorted(r["user_id"] for r in rows) == sorted([oid, oid, mid]), str([(r["user_id"], r["channel"]) for r in rows]))
    po = cdb.json_load(next(r["payload"] for r in rows if r["user_id"] == oid))
    pm = cdb.json_load(next(r["payload"] for r in rows if r["user_id"] == mid))
    ok("Egasining xulosasi: barcha filiallar, tavsiyalar", "Chilonzor" in po["text"] and "Yunusobod" in po["text"]
       and "Tavsiyalar" in po["text"] and po["week"] == int(w0) and report.label("week", w0, w1, off) in po["title"], po["text"][:200])
    ok("Menejerning xulosasi: faqat o'z filiali", "Chilonzor" in pm["text"] and "Yunusobod" not in pm["text"])
    title, text, path = notify.render("digest", po)
    ok("render('digest'): sarlavha, matn, /compare/ havolasi", title == po["title"] and text == po["text"] and path == "/compare/")
    ok("Haftasiga bir marta (qayta chaqiruv, seshanba)", notify.weekly_digest(w1 + 11 * 3600) == 0
       and notify.weekly_digest(w1 + 86400 + 3 * 3600) == 0 and len(digests()) == 3)
    cdb.execute("DELETE FROM kv WHERE key='digest_week'")
    ok("Hafta belgisi yozilmay qolgan bo'lsa (yarim yo'lda xato) — navbatdagi xabarlar takrorlanmaydi",
       notify.weekly_digest(w1 + 12 * 3600) == 0 and len(digests()) == 3 and cdb.kv_get("digest_week") == str(int(w0)))
    real = report.compare
    report.compare = lambda ids, *a, **k: (_ for _ in ()).throw(RuntimeError("x")) if ids == [b1] else real(ids, *a, **k)
    cdb.execute("DELETE FROM notify_outbox WHERE kind='digest'")
    cdb.execute("DELETE FROM kv WHERE key='digest_week'")
    n_err = notify.weekly_digest(w1 + 12 * 3600)
    report.compare = real
    ok("Bitta foydalanuvchida xato — qolganlariga baribir yuboriladi", n_err == 2
       and {r["user_id"] for r in digests()} == {oid}, str(n_err))
    ok("Chorshanba — yubormaydi (o'tkazib yuborilgan hafta yuborilmaydi)", notify.weekly_digest(w1 + 7 * 86400 + 2 * 86400 + 36000) == 0)
    r = c.put("/api/v1/me/notifications", json={"prefs": {"digest": False}}, headers=M)
    ok("Menejer xulosani o'chirdi", r.status_code == 200 and r.json()["prefs"]["digest"] is False, r.text[:120])
    c.put("/api/v1/me/notifications", json={"prefs": {"digest": True}}, headers=G)
    before = len(digests())
    n2 = notify.weekly_digest(w1 + 7 * 86400 + 86400 + 3600)        # keyingi hafta seshanba (server dushanba o'chiq edi)
    rows2 = digests()[before:]
    ok("Keyingi hafta (seshanba): faqat egasi; o'chirgan menejer va qo'riqchi yo'q", n2 == 2
       and len(rows2) == 2 and {r["user_id"] for r in rows2} == {oid}, str([(r["user_id"], r["channel"]) for r in rows2]))
    ok("force — qayta yuborish", notify.weekly_digest(w1 + 7 * 86400 + 86400 + 7200, force=True) == 2)
    cdb.execute("UPDATE users SET active=:f WHERE id=:u", {"f": False, "u": oid})
    ok("Faol bo'lmagan foydalanuvchiga yuborilmaydi", notify.weekly_digest(w1 + 14 * 86400 + 36000) == 0)


def main() -> int:
    unit()
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
