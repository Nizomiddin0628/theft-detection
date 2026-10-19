"""Kritik SMS (2.2.0): xavfsizlik signallari (yiqilish, odam yotibdi, tungi kirish, olov...) telefonlarga SMS bilan.

Shlyuzlar:
  eskiz  Eskiz.uz (notify.eskiz.uz): POST /api/auth/login (email, password) → token (30 kun) →
         POST /api/message/sms/send (mobile_phone 998…, message, from). 401 bo'lsa — qayta login (bir marta).
         DIQQAT: Eskiz har matn shablonini moderatsiyadan o'tkazadi — avval kabinetda shablonni tasdiqlating
         (docs/SAFETY.md). Tasdiqlanmagan matn yuborilmaydi (Eskiz xato qaytaradi — jurnalda ko'rinadi).
  http   Umumiy HTTP shlyuz: POST SMS_HTTP_URL, JSON {"phone": "998…", "text": "…"}, sarlavha
         Authorization: Bearer SMS_HTTP_TOKEN (berilgan bo'lsa). 2xx — yuborildi.

Login/parol/token faqat .env da (SMS_ESKIZ_EMAIL, SMS_ESKIZ_PASSWORD, SMS_FROM, SMS_HTTP_URL, SMS_HTTP_TOKEN) — bazada
saqlanmaydi. Bazada: yoqilgan/o'chiq, shlyuz turi, telefonlar, qaysi turlar, soatlik chegara (sozlamalar `sms_*`).
Matn faqat ASCII va ≤ 160 belgi (bitta SMS). Har yuborish `sms_log` ga yoziladi.
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from . import config, db
from .logutil import get_logger

log = get_logger("sms")

KIND_ASCII = {"fall": "YIQILISH", "lying": "ODAM YOTIBDI", "night": "TUNGI KIRISH", "crowd": "GAVJUMLIK",
              "blocked": "CHIQISH TO'SILGAN", "fire": "OLOV (tajriba)", "fight": "JANJAL (tajriba)"}
PROVIDERS = ("eskiz", "http")
MAX_LEN = 160
TIMEOUT = 15.0
_token: dict = {"value": None, "t": 0.0}
_lock = threading.Lock()


def ascii_text(s: str, limit: int = MAX_LEN) -> str:
    """O'zbekcha belgilarni ASCII ga: o‘/g‘ (va ʻ ’) → o'/g', qolgan ASCII bo'lmaganlari olib tashlanadi."""
    s = (s or "").replace("‘", "'").replace("’", "'").replace("ʻ", "'").replace("ʼ", "'").replace("`", "'")
    s = s.replace("—", "-").replace("–", "-").replace("«", '"').replace("»", '"').replace("…", "...")
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def normalize_phone(raw: str, provider: str = "eskiz") -> str | None:
    """"+998 90 123-45-67" → "998901234567". Eskiz: faqat O'zbekiston (998 + 9 raqam); http: 9–15 raqam."""
    d = re.sub(r"\D", "", str(raw or ""))
    if provider == "eskiz":
        if len(d) == 9:
            d = "998" + d
        return d if re.fullmatch(r"998\d{9}", d) else None
    return d if 9 <= len(d) <= 15 else None


def phones(s: dict | None = None) -> list[str]:
    s = s if s is not None else db.get_settings()
    prov = s.get("sms_provider", "eskiz")
    out = []
    for p in re.split(r"[,;\n]+", s.get("sms_phones", "") or ""):
        n = normalize_phone(p, prov) if p.strip() else None
        if n and n not in out:
            out.append(n)
    return out[:10]


def kinds(s: dict | None = None) -> list[str]:
    s = s if s is not None else db.get_settings()
    return [k for k in re.split(r"[,\s]+", s.get("sms_kinds", "") or "") if k in KIND_ASCII]


