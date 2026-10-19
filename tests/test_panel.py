"""Bulut paneli (1.7.0) sinovi — haqiqiy brauzerda (Playwright, Chromium headless).

Bulut serveri cloud/panel_dist dagi statik panelni beradi; sinov ma'lumotlari edge API orqali yuboriladi
(hodisalar, kadr va klip, ko'rsatkichlar, kamera holati). Tekshiriladi: kirish va yo'naltirish, bosh sahifadagi
monitor devori, jonli xabar (SSE) va toast, hodisani T/X/U tugmalari bilan tekshirish (bazada va edge buyrug'ida),
navbat bilan tekshirish, filial yaratish (token bir marta ko'rinadi) va tokenni yangilash, ko'rsatkich grafiklari,
xodim qo'shish, Telegram ulash (deep-link kod), sozlamalar, qo'riqchi roli cheklovlari, telefon ekrani (gorizontal
aylantirish yo'q), chiqish, xavfsizlik sarlavhalari, brauzer konsolida xato yo'qligi.

Ishga tushirish:  python -m tests.test_panel          (Chromium: PLAYWRIGHT_BROWSERS_PATH yoki `playwright install chromium`)
Rasm (ko'rib chiqish uchun): HUSHYOR_SHOTS=papka python -m tests.test_panel
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_panel_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_DB_URL"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from tests import mock_telegram as tg  # noqa: E402

TG_PORT, CLOUD_PORT = 8781, 9321
BASE = f"http://127.0.0.1:{CLOUD_PORT}"
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "dokon_namuna_20s.mp4"
SHOTS = os.environ.get("HUSHYOR_SHOTS")
tg.serve(TG_PORT)

from cloud import notify  # noqa: E402
from cloud import server as cloud  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def wait(cond, timeout=10.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


# ----------------------------------------------------------------------------- sinov ma'lumotlari
A_DEV, B_DEV = "dokon-chilonzor", "dokon-yunusobod"
KINDS = ["torso", "pocket", "bag", "offscreen", "torso", "pocket"]
CAMS_A = [{"id": 1, "name": "Kassa 1", "kind": "rtsp", "state": "ishlayapti", "running": True},
          {"id": 2, "name": "Kirish", "kind": "rtsp", "state": "ishlayapti", "running": True},
          {"id": 3, "name": "Ombor", "kind": "rtsp", "state": "qayta ulanmoqda", "running": True}]


def edge(dev: str, tok: str, path: str, **kw) -> httpx.Response:
    return httpx.post(f"{BASE}/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev},
                      timeout=30, **kw)


def ev_item(dev: str, lid: int, score: float, ago: float, kind: str, cam: str, status: str = "yangi", **kw) -> dict:
    t = time.time() - ago
    it = {"key": f"ev:{lid}", "uid": f"{dev}:{lid}", "local_id": lid, "created_at": t, "updated_at": t, "kind": kind,
          "score": score, "level": "yuqori" if score >= 75 else "o'rta", "status": status, "source_name": cam,
          "reasons": ["Qo'l kiyim ichiga kirdi", "Buyum javondan olindi, qaytmadi", "Kassadan o'tmay chiqish tomon"]}
    it.update(kw)
    return it


def frames(n: int) -> list[bytes]:
    import cv2

    cap = cv2.VideoCapture(str(SAMPLE))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 400
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n))
        okk, fr = cap.read()
        if not okk:
            break
        fr = cv2.resize(fr, (480, int(fr.shape[0] * 480 / fr.shape[1])))
        out.append(cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 72])[1].tobytes())
    cap.release()
    return out


FLOW_PLAN = {"id": 1, "name": "Asosiy zal", "w_m": 20, "h_m": 12, "cell": 0.5, "nx": 40, "ny": 24, "grid": "40x24@0.5",
             "sections": [{"id": 1, "name": "Sut", "color": "#2563eb", "pts": [[2, 2], [7, 2], [7, 6], [2, 6]]},
                          {"id": 2, "name": "Non", "color": "#16a34a", "pts": [[13, 2], [18, 2], [18, 6], [13, 6]]}]}


def seed() -> dict:
    cdb = cloud.cdb
    r = cloud.bootstrap_owner("egasi@dokon.uz", "Parol12345", "Baraka savdo")
    cid = r["company_id"]
    a_id, a_tok = cdb.create_branch(cid, "Chilonzor do'koni", A_DEV, "Chilonzor, 9-kvartal")
    b_id, b_tok = cdb.create_branch(cid, "Yunusobod do'koni", B_DEV, "")
    cdb.create_user(cid, "menejer@dokon.uz", "Parol12345", "Dilshod", "manager", [a_id])
    cdb.create_user(cid, "qorovul@dokon.uz", "Parol12345", "Sardor", "guard", [b_id])
    assert edge(A_DEV, a_tok, "hello", json={"time": time.time(), "version": "1.7.0", "cameras": CAMS_A,
                                             "pending": 0, "queues": [   # 2.0.0: kassalar holati
        {"zone_id": 11, "name": "1-kassa", "source_name": "Kassa 1", "length": 5, "max_wait": 205, "cashier": True,
         "state": "navbat", "live": True},
        {"zone_id": 12, "name": "2-kassa", "source_name": "Kassa 1", "length": 2, "max_wait": 40, "cashier": False,
         "state": "kassir_yoq", "live": True}],
                                             "flow_plans": [FLOW_PLAN]}).status_code == 200   # 2.1.0: reja
    assert edge(B_DEV, b_tok, "hello", json={"time": time.time(), "version": "1.6.0", "cameras": [
        {"id": 1, "name": "Kassa", "kind": "rtsp", "state": "ishlayapti"}], "pending": 4}).status_code == 200

    # A: 6 ta tekshirilmagan (kadr va klip bilan), 2 ta tekshirilgan; B: 2 ta tekshirilmagan
    items_a = [ev_item(A_DEV, i + 1, s, ago, KINDS[i], cam) for i, (s, ago, cam) in enumerate(
        [(91, 900, "Kassa 1"), (64, 1900, "Kirish"), (78, 3600, "Kassa 1"), (55, 5200, "Ombor"), (83, 7400, "Kirish"),
         (69, 9900, "Kassa 1")])]
    items_a += [ev_item(A_DEV, 7, 72, 20000, "torso", "Kassa 1", status="tasdiqlangan", reviewed_by="Telegram: Dilshod"),
                ev_item(A_DEV, 8, 58, 26000, "bag", "Kirish", status="rad_etilgan", reviewed_by="Telegram: Dilshod")]
    assert edge(A_DEV, a_tok, "events", json={"items": items_a}).status_code == 200
    items_b = [ev_item(B_DEV, 1, 86, 1200, "pocket", "Kassa"), ev_item(B_DEV, 2, 61, 4000, "torso", "Kassa")]
    assert edge(B_DEV, b_tok, "events", json={"items": items_b}).status_code == 200
    jpgs = frames(8)
    clip = SAMPLE.read_bytes()
    for i in range(6):
        r = edge(A_DEV, a_tok, f"events/{A_DEV}:{i + 1}/clip", data={"meta": "{}"},
                 files={"clip": ("c.mp4", clip, "video/mp4"), "thumb": ("t.jpg", jpgs[i % len(jpgs)], "image/jpeg")})
        assert r.status_code == 200, r.text
    r = edge(B_DEV, b_tok, f"events/{B_DEV}:1/clip", data={"meta": "{}"},
             files={"clip": ("c.mp4", clip, "video/mp4"), "thumb": ("t.jpg", jpgs[-1], "image/jpeg")})
    assert r.status_code == 200

    # Ko'rsatkichlar: oxirgi 24 soat, har 10 daqiqada (2 kamera + tizim)
    now = time.time()
    rows = []
    for k in range(144):
        minute = int((now - k * 600) // 60 * 60)
        hour = time.localtime(minute).tm_hour
        busy = 1.0 if 9 <= hour <= 21 else 0.15
        for sid, name, base in ((1, "Kassa 1", 3.2), (2, "Kirish", 1.6)):
            rows.append({"key": f"m:{minute}:{sid}", "minute": minute, "source_id": sid, "source_name": name,
                         "persons_avg": round(base * busy * (0.7 + 0.3 * ((k * 7 + sid) % 5) / 4), 2),
                         "persons_max": int(base * busy * 2) + 1, "fps_ai": 7.5 + sid, "active_pct": 0.4 * busy,
                         "up_pct": 1.0 if (k + sid) % 37 else 0.6, "alerts": 1 if (k * sid) % 23 == 0 else 0,
                         # 1.9.0: "Kirish" kamerasida sanash chizig'i
                         **({"in": int(6 * busy) + k % 3, "out": int(6 * busy) + (k + 1) % 3, "staff_in": k % 2,
                             "staff_out": 0} if sid == 2 else {})})
        rows.append({"key": f"m:{minute}:0", "minute": minute, "source_id": 0, "source_name": "tizim",
                     "cpu": 35 + 25 * busy, "ram": 58 + (k % 6)})
    for i in range(0, len(rows), 400):
        assert edge(A_DEV, a_tok, "metrics", json={"items": rows[i:i + 400]}).status_code == 200
    h0 = now - 2 * 3600
    assert edge(A_DEV, a_tok, "health", json={"items": [
        {"key": "h:3:down:1", "t": h0, "source_id": 3, "source_name": "Ombor", "state": "down", "reason": "kadr kelmayapti"},
        {"key": "h:3:up:1", "t": h0 + 540, "source_id": 3, "source_name": "Ombor", "state": "up", "reason": "9 daqiqa ishlamadi"},
    ]}).status_code == 200
    # 2.0.0: kassa navbati — bugungi daqiqalar (har 10 daqiqada) va qoida signallari
    d0 = cloud._local_day0()
    qrows = []
    for k in range(144):
        minute = int((now - k * 600) // 60 * 60)
        if minute < d0:
            break
        hour = time.localtime(minute).tm_hour
        L = 3.5 if hour in (12, 13, 18) else 1.2
        for zid in (11, 12):
            qrows.append({"key": f"qm:{minute}:{zid}", "minute": minute, "zone_id": zid, "zone_name": f"{zid - 10}-kassa",
                          "t_obs": 60, "len_sum": L * 60, "len_max": int(L + 1.5), "long_sec": 30 if L > 3 else 0,
                          "busy_sec": 55, "cashier_sec": 60, "cashier_known": 1, "served": 2, "wait_sum": 70 * L * 2,
                          "wait_n": 2, "wait_max": 90 * L})
    if qrows:
        assert edge(A_DEV, a_tok, "queues", json={"items": qrows}).status_code == 200
    assert edge(A_DEV, a_tok, "queue_alerts", json={"items": [
        {"key": "qa:1", "local_id": 1, "t": now - 1800, "rule": "R1", "level": "warning", "zone_name": "1-kassa",
         "title": "Qo'shimcha kassa oching", "text": "1-kassa: navbatda <b>5 kishi</b> (40 s dan beri), eng uzoq "
                                                     "kutayotgan — 3 daq 25 s.\nBoshqa kassalar: 2-kassa — band, 2 kishi"},
        {"key": "qa:2", "local_id": 2, "t": now - 600, "rule": "R2", "level": "danger", "zone_name": "2-kassa",
         "title": "Kassani tekshiring", "text": "2-kassa: navbatda <b>2 kishi</b>, kassir joyi 1 daq dan beri bo'sh."}]}
        ).status_code == 200
    # 2.1.0: oqim — bugungi soatlik yig'indi (bo'limlar, yo'nalish, heatmap)
    h0 = int(cloud._local_day0() // 3600 * 3600)
    if h0 < cloud._local_day0():
        h0 += 3600
    h0 = min(h0, int(now // 3600 * 3600))
    assert edge(A_DEV, a_tok, "flow", json={"items": [
        {"key": f"fh:1:{h0}", "plan_id": 1, "hour": h0, "grid": "40x24@0.5", "nx": 40, "ny": 24, "cell": 0.5, "obs": 3000,
         "visits": {"1": 9, "2": 4}, "moves": [[1, 2, 3]], "occ": {str(5 * 40 + 8): 60, str(6 * 40 + 9): 30},
         "pas": {str(5 * 40 + 8): 12}}]}).json()["accepted"]
    # B do'kon 3 soatdan beri aloqasiz (har qanday edge so'rovi last_seen ni yangilaydi — shuning uchun oxirida)
    cdb.execute("UPDATE branches SET last_seen=:t WHERE id=:b", {"t": time.time() - 3 * 3600, "b": b_id})
    return {"a_id": a_id, "a_tok": a_tok, "b_id": b_id, "b_tok": b_tok}


# ----------------------------------------------------------------------------- brauzer
class Console:
    """Brauzer konsolidagi xatolar va sahifa istisnolari (CSP buzilishi ham shu yerga tushadi)."""

    def __init__(self, page):
        self.errors: list[str] = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))

    def take(self) -> list[str]:
        e, self.errors = self.errors, []
        return e


def login(page, email: str, pw: str = "Parol12345") -> None:
    page.goto(f"{BASE}/login/")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/).*$"), timeout=10000)


def ev_status(uid: str) -> str | None:
    return cloud.cdb.scalar("SELECT status FROM events WHERE uid=:u", {"u": uid})


def suite(pw, s: dict) -> None:
    browser = pw.chromium.launch(executable_path=os.environ.get("HUSHYOR_CHROMIUM") or None)
    try:
        _owner(browser, s)
        _guard(browser, s)
        _account(browser, s)
        _mobile(browser, s)
    finally:
        browser.close()


def _owner(browser, s: dict) -> None:
    print("\n--- Egasi (kompyuter ekrani) ---")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="uz-UZ")
    page = ctx.new_page()
    con = Console(page)

    # Kirish: sessiyasiz sahifa → kirish sahifasi (qaytish manzili bilan)
    page.goto(f"{BASE}/events/")
    page.wait_for_url(re.compile(r"/login/\?next="), timeout=10000)
    ok("Sessiyasiz panel → kirish sahifasi (next bilan)", "next=%2Fevents%2F" in page.url, page.url)
    shot(page, "01_login")
    page.fill("input[name=email]", "egasi@dokon.uz")
    page.fill("input[name=password]", "notogri-parol")
    page.click("button[type=submit]")
    ok("Noto'g'ri parol — tushunarli xato", page.get_by_role("alert").filter(has_text="noto'g'ri").is_visible()
       or page.wait_for_selector("text=email yoki parol noto'g'ri", timeout=5000) is not None)
    con.take()   # kutilgan 401 (noto'g'ri parol) konsol xatosi hisobga olinmaydi
    page.fill("input[name=password]", "Parol12345")
    seen_login: list[dict] = []

    def spy(route):
        resp = route.fetch()
        try:
            seen_login.append(resp.json())
        except Exception:  # noqa: BLE001
            seen_login.append({"xato": True})
        route.fulfill(response=resp)
    page.route("**/api/v1/auth/login", spy)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/events/", timeout=10000)
    page.unroute("**/api/v1/auth/login")
    ok("To'g'ri parol → so'ralgan sahifaga qaytdi", page.url == f"{BASE}/events/", page.url)
    ok("Panel login javobida token yo'q (faqat HttpOnly cookie)", seen_login and seen_login[-1].get("ok")
       and "token" not in seen_login[-1], str(seen_login)[:120])
    # 3.0.0: mavzu tugmasi (chap menyu) — tizim → yorug' → tungi, qayta yuklashda saqlanadi (theme.js), → tizim
    th = "document.documentElement.getAttribute('data-theme')"
    page.click(".theme-toggle")
    t1 = page.evaluate(th)
    page.click(".theme-toggle")
    t2 = page.evaluate(th)
    page.reload()
    page.wait_for_selector(".theme-toggle", timeout=10000)
    t3 = page.evaluate(th)
    bg = [int(x) for x in re.findall(r"\d+", page.evaluate("getComputedStyle(document.body).backgroundColor"))[:3]]
    lab = page.get_attribute(".theme-toggle", "aria-label") or ""
    page.click(".theme-toggle")
    ok("Mavzu tugmasi: yorug' → tungi, qayta yuklashda saqlanadi, fon to'q; yana bosilsa — tizim bo'yicha",
       (t1, t2, t3) == ("light", "dark", "dark") and sum(bg) < 150 and lab.startswith("Mavzu: tungi")
       and page.evaluate(th) is None and page.evaluate("localStorage.getItem('hushyor-theme')") is None,
       f"{t1} {t2} {t3} {bg} {lab!r}")
    # Ochiq yo'naltirish: "/\t/evil.com" brauzerda "//evil.com" bo'lib qoladi — panel tashqariga chiqarmasligi kerak
    for bad in ("%2F%09%2Fevil.example", "%2F%5Cevil.example", "https%3A%2F%2Fevil.example", "%2F%2Fevil.example",
                "%2F.%2F%2Fevil.example", "%2Fa%2F..%2F%2Fevil.example", "%2F%252e%2F%2Fevil.example"):
        page.goto(f"{BASE}/login/?next={bad}")
        page.wait_for_url(lambda u: "/login/" not in u, timeout=10000)
        if not page.url.startswith(BASE):
            break
    ok("Kirish sahifasi boshqa saytga yo'naltirmaydi (next=...)", page.url.startswith(BASE + "/"), page.url)
    cookies = {c["name"]: c for c in ctx.cookies()}
    ok("Sessiya cookie: HttpOnly, SameSite=Strict", cookies.get("hc_session", {}).get("httpOnly") is True
       and cookies["hc_session"].get("sameSite") == "Strict")
    ok("Token JS'dan o'qilmaydi (document.cookie da yo'q)", "hc_session" not in page.evaluate("document.cookie"))

    # Bosh sahifa: monitor devori
    page.goto(f"{BASE}/")
    page.wait_for_selector(".tile", timeout=10000)
    ok("Monitor devori: 8 ta tekshirilmagan (2 filial)", page.inner_text("[data-testid=pending-count]") == "8"
       and page.locator(".tile").count() == 8, page.inner_text("[data-testid=pending-count]"))
    ok("Filial lentasi: 2 filial, biri aloqasiz", page.locator(".plate").count() == 2
       and page.locator(".plate.offline").count() == 1)
    ok("Kamera holati: 2/3 ishlayapti", "2/3" in page.locator(".plate").first.inner_text())
    loaded = "Array.from(document.querySelectorAll('.tile img')).every(i => i.complete && i.naturalWidth > 0)"
    ok("Kadrlar yuklandi (cookie bilan /clips)", wait(lambda: page.evaluate(loaded), 8)
       and page.locator(".tile img").count() == 7)
    ok("Klipsiz hodisa — 'Video yuklanmoqda' belgisi", page.locator(".tile .tile-noimg").count() == 1)
    page.wait_for_selector(".live.on", timeout=10000)
    ok("Jonli ulanish (SSE) o'rnatildi", page.locator(".live.on").count() == 1)
    shot(page, "02_dashboard")

    # Jonli: yangi hodisa → toast + devorda paydo bo'ladi
    t0 = time.time()
    r = edge(A_DEV, s["a_tok"], "events", json={"items": [ev_item(A_DEV, 50, 88, 5, "pocket", "Kassa 1")]})
    assert r.status_code == 200
    page.wait_for_selector(".toast.alert", timeout=8000)
    dt = time.time() - t0
    ok("Yangi hodisa: toast chiqdi", "Shubhali harakat" in page.inner_text(".toast.alert"), f"{dt:.2f} s")
    page.wait_for_selector(f".tile[data-uid='{A_DEV}:50']", timeout=8000)
    ok("Yangi hodisa: devorda paydo bo'ldi, son 9", page.inner_text("[data-testid=pending-count]") == "9")
    shot(page, "03_toast")

    # Hodisani ochish va klaviatura bilan tekshirish
    page.click(".toast.alert .toast-act")
    page.wait_for_url(re.compile(r"/events/\?uid=dokon-chilonzor%3A50"), timeout=8000)
    page.wait_for_selector("[data-testid=confirm]", timeout=8000)
    ok("Toast → hodisa sahifasi", True)
    ok("Hodisa ma'lumoti: harakat turi, ball, sabablar", "Cho'ntakka" in page.inner_text("h1")
       and "88" in page.inner_text(".ev-score") and page.locator(".reasons li").count() == 3)
    ok("Klip hali yo'q — kutish belgisi", page.locator("text=Video hali kelmagan").count() == 1)
    r = edge(A_DEV, s["a_tok"], f"events/{A_DEV}:50/clip", data={"meta": "{}"},
             files={"clip": ("c.mp4", SAMPLE.read_bytes(), "video/mp4"), "thumb": ("t.jpg", frames(1)[0], "image/jpeg")})
    assert r.status_code == 200, r.text
    page.wait_for_selector("video[data-testid=clip]", timeout=8000)
    vsrc = page.get_attribute("video[data-testid=clip]", "src") or ""
    ok("Klip kelishi bilan video o'zi paydo bo'ldi (jonli)", vsrc.endswith(".mp4")
       and page.request.get(BASE + vsrc).status == 200, vsrc)
    shot(page, "04_event")
    page.keyboard.press("t")
    page.wait_for_selector("[data-testid=verdict]", timeout=8000)
    uid = f"{A_DEV}:50"
    ok("T → tasdiqlandi (bazada)", wait(lambda: ev_status(uid) == "tasdiqlangan", 5), str(ev_status(uid)))
    cmd = cloud.cdb.row("SELECT * FROM commands WHERE branch_id=:b ORDER BY id DESC LIMIT 1", {"b": s["a_id"]})
    p = cloud.cdb.json_load(cmd["payload"]) if cmd else {}
    ok("Edge'ga review buyrug'i navbatda", cmd is not None and p.get("status") == "tasdiqlangan"
       and str(p.get("by", "")).startswith("Bulut:"))
    page.keyboard.press("u")
    page.wait_for_selector("[data-testid=confirm]", timeout=8000)
    ok("U → qaror bekor qilindi", wait(lambda: ev_status(uid) == "yangi", 5))
    page.keyboard.press("x")
    page.wait_for_selector("[data-testid=verdict]", timeout=8000)
    ok("X → xato signal", wait(lambda: ev_status(uid) == "rad_etilgan", 5)
       and "Xato signal" in page.inner_text("[data-testid=verdict]"))
    page.keyboard.press("Escape")
    page.wait_for_url(f"{BASE}/events/", timeout=8000)
    ok("Esc → hodisalar ro'yxati", True)

    # Ro'yxat va holat yorliqlari
    page.wait_for_selector("tr.click", timeout=8000)
    n_new = page.locator("tr.click").count()
    page.click("role=tab[name='Xato signal']")
    wait(lambda: page.locator("tr.click").count() == 2, 8)
    ok("Yorliqlar: tekshirilmagan 8, xato signal 2", n_new == 8 and page.locator("tr.click").count() == 2, str(n_new))
    shot(page, "05_events")

    # Navbat bilan tekshirish: eng eskisidan boshlab, qaror → keyingisi
    page.goto(f"{BASE}/")
    page.click("text=Navbat bilan tekshirish")
    page.wait_for_selector("[data-testid=confirm]", timeout=8000)
    first = page.url.split("uid=")[1].split("&")[0].replace("%3A", ":")
    page.keyboard.press("t")
    page.wait_for_url(lambda u: "uid=" in u and first.replace(":", "%3A") not in u, timeout=8000)
    page.wait_for_selector("[data-testid=confirm]", timeout=8000)
    second = page.url.split("uid=")[1].split("&")[0].replace("%3A", ":")
    oldest = cloud.cdb.scalar("SELECT uid FROM events WHERE status='yangi' ORDER BY created_at LIMIT 1")
    ok("Navbat: qarordan keyin keyingi eng eski hodisa", ev_status(first) == "tasdiqlangan" and second == oldest
       and first != second, f"{first} → {second}")

    # Filiallar: yaratish — token bir marta
    page.goto(f"{BASE}/branches/")
    page.wait_for_selector("[data-testid=branch]", timeout=8000)
    page.click("text=Filial qo'shish")
    page.fill("input[name=name]", "Sergeli do'koni")
    page.fill("input[name=device_id]", "dokon-sergeli")
    page.click("button:has-text('Filialni yaratish')")
    page.wait_for_selector("[data-testid=token-box]", timeout=8000)
    tok = page.inner_text("[data-testid=token]").split("=", 1)[1].strip()
    ok("Yangi filial: token ko'rsatildi", len(tok) >= 32)
    ok("Token bilan edge ulanadi", edge("dokon-sergeli", tok, "hello", json={"time": time.time()}).status_code == 200)
    shot(page, "06_branch_token")
    page.reload()
    page.wait_for_selector("[data-testid=branch] >> nth=2", timeout=8000)
    ok("Sahifa yangilangach token qayta ko'rinmaydi", page.locator("[data-testid=token-box]").count() == 0
       and tok not in page.content())
    card = page.locator("[data-testid=branch]").filter(has_text="Sergeli")
    card.locator("text=Tokenni yangilash").click()
    card.locator("text=Ha, tokenni yangilash").click()
    page.wait_for_selector("[data-testid=token-box]", timeout=8000)
    tok2 = page.inner_text("[data-testid=token]").split("=", 1)[1].strip()
    ok("Token yangilandi: eskisi 401, yangisi 200", tok2 != tok
       and edge("dokon-sergeli", tok, "hello", json={"time": time.time()}).status_code == 401
       and edge("dokon-sergeli", tok2, "hello", json={"time": time.time()}).status_code == 200)
    page.click("text=Saqladim, yopish")
    card_a = page.locator("[data-testid=branch]").filter(has_text="Chilonzor")
    card_a.locator("text=Uzilishlar tarixi").click()
    page.wait_for_selector("text=kadr kelmayapti", timeout=8000)
    ok("Kamera uzilishlari tarixi", card_a.locator("text=Tiklandi").count() == 1)

    # Ko'rsatkichlar
    page.goto(f"{BASE}/metrics/?branch={s['a_id']}")
    page.wait_for_selector(".chart svg path", timeout=10000)
    alerts = int(page.locator(".kpi b").first.inner_text())
    db_alerts = sum(int((cloud.cdb.json_load(r["data"]) or {}).get("alerts") or 0) for r in cloud.cdb.rows(
        "SELECT data FROM metrics_minute WHERE branch_id=:b AND source_id > 0 AND minute >= :t",
        {"b": s["a_id"], "t": time.time() - 86400}))
    ok("Ko'rsatkichlar: 6 grafik (1.9.0: tashriflar), signallar soni bazaga mos", page.locator(".chart").count() == 6
       and page.locator(".chart svg rect").count() > 0 and alerts == db_alerts, f"{alerts} / {db_alerts}")
    page.locator(".chart svg").first.hover()
    ok("Grafik ustida qiymat ko'rinadi", ":" in page.locator(".chart .legend").first.inner_text())
    shot(page, "07_metrics")

    # Tashriflar (1.9.0)
    page.goto(f"{BASE}/visits/")
    page.wait_for_selector("[data-testid=visits-inside]", timeout=10000)
    page.wait_for_selector(".chart svg rect", state="attached", timeout=10000)   # ma'lumotsiz kun — balandligi 0
    d0 = cloud._local_day0()
    db_in = sum(int((cloud.cdb.json_load(r["data"]) or {}).get("in") or 0) for r in cloud.cdb.rows(
        "SELECT data FROM metrics_minute WHERE branch_id=:b AND source_id > 0 AND minute >= :t",
        {"b": s["a_id"], "t": d0}))
    today_in = int(page.locator(".kpi b").nth(1).inner_text())
    ok("Tashriflar: bugun kirdi bazaga mos, 2 grafik, filial tanlovi", today_in == db_in and db_in > 0
       and page.locator(".chart").count() == 2 and page.locator("select.select").count() == 1, f"{today_in} / {db_in}")
    page.select_option("select.select", label="Yunusobod do'koni")
    page.wait_for_selector("text=Hali sanash ma'lumoti yo'q", timeout=8000)
    page.select_option("select.select", label="Chilonzor do'koni")
    page.wait_for_selector("[data-testid=visits-inside]", timeout=8000)

    def kpi_in() -> int | None:
        try:
            return int(page.locator(".kpi b").nth(1).inner_text(timeout=1000))
        except Exception:  # noqa: BLE001 — so'rov hali kelmagan (yuklanmoqda / eski filial)
            return None
    # 2.6.0: filial almashgach so'rov javobi kelguncha kutiladi (yuklangan serverda darhol o'qish yiqilardi)
    wait(lambda: kpi_in() == db_in, 8)
    ok("Filial tanlovi: sanashsiz filial — bo'sh holat, Chilonzor — o'sha son", kpi_in() == db_in, f"{kpi_in()} / {db_in}")
    shot(page, "07b_visits")

    # Navbat (2.0.0)
    page.goto(f"{BASE}/queues/")
    page.wait_for_selector("[data-testid=queue-now]", timeout=10000)
    page.wait_for_selector(".chart svg rect", state="attached", timeout=10000)
    served_db = sum(int(r["served"] or 0) for r in cloud.cdb.rows(
        "SELECT served FROM queue_minute WHERE branch_id=:b AND minute >= :t", {"b": s["a_id"], "t": cloud._local_day0()}))
    cards = page.locator("[data-testid=queue-card]")
    ok("Navbat: kassalar hozir (2 karta), jami 7 kishi, kassir yo'q 1, bugun o'tdi bazaga mos",
       cards.count() == 2 and page.locator(".kpi b").nth(0).inner_text() == "7"
       and page.locator(".kpi b").nth(2).inner_text() == "1" and int(page.locator(".kpi b").nth(3).inner_text()) == served_db,
       f"{cards.count()} karta, o'tdi {page.locator('.kpi b').nth(3).inner_text()} / {served_db}")
    ok("Navbat: signallar ro'yxati (matn oddiy, teg ko'rinmaydi)", page.locator(".qalerts li").count() == 2
       and "<b>" not in page.locator(".qalerts").inner_text() and "5 kishi" in page.locator(".qalerts").inner_text())
    shot(page, "07c_queues")

    # Oqim (2.1.0)
    page.goto(f"{BASE}/flow/")
    page.wait_for_selector("[data-testid=flow-total]", timeout=10000)
    page.wait_for_timeout(500)
    blank = page.evaluate("""() => { const c = document.querySelector('[data-testid=flow-canvas]');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
        for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] < 120 && d[i + 2] < 120) n++; return n; }""")
    ok("Oqim: jami 13 tashrif, eng ko'p 'Sut', yo'nalish Sut → Non, heatmap chizildi",
       page.locator("[data-testid=flow-total] b").inner_text() == "13" and "Sut → Non" in page.inner_text("body")
       and blank > 20, f"qizil piksel {blank}")
    shot(page, "07d_flow")

    # Xodimlar
    page.goto(f"{BASE}/users/")
    page.wait_for_selector("[data-testid=user-row]", timeout=8000)
    page.click("text=Xodim qo'shish")
    page.fill("form input[name=name]", "Jasur")
    page.fill("form input[name=email]", "jasur@dokon.uz")
    page.fill("form input[name=password]", "Qorovul2026")
    page.locator("form label").filter(has_text="Chilonzor").locator("input").check()
    page.click('button:has-text("Xodimni qo\'shish")')
    page.wait_for_selector("text=jasur@dokon.uz", timeout=8000)
    u = cloud.cdb.row("SELECT * FROM users WHERE email='jasur@dokon.uz'")
    ok("Xodim qo'shildi (qo'riqchi, Chilonzor)", u is not None and u["role"] == "guard"
       and cloud.cdb.branch_ids_for(u) == [s["a_id"]])
    shot(page, "08_users")

    # 1.8.0: xodimni o'zgartirish (rol, filiallar), parol berish, jurnal
    row = page.locator("[data-testid=user-row]").filter(has_text="jasur@dokon.uz")
    row.locator("button:has-text(\"O'zgartirish\")").click()
    ed = page.locator("form.user-edit")
    ed.locator("select[name=role]").select_option("manager")
    ed.locator("label").filter(has_text="Yunusobod").locator("input").check()
    ed.locator("button:has-text('Saqlash')").click()

    def jasur():
        return cloud.cdb.row("SELECT * FROM users WHERE email='jasur@dokon.uz'")
    ok("Xodim o'zgartirildi: menejer, ikki filial", wait(lambda: jasur()["role"] == "manager"
       and cloud.cdb.branch_ids_for(jasur()) == sorted([s["a_id"], s["b_id"]]), 8))
    row.locator("button:has-text(\"O'zgartirish\")").click()
    ed.locator("input[name=new-password]").fill("12345678")
    ed.locator("button:has-text(\"Parolni o'rnatish\")").click()
    ok("Oddiy parol rad etiladi (12345678)", page.wait_for_selector("text=juda oddiy", timeout=8000) is not None)
    con.take()   # kutilgan 400
    ed.locator("input[name=new-password]").fill("Jasur-Yangi-2026")
    ed.locator("button:has-text(\"Parolni o'rnatish\")").click()
    page.wait_for_selector("text=Yangi parol o'rnatildi", timeout=8000)
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "jasur@dokon.uz", "password": "Jasur-Yangi-2026"})
    ok("Egasi bergan yangi parol bilan kirish mumkin", r.status_code == 200, str(r.status_code))
    page.reload()
    page.wait_for_selector("[data-testid=audit] tbody tr", timeout=8000)
    txt = page.inner_text("[data-testid=audit]")
    ok("Jurnal: xodim qo'shildi / o'zgartirildi / parol tiklandi / kirish", all(
        x in txt for x in ("Xodim qo'shildi", "Xodim o'zgartirildi", "Parol tiklandi", "Kirdi")), txt[:200])
    page.select_option("select[aria-label='Amal turi']", "password_reset")
    page.wait_for_timeout(500)
    rows_ = page.locator("[data-testid=audit] tbody tr").all_inner_texts()
    ok("Jurnal filtri: faqat tanlangan amal", rows_ and all("Parol tiklandi" in t for t in rows_), str(rows_)[:200])
    shot(page, "08b_users_audit")

    # Sozlamalar: Telegram ulash, afzalliklar, sinov xabari, service worker
    page.goto(f"{BASE}/settings/")
    page.wait_for_selector("text=Qayerga keladi", timeout=8000)
    page.click("text=Telegram'ni ulash")
    page.wait_for_selector("[data-testid=tg-link]", timeout=8000)
    href = page.locator("[data-testid=tg-link] a").get_attribute("href") or ""
    m = re.search(r"start=([A-Za-z0-9_\-]+)", href)
    ok("Telegram havolasi (t.me/<bot>?start=kod)", href.startswith("https://t.me/hushyor_test_bot?start=") and m)
    tg.text_message(5551, f"/start {m.group(1) if m else ''}", first_name="Egasi")
    page.wait_for_selector("[data-testid=target-telegram]", timeout=15000)
    ok("Botda /start → panelda o'zi paydo bo'ldi", page.locator("[data-testid=tg-link]").count() == 0)
    page.locator("[data-testid=pref-camera]").uncheck()
    owner = cloud.cdb.row("SELECT * FROM users WHERE email='egasi@dokon.uz'")
    ok("Afzallik saqlandi (kamera xabari o'chdi)", wait(lambda: notify.get_prefs(owner)["camera"] is False, 5))
    page.click("text=Sinov xabarini yuborish")
    page.wait_for_selector(".toast.ok", timeout=8000)
    ok("Sinov xabari: toast va Telegram'ga yetdi", "1 ta manzil" in page.inner_text(".toast.ok")
       and wait(lambda: any("Sinov" in str(c["data"].get("text", "")) for c in tg.calls("sendMessage")
                            if str(c["data"].get("chat_id")) == "5551"), 8))
    srv = page.wait_for_selector("[data-testid=server-status]", timeout=8000)
    ok("Egasida server holati: versiya, baza, disk, zaxira", srv is not None and cloud.VERSION in srv.inner_text()
       and "SQLite" in srv.inner_text() and "GB" in srv.inner_text(), srv.inner_text()[:160] if srv else "")
    scope = page.evaluate("navigator.serviceWorker.register('/sw.js', {scope: '/'}).then(r => r.scope)")
    ok("Service worker ro'yxatdan o'tdi (push uchun)", scope == f"{BASE}/", scope)
    shot(page, "09_settings")

    errs = con.take()
    ok("Brauzer konsolida xato yo'q (egasi)", not errs, "; ".join(errs)[:400])

    # Chiqish
    page.click("button:has-text('Chiqish')")
    page.wait_for_url(re.compile(r"/login/"), timeout=8000)
    ok("Chiqish → kirish sahifasi, sessiya yopildi", page.request.get(f"{BASE}/api/v1/me").status == 401)
    ctx.close()


def _guard(browser, s: dict) -> None:
    print("\n--- Qo'riqchi (bitta filial) ---")
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    con = Console(page)
    login(page, "qorovul@dokon.uz")
    page.wait_for_selector(".wall", timeout=8000)
    page.wait_for_selector(".tile", timeout=8000)
    ok("Faqat o'z filiali: 2 hodisa, 1 filial", page.locator(".tile").count() == 2 and page.locator(".plate").count() == 1)
    ok("Menyuda 'Xodimlar' yo'q", page.locator(".rail-nav >> text=Xodimlar").count() == 0)
    page.goto(f"{BASE}/users/")
    page.wait_for_selector("text=faqat kompaniya egasi uchun", timeout=8000)
    ok("Xodimlar sahifasi yopiq", True)
    page.goto(f"{BASE}/events/?uid={A_DEV}%3A1")
    page.wait_for_selector("text=Hodisa ochilmadi", timeout=8000)
    ok("Boshqa filial hodisasi ochilmaydi", page.locator("[data-testid=confirm]").count() == 0)
    page.goto(f"{BASE}/branches/")
    page.wait_for_selector("[data-testid=branch]", timeout=8000)
    ok("Filiallar: qo'shish va token tugmalari yo'q", page.locator("text=Filial qo'shish").count() == 0
       and page.locator("text=Tokenni yangilash").count() == 0)
    page.goto(f"{BASE}/settings/")
    page.wait_for_selector("text=Nima haqida xabar beriladi", timeout=8000)
    ok("Qo'riqchida yetkazish statistikasi yo'q", page.locator("text=Yetkazish, oxirgi 24 soat").count() == 0)
    errs = [e for e in con.take() if "404" not in e]   # boshqa filial hodisasi — kutilgan 404
    ok("Brauzer konsolida xato yo'q (qo'riqchi)", not errs, "; ".join(errs)[:300])
    ctx.close()


def _account(browser, s: dict) -> None:
    """1.8.0: xodim o'z parolini almashtiradi; boshqa qurilmadagi kirish yopiladi; hamma qurilmalardan chiqish."""
    print("\n--- Kirish xavfsizligi (xodim) ---")
    other = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "jasur@dokon.uz", "password": "Jasur-Yangi-2026"})
    other_tok = other.json().get("token", "")
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    con = Console(page)
    login(page, "jasur@dokon.uz", "Jasur-Yangi-2026")
    page.goto(f"{BASE}/settings/")
    page.wait_for_selector("form[aria-label='Parolni almashtirish']", timeout=8000)
    ok("Menejerda server holati paneli yo'q", page.locator("[data-testid=server-status]").count() == 0)
    f = page.locator("form[aria-label='Parolni almashtirish']")
    f.locator("input[name=current]").fill("notogri-parol")
    f.locator("input[name=new]").fill("Jasur-Boshqa-2027")
    f.locator("input[name=again]").fill("Jasur-Boshqa-2027")
    f.locator("button[type=submit]").click()
    ok("Joriy parol xato — rad etildi", page.wait_for_selector("text=joriy parol noto'g'ri", timeout=8000) is not None)
    con.take()
    f.locator("input[name=current]").fill("Jasur-Yangi-2026")
    f.locator("button[type=submit]").click()
    page.wait_for_selector("text=Parol almashtirildi", timeout=8000)
    ok("Parol almashtirildi; shu brauzer kirishda qoldi", page.request.get(f"{BASE}/api/v1/me").status == 200)
    ok("Boshqa qurilmadagi kirish yopildi", httpx.get(f"{BASE}/api/v1/me", headers={
        "Authorization": f"Bearer {other_tok}"}).status_code == 401)
    ok("Eski parol endi ishlamaydi, yangisi ishlaydi", httpx.post(f"{BASE}/api/v1/auth/login", json={
        "email": "jasur@dokon.uz", "password": "Jasur-Yangi-2026"}).status_code == 401 and httpx.post(
        f"{BASE}/api/v1/auth/login", json={"email": "jasur@dokon.uz", "password": "Jasur-Boshqa-2027"}).status_code == 200)
    page.click("text=Hamma qurilmalardan chiqish")
    page.click("text=Ha, hamma joydan chiqish")
    page.wait_for_url(re.compile(r"/login/"), timeout=8000)
    n = cloud.cdb.scalar("SELECT COUNT(*) FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.email='jasur@dokon.uz'")
    ok("Hamma qurilmalardan chiqish — sessiyalar 0", n == 0, str(n))
    errs = con.take()
    ok("Brauzer konsolida xato yo'q (xodim parol)", not errs, "; ".join(errs)[:300])
    ctx.close()


