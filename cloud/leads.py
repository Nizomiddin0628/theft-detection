"""Saytdagi so'rovlar — lidlar (2.6.0, 10-bosqich).

Sayt (/sayt/) formasi → `POST /api/v1/leads` (ochiq, kirishsiz, faqat JSON): ism, telefon, do'kon nomi, shahar,
filiallar soni, izoh. Himoya:
  * "honeypot" maydoni (odamga ko'rinmaydi, bot to'ldiradi) va forma ochilganidan MIN_FILL_SEC dan tez yuborish —
    jim "qabul qilindi" (bot o'rganmasin), bazaga yozilmaydi;
  * IP bo'yicha chegara (soatiga IP_HOUR, kuniga IP_DAY; IPv6 — /64 tarmoq bo'yicha) — 429;
  * umumiy kunlik chegara: TOTAL_DAY dan keyin so'rov saqlanadi, lekin xabar yuborilmaydi (hujumda Telegram to'lmasin,
    haqiqiy mijoz yo'qolmasin), HARD_DAY dan keyin — 429;
  * faqat aniq `application/json` va boshqa saytdan emas (server.py) — begona sahifa brauzer orqali yubora olmaydi;
  * bitta telefon 24 soatda bir marta: takror yuborilsa faqat takrorlar soni oshadi (xabar qayta ketmaydi; begona
    odam telefon raqamini bilsa ham mavjud so'rov matnini o'zgartira olmaydi). Javob har doim faqat {"ok": true} —
    raqam avval yuborilganmi, nechta so'rov borligi tashqariga chiqmaydi (server.py).
Xabar: bulut boti (CLOUD_TELEGRAM_BOT_TOKEN) → CLOUD_LEADS_CHAT (tugmalar: 📞 bog'lanildi / 🤝 pilot / ✖️ rad) va
CLOUD_SMTP_* → CLOUD_LEADS_EMAIL. Yuborilmasa — qayta urinish (1, 2, 4… daqiqa, keyin har soat, 2 kungacha;
notify.Worker → retry_async).
Holatlar: yangi → boglanildi → pilot | rad (Telegram tugmasi, admin API yoki `python -m cloud.server --lead`).
10-bosqich darvozasi: ≥ GATE_LEADS so'rov va ≥ GATE_PILOTS pilot kelishuvi (`python -m cloud.server --leads`).
IP manzil saqlanmaydi (faqat server siri bilan HMAC — chegara uchun); jurnalga ism/telefon yozilmaydi.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import logging
import math
import re
import secrets
import threading
import time
from email.message import EmailMessage

from . import db as cdb

log = logging.getLogger("hushyor.cloud.leads")

STATUSES = ("yangi", "boglanildi", "pilot", "rad")
LABELS = {"yangi": "Yangi", "boglanildi": "Bog'lanildi", "pilot": "Pilot kelishildi", "rad": "Rad etildi"}
ALIASES = {"bog'lanildi": "boglanildi", "bogʻlanildi": "boglanildi", "boglandi": "boglanildi", "rad_etildi": "rad"}
MIN_FILL_SEC = 3.0
IP_HOUR, IP_DAY, TOTAL_DAY, HARD_DAY = 5, 20, 300, 1000
HITS_MAX = 5000                 # IP chegarasi xotirasi (eng eski yozuvlar chiqarib tashlanadi)
HONEYPOT = "hp_extra"           # odamga ko'rinmaydigan maydon nomi (brauzer/parol menejeri avto-to'ldirmaydigan)
DUP_SEC = 86400
MAX_TRIES = 50                  # 1, 2, 4 … 32 daqiqa, keyin har soat — ~2 kun (Telegram/internet uzoq uzilsa ham)
RETRY_MAX_SEC = 3600
GATE_LEADS, GATE_PILOTS = 10, 3
BODY_LIMIT = 16 * 1024
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ -‮﻿]")
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()
_inflight: set[int] = set()     # hozir yuborilayotgan so'rovlar (oqim va qayta urinish bir vaqtda yubormasin)
_salt: list[bytes] = []
_salt_lock = threading.Lock()


class Invalid(ValueError):
    """Maydon noto'g'ri (400)."""


class Limited(ValueError):
    """Juda ko'p so'rov (429)."""


def _clean(v, limit: int, multiline: bool = False) -> str:
    s = _CTRL.sub("", str(v if v is not None else ""))
    if multiline:
        s = "\n".join(" ".join(x.split()) for x in s.replace("\r", "").split("\n"))
        s = re.sub(r"\n{3,}", "\n\n", s).strip()
    else:
        s = " ".join(s.split())
    return s[:limit]


def normalize_phone(raw) -> str:
    """+998 90 123-45-67 → 998901234567. O'zbekiston raqami 9 xonali yozilsa 998 qo'shiladi. Noto'g'ri — Invalid."""
    s = str(raw or "")
    if re.search(r"[A-Za-z]", s):
        raise Invalid("Telefon raqamida harf bo'lmasin")
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 9:
        d = "998" + d
    if d.startswith("998") and len(d) != 12:
        raise Invalid("Telefon: +998 XX XXX XX XX ko'rinishida yozing")
    if not 9 <= len(d) <= 15:
        raise Invalid("Telefon raqami noto'g'ri")
    return d


def fmt_phone(d: str) -> str:
    if len(d) == 12 and d.startswith("998"):
        return f"+998 {d[3:5]} {d[5:8]} {d[8:10]} {d[10:12]}"
    return "+" + d


def _ip_key(ip: str) -> str:
    """Chegara kaliti: IPv4 — manzil, IPv6 — /64 tarmoq (bitta abonent ko'p manzil oladi)."""
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ip or "?"
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


def _ip_hash(ip: str) -> str:
    """HMAC (server siri bazada) — IP'ni hash'dan tiklab bo'lmaydi."""
    with _salt_lock:
        if not _salt:
            v = cdb.kv_get("leads_salt", "")
            if not v:
                v = secrets.token_hex(16)
                cdb.kv_set("leads_salt", v)
            _salt.append(v.encode())
    return hmac.new(_salt[0], _ip_key(ip).encode(), hashlib.sha256).hexdigest()


def _rate(ip_h: str, now: float) -> None:
    with _lock:
        if ip_h not in _hits and len(_hits) >= HITS_MAX:
            # Xotira chegarali: avval eskirganlar, yetmasa — eng uzoq vaqt oldin ko'ringanlari chiqariladi
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 86400]:
                _hits.pop(k, None)
            if len(_hits) >= HITS_MAX:              # ~10% bir yo'la (har yangi IP'da saralanmasin)
                for k in sorted(_hits, key=lambda k: _hits[k][-1] if _hits[k] else 0)[:HITS_MAX // 10]:
                    _hits.pop(k, None)
        h = [t for t in _hits.get(ip_h, []) if now - t < 86400]
        if len(h) >= IP_DAY or len([t for t in h if now - t < 3600]) >= IP_HOUR:
            _hits[ip_h] = h
            raise Limited("Juda ko'p so'rov — birozdan keyin qayta urinib ko'ring yoki telefon qiling")
        h.append(now)
        _hits[ip_h] = h


def create(body, ip: str, now: float | None = None, deliver_async: bool = True) -> dict:
    """Saytdan so'rov. Qaytaradi {"ok": True, "id": n | None}. Bot belgisi — id None (jim)."""
    now = time.time() if now is None else now
    if not isinstance(body, dict):
        raise Invalid("JSON obyekt kutilgan")
    if str(body.get(HONEYPOT) or "").strip():
        log.info("Sayt formasi: bot (honeypot) — rad etildi")
        return {"ok": True, "id": None}
    try:
        elapsed = float(body.get("elapsed"))
    except (TypeError, ValueError, OverflowError):
        raise Invalid("Forma eskirgan — sahifani yangilab, qaytadan yuboring") from None
    if not math.isfinite(elapsed):
        raise Invalid("Forma eskirgan — sahifani yangilab, qaytadan yuboring")
    if elapsed < MIN_FILL_SEC:
        log.info("Sayt formasi: juda tez to'ldirildi (%.1f s) — bot deb hisoblandi", elapsed)
        return {"ok": True, "id": None}
    name = _clean(body.get("name"), 100)
    if len(name) < 2:
        raise Invalid("Ismingizni yozing")
    phone = normalize_phone(body.get("phone"))
    vals = {"name": name, "phone": phone, "store": _clean(body.get("store"), 200), "city": _clean(body.get("city"), 100),
            "branches": _clean(body.get("branches"), 20), "message": _clean(body.get("message"), 2000, True),
            "source": _clean(body.get("source"), 200)}
    day_n = cdb.scalar("SELECT COUNT(*) FROM leads WHERE created_at >= :t", {"t": now - 86400}) or 0
    if day_n >= HARD_DAY:
        log.warning("Sayt formasi: kunlik qat'iy chegara (%d) — yangi so'rovlar vaqtincha qabul qilinmaydi", HARD_DAY)
        raise Limited("Hozir so'rovlar juda ko'p — iltimos, telefon qiling yoki keyinroq yozing")
    ip_h = _ip_hash(ip)
    _rate(ip_h, now)
    quiet = day_n >= TOTAL_DAY                    # hujum ehtimoli: saqlanadi, lekin xabar yuborilmaydi
    dup = cdb.row("SELECT * FROM leads WHERE phone=:p AND created_at >= :t ORDER BY id DESC LIMIT 1",
                  {"p": phone, "t": now - DUP_SEC})
    if dup:
        cdb.execute("UPDATE leads SET repeats=repeats+1, updated_at=:n WHERE id=:i", {"n": now, "i": dup["id"]})
        log.info("So'rov #%s: shu telefondan takror (24 soat ichida)", dup["id"])
        return {"ok": True, "id": int(dup["id"]), "repeat": True}
    # notify_next: birinchi yuborish — shu oqimda; qayta urinish (Worker) faqat 2 daqiqadan keyin (ikki marta ketmasin)
    extra = ({"notify_tries": MAX_TRIES, "notify_next": None,
              "notify_error": f"kunlik chegara ({TOTAL_DAY}) — xabar yuborilmadi"} if quiet
             else {"notify_tries": 0, "notify_next": now + 120})
    with cdb.engine().begin() as c:
        r = c.execute(cdb.leads.insert().values(created_at=now, updated_at=now, status="yangi", ip_hash=ip_h,
                                                repeats=0, **extra, **vals))
        lid = int(r.inserted_primary_key[0])
    log.info("Yangi so'rov #%d (saytdan)%s", lid, " — kunlik chegaradan keyin, xabarsiz" if quiet else "")
    if quiet:
        log.warning("Sayt formasi: kunlik %d ta so'rovdan oshdi — xabar yuborilmayapti (hujum bo'lishi mumkin)", TOTAL_DAY)
        return {"ok": True, "id": lid}
    if deliver_async:
        threading.Thread(target=_deliver_safe, args=(lid,), daemon=True, name=f"lead-{lid}").start()
    return {"ok": True, "id": lid}


def get(lead_id: int) -> dict | None:
    return cdb.row("SELECT * FROM leads WHERE id=:i", {"i": int(lead_id)})


def public(r: dict) -> dict:
    out = {k: r.get(k) for k in ("id", "created_at", "updated_at", "name", "store", "city", "branches", "message",
                                 "source", "status", "note", "repeats", "notified_at", "notify_tries", "notify_error")}
    out["phone"] = fmt_phone(r.get("phone") or "")
    out["status_label"] = LABELS.get(r.get("status") or "", r.get("status"))
    return out


def text_for(r: dict, by: str | None = None) -> str:
    """Telegram (HTML) xabari. by — holatni kim o'zgartirdi (tugma bosgan; bazaga yozilmaydi)."""
    from . import notify

    e = lambda v: html.escape(str(v or ""), quote=False)  # noqa: E731
    lines = [f"🆕 <b>Saytdan so'rov #{r['id']}</b>", f"👤 {e(r['name'])}", f"📞 {e(fmt_phone(r['phone']))}"]
    if r.get("store"):
        lines.append(f"🏪 {e(r['store'])}" + (f" · {e(r['city'])}" if r.get("city") else ""))
    elif r.get("city"):
        lines.append(f"📍 {e(r['city'])}")
    if r.get("branches"):
        lines.append(f"🏬 Filiallar: {e(r['branches'])}")
    if r.get("message"):
        lines.append(f"💬 {e(r['message'][:1500])}")
    lines.append(f"🕒 {notify.fmt_time(r['created_at'])}" + (f" · {e(r['source'])}" if r.get("source") else ""))
    if r.get("status") and r["status"] != "yangi":
        lines.append(f"\nHolat: <b>{e(LABELS.get(r['status'], r['status']))}</b>" + (f" ({e(by)})" if by else "") +
                     (f" — {e(r['note'])}" if r.get("note") else ""))
    return "\n".join(lines)


def markup(lead_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "📞 Bog'lanildi", "callback_data": f"l:{lead_id}:boglanildi"},
                                 {"text": "🤝 Pilot", "callback_data": f"l:{lead_id}:pilot"},
                                 {"text": "✖️ Rad", "callback_data": f"l:{lead_id}:rad"}]]}


