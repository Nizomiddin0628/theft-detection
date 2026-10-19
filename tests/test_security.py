"""Xavfsizlik sinovi (1.8.0, 2-bosqich yakuni) — bulut serveri OWASP Top 10 bo'yicha.

A01 ruxsat: har boshqaruv endpointi sessiyasiz 401; boshqa kompaniya/filial resurslari (IDOR) — 403/404; rollar.
A02 kriptografiya: sessiya va qurilma tokeni bazada hash; cookie HttpOnly/SameSite=Strict, HTTPS'da Secure; HSTS.
A03 in'ektsiya: SQL (parametrlar), yo'l (klip nomi), XSS (Telegram/email matni escape), sarlavha (email Subject).
A04 dizayn: login bloki (IP / email / email+IP), parol siyosati, parolni almashtirish → boshqa sessiyalar yopiladi.
A05 sozlama: xavfsizlik sarlavhalari, CSP 'unsafe-inline'siz, docs/openapi yopiq, xato tafsiloti chiqmaydi.
A07 autentifikatsiya: o'chirilgan xodim, hamma qurilmalardan chiqish, CLI orqali parol tiklash.
A09 jurnal: audit (kirish, xato kirish, xodim, token, tekshirish, klip ko'rish) — faqat o'z kompaniyasi.
Qo'shimcha: event loop bloklanmaydi (sinxron baza so'rovlari threadpool'da), SSE chegarasi, kliplar muddati va disk,
zaxira nusxa, repoda maxfiy kalit yo'qligi.

Ishga tushirish:  python -m tests.test_security
                  CLOUD_TEST_PG_URL=postgresql+psycopg://postgres@127.0.0.1:5433/hushyor_test python -m tests.test_security
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_sec_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
for k in ("CLOUD_TELEGRAM_BOT_TOKEN", "CLOUD_SMTP_HOST", "CLOUD_PUBLIC_URL", "CLOUD_EDGE_TOKENS", "CLOUD_ADMIN_TOKEN"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from cloud import live, maintenance, notify  # noqa: E402
from cloud import server as cloud  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT = 9331
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


def serve() -> uvicorn.Server:
    # Ishlab chiqarishdagi kabi: proksi sarlavhalari faqat 127.0.0.1 dan (nginx bitta serverda)
    srv = uvicorn.Server(uvicorn.Config(cloud.app, host="127.0.0.1", port=PORT, log_level="error", proxy_headers=True,
                                        forwarded_allow_ips="127.0.0.1", server_header=False))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    return srv


def login(email: str, pw: str = "Parol12345", **kw) -> dict:
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": pw}, **kw)
    assert r.status_code == 200, (email, r.status_code, r.text)
    return {"Authorization": f"Bearer {r.json()['token']}"}


def edge(dev: str, tok: str, path: str, **kw) -> httpx.Response:
    return httpx.post(f"{BASE}/api/v1/edge/{path}", headers={"Authorization": f"Bearer {tok}", "X-Device-Id": dev,
                                                             **kw.pop("headers", {})}, timeout=30, **kw)


def ev(dev: str, lid: int, ago: float = 30, **kw) -> dict:
    t = time.time() - ago
    it = {"key": f"ev:{lid}", "uid": f"{dev}:{lid}", "local_id": lid, "created_at": t, "updated_at": t, "kind": "torso",
          "score": 80, "status": "yangi", "source_name": "Kassa", "reasons": ["Qo'l kiyim ichiga kirdi"]}
    it.update(kw)
    return it


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 300


def seed() -> dict:
    cdb = cloud.cdb
    a = cloud.bootstrap_owner("egasi@a.uz", "Parol12345", "A savdo")
    b = cloud.bootstrap_owner("egasi@b.uz", "Parol12345", "B savdo")
    a1, tok_a1 = cdb.create_branch(a["company_id"], "A-1", "devA1")
    a2, tok_a2 = cdb.create_branch(a["company_id"], "A-2", "devA2")
    b1, tok_b1 = cdb.create_branch(b["company_id"], "B-1", "devB1")
    cdb.create_user(a["company_id"], "menejer@a.uz", "Parol12345", "Menejer", "manager", [a1])
    cdb.create_user(a["company_id"], "qorovul@a.uz", "Parol12345", "Qorovul", "guard", [a2])
    for dev, tok in (("devA1", tok_a1), ("devA2", tok_a2), ("devB1", tok_b1)):
        assert edge(dev, tok, "hello", json={"time": time.time(), "version": "1.8.0", "cameras": []}).status_code == 200
        assert edge(dev, tok, "events", json={"items": [ev(dev, 1), ev(dev, 2, ago=50)]}).status_code == 200
        r = edge(dev, tok, f"events/{dev}:1/clip", data={"meta": "{}"},
                 files={"clip": ("c.mp4", MP4, "video/mp4"), "thumb": ("t.jpg", JPG, "image/jpeg")})
        assert r.status_code == 200, r.text
    return {"a": a, "b": b, "a1": a1, "a2": a2, "b1": b1, "tok": {"devA1": tok_a1, "devA2": tok_a2, "devB1": tok_b1}}


# ----------------------------------------------------------------------------- A01: ruxsat
# /api/v1/leads — saytdagi ochiq forma (2.6.0; himoyasi tests/test_demo.py da: honeypot, chegara, faqat JSON)
PUBLIC = {"/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/status", "/healthz", "/api/v1/leads"}


def a01_access(s: dict) -> None:
    print("\n--- A01 Ruxsatlar (sessiyasiz, IDOR, rollar) ---")
    # Har boshqaruv endpointi (marshrutlar ro'yxatidan avtomatik) sessiyasiz — 401
    bad = []
    n = 0
    for r in cloud.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/v1/") or path in PUBLIC or "/edge/" in path or "/admin/" in path:
            continue
        url = BASE + path.replace("{branch_id}", str(s["a1"])).replace("{user_id}", "1").replace(
            "{uid}", "devA1:1").replace("{target_id}", "1").replace("{device_id}", "devA1").replace("{name}", "devA1_1.mp4").replace("{tid}", "1")
        for m in sorted(getattr(r, "methods", set()) - {"HEAD"}):
            n += 1
            code = httpx.request(m, url, json={} if m in ("POST", "PUT") else None, timeout=10).status_code
            if code != 401:
                bad.append(f"{m} {path} → {code}")
    ok(f"Sessiyasiz har boshqaruv endpointi 401 ({n} ta)", not bad and n >= 30, "; ".join(bad))
    adm = httpx.get(f"{BASE}/api/v1/admin/summary")
    ok("Superadmin API tokensiz yopiq", adm.status_code in (401, 403), str(adm.status_code))

    A, B = login("egasi@a.uz"), login("egasi@b.uz")
    mgr, grd = login("menejer@a.uz"), login("qorovul@a.uz")
    a_user = cloud.cdb.row("SELECT id FROM users WHERE email='menejer@a.uz'")["id"]
    checks = [
        ("GET", "/api/v1/events/devA1:1", None, (404,)),
        ("POST", "/api/v1/events/devA1:1/review", {"status": "rad_etilgan"}, (404,)),
        ("GET", "/api/v1/clips/devA1/devA1_1.mp4", None, (403, 404)),
        ("GET", "/api/v1/clips/devA1/devA1_1.jpg", None, (403, 404)),
        ("GET", f"/api/v1/branches/{s['a1']}/cameras", None, (403,)),
        ("GET", f"/api/v1/branches/{s['a1']}/metrics", None, (403,)),
        ("POST", f"/api/v1/branches/{s['a1']}/token", {}, (403,)),
        ("GET", f"/api/v1/events?branch_id={s['a1']}", None, (403,)),
        ("POST", f"/api/v1/users/{a_user}/update", {"role": "owner"}, (404,)),
        ("POST", f"/api/v1/users/{a_user}/password", {"password": "Boshqa-Parol-1"}, (404,)),
        ("POST", f"/api/v1/users/{a_user}/activate", {}, (404,)),
    ]
    leaks = []
    for m, p, body, want in checks:
        code = httpx.request(m, BASE + p, json=body, headers=B).status_code
        if code not in want:
            leaks.append(f"{m} {p} → {code}")
    r = httpx.post(f"{BASE}/api/v1/users/{a_user}/deactivate", headers=B)
    if r.json().get("updated") != 0:
        leaks.append("deactivate boshqa kompaniya xodimini o'chirdi")
    evs = httpx.get(f"{BASE}/api/v1/events", headers=B).json()["events"]
    if any(not e["uid"].startswith("devB1:") for e in evs):
        leaks.append("events ro'yxatida boshqa kompaniya hodisasi")
    brs = httpx.get(f"{BASE}/api/v1/branches", headers=B).json()["branches"]
    if [b["device_id"] for b in brs] != ["devB1"]:
        leaks.append("branches: boshqa kompaniya filiali")
    users = httpx.get(f"{BASE}/api/v1/users", headers=B).json()["users"]
    if any(not u["email"].endswith("@b.uz") for u in users):
        leaks.append("users: boshqa kompaniya xodimi")
    ok("Boshqa kompaniya resurslari ko'rinmaydi va o'zgarmaydi (IDOR, 15 holat)", not leaks, "; ".join(leaks))
    ok("Hodisa A hali 'yangi' (B tekshira olmadi)",
       cloud.cdb.scalar("SELECT status FROM events WHERE uid='devA1:1'") == "yangi")
    ok("A egasining sessiyasi ishlaydi (taqqoslash uchun)", httpx.get(f"{BASE}/api/v1/events/devA1:1", headers=A).status_code == 200)

    # Rollar
    deny = []
    for who, hdr, reqs in (("qo'riqchi", grd, [("GET", "/api/v1/users"), ("POST", "/api/v1/users"), ("POST", "/api/v1/branches"),
                                               ("POST", f"/api/v1/branches/{s['a2']}/token"), ("GET", "/api/v1/audit"),
                                               ("GET", "/api/v1/system"), ("GET", "/api/v1/notify/stats"),
                                               ("POST", f"/api/v1/users/{a_user}/password")]),
                           ("menejer", mgr, [("GET", "/api/v1/users"), ("POST", "/api/v1/branches"), ("GET", "/api/v1/audit"),
                                             ("GET", "/api/v1/system"), ("POST", f"/api/v1/branches/{s['a1']}/token"),
                                             ("POST", f"/api/v1/users/{a_user}/update")])):
        for m, p in reqs:
            code = httpx.request(m, BASE + p, json={"name": "x", "device_id": "zzz", "password": "Yaxshi-Parol-9"}
                                 if m == "POST" else None, headers=hdr).status_code
            if code != 403:
                deny.append(f"{who} {m} {p} → {code}")
    ok("Rollar: qo'riqchi/menejer egasi amallarini bajara olmaydi (14 holat)", not deny, "; ".join(deny))
    gev = httpx.get(f"{BASE}/api/v1/events", headers=grd).json()["events"]
    ok("Qo'riqchi faqat o'z filiali hodisalarini ko'radi", gev and all(e["uid"].startswith("devA2:") for e in gev))
    ok("Qo'riqchi boshqa filial hodisasini tekshira olmaydi", httpx.post(
        f"{BASE}/api/v1/events/devA1:2/review", json={"status": "tasdiqlangan"}, headers=grd).status_code == 404)
    me_id = cloud.cdb.row("SELECT id FROM users WHERE email='egasi@a.uz'")["id"]
    r = httpx.post(f"{BASE}/api/v1/users/{me_id}/update", json={"role": "guard", "branch_ids": [s["a1"]]}, headers=A)
    ok("Egasi o'z rolini tushira olmaydi (kompaniya egasiz qolmaydi)", r.status_code == 400, r.text[:80])
    r = httpx.post(f"{BASE}/api/v1/users/{a_user}/update", json={"role": "manager", "branch_ids": [s["b1"]]}, headers=A)
    ok("Boshqa kompaniya filialini biriktirib bo'lmaydi", r.status_code == 400, r.text[:80])


# ----------------------------------------------------------------------------- A02/A05: sarlavhalar, cookie, xato
def a05_headers() -> None:
    print("\n--- A02/A05 Sarlavhalar, cookie, xato tafsiloti ---")
    r = httpx.get(f"{BASE}/login/")
    csp = r.headers.get("content-security-policy", "")
    sm = re.search(r"script-src([^;]*)", csp)
    ok("CSP: tashqi manba yo'q, skriptlarda 'unsafe-inline' yo'q", sm and "'unsafe-inline'" not in sm.group(1)
       and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "http" not in csp, csp[:120])
    import base64
    import hashlib

    def inline_ok(resp) -> list[str]:
        """Sahifadagi har ichki skript CSP'dagi sha256 ro'yxatida bormi (aks holda brauzer uni bloklaydi)."""
        c = resp.headers.get("content-security-policy", "")
        miss_ = []
        for body in re.findall(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", resp.content, re.S | re.I):
            h = "'sha256-" + base64.b64encode(hashlib.sha256(body).digest()).decode() + "'"
            if h not in c:
                miss_.append(h[:20])
        return miss_
    r404 = httpx.get(f"{BASE}/bunday-sahifa-yoq")
    ok("404 sahifasi: ichki skriptlari CSP xeshlarida (sahifa ishlaydi)", r404.status_code == 404
       and "sha256-" in r404.headers.get("content-security-policy", "") and not inline_ok(r404), str(inline_ok(r404)))
    ok("Panel sahifasi: barcha ichki skriptlar CSP xeshlarida", not inline_ok(r), str(inline_ok(r)))
    need = {"x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "same-origin",
            "cross-origin-opener-policy": "same-origin"}
    miss = [k for k, v in need.items() if r.headers.get(k) != v]
    ok("XFO, nosniff, Referrer-Policy, COOP, Permissions-Policy", not miss and "camera=()" in
       r.headers.get("permissions-policy", ""), str(miss))
    ok("HTTP'da HSTS yo'q (lokal kompyuterda brauzer qulflanib qolmasin)", "strict-transport-security" not in r.headers)
    r = httpx.get(f"{BASE}/login/", headers={"X-Forwarded-Proto": "https"})
    ok("Proksi orqali HTTPS'da HSTS bor", "max-age=" in r.headers.get("strict-transport-security", ""))
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "Parol12345", "session": "cookie"},
                   headers={"X-Forwarded-Proto": "https"})
    sc = r.headers.get("set-cookie", "")
    ok("HTTPS'da sessiya cookie: HttpOnly; Secure; SameSite=Strict; token JSON'da yo'q",
       "httponly" in sc.lower() and "secure" in sc.lower() and "samesite=strict" in sc.lower() and "token" not in r.json(), sc[:120])
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "Parol12345", "session": "cookie"})
    ok("HTTP'da (127.0.0.1) cookie Secure emas — lokal panel ishlaydi", "secure" not in r.headers.get("set-cookie", "").lower())
    tok = r.cookies.get("hc_session")
    ok("Sessiya tokeni bazada ochiq saqlanmaydi (sha256)", tok and not cloud.cdb.row(
        "SELECT 1 FROM sessions WHERE token_hash=:t", {"t": tok}) and cloud.cdb.row(
        "SELECT 1 FROM sessions WHERE token_hash=:t", {"t": cloud.cdb.sha256(tok)}))
    ok("Qurilma tokeni bazada ochiq saqlanmaydi", all(len(h) == 64 for h in [
        x["token_hash"] for x in cloud.cdb.rows("SELECT token_hash FROM branches")]))
    ok("API javobi: Cache-Control no-store", httpx.get(f"{BASE}/api/v1/auth/status").headers.get("cache-control") == "no-store")
    ok("/docs, /redoc, /openapi.json — 404", all(httpx.get(BASE + p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json")))
    # Kutilmagan xato: mijozga ichki tafsilot chiqmaydi
    orig = cloud.cdb.branch_ids_for

    def boom(*_a, **_k):
        raise RuntimeError("SECRET-SQL-DETAIL password=hunter2")
    import logging

    quiet = [logging.getLogger(n) for n in ("uvicorn.error", "hushyor.cloud")]
    levels = [lg.level for lg in quiet]
    for lg in quiet:
        lg.setLevel(logging.CRITICAL)       # kutilgan xato izi sinov chiqishini to'ldirmasin
    cloud.cdb.branch_ids_for = boom
    try:
        r = httpx.get(f"{BASE}/api/v1/me", headers=login("egasi@a.uz"))
    finally:
        cloud.cdb.branch_ids_for = orig
        for lg, lv in zip(quiet, levels):
            lg.setLevel(lv)
    ok("500 xatoda tafsilot (SQL, parol, stack) mijozga chiqmaydi", r.status_code == 500 and "SECRET" not in r.text
       and "hunter2" not in r.text and "Traceback" not in r.text, r.text[:100])


# ----------------------------------------------------------------------------- A03: in'ektsiya
def a03_injection(s: dict) -> None:
    print("\n--- A03 In'ektsiya (SQL, yo'l, XSS, sarlavha) ---")
    A = login("egasi@a.uz")
    n0 = cloud.cdb.scalar("SELECT COUNT(*) FROM events")
    probes = [("/api/v1/events?status=yangi' OR '1'='1", (400,)), ("/api/v1/events?branch_id=1 OR 1=1", (422,)),
              ("/api/v1/events?since=1;DROP TABLE events", (422,)), ("/api/v1/events?limit=1;--", (422,)),
              ("/api/v1/audit?action=login_ok' OR '1'='1", (400,)),
              (f"/api/v1/branches/{s['a1']}/metrics?hours=1e309", (200, 422))]
    bad = [f"{p} → {c}" for p, want in probes if (c := httpx.get(BASE + p, headers=A).status_code) not in want]
    ok("SQL in'ektsiya urinishlari rad etiladi, baza buzilmaydi", not bad and cloud.cdb.scalar(
        "SELECT COUNT(*) FROM events") == n0, "; ".join(bad))
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "' OR '1'='1' --", "password": "' OR '1'='1"})
    ok("Login'da SQL in'ektsiya — 401", r.status_code == 401)
    cloud._login_fails.clear()
    trav = []
    for p in ("/api/v1/clips/devA1/..%2F..%2Fcloud.db", "/api/v1/clips/devA1/....%2Fcloud.db", "/api/v1/clips/..%2F/x.mp4",
              "/api/v1/clips/devA1/devA1_1.mp4%00.jpg", "/api/v1/clips/devA1/vapid_private.pem",
              "/api/v1/clips/devA1/devA1_1.MP4"):
        c = httpx.get(BASE + p, headers=A).status_code
        if c not in (400, 404, 422):
            trav.append(f"{p} → {c}")
    ok("Klip yo'lida katalogdan chiqish (path traversal) — rad", not trav, "; ".join(trav))
    # XSS: edge'dan kelgan matn Telegram/email'da escape qilinadi
    p = notify._event_payload({"uid": "devA1:9", "data": {"reasons": ["<script>alert(1)</script>"]},
                               "source_name": "<img src=x onerror=alert(1)>", "kind": "torso", "score": 80},
                              {"id": 1, "name": "<b>Filial</b>"})
    _, txt, _ = notify.render("event", p)
    ok("Telegram matnida HTML escape (<script>, <img onerror>)", "<script>" not in txt and "<img" not in txt
       and "&lt;script&gt;" in txt)
    title, _, _ = notify.render("event", {**p, "branch": "Do'kon\r\nBcc: hujum@evil.uz"})
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = " ".join(f"Hushyor AI: {title}".split())[:200]
    ok("Email Subject'ga sarlavha in'ektsiyasi (CR/LF) o'tmaydi", "\n" not in str(msg["Subject"]) and "Bcc" not in str(msg.keys()))
    # Rasm o'rnida boshqa narsa (HTML) saqlanmaydi
    dev, tok = "devA1", s["tok"]["devA1"]
    edge(dev, tok, "events", json={"items": [ev(dev, 3)]})
    r = edge(dev, tok, f"events/{dev}:3/clip", data={"meta": "{}"}, files={
        "clip": ("c.mp4", MP4, "video/mp4"), "thumb": ("t.jpg", b"<html><script>alert(1)</script>", "image/jpeg")})
    ok("JPEG bo'lmagan 'rasm' saqlanmaydi", r.status_code == 200 and cloud.cdb.scalar(
        "SELECT thumb_file FROM events WHERE uid='devA1:3'") is None)


# ----------------------------------------------------------------------------- A04/A07: login, parol, sessiyalar
def a07_auth(s: dict) -> None:
    print("\n--- A04/A07 Login bloki, parol siyosati, sessiyalar ---")
    cloud._login_fails.clear()
    for _ in range(10):
        httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "xato"})
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "Parol12345"})
    ok("10 xato (bitta IP + email) → 429, to'g'ri parol ham kutadi", r.status_code == 429)
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@b.uz", "password": "Parol12345"})
    ok("Shu IP'dan boshqa akkaunt hali kira oladi (ofis NAT)", r.status_code == 200)
    cloud._login_fails.clear()
    # Taqsimlangan tavakkal: bitta email, ko'p IP (X-Forwarded-For — ishonchli proksi orqali)
    for i in range(30):
        httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@b.uz", "password": "xato"},
                   headers={"X-Forwarded-For": f"203.0.113.{i + 1}"})
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@b.uz", "password": "Parol12345"},
                   headers={"X-Forwarded-For": "198.51.100.7"})
    ok("30 xato (bitta email, 30 IP) → email bloklanadi", r.status_code == 429)
    ip_rows = {x["ip"] for x in cloud.cdb.rows("SELECT ip FROM audit_log WHERE action='login_fail' AND actor='egasi@b.uz'")}
    ok("Proksi orqasida haqiqiy IP jurnalga yoziladi (X-Forwarded-For)", "203.0.113.5" in ip_rows, str(sorted(ip_rows))[:80])
    cloud._login_fails.clear()
    # Bir vaqtda 40 ta xato urinish (chegara tekshiruvi va hisoblash orasidagi poyga): parol 10 martadan ko'p tekshirilmaydi
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(40) as ex:
        codes = list(ex.map(lambda _i: httpx.post(f"{BASE}/api/v1/auth/login", timeout=30,
                                                   json={"email": "egasi@a.uz", "password": "xato"}).status_code, range(40)))
    ok("Parallel 40 xato login: ≤ 10 tasi tekshiriladi (401), qolgani 429", codes.count(401) <= 10
       and codes.count(429) >= 30, f"401={codes.count(401)} 429={codes.count(429)}")
    cloud._login_fails.clear()

    A = login("egasi@a.uz")
    weak = []
    for pw in ("1234567", "12345678", "password", "qwerty123", "aaaaaaaa", "kassir@a.uz", "Admin123"):
        r = httpx.post(f"{BASE}/api/v1/users", json={"email": "kassir@a.uz", "password": pw, "role": "guard",
                                                      "branch_ids": [s["a1"]]}, headers=A)
        if r.status_code != 400:
            weak.append(pw)
    ok("Parol siyosati: qisqa, keng tarqalgan, bir xil belgili, email bilan bir xil — rad", not weak, str(weak))
    r = httpx.post(f"{BASE}/api/v1/users", json={"email": "kassir@a.uz", "password": "Kassir-Yaxshi-7", "role": "guard",
                                                  "branch_ids": [s["a1"]], "name": "Kassir"}, headers=A)
    kid = r.json()["user"]["id"]
    K1, K2 = login("kassir@a.uz", "Kassir-Yaxshi-7"), login("kassir@a.uz", "Kassir-Yaxshi-7")
    r = httpx.post(f"{BASE}/api/v1/me/password", json={"current": "xato", "new": "Kassir-Yangi-8"}, headers=K1)
    ok("Parol almashtirish: joriy parol xato — 400", r.status_code == 400)
    for _ in range(5):
        httpx.post(f"{BASE}/api/v1/me/password", json={"current": "xato", "new": "Kassir-Yangi-8"}, headers=K1)
    r = httpx.post(f"{BASE}/api/v1/me/password", json={"current": "Kassir-Yaxshi-7", "new": "Kassir-Yangi-8"}, headers=K1)
    ok("Joriy parolni tavakkal qilish bloklanadi (5 xato → 429)", r.status_code == 429)
    cloud._login_fails.clear()
    r = httpx.post(f"{BASE}/api/v1/me/password", json={"current": "Kassir-Yaxshi-7", "new": "Kassir-Yangi-8"}, headers=K1)
    ok("Parol almashtirildi; joriy sessiya qoldi, boshqasi yopildi", r.status_code == 200
       and httpx.get(f"{BASE}/api/v1/me", headers=K1).status_code == 200
       and httpx.get(f"{BASE}/api/v1/me", headers=K2).status_code == 401, r.text[:80])
    r = httpx.post(f"{BASE}/api/v1/users/{kid}/password", json={"password": "Egasi-Bergan-9"}, headers=A)
    ok("Egasi xodimga parol beradi → xodimning hamma sessiyalari yopiladi", r.status_code == 200
       and httpx.get(f"{BASE}/api/v1/me", headers=K1).status_code == 401)
    K3 = login("kassir@a.uz", "Egasi-Bergan-9")
    r = httpx.post(f"{BASE}/api/v1/auth/logout-all", headers=K3)
    ok("Hamma qurilmalardan chiqish", r.status_code == 200 and httpx.get(f"{BASE}/api/v1/me", headers=K3).status_code == 401)
    K4 = login("kassir@a.uz", "Egasi-Bergan-9")
    httpx.post(f"{BASE}/api/v1/users/{kid}/deactivate", headers=A)
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "kassir@a.uz", "password": "Egasi-Bergan-9"})
    ok("O'chirilgan xodim: sessiyasi yopildi, kira olmaydi", httpx.get(f"{BASE}/api/v1/me", headers=K4).status_code == 401
       and r.status_code == 401)
    cloud._login_fails.clear()
    httpx.post(f"{BASE}/api/v1/users/{kid}/activate", headers=A)
    ok("Qaytarilgan xodim yana kira oladi", httpx.post(f"{BASE}/api/v1/auth/login", json={
        "email": "kassir@a.uz", "password": "Egasi-Bergan-9"}).status_code == 200)
    # CSRF (cookie sessiya): boshqa saytdan POST
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "Parol12345", "session": "cookie"})
    ck = {"Cookie": f"hc_session={r.cookies.get('hc_session')}"}
    c1 = httpx.post(f"{BASE}/api/v1/me/password", json={"current": "Parol12345", "new": "Hujum-Parol-1"},
                    headers={**ck, "Origin": "https://evil.example"}).status_code
    c2 = httpx.post(f"{BASE}/api/v1/users/{kid}/deactivate", headers={**ck, "Sec-Fetch-Site": "cross-site"}).status_code
    c3 = httpx.post(f"{BASE}/api/v1/auth/logout-all", headers={**ck, "Origin": "null"}).status_code
    c4 = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "egasi@a.uz", "password": "Parol12345"},
                    headers={"Sec-Fetch-Site": "cross-site"}).status_code
    ok("CSRF: boshqa saytdan parol/xodim/chiqish/login — 403", (c1, c2, c3, c4) == (403, 403, 403, 403), str((c1, c2, c3, c4)))
    ok("CSRF urinishi parolni o'zgartirmadi", httpx.post(f"{BASE}/api/v1/auth/login", json={
        "email": "egasi@a.uz", "password": "Parol12345"}).status_code == 200)
    # Buyruq qatori: egasi parolni unutsa (server kompyuterida)
    env = {**os.environ, "HUSHYOR_NEW_PASSWORD": "Tiklangan-Parol-5", "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "cloud.server", "--set-password", "egasi@b.uz"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    ok("CLI: --set-password (parolni unutgan egasi)", "almashtirildi" in out.stdout and httpx.post(
        f"{BASE}/api/v1/auth/login", json={"email": "egasi@b.uz", "password": "Tiklangan-Parol-5"}).status_code == 200,
       (out.stdout + out.stderr)[-200:])


# ----------------------------------------------------------------------------- edge kirishi
def edge_auth(s: dict) -> None:
    print("\n--- Edge (do'kon) kirishi ---")
    t = s["tok"]
    res = {
        "boshqa filial tokeni": edge("devA1", t["devB1"], "hello", json={}).status_code,
        "tokensiz": httpx.post(f"{BASE}/api/v1/edge/hello", headers={"X-Device-Id": "devA1"}, json={}).status_code,
        "noto'g'ri device_id": edge("../x", t["devA1"], "hello", json={}).status_code,
    }
    ok("Edge: begona token / tokensiz — 401, noto'g'ri qurilma — 400", res == {
        "boshqa filial tokeni": 401, "tokensiz": 401, "noto'g'ri device_id": 400}, str(res))
    r = edge("devA1", t["devA1"], "events", json={"items": [ev("devB1", 9)]})
    ok("Edge boshqa qurilma nomidan hodisa yoza olmaydi", r.status_code == 200 and r.json()["rejected"]
       and cloud.cdb.row("SELECT 1 FROM events WHERE uid='devB1:9'") is None)
    import socket

    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as sk:
        sk.sendall((f"POST /api/v1/edge/events HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {t['devA1']}\r\n"
                    f"X-Device-Id: devA1\r\nContent-Type: application/json\r\nContent-Length: {9 * 1024 * 1024}\r\n\r\n"
                    "{").encode())
        head = sk.recv(200).decode(errors="ignore")
    ok("Katta tana (e'lon qilingan 9 MB JSON) — 413, tana kutilmaydi", head.startswith("HTTP/1.1 413"), head.split("\r\n")[0])

    def chunks():
        yield b'{"items": []}'
    ch = edge("devA1", t["devA1"], "events", content=chunks(), headers={"Content-Type": "application/json"})
    ok("Chunked tana — 411", ch.status_code == 411)
    ok("Edge yo'lida panel cookie'si ishlamaydi (faqat qurilma tokeni)", httpx.post(
        f"{BASE}/api/v1/edge/hello", headers={"X-Device-Id": "devA1", "Cookie": "hc_session=x"}, json={}).status_code == 401)


# ----------------------------------------------------------------------------- A09: audit
def a09_audit(s: dict) -> None:
    print("\n--- A09 Audit jurnali ---")
    A, B = login("egasi@a.uz"), login("egasi@b.uz", "Tiklangan-Parol-5")
    httpx.post(f"{BASE}/api/v1/events/devA1:2/review", json={"status": "tasdiqlangan"}, headers=A)
    httpx.get(f"{BASE}/api/v1/clips/devA1/devA1_1.mp4", headers=A)
    httpx.get(f"{BASE}/api/v1/clips/devA1/devA1_1.mp4", headers={**A, "Range": "bytes=0-100"})
    s["tok"]["devA2"] = httpx.post(f"{BASE}/api/v1/branches/{s['a2']}/token", headers=A).json()["device_token"]
    items = httpx.get(f"{BASE}/api/v1/audit?limit=500", headers=A).json()["items"]
    acts = {i["action"] for i in items}
    need = {"login_ok", "login_fail", "review", "clip_view", "token_rotate", "user_create", "password_change",
            "password_reset", "user_deactivate", "user_activate", "logout_all", "password_fail"}
    ok("Jurnalda: kirish, xato kirish, tekshirish, klip, token, xodim, parol amallari", need <= acts, str(need - acts))
    ok("Klip ko'rish bitta yozuv (video Range so'rovlari takrorlamaydi)",
       sum(1 for i in items if i["action"] == "clip_view" and i["target"] == "devA1/devA1_1.mp4") == 1)
    ok("Har yozuvda IP va kim", all(i["ip"] and i["actor"] for i in items if i["action"] in ("review", "clip_view", "login_ok")))
    bitems = httpx.get(f"{BASE}/api/v1/audit?limit=500", headers=B).json()["items"]
    ok("B kompaniyasi A ning jurnalini ko'rmaydi", bitems and all(i["actor"].endswith("@b.uz") or i["actor"].startswith("server")
                                                             for i in bitems), str({i["actor"] for i in bitems}))
    ok("Jurnalda parol/token matni yo'q", not any(x in str(items) for x in ("Parol12345", "Kassir-Yangi-8", s["tok"]["devA1"])))
    r = httpx.get(f"{BASE}/api/v1/audit?action=review", headers=A).json()["items"]
    ok("Jurnal filtri (amal turi)", r and all(i["action"] == "review" for i in r))


# ----------------------------------------------------------------------------- event loop, SSE, kliplar, zaxira
def resilience(s: dict) -> None:
    print("\n--- Event loop bloklanmaydi, SSE chegarasi ---")
    A = login("egasi@a.uz")
    orig = cloud.cdb.upsert

    def slow(*a, **k):
        time.sleep(1.5)
        return orig(*a, **k)
    lat: list[float] = []
    cloud.cdb.upsert = slow
    try:
        th = threading.Thread(target=lambda: edge("devA1", s["tok"]["devA1"], "metrics", json={"items": [
            {"key": "m:1", "minute": int(time.time() // 60 * 60), "source_id": 0, "cpu": 10}]}))
        th.start()
        time.sleep(0.3)
        for _ in range(3):
            t0 = time.time()
            httpx.get(f"{BASE}/healthz")
            lat.append(time.time() - t0)
        th.join()
    finally:
        cloud.cdb.upsert = orig
    ok("Sekin baza so'rovi paytida server javob beradi (threadpool)", max(lat) < 0.5, f"{max(lat):.2f} s")

    async def sse_cap():
        hub = live.Hub()
        hub.bind(asyncio.get_running_loop())
        qs = [hub.subscribe(7, {1}, per_user=3) for _ in range(5)]
        closed = [q.qsize() > 0 and q.get_nowait() is None for q in qs]
        return closed
    closed = asyncio.run(sse_cap())
    ok("SSE: bitta xodimga ko'pi bilan N oqim — eng eskilari yopiladi", closed == [True, True, False, False, False], str(closed))

    async def sse_sessions():
        hub = live.Hub()
        hub.bind(asyncio.get_running_loop())
        a = [hub.subscribe(8, {1}, per_user=24, session="brauzerA", per_session=2) for _ in range(3)]
        b = [hub.subscribe(8, {1}, per_user=24, session=f"planshet{i}", per_session=2) for i in range(10)]
        return [q.qsize() > 0 for q in a], [q.qsize() > 0 for q in b]
    sa, sb = asyncio.run(sse_sessions())
    ok("SSE: bitta brauzerdagi ortiqcha tab yopiladi, bitta akkauntli 10 planshet bir-birini uzmaydi",
       sa == [True, False, False] and not any(sb), f"{sa} {sb}")
    with httpx.stream("GET", f"{BASE}/api/v1/stream", headers=A, timeout=10) as r:
        first = next(r.iter_lines())
        ok("SSE oqimi ochiladi (sessiya bilan)", r.status_code == 200 and first.startswith("retry"))

    print("\n--- Kliplar muddati, disk, zaxira nusxa ---")
    cdb = cloud.cdb
    dev, tok = "devA2", s["tok"]["devA2"]
    old = [ev(dev, 11, ago=40 * 86400), ev(dev, 12, ago=40 * 86400, status="tasdiqlangan"), ev(dev, 13, ago=200 * 86400,
                                                                                               status="tasdiqlangan")]
    edge(dev, tok, "events", json={"items": old})
    for lid in (11, 12, 13):
        edge(dev, tok, f"events/{dev}:{lid}/clip", data={"meta": "{}"}, files={"clip": ("c.mp4", MP4, "video/mp4"),
                                                                               "thumb": ("t.jpg", JPG, "image/jpeg")})
    f11 = cdb.CLIPS_DIR / dev / f"{dev}_11.mp4"
    # Edge soati noto'g'ri (yoki navbatdan kech kelgan): created_at 40 kun oldin, lekin bulutga HOZIR keldi
    cfg0 = {**maintenance.settings(), "clip_days": 30, "confirmed_days": 180, "min_free_gb": 0}
    maintenance.prune_clips(cfg=cfg0)
    ok("Muddat bulut soati bo'yicha: eski sanali, lekin hozir kelgan klip o'chmaydi",
       cdb.scalar("SELECT clip_file FROM events WHERE uid=:u", {"u": f"{dev}:11"}) and f11.exists())
    for lid, days in ((11, 40), (12, 40), (13, 200)):     # endi haqiqatan ham shuncha kun oldin kelgan deb olamiz
        cdb.execute("UPDATE events SET received_at=:t WHERE uid=:u", {"t": time.time() - days * 86400, "u": f"{dev}:{lid}"})
    stale = cdb.CLIPS_DIR / dev / "eski.123.part"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 2 * 86400,) * 2)
    cfg = {**maintenance.settings(), "clip_days": 30, "confirmed_days": 180, "min_free_gb": 0}
    out = maintenance.prune_clips(cfg=cfg)
    clip = {lid: cdb.scalar("SELECT clip_file FROM events WHERE uid=:u", {"u": f"{dev}:{lid}"}) for lid in (11, 12, 13)}
    ok("30 kundan eski klip o'chdi, tasdiqlangan (40 kun) qoldi, 200 kunlik tasdiqlangan o'chdi",
       clip[11] is None and clip[12] and clip[13] is None and not f11.exists(), f"{out} {clip}")
    ok("Hodisa yozuvi qoldi (faqat fayl o'chdi); chala .part tozalandi",
       cdb.scalar("SELECT COUNT(*) FROM events WHERE uid=:u", {"u": f"{dev}:11"}) == 1 and not stale.exists())
    big = {**cfg, "clip_days": 0, "min_free_gb": 10 ** 6}
    fresh = cdb.scalar("SELECT COUNT(*) FROM events WHERE clip_file IS NOT NULL AND status <> 'tasdiqlangan' AND "
                       "received_at > :t", {"t": time.time() - 3600})
    cdb.execute("UPDATE events SET received_at=:t WHERE uid=:u", {"t": time.time() - 3 * 86400, "u": f"{dev}:12"})
    cdb.execute("UPDATE events SET status='yangi' WHERE uid=:u", {"u": f"{dev}:12"})
    out = maintenance.prune_clips(cfg=big)
    left_old = cdb.scalar("SELECT COUNT(*) FROM events WHERE clip_file IS NOT NULL AND status <> 'tasdiqlangan' AND "
                          "received_at < :t", {"t": time.time() - 86400})
    left_new = cdb.scalar("SELECT COUNT(*) FROM events WHERE clip_file IS NOT NULL AND status <> 'tasdiqlangan' AND "
                          "received_at > :t", {"t": time.time() - 3600})
    ok("Diskda joy kam: eski tasdiqlanmagan kliplar o'chadi, oxirgi sutkadagilari himoyada", out["low_disk"] > 0
       and left_old == 0 and left_new == fresh and fresh > 0, f"{out} eski={left_old} yangi={left_new}/{fresh}")
    if maintenance._sqlite_path() is not None:
        dest = Path(tempfile.mkdtemp(prefix="hushyor_bak_"))
        (cdb.DATA_DIR / "vapid_private.pem").write_text("test-key")
        (dest / "cloud-yangilashdan-oldin.db").write_text("qo'lda olingan nusxa")
        other = Path(tempfile.mkdtemp(prefix="hushyor_bak2_"))
        for _ in range(3):
            maintenance.backup(other, keep=2)
            time.sleep(1.05)            # nom soniya aniqligida
        ok("Boshqa papkaga qo'lda olingan zaxiralar aylantirilmaydi (o'chirilmaydi)", len(list(other.glob("cloud-*.db.gz"))) == 3)
        os.environ["CLOUD_BACKUP_DIR"] = str(dest)
        try:
            paths = [maintenance.backup(dest, keep=2) for _ in range(3) if not time.sleep(1.05)]
        finally:
            os.environ.pop("CLOUD_BACKUP_DIR", None)
        files = sorted(p.name for p in dest.glob("cloud-2*.db.gz"))     # 3.2.0: siqilgan
        ok("Qo'lda qo'yilgan 'cloud-yangilashdan-oldin.db' o'chmadi", (dest / "cloud-yangilashdan-oldin.db").exists())
        import gzip
        import shutil
        unz = dest / "tekshiruv.db"
        with gzip.open(paths[-1], "rb") as fi, open(unz, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        con = sqlite3.connect(str(unz))
        n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        con.close()
        unz.unlink()
        ok("Zaxira nusxa: siqilgan (.db.gz), ochilganda izchil SQLite (hodisalar bor), VAPID kaliti, eng yangi 2 tasi qoladi",
           paths[-1].name.endswith(".db.gz") and len(files) == 2 and n > 0 and len(list(dest.glob("vapid-*.pem"))) == 2, f"{files} n={n}")
        ok("Oxirgi zaxira vaqti saqlanadi (panel/monitoring)", maintenance.last_backup()[1] == paths[-1].name)
    st = httpx.get(f"{BASE}/api/v1/system", headers=A).json()
    ok("/system: versiya, disk, kliplar, zaxira; server fayl yo'li chiqmaydi", st.get("version") == cloud.VERSION
       and "free_gb" in st and "backup_dir" not in st and str(cdb.DATA_DIR) not in str(st), str(st)[:160])


# ----------------------------------------------------------------------------- edge (do'kon) paneli
EDGE_PORT = 9332


def edge_suite() -> None:
    """Do'kon kompyuteridagi panel (app/web.py): kirish, sarlavhalar, CSRF, yo'l, SSRF, XSS, standart parol."""
    print("\n--- Edge (do'kon) paneli ---")
    from app import config, db
    from app import web

    db.init_db()
    srv = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=EDGE_PORT, log_level="error", lifespan="off"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    eb = f"http://127.0.0.1:{EDGE_PORT}"
    try:
        bad = []
        n = 0
        for r in web.app.routes:
            path = getattr(r, "path", "")
            if not path.startswith("/api/") or not hasattr(r, "methods"):
                continue
            url = eb + re.sub(r"\{[^}]+\}", "1", path)
            for m in sorted(r.methods - {"HEAD"}):
                n += 1
                code = httpx.request(m, url, timeout=10, follow_redirects=False).status_code
                if code not in (401, 403):
                    bad.append(f"{m} {path} → {code}")
        ok(f"Edge: har API endpointi kirishsiz yopiq ({n} ta)", not bad and n >= 50, "; ".join(bad[:5]))
        web._login_fails.clear()
        lan = {"X-Forwarded-For": "192.168.1.50"}
        r = httpx.post(f"{eb}/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"}, headers=lan)
        default = config.ADMIN_PASSWORD == "admin123"
        ok("Standart parol (admin123) bilan tarmoqdan kirib bo'lmaydi (faqat shu kompyuterdan)",
           (r.headers.get("location") == "/login?error=default") if default else r.status_code == 303)
        c = httpx.Client(base_url=eb, timeout=30, follow_redirects=True)
        r = c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"})
        ok("Shu kompyuterdan kirish ishlaydi", r.status_code == 200 and "Boshqaruv" in r.text)
        cookie = c.cookies.get("hushyor_session") or ""
        sc = [h for k, h in r.history[0].headers.multi_items() if k.lower() == "set-cookie"] if r.history else []
        ok("Edge sessiya cookie: HttpOnly, SameSite=Lax", cookie and sc and "httponly" in sc[0].lower()
           and "samesite=lax" in sc[0].lower(), (sc or [""])[0][:80])
        trav = [p for p in ("..", "...", "..%2Fhushyor.db", "%2E%2E", ".") if
                c.get(f"/media/clips/{p}").status_code not in (400, 404)]
        ok("Edge: /media/clips yo'lidan chiqib bo'lmaydi", not trav, str(trav))
        ssrf = []
        for u in ("http://127.0.0.1:8000/healthz", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                  "http://10.0.0.1/a.mp4", "http://192.168.1.1/a.mp4", "http://[::1]/a.mp4", "file:///etc/passwd",
                  "http://0.0.0.0:9000/", "http://[64:ff9b::7f00:1]/a.mp4", "http://[::ffff:192.168.1.1]/a.mp4",
                  "http://" + "a" * 70 + ".example.com/a.mp4"):
            code = c.post("/api/upload-url", json={"url": u}).status_code
            if code != 400:
                ssrf.append(f"{u} → {code}")
        ok("SSRF: video havolasi lokal tarmoq/shu kompyuter/metadata manziliga yuborilmaydi", not ssrf, "; ".join(ssrf))
        # DNS rebinding: ulanish aynan tekshirilgan IP'ga (Host — asl nom); yo'naltirish lokal manzilga — rad
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen = []

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Host")))
                if self.path == "/redir":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{hs.server_address[1]}/a.mp4")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"x")
        hs = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=hs.serve_forever, daemon=True).start()
        real = web.resolve_public
        web.resolve_public = lambda u: (True, "", "127.0.0.1") if "video.test" in u else real(u)
        try:
            cl, rr = web.open_public(f"http://video.test:{hs.server_address[1]}/a.mp4")
            rr.close()
            cl.close()
            pinned = seen[-1] == ("/a.mp4", f"video.test:{hs.server_address[1]}")
            try:
                web.open_public(f"http://video.test:{hs.server_address[1]}/redir")
                redir_ok = False
            except ValueError:
                redir_ok = True
        finally:
            web.resolve_public = real
            hs.shutdown()
        ok("SSRF: ulanish tekshirilgan IP'ga (DNS rebinding yo'q), lokalga yo'naltirish rad etiladi", pinned and redir_ok,
           f"{seen} redir_ok={redir_ok}")
        r = httpx.post(f"{eb}/login", data={"username": "admin", "password": "xato-parol", "next": "/"})
        c2 = httpx.Client(base_url=eb, timeout=30)
        bad_next = []
        for nx in ("/\\evil.example", "//evil.example", "https://evil.example", "/\t/evil.example"):
            loc = c2.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": nx}).headers.get("location")
            if loc != "/":
                bad_next.append(f"{nx!r}→{loc}")
            c2.cookies.clear()
        web._login_fails.clear()
        ok("Edge: kirishdan keyin boshqa saytga yo'naltirib bo'lmaydi (/\\evil, //evil)", not bad_next, "; ".join(bad_next))
        eid = db.insert_event({"source_id": 1, "source_name": "<script>alert(1)</script>", "created_at": time.time(),
                               "score": 70, "level": "<img src=x onerror=alert(2)>", "kind": "pocket",
                               "reasons": ["<b>qalin</b>"], "breakdown": {}, "track_id": 1, "status": "yangi"})
        page = c.get(f"/events/{eid}").text
        ok("XSS: hodisa sahifasida HTML escape (kamera nomi, daraja, sabab)", "<script>alert(1)" not in page
           and "<img src=x" not in page and "&lt;script&gt;" in page)
        db.execute("DELETE FROM events WHERE id=?", (eid,))
        r = c.get("/")
        ok("Edge HTML: CSP, X-Frame-Options, nosniff, Referrer-Policy", all(
            h in r.headers for h in ("content-security-policy", "x-frame-options", "x-content-type-options",
                                     "referrer-policy")))
        r = c.post("/api/settings", json={"cooldown_sec": 1}, headers={"Origin": "http://evil.example"})
        ok("Edge CSRF: boshqa saytdan sozlama o'zgartirilmaydi", r.status_code == 403)
        r1 = c.post("/api/settings", json={"cooldown_sec": 1}, headers={"Sec-Fetch-Site": "same-site"})
        r2 = c.post("/api/settings", json={"cooldown_sec": 1}, headers={"Sec-Fetch-Site": "same-site",
                                                                         "Origin": "http://127.0.0.1:9999"})
        r3 = c.post("/api/settings", json={"cooldown_sec": 1}, headers={"Sec-Fetch-Site": "same-origin", "Origin": eb})
        ok("Edge CSRF: shu kompyuterdagi boshqa port (same-site) rad etiladi, o'z sahifasi o'tadi",
           r1.status_code == 403 and r2.status_code == 403 and r3.status_code == 200, f"{r1.status_code} {r2.status_code} {r3.status_code}")
        r = c.get("/")
        csp = r.headers.get("content-security-policy", "")
        ok("Edge CSP: tashqi skript, object, base, forma va ramka taqiqlangan",
           all(x in csp for x in ("default-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
                                  "frame-ancestors 'none'")), csp[:120])
    finally:
        srv.should_exit = True
        time.sleep(0.5)


# ----------------------------------------------------------------------------- sozlama va repo
def config_and_repo() -> None:
    print("\n--- Sozlama eslatmalari va repo (maxfiy kalitlar) ---")
    saved = (dict(cloud._state), notify.cfg)
    cloud._state.update(tokens=[(None, "eski-umumiy-token")], admin="qisqa")
    notify.cfg = notify.Config(public_url="http://dokon.example.uz")
    w = cloud.security_warnings("0.0.0.0")
    cloud._state.clear()
    cloud._state.update(saved[0])
    notify.cfg = saved[1]
    ok("Ishga tushishda xavfli sozlama eslatiladi (eski token, qisqa admin token, HTTPS'siz)", len(w) >= 3, str(w)[:200])
    tmp = Path(tempfile.mkdtemp(prefix="hushyor_env_"))
    (tmp / "cloud").mkdir()
    (tmp / ".env").write_text("\ufeff# izoh\nexport CLOUD_DB_URL='postgresql+psycopg://u:p@h/db'\nCLOUD_X=1\n", encoding="utf-8")
    real_root, had = cloud.cdb.ROOT, os.environ.pop("CLOUD_DB_URL", None)
    cloud.cdb.ROOT = tmp / "cloud"
    try:
        got = (cloud.cdb.env("CLOUD_DB_URL"), cloud.cdb.env("CLOUD_YOQ", "sukut"))
    finally:
        cloud.cdb.ROOT = real_root
        if had is not None:
            os.environ["CLOUD_DB_URL"] = had
    ok("CLOUD_DB_URL .env faylidan ham o'qiladi (BOM, export, qo'shtirnoq)", got == ("postgresql+psycopg://u:p@h/db", "sukut"),
       str(got))
    try:
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        files = []
    pats = {"Telegram bot tokeni": re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
            "shaxsiy kalit": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            "AWS kaliti": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), "GitHub tokeni": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36}\b")}
    found = []
    for f in files:
        p = ROOT / f
        if not p.is_file() or p.suffix in (".onnx", ".joblib", ".mp4", ".png", ".jpg", ".woff2") or p.stat().st_size > 3e6:
            continue
        body = p.read_bytes()
        found += [f"{f}: {k}" for k, rx in pats.items() if rx.search(body)]
    tracked_bad = [f for f in files if f == ".env" or f.startswith(("data/", "cloud/data/")) and f != "data/.gitkeep"]
    ok("Repoda maxfiy kalit yo'q (Telegram/kalit/AWS/GitHub)", files and not found, "; ".join(found)[:200])
    ok("Repoda .env, data/, cloud/data/ yo'q", not tracked_bad, str(tracked_bad))
    nonascii = [f for f in files if f.endswith((".ps1", ".bat", ".cmd")) and (ROOT / f).is_file()
                and any(b > 127 for b in (ROOT / f).read_bytes())]
    ok("Windows skriptlari (.ps1/.bat) faqat ASCII — PowerShell 5.1 BOM'siz faylni ANSI'da o'qiydi", not nonascii,
       str(nonascii))
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    ok(".gitignore: .env, data/*, cloud/data/", all(x in gi for x in (".env", "data/*", "cloud/data/")))


def run(label: str) -> None:
    print(f"\n=== {label} ===")
    cloud.cdb.reset_engine()
    cloud.cdb.meta.drop_all(cloud.cdb.engine())
    cloud.init_db()
    notify.cfg = notify.Config(tz_offset=5, offline_sec=0, escalate_sec=0)
    cloud._state.update(tokens=[], admin="superadmin-sinov-tokeni-uzunroq-1234567", fail=False, fail_paths=(),
                        notify_configured=True, no_notify=True)
    cloud._login_fails.clear()
    cloud._clip_seen.clear()
    srv = serve()
    try:
        s = seed()
        a01_access(s)
        a05_headers()
        a03_injection(s)
        a07_auth(s)
        edge_auth(s)
        a09_audit(s)
        resilience(s)
        config_and_repo()
    finally:
        srv.should_exit = True
        time.sleep(0.8)


def main() -> int:
    edge_suite()
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