def _mobile(browser, s: dict) -> None:
    print("\n--- Telefon ekrani (390×844) ---")
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                  has_touch=True, color_scheme=scheme)
        page = ctx.new_page()
        con = Console(page)
        login(page, "menejer@dokon.uz")
        page.wait_for_selector(".tile", timeout=8000)
        bad = []
        for path in ("/", "/events/", f"/events/?uid={A_DEV}%3A2", "/branches/", f"/metrics/?branch={s['a_id']}", "/visits/",
                     "/settings/"):
            page.goto(BASE + path)
            page.wait_for_selector(".page-head", timeout=8000)
            page.wait_for_timeout(250)
            sw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            if sw[0] > sw[1] + 1:
                bad.append(f"{path}: {sw[0]}>{sw[1]}")
            if scheme == "light" and path in ("/", f"/events/?uid={A_DEV}%3A2"):
                shot(page, "10_mobile_" + ("dashboard" if path == "/" else "event"))
        ok(f"Telefonda gorizontal aylantirish yo'q ({scheme})", not bad, ", ".join(bad))
        if scheme == "light":
            box = page.locator(".rail").bounding_box()
            ok("Telefonda menyu pastda", box is not None and box["y"] > 700, str(box))
        else:
            page.goto(f"{BASE}/")
            page.wait_for_selector(".tile", timeout=8000)
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            ok("Qorong'i rejim (prefers-color-scheme)", bg == "rgb(13, 22, 32)", bg)
            shot(page, "11_mobile_dark")
        errs = con.take()
        ok(f"Brauzer konsolida xato yo'q (telefon, {scheme})", not errs, "; ".join(errs)[:300])
        ctx.close()