def configured(s: dict | None = None) -> tuple[bool, str]:
    """(tayyor, sabab) — panel uchun."""
    s = s if s is not None else db.get_settings()
    prov = s.get("sms_provider", "eskiz")
    if prov == "eskiz" and not (config.SMS_ESKIZ_EMAIL and config.SMS_ESKIZ_PASSWORD):
        return False, ".env da SMS_ESKIZ_EMAIL va SMS_ESKIZ_PASSWORD yo'q"
    if prov == "http" and not config.SMS_HTTP_URL:
        return False, ".env da SMS_HTTP_URL yo'q"
    if prov not in PROVIDERS:
        return False, "noma'lum shlyuz"
    if not phones(s):
        return False, "telefon raqami kiritilmagan"
    return True, ""


def mask(phone: str) -> str:
    return ("•" * max(0, len(phone) - 4)) + phone[-4:] if phone else ""


# ----------------------------------------------------------------------------- shlyuzlar
def _eskiz_login(client: httpx.Client) -> str:
    r = client.post(f"{config.SMS_ESKIZ_BASE}/auth/login",
                    data={"email": config.SMS_ESKIZ_EMAIL, "password": config.SMS_ESKIZ_PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"Eskiz login xatosi: HTTP {r.status_code}")
    try:
        tok = (r.json().get("data") or {}).get("token")
    except ValueError:
        tok = None
    if not tok:
        raise RuntimeError("Eskiz login: token qaytmadi")
    _token.update(value=tok, t=time.time())
    return tok


def _eskiz_send(phone: str, text: str) -> str:
    with httpx.Client(timeout=TIMEOUT) as client:
        with _lock:
            tok = _token["value"] if _token["value"] and time.time() - _token["t"] < 25 * 86400 else None
            if tok is None:
                tok = _eskiz_login(client)
        for attempt in range(2):
            r = client.post(f"{config.SMS_ESKIZ_BASE}/message/sms/send",
                            data={"mobile_phone": phone, "message": text, "from": config.SMS_FROM},
                            headers={"Authorization": f"Bearer {tok}"})
            if r.status_code == 401 and attempt == 0:
                with _lock:
                    tok = _eskiz_login(client)       # token eskirgan — qayta login, bir marta
                continue
            if 200 <= r.status_code < 300:
                try:
                    j = r.json()
                except ValueError:
                    j = {}
                st = str(j.get("status") or "").lower()
                if st in ("error", "failed"):
                    raise RuntimeError(f"Eskiz: {str(j.get('message') or j)[:200]}")
                return str(j.get("id") or j.get("message") or "ok")[:100]
            raise RuntimeError(f"Eskiz: HTTP {r.status_code} {r.text[:200]}")
    raise RuntimeError("Eskiz: yuborilmadi")


def _http_send(phone: str, text: str) -> str:
    headers = {"Authorization": f"Bearer {config.SMS_HTTP_TOKEN}"} if config.SMS_HTTP_TOKEN else {}
    r = httpx.post(config.SMS_HTTP_URL, json={"phone": phone, "text": text}, headers=headers, timeout=TIMEOUT)
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"HTTP shlyuz: {r.status_code} {r.text[:200]}")
    return "ok"


def send(phone: str, text: str, provider: str | None = None) -> tuple[bool, str]:
    provider = provider or db.get_setting("sms_provider", "eskiz")
    text = ascii_text(text)
    try:
        info = _eskiz_send(phone, text) if provider == "eskiz" else _http_send(phone, text)
        return True, info
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def _log(event_id, phone, kind, provider, text, ok, error, test=False) -> None:
    db.execute("DELETE FROM sms_log WHERE t < ?", (time.time() - 90 * 86400,))   # jurnal 90 kun
    db.execute("INSERT INTO sms_log(t, event_id, phone, kind, provider, text, ok, error, test) VALUES(?,?,?,?,?,?,?,?,?)",
               (time.time(), event_id, phone, kind, provider, text, int(bool(ok)), error, int(bool(test))))


def sent_last_hour() -> int:
    return db.scalar("SELECT COUNT(*) FROM sms_log WHERE t>=? AND ok=1 AND test=0", (time.time() - 3600,)) or 0


def event_text(ev: dict) -> str:
    store = db.get_setting("store_name", "")
    when = time.strftime("%H:%M", time.localtime(ev.get("created_at") or time.time()))
    txt = (f"Hushyor: {KIND_ASCII.get(ev.get('kind'), str(ev.get('kind')).upper())} - {store}, "
           f"{ev.get('source_name') or ''} {when}. Telegramdagi rasmni ko'ring")
    return ascii_text(txt)