def channels() -> dict:
    from . import notify

    c = notify.cfg
    return {"telegram": bool(c.tg_token and c.leads_chat and not notify.state.get("tg_conflict")),
            "email": bool(c.smtp_host and c.smtp_from and c.leads_email)}


def deliver(lead_id: int, now: float | None = None) -> bool:
    """So'rovni Telegram va/yoki emailga yuborish. Kamida bittasi o'tsa — yuborilgan. Bitta so'rovni bir vaqtda faqat
    bitta oqim yuboradi (yangi so'rov oqimi va qayta urinish ustma-ust tushsa ham ikki marta ketmaydi)."""
    now = time.time() if now is None else now
    with _lock:
        if lead_id in _inflight:
            return False                       # boshqa oqim hozir yubormoqda
        _inflight.add(lead_id)
    try:
        return _deliver(lead_id, now)
    finally:
        with _lock:
            _inflight.discard(lead_id)


def _deliver(lead_id: int, now: float) -> bool:
    from . import notify

    r = get(lead_id)
    if not r or r.get("notified_at"):
        return bool(r)
    ch = channels()
    if not ch["telegram"] and not ch["email"]:
        # Urinishlar sanalmaydi: kanal keyin sozlansa (server qayta ishga tushganda) 2 kun ichidagilar yuboriladi
        cdb.execute("UPDATE leads SET notify_error=:e, notify_next=NULL WHERE id=:i",
                    {"e": "kanal sozlanmagan (CLOUD_LEADS_CHAT yoki CLOUD_LEADS_EMAIL)", "i": lead_id})
        log.warning("So'rov #%d: xabar kanali sozlanmagan — faqat bazada (python -m cloud.server --leads)", lead_id)
        return False
    # Avval urinish sanaladi va keyingi vaqt qo'yiladi: yuborish paytida har qanday (kutilmagan ham) xato bo'lsa,
    # qayta urinish faqat shu vaqtda va ko'pi bilan MAX_TRIES marta (har 30 s da qayta-qayta yuborilmaydi)
    old = int(r.get("notify_tries") or 0)
    tries = old + 1
    nxt = now + min(60 * 2 ** min(tries - 1, 12), RETRY_MAX_SEC) if tries < MAX_TRIES else None
    if not cdb.execute("UPDATE leads SET notify_tries=:t, notify_next=:x WHERE id=:i AND notified_at IS NULL "
                       "AND notify_tries=:o", {"t": tries, "x": nxt, "i": lead_id, "o": old}):
        return False                           # boshqa jarayon allaqachon band qildi yoki yuborildi
    errs: list[str] = []
    ok = False

    def sent() -> None:
        # Kanal xatosidan alohida: baza band bo'lsa ham yuborilgan kanal "xato" deb qayta yuborilmasin (oxirgi UPDATE
        # ham notified_at ni qo'yadi)
        try:
            cdb.execute("UPDATE leads SET notified_at=:n, notify_next=NULL WHERE id=:i AND notified_at IS NULL",
                        {"n": now, "i": lead_id})
        except Exception:  # noqa: BLE001
            log.exception("So'rov #%d: yuborildi, lekin bazaga belgilab bo'lmadi", lead_id)
    if ch["telegram"]:
        try:
            notify._tg("sendMessage", {"chat_id": notify.cfg.leads_chat, "text": text_for(r), "parse_mode": "HTML",
                                       "disable_web_page_preview": True, "reply_markup": markup(lead_id)})
            ok = True
        except Exception as e:  # noqa: BLE001 — Retry/Permanent va kutilmaganlari (masalan, SMTP sozlamasi buzuq)
            errs.append(f"Telegram: {str(e)[:120] or e.__class__.__name__}")
        if ok:
            sent()
    if ch["email"]:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"Hushyor AI: saytdan so'rov #{lead_id} — {' '.join(r['name'].split())[:60]}"
            msg["From"] = notify.cfg.smtp_from
            msg["To"] = notify.cfg.leads_email
            msg.set_content(notify._plain(text_for(r)) + "\n\nHolatni o'zgartirish: python -m cloud.server --lead "
                            f"{lead_id} boglanildi|pilot|rad")
            notify.smtp_send(msg)
            mailed = True
        except Exception as e:  # noqa: BLE001
            mailed = False
            errs.append(f"Email: {str(e)[:120] or e.__class__.__name__}")
        if mailed and not ok:
            ok = True
            sent()
    if ok:
        cdb.execute("UPDATE leads SET notify_error=:e, notified_at=COALESCE(notified_at, :n), notify_next=NULL "
                    "WHERE id=:i", {"e": "; ".join(errs)[:300] or None, "n": now, "i": lead_id})
    else:
        cdb.execute("UPDATE leads SET notify_error=:e WHERE id=:i", {"e": "; ".join(errs)[:300] or None, "i": lead_id})
    if not ok:
        log.warning("So'rov #%d xabari yuborilmadi (%d-urinish): %s", lead_id, tries, "; ".join(errs)[:200])
    return ok