def headers_check() -> None:
    print("\n--- Sarlavhalar va statik fayllar ---")
    r = httpx.get(f"{BASE}/")
    csp = r.headers.get("content-security-policy", "")
    ok("Panel sahifasi: CSP, X-Frame-Options", r.status_code == 200 and "default-src 'self'" in csp
       and "frame-ancestors 'none'" in csp and r.headers.get("x-frame-options") == "DENY")
    ok("HTML keshlanmaydi", r.headers.get("cache-control") == "no-cache")
    script_src = re.search(r"script-src([^;]*)", csp)
    ok("CSP: script-src da 'unsafe-inline' yo'q, ichki skriptlar sha256 bilan", script_src is not None
       and "'unsafe-inline'" not in script_src.group(1) and "'sha256-" in script_src.group(1), csp[:160])
    ok("Permissions-Policy (kamera/mikrofon/joylashuv o'chiq), COOP", "camera=()" in r.headers.get("permissions-policy", "")
       and r.headers.get("cross-origin-opener-policy") == "same-origin")
    r2 = httpx.get(f"{BASE}/api/v1/auth/status")
    ok("API javoblari keshlanmaydi (no-store)", r2.headers.get("cache-control") == "no-store")
    ok("OpenAPI/docs yopiq", all(httpx.get(f"{BASE}{p}").status_code == 404 for p in ("/docs", "/openapi.json", "/redoc")))
    r = httpx.get(f"{BASE}/sw.js")
    ok("sw.js: Service-Worker-Allowed, no-cache", r.status_code == 200 and r.headers.get("service-worker-allowed") == "/"
       and r.headers.get("cache-control") == "no-cache")
    r = httpx.get(f"{BASE}/yoq-sahifa/")
    ok("Noma'lum sahifa → 404 (panel sahifasi)", r.status_code == 404 and "Sahifa topilmadi" in r.text)
    r = httpx.get(f"{BASE}/api/v1/auth/status")
    ok("auth/status sessiyasiz — 200, authenticated=false", r.status_code == 200 and r.json()["authenticated"] is False)
    r = httpx.get(f"{BASE}/api/v1/me")
    ok("API sessiyasiz — 401 (o'zgarmagan)", r.status_code == 401)