# ----------------------------------------------------------------------------- hodisa → SMS
def notify_event(event_id: int, sync: bool = False) -> int:
    """Hodisa yaratilganda (manager.create_event). Sozlanmagan / tur tanlanmagan bo'lsa — hech narsa."""
    s = db.get_settings()
    if s.get("sms_enabled", "0") != "1":
        return 0
    ev = db.get_event(event_id)
    if not ev or ev.get("job_id") is not None or ev.get("kind") not in kinds(s):
        return 0
    ok, why = configured(s)
    if not ok:
        log.warning("SMS yuborilmadi (#%d): %s", event_id, why)
        return 0
    if sync:
        return _deliver(ev, s)
    threading.Thread(target=_deliver_retry, args=(ev, s), daemon=True, name=f"sms-{event_id}").start()
    return len(phones(s))


SMS_RETRY = (30.0, 120.0)        # 3.2.0: tarmoq xatosi bilan yetmagan SMS — qayta (yiqilish xabari muhim)


def _deliver_retry(ev: dict, s: dict) -> int:
    n = _deliver(ev, s)
    for delay in SMS_RETRY:
        missing = [ph for ph in phones(s)
                   if not db.scalar("SELECT 1 FROM sms_log WHERE event_id=? AND phone=? AND ok=1", (ev["id"], ph))]
        if not missing:
            break
        time.sleep(delay)
        n += _deliver(ev, s)
    return n


_deliver_lock = threading.Lock()


def _deliver(ev: dict, s: dict) -> int:
    with _deliver_lock:                  # bir vaqtda bir nechta hodisa — soatlik chegara va takror tekshiruvi buzilmasin
        return _deliver_locked(ev, s)


def _deliver_locked(ev: dict, s: dict) -> int:
    try:
        limit = int(float(s.get("sms_max_per_hour", "6") or 6))
    except ValueError:
        limit = 6
    prov = s.get("sms_provider", "eskiz")
    text = event_text(ev)
    n = 0
    for ph in phones(s):
        if db.scalar("SELECT 1 FROM sms_log WHERE event_id=? AND phone=? AND ok=1", (ev["id"], ph)):
            continue
        if sent_last_hour() >= limit:
            _log(ev["id"], ph, ev.get("kind"), prov, text, False, f"soatlik chegara ({limit}) — yuborilmadi")
            log.warning("SMS soatlik chegarasi (%d) — #%d yuborilmadi", limit, ev["id"])
            continue
        ok, info = send(ph, text, prov)
        _log(ev["id"], ph, ev.get("kind"), prov, text, ok, None if ok else info)
        if ok:
            n += 1
            log.info("SMS yuborildi: #%d → %s", ev["id"], mask(ph))
        else:
            log.warning("SMS xatosi: #%d → %s: %s", ev["id"], mask(ph), info)
    return n


_last_test = {"t": 0.0}


def send_test() -> dict:
    if time.time() - _last_test["t"] < 20:
        return {"ok": False, "error": "Sinov SMS 20 soniyada bir marta", "results": []}
    s = db.get_settings()
    ok, why = configured(s)
    if not ok:
        return {"ok": False, "error": why, "results": []}
    _last_test["t"] = time.time()
    prov = s.get("sms_provider", "eskiz")
    text = ascii_text(f"Hushyor AI: sinov SMS ({s.get('store_name', '')}). Xavfsizlik signallari shu raqamga keladi")
    res = []
    for ph in phones(s):
        good, info = send(ph, text, prov)
        _log(None, ph, "test", prov, text, good, None if good else info, test=True)
        res.append({"phone": mask(ph), "ok": good, "info": info})
    return {"ok": all(r["ok"] for r in res), "results": res, "text": text}


def recent(limit: int = 30) -> list[dict]:
    out = []
    for r in db.rows("SELECT * FROM sms_log ORDER BY id DESC LIMIT ?", (int(limit),)):
        r["phone"] = mask(r.get("phone") or "")
        out.append(r)
    return out
