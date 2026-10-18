"""2.6.0 (10-bosqich) sinovi: demo rejimi (edge: namuna do'kon, alohida papka, banner, tayyorlik; bulut: 3 filialli
namuna kompaniya), landing sayt (/sayt/, aloqa .env dan, CSP, telefon kengligi) va saytdagi so'rovlar (lidlar:
tekshiruv, honeypot, tezlik, IP chegarasi, takror telefon, Telegram/email xabari va qayta urinish, tugmalar bilan
holat, admin API, CLI va darvoza). Hammasi vaqtinchalik papkalarda — jonli data/ va cloud/data/ ga tegilmaydi.
Kun vaqti va vaqt mintaqasiga bog'liq emas. SQLite; CLOUD_TEST_PG_URL bilan bulut qismi PostgreSQL'da ham.

    python -m tests.test_demo
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hushyor_demo_"))
os.environ["HUSHYOR_DATA_DIR"] = str(_TMP / "demo")          # demo.reset() faqat "demo" nomli papkada ishlaydi
os.environ["HUSHYOR_DEMO"] = "1"
os.environ["ADMIN_USER"], os.environ["ADMIN_PASSWORD"] = "demo", "demo12345"
os.environ["CLOUD_DATA_DIR"] = str(_TMP / "cloud")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN",
          "CLOUD_URL", "EDGE_TOKEN", "CLOUD_LEADS_CHAT", "CLOUD_LEADS_EMAIL", "CLOUD_DEMO", "CLOUD_SITE_PHONE",
          "CLOUD_SITE_TELEGRAM", "CLOUD_SITE_EMAIL", "CLOUD_SITE_ADDRESS", "CLOUD_SITE_PRICE", "CLOUD_DB_URL"):
    os.environ.pop(k, None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db  # noqa: E402
from tests import mock_telegram as tg  # noqa: E402

results: list[bool] = []
ADMIN = "superadmin-sinov-tokeni-uzunroq-1234567"


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait(fn, timeout: float, step: float = 0.3) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step)
    return False


# ============================================================================ 1. edge demo
def edge_suite() -> None:
    print("\n--- Edge demo: ma'lumot, sahifalar, tayyorlik ---")
    from app import demo, pilot, reports, web

    ok("Demo rejimi yoqilgan, papka — vaqtinchalik 'demo'", config.DEMO and config.DATA_DIR == _TMP / "demo")
    db.init_db()
    ok("Bo'sh baza — is_empty()", demo.is_empty())
    t0 = time.time()
    st = demo.seed()
    dt = time.time() - t0
    ok("30 kunlik ma'lumot (tez)", st["metrics"] > 30 * 1440 * 3 and st["counts"] > 5000 and st["queue_minutes"] > 5000
       and st["events"] >= 30 and st["section_visits"] > 5000 and st["flow_hours"] > 100 and 24 <= st["reports"] <= 28
       and dt < 60, f"{st}, {dt:.1f} s")
    ok("Endi bo'sh emas (qayta ishga tushganda qayta yaratilmaydi)", not demo.is_empty())
    srcs = db.rows("SELECT * FROM sources ORDER BY id")
    ok("3 ta kamera — samples/ videolari (aylanib), har birida zona", len(srcs) == 3
       and all(s["kind"] == "file" and Path(s["uri"]).is_file() and s["loop"] for s in srcs)
       and db.scalar("SELECT COUNT(DISTINCT source_id) FROM zones") == 3)
    ev = db.rows("SELECT id, created_at, status, clip_path FROM events ORDER BY id")
    ok("Signallar vaqt tartibida, ko'pchiligi tekshirilgan, javobsizlari faqat oxirgi kunlarda",
       all(a["created_at"] <= b["created_at"] for a, b in zip(ev, ev[1:]))
       and sum(e["status"] != "yangi" for e in ev) >= 0.8 * len(ev)
       and all(e["created_at"] >= time.time() - 4 * 86400 for e in ev if e["status"] == "yangi"))
    clip = next((e["clip_path"] for e in ev if e["clip_path"]), None)
    ok("Signal kliplari namuna videodan (mp4 + rasm)", clip and (config.CLIPS_DIR / clip).stat().st_size > 10000
       and (config.CLIPS_DIR / clip.replace(".mp4", ".jpg")).is_file(), str(clip))
    cal = db.row("SELECT * FROM cam_calib")
    ok("Zal kamerasi rejaga bog'langan (kalibrlash, tekshiruv nuqtalari bilan)", cal and cal["enabled"]
       and cal["source_id"] == srcs[0]["id"] and '"check": 1' in cal["pairs"])
    ok("Pilot boshlangan, egasining baholari bor", pilot.since_until()[0]
       and db.scalar("SELECT COUNT(*) FROM pilot_feedback") >= 2)
    t1 = time.time()
    cams = reports.cameras(t1 - 7 * 86400, t1)["cams"]
    ok("Demo rejimida namuna videolar kamera hisobida (hisobot, pilot)", len(cams) == 3, str(len(cams)))

    c = TestClient(web.app)
    r = c.post("/login", data={"username": "demo", "password": "demo12345", "next": "/"}, follow_redirects=False)
    ok("Demo login: demo / demo12345", r.status_code in (302, 303), str(r.status_code))
    bad = []
    for path in ("/", "/events", "/reports", "/visits", "/navbat", "/oqim", "/xavfsizlik", "/pilot", "/live", "/help"):
        p = c.get(path)
        if p.status_code != 200 or 'data-testid="demo-banner"' not in p.text:
            bad.append(f"{path}:{p.status_code}")
    ok("Har bo'limda DEMO banneri", not bad, ", ".join(bad))
    ok("Bosh sahifada 'Telegram ulanmagan' ogohlantirishi yo'q", 'id="tg-warn"' not in c.get("/").text)
    rd = c.get("/api/pilot/readiness?force=1").json()
    items = {i["key"]: i for i in rd["items"]} if isinstance(rd.get("items"), list) else {}
    # Sinovda kamera oqimlari ishga tushmagan (ilova lifespan'siz) — "Kameralar" bu yerda tekshirilmaydi (pastda: jonli)
    ok("Tayyorlik: parol — demo, Telegram va avtostart — ixtiyoriy (muammo emas)", set(rd["bad"]) <= {"Kameralar"}
       and items.get("password", {}).get("state") == "ok" and items.get("telegram", {}).get("optional")
       and items.get("autostart", {}).get("optional") and items.get("cameras"), str(rd["bad"]))
    tr = c.post("/api/model/train", data={"synthetic": "100"})
    ok("Demo: model o'qitish o'chirilgan (umumiy models/ papkasi o'zgarmasin)", tr.status_code == 400, str(tr.status_code))
    from app import diagnostics
    ok("Demo: diagnostika haqiqiy .env ni o'qimaydi", "demo" in str(diagnostics._env_keys().get("_fayl", "")))
    cj = c.get(f"/api/flow/calib/{srcs[0]['id']}").json()
    ok("Oqim: kalibrlash moslashuvi yaxshi (LOO < 0.3 m)", cj["calib"] and cj["calib"]["fit"]["ok"]
       and (cj["calib"]["fit"]["loo_rms"] or 9) < 0.3, str((cj["calib"] or {}).get("stats")))

    # Ehtiyot: reset faqat demo rejimida va "demo" papkasida
    import app.demo as dm

    real = dm.DATA_DIR
    dm.DATA_DIR = _TMP / "haqiqiy"
    try:
        dm.reset()
        refused = False
    except RuntimeError:
        refused = True
    dm.DATA_DIR = real
    ok("reset() 'demo' bo'lmagan papkada ishlamaydi", refused)


def script_suite() -> None:
    print("\n--- scripts/demo.py: bitta buyruq, alohida papka, band port ---")
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_script", ROOT / "scripts" / "demo.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    env = m.demo_env(8090)
    ok("Muhit: Telegram, bulut, SMS, tashqi manzil bo'sh; HUSHYOR_DEMO=1; faqat 127.0.0.1",
       all(env[k] == "" for k in ("TELEGRAM_BOT_TOKEN", "CLOUD_URL", "EDGE_TOKEN", "SMS_ESKIZ_EMAIL", "PUBLIC_URL"))
       and env["HUSHYOR_DEMO"] == "1" and env["HOST"] == "127.0.0.1" and env["HUSHYOR_DATA_DIR"] == str(m.DEMO_DIR))
    ok("Sukut papka — data/demo (haqiqiy data/ emas)", m.ROOT / "data" / "demo" == Path(m.DEMO_DIR)
       or os.environ.get("HUSHYOR_DEMO_DIR"))
    base = dict(os.environ)
    for k in ("HUSHYOR_DATA_DIR", "HUSHYOR_DEMO", "ADMIN_USER", "ADMIN_PASSWORD"):
        base.pop(k, None)
    d2 = _TMP / "s" / "demo"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--seed-only", "--days", "7"],
                       env={**base, "HUSHYOR_DEMO_DIR": str(d2), "TELEGRAM_BOT_TOKEN": "777:HAQIQIY"},
                       capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    ok("--seed-only: alohida papkada baza yaratildi", r.returncode == 0 and (d2 / "hushyor.db").is_file()
       and "Tayyor" in r.stdout, (r.stdout + r.stderr)[-300:])
    ok("Belgi: to'liq yaratilgandan keyin bugungi sana", (d2 / ".seeded").read_text().strip() == time.strftime("%Y-%m-%d"))
    (d2 / ".seeded").write_text("2026-01-01")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--seed-only", "--days", "7"],
                       env={**base, "HUSHYOR_DEMO_DIR": str(d2)}, capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    ok("Boshqa kunda yaratilgan demo — avtomatik qayta yaratiladi", r.returncode == 0 and "qayta yaratiladi" in r.stdout
       and "Tayyor" in r.stdout and (d2 / ".seeded").read_text().strip() == time.strftime("%Y-%m-%d"), r.stdout[-200:])
    (d2 / ".seeded").unlink()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--seed-only", "--days", "7"],
                       env={**base, "HUSHYOR_DEMO_DIR": str(d2)}, capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    ok("Yarim yo'lda to'xtatilgan (belgisiz) — qayta yaratiladi", r.returncode == 0 and "tugallanmagan" in r.stdout
       and (d2 / ".seeded").is_file(), r.stdout[-200:])
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--seed-only"],
                       env={**base, "HUSHYOR_DEMO_DIR": str(_TMP / "boshqa")}, capture_output=True, text=True, timeout=60)
    ok("'demo' nomli bo'lmagan papka rad etiladi (--reset xavfsiz)", r.returncode == 2 and not (_TMP / "boshqa").exists())
    port = free_port()
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(1)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--port", str(port), "--no-browser"],
                       env={**base, "HUSHYOR_DEMO_DIR": str(d2)}, capture_output=True, text=True, timeout=60)
    s.close()
    ok("Port band — xabar va chiqish (bazaga tegmaydi)", r.returncode == 2 and "port band" in r.stdout, r.stdout[-200:])
    # To'liq ishga tushish: bitta buyruq → panel ochiladi, login, banner, ma'lumot
    port = free_port()
    logf = open(_TMP / "demo_run.log", "w+b")          # quvur emas: bola jarayon to'lib qolgan quvurda osilmasin
    p = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "demo.py"), "--port", str(port), "--no-browser"],
                         env={**base, "HUSHYOR_DEMO_DIR": str(d2)}, stdout=logf, stderr=subprocess.STDOUT,
                         cwd=str(ROOT))
    try:
        up = wait(lambda: httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2, trust_env=False).status_code == 200, 120, 1)
        good = False
        if up:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20) as h:
                h.post("/login", data={"username": "demo", "password": "demo12345", "next": "/"})
                page = h.get("/")
                good = page.status_code == 200 and 'data-testid="demo-banner"' in page.text
                ready = wait(lambda: h.get("/api/pilot/readiness?force=1").json()["bad"] == [], 60, 2)
        ok("Bitta buyruq: demo panel ochildi, login ishladi, DEMO banneri", up and good)
        ok("Jonli demo: tayyorlik ro'yxatida muammo yo'q (namuna videolar kamera sifatida ishlayapti)", up and ready)
    finally:
        if os.name == "nt":
            # Windows: konsol yopilsa yoki Ctrl+C — ikkala jarayon ham to'xtaydi; sinovda butun daraxt to'xtatiladi
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.terminate()                               # SIGTERM: scripts/demo.py panel jarayonini ham to'xtatadi
        try:
            p.wait(timeout=40)
        except subprocess.TimeoutExpired:
            p.kill()
    gone = wait(lambda: not _port_open(port), 30, 0.5)
    logf.seek(0)
    out = logf.read().decode("utf-8", "replace")
    logf.close()
    ok("To'xtatilganda panel ham to'xtaydi (port bo'shaydi)", gone and p.returncode is not None, out[-300:] if not gone else "")
    ok("Haqiqiy bot tokeni demo jarayonga o'tmadi", "777:HAQIQIY" not in out and "Telegram bot ishga tushdi" not in out)


# ============================================================================ 2. bulut demo, sayt, lidlar
def cloud_suite(label: str) -> None:
    print(f"\n--- Bulut ({label}): demo kompaniya, sayt, lidlar ---")
    from cloud import demo as cdemo
    from cloud import leads, notify
    from cloud import server as cloud

    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    notify.cfg = notify.Config(tz_offset=5, offline_sec=0, escalate_sec=0)
    cloud._state.update(tokens=[], admin=ADMIN, fail=False, fail_paths=(), notify_configured=True, no_notify=True)
    cloud._login_fails.clear()
    leads._hits.clear()
    leads._salt.clear()                      # yangi baza — yangi sir (kv jadvalida)
    c = TestClient(cloud.app)

    env = cdemo.demo_env()
    ok(f"{label}: demo muhiti — bot, email, tokenlar, baza manzili bo'sh; alohida papka", env["CLOUD_DEMO"] == "1"
       and all(env[k] == "" for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_ADMIN_TOKEN",
                                       "CLOUD_EDGE_TOKENS", "CLOUD_LEADS_CHAT", "CLOUD_DB_URL"))
       and env["CLOUD_DATA_DIR"].endswith(os.path.join("cloud", "data", "demo")))
    real = cdemo.DEMO_DIR, cdemo.MARK
    tmpd = _TMP / f"cd_{label}" / "demo"
    tmpd.mkdir(parents=True)
    cdemo.DEMO_DIR, cdemo.MARK = tmpd, tmpd / ".seeded"
    try:
        e0 = cdemo.stale()
        (tmpd / "cloud.db").write_text("x")
        e1 = cdemo.stale()
        cdemo.MARK.write_text("2026-01-01")
        e2 = cdemo.stale()
        cdemo.MARK.write_text(cdemo._today())
        e3 = cdemo.stale()
        cdemo.reset()
        gone = not tmpd.exists()
    finally:
        cdemo.DEMO_DIR, cdemo.MARK = real
    ok(f"{label}: bulut demo — yarim yo'lda / boshqa kunda yaratilgani qayta yaratiladi, reset papkani o'chiradi",
       e0 is None and "tugallanmagan" in (e1 or "") and "2026-01-01" in (e2 or "") and e3 is None and gone,
       str((e0, e1, e2, e3, gone)))
    ok(f"{label}: bo'sh baza", cdemo.is_empty())
    t0 = time.time()
    st = cdemo.seed()
    ok(f"{label}: 3 filial, 30 kun (tez)", st["branches"] == 3 and st["events"] >= 60 and st["visits_min"] > 10000
       and st["queue_min"] > 10000 and st["flow_hours"] > 100 and time.time() - t0 < 120, f"{st}")
    tok = c.post("/api/v1/auth/login", json={"email": cdemo.EMAIL, "password": cdemo.PASSWORD}).json().get("token")
    h = {"Authorization": f"Bearer {tok}"}
    bs = c.get("/api/v1/branches", headers=h).json()["branches"]
    ok(f"{label}: filiallar onlayn, tayyorlik va bugungi tashriflar bor", len(bs) == 3 and all(b["online"] for b in bs)
       and all(b["readiness"] for b in bs) and all(b["visits_today"] is not None for b in bs),
       str([(b["name"], b["online"]) for b in bs]))
    ids = {b["name"]: b["id"] for b in bs}
    cmp_ = c.get("/api/v1/compare?period=week", headers=h).json()
    keys = {r["key"] for r in cmp_["recs"]}
    ok(f"{label}: taqqoslash — tavsiyalar (Yunusobod kutish, Samarqand xato va o'chiq vaqt)",
       len(cmp_["branches"]) == 3 and f"wait:{ids['Yunusobod filiali']}" in keys
       and f"false:{ids['Samarqand filiali']}" in keys and f"uptime:{ids['Samarqand filiali']}" in keys, str(keys))
    q = c.get("/api/v1/queues", headers=h).json()
    fl = c.get("/api/v1/flow", headers=h).json()
    vi = c.get("/api/v1/visits", headers=h).json()
    evs = c.get("/api/v1/events?limit=500", headers=h).json()["events"]
    ok(f"{label}: navbat, oqim, tashriflar, hodisalar — har bo'limda ma'lumot",
       len(q["branches"]) == 3 and all(b["live"] is not None for b in q["branches"]) and q["alerts"]
       and fl["plan"] and fl["total"] > 0 and len(fl["sections"]) == 6
       and sum(d["in"] for d in vi["days"]) > 1000 and len(evs) >= 60)
    clip = next((e for e in evs if e["clip_file"]), None)
    if clip:
        dev, name = clip["clip_file"].split("/")
        ok(f"{label}: klip panelda ochiladi", c.get(f"/api/v1/clips/{dev}/{name}", headers=h).status_code == 200)
    else:
        ok(f"{label}: klip (OpenCV yo'q — kliplarsiz)", True, "o'tkazib yuborildi")
    m = c.get(f"/api/v1/branches/{ids['Chilonzor filiali']}/metrics?hours=24", headers=h).json()["rows"]
    ok(f"{label}: ko'rsatkichlar (24 soat, kameralar va tizim)", len({r['source_id'] for r in m}) == 4)
    ls0 = cdb.scalar("SELECT MAX(last_seen) FROM branches")
    cdemo.tick(time.time() + 5)
    ok(f"{label}: har daqiqalik 'hello' taqlidi", cdb.scalar("SELECT MAX(last_seen) FROM branches") > ls0)
    ok(f"{label}: oddiy serverda demo belgisi yo'q", "demo" not in TestClient(cloud.app).get("/api/v1/auth/status").json()
       and c.get("/api/v1/me", headers=h).json()["demo"] is False)
    cloud.DEMO = True
    try:
        s_ = TestClient(cloud.app).get("/api/v1/auth/status").json()
        ok(f"{label}: demo serverda kirish oynasi login/parolni oladi, panel banneri", s_.get("demo")
           and s_.get("demo_login", {}).get("email") == cdemo.EMAIL and c.get("/api/v1/me", headers=h).json()["demo"])
    finally:
        cloud.DEMO = False

    # ---- sayt
    r = c.get("/sayt", follow_redirects=False)
    ok(f"{label}: /sayt → /sayt/", r.status_code in (307, 308) and r.headers["location"] == "/sayt/")
    r = c.get("/sayt/")
    html_ = r.text
    ok(f"{label}: sayt — o'zbekcha, forma, qat'iy CSP (ichki skript yo'q)", r.status_code == 200
       and 'lang="uz"' in html_ and 'id="lead-form"' in html_ and 'name="hp_extra"' in html_
       and r.headers.get("content-security-policy") == cloud.SITE_CSP and "unsafe-inline" not in cloud.SITE_CSP
       and not re.search(r"<script(?![^>]*\bsrc=)", html_) and " on" + "click=" not in html_)
    ok(f"{label}: tashqi manba yo'q (shrift, CDN, skript)", not re.search(r'(src|href)="https?://', html_)
       and "@import" not in (ROOT / "site" / "style.css").read_text(encoding="utf-8"))
    imgs = re.findall(r'src="(img/[^"]+)"', html_)
    ok(f"{label}: barcha rasmlar bor va beriladi", imgs and all(c.get("/sayt/" + i).status_code == 200 for i in imgs)
       and all(c.get("/sayt/" + f).status_code == 200 for f in ("app.js", "style.css")), str(imgs))
    ok(f"{label}: saytda soxta telefon raqami yo'q", not re.search(r"\+998\s?\d{2}\s?\d{3}", html_.replace(
        'placeholder="+998 90 123 45 67"', "")))
    js = c.get("/sayt/sozlama.js").text
    ok(f"{label}: aloqa qatorlari bo'sh (.env da berilmagan), narx — so'rov bo'yicha", '"phone": ""' in js
       and "so'rov bo'yicha" in js)
    os.environ.update(CLOUD_SITE_PHONE="+998 71 000 00 00", CLOUD_SITE_TELEGRAM="@hushyor_uz",
                      CLOUD_SITE_EMAIL="a@b.uz</script><b>", CLOUD_SITE_PRICE="kelishiladi")
    try:
        js = c.get("/sayt/sozlama.js").text
        ok(f"{label}: .env dagi aloqa — sozlama.js da (Telegram havolaga, '<' ekranlangan)", "+998 71 000 00 00" in js
           and "https://t.me/hushyor_uz" in js and "</script>" not in js and "\\u003c/script" in js and "kelishiladi" in js)
    finally:
        for k in ("CLOUD_SITE_PHONE", "CLOUD_SITE_TELEGRAM", "CLOUD_SITE_EMAIL", "CLOUD_SITE_PRICE"):
            os.environ.pop(k, None)

    # ---- lidlar: tekshiruv
    P = "/api/v1/leads"
    good = {"name": "Sardor <b>aka</b>", "phone": "+998 (90) 123-45-67", "store": "Baraka market", "city": "Toshkent",
            "branches": "2–3", "message": "6 ta kamera\r\nbor", "elapsed": 9.5, "source": "sayt"}
    r = c.post(P, json=good)
    lid = cdb.scalar("SELECT id FROM leads WHERE phone='998901234567'")
    row = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": lid or 0}) or {}
    ok(f"{label}: javob faqat {{ok: true}} (id, takror, bot belgisi tashqariga chiqmaydi)", r.json() == {"ok": True})
    ok(f"{label}: so'rov qabul qilindi — telefon normallashgan, izoh tozalangan", r.status_code == 200 and lid
       and row.get("phone") == "998901234567" and row.get("status") == "yangi" and row.get("message") == "6 ta kamera\nbor"
       and row.get("ip_hash") and "testclient" not in str(row), str(r.json()))
    ok(f"{label}: kanal sozlanmagan — faqat bazada (urinish sanalmaydi — kanal sozlangach yuboriladi)", wait(
        lambda: (cdb.row("SELECT notify_error, notify_next FROM leads WHERE id=:i", {"i": lid}) or {}).get("notify_error"),
        5) and cdb.scalar("SELECT notify_next FROM leads WHERE id=:i", {"i": lid}) is None
       and cdb.scalar("SELECT notify_tries FROM leads WHERE id=:i", {"i": lid}) == 0)
    n0 = cdb.scalar("SELECT COUNT(*) FROM leads")
    r1 = c.post(P, json={**good, "phone": "901112233", "hp_extra": "http://spam"})
    r2 = c.post(P, json={**good, "phone": "901112244", "elapsed": 1.2})
    ok(f"{label}: bot (honeypot / juda tez) — jim 'qabul', bazaga yozilmaydi", r1.status_code == r2.status_code == 200
       and r1.json() == r2.json() == {"ok": True} and cdb.scalar("SELECT COUNT(*) FROM leads") == n0)
    codes = [c.post(P, json={**good, "phone": "90111"}).status_code,
             c.post(P, json={**good, "name": " a "}).status_code,
             c.post(P, json={k: v for k, v in good.items() if k != "elapsed"}).status_code,
             c.post(P, json={**good, "phone": "+998 90 12a 45 67"}).status_code,
             c.post(P, json={**good, "phone": "99890123456"}).status_code,
             c.post(P, content="name=a&phone=1", headers={"content-type": "application/x-www-form-urlencoded"}).status_code,
             c.post(P, content=b'{"name": "' + b"x" * 20000 + b'"}', headers={"content-type": "application/json"}).status_code,
             c.post(P, json=["x"]).status_code,
             c.post(P, content=b'{"name": "Ali", "phone": "901112255", "elapsed": 1e999}',
                    headers={"content-type": "application/json"}).status_code,
             c.post(P, content=b'{"name": "Ali", "phone": "901112255", "elapsed": 1' + b"0" * 400 + b'}',
                    headers={"content-type": "application/json"}).status_code]
    ok(f"{label}: noto'g'ri — 400 (telefon, ism, eski forma, harf, 998 qisqa), 415, 413, 400, cheksiz/ulkan vaqt 400",
       codes == [400, 400, 400, 400, 400, 415, 413, 400, 400, 400], str(codes))
    big = c.post(P, content=b'{"name": "' + b"x" * 20000 + b'"}', headers={"content-type": "application/json"})
    ok(f"{label}: 413 xabari KB da", "16 KB" in big.json().get("detail", ""), big.text[:100])
    codes = [c.post(P, json={**good, "phone": "901112266"}, headers={"sec-fetch-site": "cross-site"}).status_code,
             c.post(P, json={**good, "phone": "901112266"}, headers={"origin": "https://begona.example"}).status_code,
             c.post(P, content=b'{"name": "Ali", "phone": "901112266", "elapsed": 9}',
                    headers={"content-type": "text/plain;application/json"}).status_code,
             c.post(P, content=b'{"name": "Ali", "phone": "901112266", "elapsed": 9}',
                    headers={"content-type": "application/json; charset=utf-8", "origin": "http://testserver",
                             "sec-fetch-site": "same-origin"}).status_code,
             c.post(P, json={**good, "phone": "901112277"}, headers={"origin": "http://testserver"}).status_code]
    ok(f"{label}: boshqa saytdan (Sec-Fetch-Site, Origin) — 403; 'text/plain;application/json' — 415; o'z saytidan "
       "(Sec-Fetch-Site va faqat Origin bilan) — 200", codes == [403, 403, 415, 200, 200], str(codes))
    leads._hits.clear()
    n0 = cdb.scalar("SELECT COUNT(*) FROM leads")
    r = c.post(P, json={**good, "phone": "90 123 45 67", "message": "yana bir savol"})
    row = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": lid})
    ok(f"{label}: shu telefon 24 soatda takror — faqat takror soni (matn o'zgarmaydi, yangi qator yo'q)",
       r.json() == {"ok": True} and row["repeats"] == 1 and row["message"] == "6 ta kamera\nbor"
       and cdb.scalar("SELECT COUNT(*) FROM leads") == n0)
    leads._hits.clear()
    codes = [c.post(P, json={**good, "phone": f"9077700{i:02d}"}).status_code for i in range(7)]
    ok(f"{label}: bitta IP — soatiga 5 ta, keyin 429", codes == [200] * 5 + [429, 429], str(codes))
    leads._hits.clear()
    old = leads.TOTAL_DAY, leads.HARD_DAY
    leads.TOTAL_DAY = cdb.scalar("SELECT COUNT(*) FROM leads")
    try:
        r = c.post(P, json={**good, "phone": "905550011"})
        q = cdb.row("SELECT * FROM leads WHERE phone='998905550011'") or {}
        ok(f"{label}: kunlik chegaradan keyin — saqlanadi, lekin xabar yuborilmaydi", r.status_code == 200
           and q.get("notify_tries") == leads.MAX_TRIES and "chegara" in (q.get("notify_error") or ""))
        leads.HARD_DAY = cdb.scalar("SELECT COUNT(*) FROM leads")
        r = c.post(P, json={**good, "phone": "905550022"})
        ok(f"{label}: qat'iy kunlik chegara — 429", r.status_code == 429)
    finally:
        leads.TOTAL_DAY, leads.HARD_DAY = old
    leads._hits.clear()
    for i in range(leads.HITS_MAX + 200):
        leads._rate(f"ip{i}", time.time())
    ok(f"{label}: IP chegarasi xotirasi chegarali (eng eskilari chiqariladi)", len(leads._hits) <= leads.HITS_MAX
       and "ip0" not in leads._hits and f"ip{leads.HITS_MAX + 199}" in leads._hits, str(len(leads._hits)))
    leads._hits.clear()
    k = leads._ip_key
    ok(f"{label}: IP kaliti — IPv6 /64 bo'yicha, IPv4-mapped — IPv4", k("2001:db8::1") == k("2001:db8::ffff:1")
       != k("2001:db8:0:1::1") and k("::ffff:10.1.2.3") == "10.1.2.3" and k("10.1.2.3") == "10.1.2.3")
    import hashlib
    ok(f"{label}: IP hash — server siri bilan HMAC (oddiy sha256 emas, barqaror)", leads._ip_hash("10.1.2.3")
       == leads._ip_hash("10.1.2.3") != hashlib.sha256(b"hushyor-lead:10.1.2.3").hexdigest()
       and cdb.kv_get("leads_salt", ""))
    ok(f"{label}: email matni: '< 5 mln, > 3' o'chib ketmaydi", notify._plain("<b>narx</b> &lt; 5 mln, &gt; 3 kamera")
       == "narx < 5 mln, > 3 kamera")

    # ---- xabar: Telegram (soxta server) va email
    port = free_port()
    tg.serve(port)
    notify.cfg = notify.Config(tg_token="777:LEADS", tg_api=f"http://127.0.0.1:{port}", tz_offset=5,
                               leads_chat="-100500")
    tg.CALLS.clear()
    r = leads.create({**good, "phone": "+998 93 000 11 22", "elapsed": 5}, "10.0.0.9", deliver_async=False)
    lid2 = r["id"]
    okd = leads.deliver(lid2)
    sm = tg.calls("sendMessage")
    txt = sm[-1]["data"]["text"] if sm else ""
    kb = str(sm[-1]["data"].get("reply_markup")) if sm else ""
    ok(f"{label}: Telegram — so'rovlar chatiga, ism ekranlangan, telefon chiroyli, tugmalar", okd and len(sm) == 1
       and str(sm[0]["data"]["chat_id"]) == "-100500" and "&lt;b&gt;aka&lt;/b&gt;" in txt and "+998 93 000 11 22" in txt
       and f"l:{lid2}:pilot" in kb and f"l:{lid2}:rad" in kb, txt[:200])
    ok(f"{label}: yuborilgan — qayta yuborilmaydi", leads.deliver(lid2) and len(tg.calls("sendMessage")) == 1)
    r5 = leads.create({**good, "phone": "+998 93 000 11 55", "elapsed": 5}, "10.0.0.9", deliver_async=False)["id"]
    nn = cdb.scalar("SELECT notify_next FROM leads WHERE id=:i", {"i": r5})

    def drain(at: float) -> int:
        tot = 0
        for _ in range(20):                 # har chaqiruvda ko'pi bilan 5 ta — hammasi yuborilguncha
            leads._last_retry[0] = 0
            n_ = leads.retry_pending(at)
            tot += n_
            if not n_:
                break
        return tot
    sent0 = len(tg.calls("sendMessage"))
    late = drain(time.time() + 10)
    ok(f"{label}: kanal sozlanmagan paytda kelgan so'rovlar (#1 ham) kanal sozlangach yuboriladi", late >= 2
       and len(tg.calls("sendMessage")) == sent0 + late
       and cdb.scalar("SELECT notified_at FROM leads WHERE id=:i", {"i": lid}), str(late))
    leads._inflight.add(r5)
    busy = leads.deliver(r5)
    leads._inflight.discard(r5)
    ok(f"{label}: yangi so'rovni qayta urinish 2 daqiqagacha olmaydi, yuborilayotganini ikkinchi oqim yubormaydi",
       nn and nn - time.time() > 100 and busy is False
       and cdb.scalar("SELECT notified_at FROM leads WHERE id=:i", {"i": r5}) is None)
    ok(f"{label}: ... 2 daqiqadan keyin qayta urinish uni yuboradi", drain(time.time() + 200) == 1
       and cdb.scalar("SELECT notified_at FROM leads WHERE id=:i", {"i": r5}))
    tg.CALLS.clear()
    lid3 = leads.create({**good, "phone": "+998 93 000 11 33", "elapsed": 5}, "10.0.0.9", deliver_async=False)["id"]
    tg.FAIL_NEXT["sendMessage"] = 1
    now = time.time()
    leads._last_retry[0] = 0
    first = leads.deliver(lid3, now)
    r3 = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": lid3})
    early = leads.retry_pending(now + 10)
    leads._last_retry[0] = 0
    late = leads.retry_pending(now + 61)
    ok(f"{label}: Telegram xatosi — keyinroq qayta urinish (1 daqiqa), keyin yetdi", not first
       and r3["notify_tries"] == 1 and abs(r3["notify_next"] - now - 60) < 1 and early == 0 and late == 1
       and cdb.scalar("SELECT notified_at FROM leads WHERE id=:i", {"i": lid3}))
    l8 = leads.create({**good, "phone": "+998 93 000 11 88", "elapsed": 5}, "10.0.0.18", deliver_async=False)["id"]
    cdb.execute("UPDATE leads SET notify_tries=9 WHERE id=:i", {"i": l8})
    tg.FAIL_NEXT["sendMessage"] = 1
    t8 = time.time()
    leads.deliver(l8, t8)
    r8 = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": l8})
    tg.FAIL_NEXT.clear()
    ok(f"{label}: qayta urinish oralig'i ko'pi bilan 1 soat, 2 kungacha (Telegram uzoq uzilsa ham yo'qolmaydi)",
       r8["notify_tries"] == 10 and abs(r8["notify_next"] - t8 - 3600) < 1 and leads.MAX_TRIES * 3600 > 40 * 3600)
    leads._last_retry[0] = 0
    leads.retry_pending(t8 + 3601)
    # Kutilmagan xato (masalan, SMTP paroli/host buzuq) — Telegram yetgan bo'lsa yuborilgan; faqat email bo'lsa —
    # urinish sanaladi va qayta urinish faqat vaqtida (har 30 s da qayta-qayta emas)
    real_send = notify.smtp_send

    def boom(_msg):
        raise UnicodeEncodeError("ascii", "ʻ", 0, 1, "sinov")
    notify.smtp_send = boom
    try:
        notify.cfg = notify.Config(tg_token="777:LEADS", tg_api=f"http://127.0.0.1:{port}", tz_offset=5,
                                   leads_chat="-100500", smtp_host="smtp.test", smtp_from="bot@hushyor.uz",
                                   leads_email="sotuv@hushyor.uz")
        tg.CALLS.clear()
        l6 = leads.create({**good, "phone": "+998 93 000 11 66", "elapsed": 5}, "10.0.0.16", deliver_async=False)["id"]
        ok6 = leads.deliver(l6)
        r6 = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": l6})
        leads._last_retry[0] = 0
        again = leads.retry_pending(time.time() + 600)
        ok(f"{label}: Telegram yetdi, email kutilmagan xato — yuborilgan, xato yozilgan, qayta yuborilmaydi", ok6
           and r6["notified_at"] and "Email" in (r6["notify_error"] or "") and again == 0
           and len(tg.calls("sendMessage")) == 1)
        notify.cfg = notify.Config(tz_offset=5, smtp_host="smtp.test", smtp_from="bot@hushyor.uz",
                                   leads_email="sotuv@hushyor.uz")
        l7 = leads.create({**good, "phone": "+998 93 000 11 77", "elapsed": 5}, "10.0.0.17", deliver_async=False)["id"]
        t7 = time.time()
        bad7 = leads.deliver(l7, t7)
        leads._last_retry[0] = 0
        soon = leads.retry_pending(t7 + 30)
        r7 = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": l7})
        ok(f"{label}: faqat email, kutilmagan xato — urinish sanaldi, keyingisi 1 daqiqadan keyin (oqim to'xtamaydi)",
           bad7 is False and soon == 0 and r7["notify_tries"] == 1 and abs(r7["notify_next"] - t7 - 60) < 1
           and not r7["notified_at"])
        cdb.execute("UPDATE leads SET notify_tries=:m WHERE id=:i", {"m": leads.MAX_TRIES, "i": l7})
    finally:
        notify.smtp_send = real_send
    sent_mail = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent_mail.append(msg)
    real_smtp = notify.smtplib.SMTP
    notify.smtplib.SMTP = FakeSMTP
    notify.cfg = notify.Config(tz_offset=5, smtp_host="smtp.test", smtp_from="bot@hushyor.uz", smtp_tls="none",
                               leads_email="sotuv@hushyor.uz")
    try:
        lid4 = leads.create({**good, "phone": "+998 93 000 11 44", "elapsed": 5}, "10.0.0.9", deliver_async=False)["id"]
        okm = leads.deliver(lid4)
    finally:
        notify.smtplib.SMTP = real_smtp
    ok(f"{label}: email — so'rovlar manziliga (matn, holat buyrug'i)", okm and len(sent_mail) == 1
       and sent_mail[0]["To"] == "sotuv@hushyor.uz" and f"#{lid4}" in sent_mail[0]["Subject"]
       and "998 93 000 11 44" in sent_mail[0].get_content() and "\n" not in str(sent_mail[0]["Subject"]),
       str(sent_mail[0]["Subject"]) if sent_mail else "xat yo'q")

    # ---- Telegram tugmalari va /lidlar
    notify.cfg = notify.Config(tg_token="777:LEADS", tg_api=f"http://127.0.0.1:{port}", tz_offset=5,
                               leads_chat="-100500")
    poller = notify.TelegramPoller()
    tg.CALLS.clear()
    poller.on_callback({"id": "cb1", "data": f"l:{lid2}:pilot", "from": {"first_name": "Nizomiddin"},
                        "message": {"message_id": 7, "chat": {"id": -100500}}})
    r2_ = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": lid2})
    ed = tg.calls("editMessageText")
    ok(f"{label}: Telegram tugmasi — holat 'pilot', xabarda kim bosgani (izohga yozilmaydi)", r2_["status"] == "pilot"
       and not r2_["note"] and ed and "Pilot kelishildi</b> (Nizomiddin)" in ed[-1]["data"]["text"])
    leads.set_status(lid2, "pilot", "3 kamera, 1-noyabrdan")
    poller.on_callback({"id": "cb3", "data": f"l:{lid2}:boglanildi", "from": {"first_name": "Ali"},
                        "message": {"message_id": 7, "chat": {"id": -100500}}})
    r2_ = cdb.row("SELECT * FROM leads WHERE id=:i", {"i": lid2})
    ok(f"{label}: tugma bor izohni o'chirmaydi", r2_["status"] == "boglanildi" and r2_["note"] == "3 kamera, 1-noyabrdan")
    leads.set_status(lid2, "pilot")
    poller.on_callback({"id": "cb2", "data": f"l:{lid2}:rad", "from": {"first_name": "Begona"},
                        "message": {"message_id": 7, "chat": {"id": 999}}})
    ans = tg.calls("answerCallbackQuery")
    ok(f"{label}: boshqa chatdan tugma — rad etiladi", cdb.scalar("SELECT status FROM leads WHERE id=:i", {"i": lid2})
       == "pilot" and ans and "Ruxsat" in ans[-1]["data"]["text"])
    tg.CALLS.clear()
    poller.on_message({"chat": {"id": -100500}, "text": "/lidlar", "from": {"first_name": "N"}})
    rep = tg.calls("sendMessage")
    ok(f"{label}: /lidlar — so'rovlar va darvoza (faqat so'rovlar chatida)", rep and "darvozasi" in rep[-1]["data"]["text"])
    tg.CALLS.clear()
    poller.on_message({"chat": {"id": -100777}, "text": "/lidlar", "from": {"first_name": "N"}})
    poller.on_message({"chat": {"id": -100777}, "text": "/chatid@hushyor_test_bot", "from": {"first_name": "N"}})
    rep = [x["data"]["text"] for x in tg.calls("sendMessage")]
    ok(f"{label}: boshqa guruhda /lidlar yo'q; /chatid — guruh raqami (CLOUD_LEADS_CHAT uchun)", len(rep) == 2
       and "darvozasi" not in rep[0] and "CLOUD_LEADS_CHAT=-100777" in rep[1], str(rep)[:200])
    tg.CALLS.clear()
    poller.on_message({"chat": {"id": -100500, "type": "supergroup"}, "text": "Rahmat, bog'landim", "from": {"first_name": "N"}})
    poller.on_message({"chat": {"id": -100888, "type": "group"}, "text": "salom", "from": {"first_name": "N"}})
    quiet = not tg.calls("sendMessage")
    poller.on_message({"chat": {"id": 5555, "type": "private"}, "text": "salom", "from": {"first_name": "N"}})
    ok(f"{label}: so'rovlar guruhidagi va begona guruhdagi oddiy xabarga bot javob bermaydi (shaxsiy chatda — beradi)",
       quiet and len(tg.calls("sendMessage")) == 1)

    # ---- admin API va darvoza
    ok(f"{label}: admin API — tokensiz yo'q", c.get("/api/v1/admin/leads").status_code == 401)
    ah = {"Authorization": f"Bearer {ADMIN}"}
    a = c.get("/api/v1/admin/leads", headers=ah).json()
    ok(f"{label}: admin ro'yxati — telefon formatlangan, holatlar soni, darvoza", a["total"] >= 8
       and a["leads"][0]["phone"].startswith("+998") and a["by_status"]["pilot"] == 1 and not a["gate"]["ok"]
       and "ip_hash" not in a["leads"][0])
    codes = [c.post(f"/api/v1/admin/leads/{lid}", headers=ah, json={"status": "bog'lanildi", "note": "ertaga"}).status_code,
             c.post(f"/api/v1/admin/leads/{lid}", headers=ah, json={"status": "zo'r"}).status_code,
             c.post("/api/v1/admin/leads/99999", headers=ah, json={"status": "rad"}).status_code]
    ok(f"{label}: admin holat: 200 / noto'g'ri holat 400 / yo'q so'rov 404", codes == [200, 400, 404]
       and cdb.scalar("SELECT status FROM leads WHERE id=:i", {"i": lid}) == "boglanildi", str(codes))
    for i in range(12):
        leads.create({**good, "phone": f"+998 94 100 20 {i:02d}", "elapsed": 5}, f"10.1.0.{i}", deliver_async=False)
    for x in leads.listing("yangi")[:2]:
        leads.set_status(x["id"], "pilot")
    g = leads.summary()["gate"]
    ok(f"{label}: 10-bosqich darvozasi: ≥ 10 so'rov va ≥ 3 pilot", g["ok"] and g["pilots"] == 3 and g["leads"] >= 10, str(g))
    if label == "SQLite":
        r = subprocess.run([sys.executable, "-m", "cloud.server", "--lead", str(lid), "rad", "narx", "qimmat"],
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                           env={**os.environ, "CLOUD_DATA_DIR": str(_TMP / "cloud")})
        ok("CLI: --lead ID HOLAT izoh, keyin hisobot va darvoza", r.returncode == 0 and "Rad etildi" in r.stdout
           and "O'TDI" in r.stdout and cdb.scalar("SELECT note FROM leads WHERE id=:i", {"i": lid}) == "narx qimmat",
           (r.stdout + r.stderr)[-300:])
    notify.cfg = notify.Config(tz_offset=5)


def browser_suite() -> None:
    """Sayt telefon kengligida: gorizontal siljish yo'q, forma yuboriladi (haqiqiy brauzer)."""
    print("\n--- Sayt brauzerda (telefon va kompyuter) ---")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ok("Playwright yo'q — brauzer sinovi o'tkazilmadi", True)
        return
    import uvicorn

    from cloud import leads, notify
    from cloud import server as cloud

    os.environ.pop("CLOUD_DB_URL", None)
    cloud.cdb.reset_engine()
    cloud.init_db()
    notify.cfg = notify.Config(tz_offset=5)
    leads._hits.clear()
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(cloud.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    n0 = cloud.cdb.scalar("SELECT COUNT(*) FROM leads")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for name, vp, mob in (("telefon", {"width": 360, "height": 740}, True),
                                  ("kompyuter", {"width": 1366, "height": 900}, False)):
                ctx = b.new_context(viewport=vp, is_mobile=mob, has_touch=mob)
                pg = ctx.new_page()
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                pg.goto(f"http://127.0.0.1:{port}/sayt/")
                pg.wait_for_load_state("networkidle")
                sw = pg.evaluate("document.documentElement.scrollWidth")
                ok(f"{name}: gorizontal siljish yo'q, xato yo'q", sw <= vp["width"] and not errs, f"{sw} {errs[:2]}")
                pg.click('[role="tab"]:has-text("Kassa navbati")')
                ok(f"{name}: ekranlar tablari ishlaydi", pg.is_visible("#sh-2") and not pg.is_visible("#sh-1"))
                pg.click('button[type="submit"]')
                ok(f"{name}: bo'sh forma — xabar, yuborilmaydi", "Ismingizni" in pg.inner_text("#form-msg"))
                pg.wait_for_timeout(3100)                # odam kabi: forma ochilganidan ≥ 3 s
                pg.fill('input[name="name"]', "Brauzer Sinov")
                pg.fill('input[name="phone"]', "+998 97 " + ("111 22 33" if mob else "111 22 44"))
                pg.select_option('select[name="branches"]', "2–3")
                pg.click('button[type="submit"]')
                # (wait_for_function ishlatilmaydi: saytning CSP'si sahifada eval'ni taqiqlaydi)
                pg.locator("#form-msg", has_text="Rahmat").wait_for(timeout=10000)
                ok(f"{name}: forma yuborildi — 'Rahmat' va forma tozalandi", pg.input_value('input[name="name"]') == "")
                ctx.close()
            b.close()
    finally:
        srv.should_exit = True
    ok("Brauzerdan 2 ta so'rov bazada", cloud.cdb.scalar("SELECT COUNT(*) FROM leads") == n0 + 2)


def panel_build_check() -> None:
    print("\n--- Bulut paneli build (DEMO banneri) ---")
    from cloud import server as cloud

    found = any("demo-banner" in p.read_text(encoding="utf-8", errors="ignore")
                for p in cloud.PANEL_DIR.rglob("*.js")) if cloud.PANEL_DIR.is_dir() else False
    ok("panel_dist: DEMO banneri va kirish oynasidagi demo login", found)


def main() -> int:
    t = time.time()
    edge_suite()
    script_suite()
    cloud_suite("SQLite")
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        cloud_suite("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)
    else:
        print("\n  (PostgreSQL sinovi o'tkazilmadi: CLOUD_TEST_PG_URL berilmagan)")
    browser_suite()
    panel_build_check()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