def source_hash() -> str:
    """cloud/panel/scripts/copy-dist.mjs dagi sourceHash() bilan bir xil (CRLF hisobga olinmaydi)."""
    import hashlib

    root = ROOT / "cloud" / "panel"
    files = [p for d in ("app", "components", "lib", "public") for p in (root / d).rglob("*") if p.is_file()]
    files += [root / f for f in ("package.json", "package-lock.json", "next.config.mjs", "tsconfig.json")]
    h = hashlib.sha256()
    for rel, f in sorted((f.relative_to(root).as_posix(), f) for f in files):
        body = f.read_bytes().decode("latin-1").replace("\r", "")
        h.update((rel + "\0" + body + "\0").encode("latin-1"))
    return h.hexdigest()


def build_check() -> None:
    import json

    print("\n--- Panel build ---")
    try:
        info = json.loads((cloud.PANEL_DIR / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    same = info.get("source_sha256") == source_hash()
    ok("cloud/panel_dist manbaga mos (build eskirmagan)", same,
       "" if same else ("manba o'zgargan — cd cloud/panel && npm run build" if info else "build.json yo'q"))
    ok(f"Panel versiyasi = server versiyasi ({cloud.VERSION})", info.get("version") == cloud.VERSION,
       str(info.get("version")))


def main() -> int:
    if not (cloud.PANEL_DIR / "index.html").is_file():
        print("FAIL: cloud/panel_dist topilmadi — avval: cd cloud/panel && npm ci && npm run build")
        return 1
    build_check()
    from playwright.sync_api import sync_playwright

    cloud.init_db()
    notify.cfg = notify.Config(tg_token="777:CLOUD", tg_api=f"http://127.0.0.1:{TG_PORT}", tz_offset=5)
    cloud._state.update(tokens=[], admin=None, notify_configured=True, no_notify=False)
    srv = uvicorn.Server(uvicorn.Config(cloud.app, host="127.0.0.1", port=CLOUD_PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    wait(lambda: notify.state["tg_bot"] is not None, 10)
    t0 = time.time()
    s = seed()
    print(f"Ma'lumot tayyor: {time.time() - t0:.1f} s")
    headers_check()
    with sync_playwright() as pw:
        suite(pw, s)
    srv.should_exit = True
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