def _deliver_safe(lead_id: int) -> None:
    try:
        deliver(lead_id)
    except Exception:  # noqa: BLE001
        log.exception("So'rov #%d xabarida kutilmagan xato", lead_id)


_last_retry = [0.0]
_retry_thread: list = [None]


def retry_pending(now: float | None = None) -> int:
    """Vaqti kelgan yuborilmagan so'rovlar (2 kundan eski emas; kanal sozlanmagan paytda kelganlari ham — kanal
    sozlangach). Kanal yo'q — hech narsa qilinmaydi."""
    now = time.time() if now is None else now
    if now - _last_retry[0] < 30:
        return 0
    _last_retry[0] = now
    ch = channels()
    if not ch["telegram"] and not ch["email"]:
        return 0
    n = 0
    for r in cdb.rows("SELECT id FROM leads WHERE notified_at IS NULL AND notify_tries < :m AND created_at >= :old "
                      "AND (notify_next IS NULL OR notify_next <= :n) ORDER BY id LIMIT 5",
                      {"n": now, "m": MAX_TRIES, "old": now - 2 * 86400}):
        try:
            n += bool(deliver(int(r["id"]), now))
        except Exception:  # noqa: BLE001 — bitta so'rov xatosi keyingilarini to'smasin
            log.exception("So'rov #%s xabarini qayta yuborishda xato", r["id"])
    return n


def retry_async() -> None:
    """notify.Worker dan: qayta urinish alohida oqimda (Telegram/SMTP osilsa, aloqa uzilishi va eskalatsiya
    tekshiruvlari kechikmasin)."""
    t = _retry_thread[0]
    if t is not None and t.is_alive():
        return

    def run() -> None:
        try:
            retry_pending()
        except Exception:  # noqa: BLE001
            log.exception("Sayt so'rovlarini qayta yuborishda xato")
    _retry_thread[0] = threading.Thread(target=run, daemon=True, name="leads-retry")
    _retry_thread[0].start()


def norm_status(status: str) -> str:
    st = ALIASES.get(str(status or "").strip().lower(), str(status or "").strip().lower())
    if st not in STATUSES:
        raise Invalid("Holat: " + ", ".join(STATUSES))
    return st


def set_status(lead_id: int, status: str, note: str | None = None, now: float | None = None) -> dict:
    """note None — izoh o'zgarmaydi."""
    st = norm_status(status)
    r = get(lead_id)
    if not r:
        raise LookupError("so'rov topilmadi")
    vals = {"s": st, "n": time.time() if now is None else now, "i": int(lead_id)}
    sql = "UPDATE leads SET status=:s, updated_at=:n"
    if note is not None:
        sql += ", note=:note"
        vals["note"] = _clean(note, 1000, True)
    cdb.execute(sql + " WHERE id=:i", vals)
    log.info("So'rov #%d holati: %s", lead_id, st)
    return get(lead_id) or {}


def listing(status: str | None = None, limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 1000))
    if status:
        rows = cdb.rows("SELECT * FROM leads WHERE status=:s ORDER BY id DESC LIMIT :l", {"s": norm_status(status), "l": limit})
    else:
        rows = cdb.rows("SELECT * FROM leads ORDER BY id DESC LIMIT :l", {"l": limit})
    return [public(r) for r in rows]


def summary() -> dict:
    """Holatlar bo'yicha soni va 10-bosqich darvozasi (≥ 10 so'rov, ≥ 3 pilot)."""
    by = {s: 0 for s in STATUSES}
    for r in cdb.rows("SELECT status, COUNT(*) AS n FROM leads GROUP BY status"):
        by[r["status"]] = by.get(r["status"], 0) + int(r["n"])
    total = sum(by.values())
    unsent = cdb.scalar("SELECT COUNT(*) FROM leads WHERE notified_at IS NULL") or 0
    return {"total": total, "by_status": by, "unsent": int(unsent),
            "gate": {"leads": total, "need_leads": GATE_LEADS, "pilots": by.get("pilot", 0), "need_pilots": GATE_PILOTS,
                     "ok": total >= GATE_LEADS and by.get("pilot", 0) >= GATE_PILOTS}}


def cli_report() -> str:
    s = summary()
    g = s["gate"]
    lines = [f"Saytdan so'rovlar: {s['total']}  (" + ", ".join(f"{LABELS[k]}: {v}" for k, v in s["by_status"].items()) + ")"]
    if s["unsent"]:
        lines.append(f"Xabari yuborilmagan: {s['unsent']} (CLOUD_LEADS_CHAT / CLOUD_LEADS_EMAIL sozlamasini tekshiring)")
    lines.append(f"10-bosqich darvozasi: so'rovlar {g['leads']}/{g['need_leads']}, pilot {g['pilots']}/{g['need_pilots']} — "
                 + ("O'TDI ✅" if g["ok"] else "hali yo'q"))
    rows = listing(limit=50)
    if rows:
        lines.append("")
        lines.append(f"{'#':>4}  {'sana':<16} {'holat':<16} {'telefon':<18} ism / do'kon")
        from . import notify

        for r in rows:
            lines.append(f"{r['id']:>4}  {notify.fmt_time(r['created_at'])[:16]:<16} {r['status_label']:<16} "
                         f"{r['phone']:<18} {r['name']}" + (f" / {r['store']}" if r.get("store") else "")
                         + (f"  [{r['note']}]" if r.get("note") else ""))
    return "\n".join(lines)
