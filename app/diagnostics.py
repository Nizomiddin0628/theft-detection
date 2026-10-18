"""Diagnostika paketi (2.5.0, 9-bosqich): masofadan yordam uchun bitta ZIP.

Ichida: info.json (versiyalar, tizim, disk, kameralar, sozlamalar, .env kalitlari, bulut va Telegram holati, tayyorlik,
darvozalar, oxirgi 7 kun raqamlari), jurnallar (app.log va aylantirilganlari, supervisor.log — har biri oxirgi
LOG_TAIL bayt) va README.txt. Maxfiy ma'lumot chiqmaydi: parol / token / kalit / sir / telefon / email qiymatlari
yashiriladi (sozlamalar, .env); jurnal matnlarida — Telegram bot tokeni, Bearer, manzildagi login:parol, so'rovdagi
key=/pwd=/token=, obunachilarning chat raqami, ismi va username'i, SMS telefonlari. Obunachilar — faqat soni. Hodisa
rasmlari va kliplar kirmaydi.
"""
from __future__ import annotations

import io
import json
import platform
import re
import shutil
import sys
import time
import zipfile

from . import db
from .logutil import get_logger

log = get_logger("diagnostics")

LOG_TAIL = 2_000_000
SECRET_KEY_RE = re.compile(r"(PASS|TOKEN|SECRET|KEY|CODE|PAROL|AUTH|COOKIE|SESSION|SALT|PRIVATE|PHONE|TELEFON|"
                           r"EMAIL|MAIL|LOGIN|USER_?ID|CHAT)", re.I)
SAFE_KEYS = {"PORT", "HOST", "CLOUD_URL", "TELEGRAM_API_BASE", "ADMIN_USER", "HUSHYOR_DATA_DIR", "LOG_LEVEL",
             "SMS_PROVIDER", "AI_THREADS"}
_PATTERNS = [
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"), "<TELEGRAM_TOKEN>"),         # bot tokeni (".../bot<token>/")
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1<TOKEN>"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@"), r"\1***@"),          # rtsp://user:pa@ss@ — oxirgi @ gacha
    (re.compile(r"(Yangi obunachi: ).*? \((-?\d+)\)"), r"\1<ism> (<chat>)"),      # 2.5.0 dan oldingi jurnal qatori
    # Telegram'da tekshirgan / navbat tugmasini bosgan odam ismi (bot jurnali)
    (re.compile(r"(Hodisa #\d+: (?:tasdiqlangan|rad_etilgan|yangi|qaror bekor qilindi) \().*?(\))\s*$", re.M),
     r"\1<ism>\2"),
    (re.compile(r"(Navbat signallari (?:yoqildi|o'chirildi): ).*$", re.M), r"\1<ism>"),
    (re.compile(r"(?i)([?&;](?:key|api_?key|pwd|pass|password|passwd|token|secret|auth|code|sig|signature)=)[^&\s#]+"),
     r"\1***"),
    (re.compile(r"(?i)((?:password|parol|token|secret|api_key|apikey)\s*[=:]\s*)\S+"), r"\1***"),
]


def redact(text: str, extra: list[str] | None = None) -> str:
    """Matndagi maxfiy qiymatlarni yashirish. extra — shu o'rnatishga xos qiymatlar (obunachilar chat raqami, ismi,
    username'i, SMS telefonlari) — matnda qayerda bo'lsa ham."""
    for rx, rep_ in _PATTERNS:
        text = rx.sub(rep_, text)
    for v in extra or []:
        if v.lstrip("+-").isdigit():
            text = re.sub(rf"(?<![0-9]){re.escape(v)}(?![0-9])", "<chat>", text)
        elif v.startswith("@"):
            text = re.sub(rf"{re.escape(v)}(?![\w])", "@<ism>", text)
        else:                                       # to'liq ism (bir necha so'z) — butun so'z sifatida
            text = re.sub(rf"(?<![\w]){re.escape(v)}(?![\w])", "<ism>", text)
    return text


def _private_values() -> list[str]:
    """Jurnalda uchrashi mumkin bo'lgan shaxsiy qiymatlar (uzunlari oldin — qisqasi uzunining ichida qolmasin)."""
    vals: set[str] = set()
    try:
        for r in db.rows("SELECT chat_id, name, username FROM subscribers"):
            if r.get("chat_id") is not None and len(str(r["chat_id"]).lstrip("-")) >= 5:
                vals.add(str(r["chat_id"]))
            name = (r.get("name") or "").strip()
            # Faqat to'liq ism (≥ 2 so'z) — bitta so'zli ism ("Ali", "Kassa" guruhi) oddiy matnni buzmasin
            if len(name) >= 6 and len(name.split()) >= 2:
                vals.add(name)
            un = (r.get("username") or "").strip().lstrip("@")
            if len(un) >= 3:
                vals.add("@" + un)
        for p in re.split(r"[,;\s]+", db.get_setting("sms_phones", "") or ""):
            d = re.sub(r"\D", "", p)
            if len(d) >= 7:
                vals.update({p.strip(), d, "+" + d})
    except Exception:  # noqa: BLE001
        log.exception("Diagnostika: shaxsiy qiymatlar")
    return sorted((v for v in vals if v), key=len, reverse=True)


def _mask_value(key: str, value) -> str:
    v = "" if value is None else str(value)
    if key.upper() in SAFE_KEYS:
        return redact(v)
    if SECRET_KEY_RE.search(key):
        return "<berilgan>" if v else ""
    return redact(v)


def _env_keys() -> dict:
    """.env dagi kalitlar: maxfiylari — faqat "berilgan / bo'sh"."""
    from .config import BASE_DIR

    out = {}
    p = BASE_DIR / ".env"
    if not p.exists():
        return {"_fayl": "yo'q"}
    try:
        for raw in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            k, v = line.split("=", 1)
            k = k.strip()
            out[k] = _mask_value(k, v.strip().strip('"').strip("'"))
    except OSError as e:
        out["_xato"] = str(e)
    return out


def _tail(path, limit: int = LOG_TAIL) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                f.readline()                       # yarim qatordan boshlanmasin
            return f.read()
    except OSError:
        return b""


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  (paket baribir yig'ilsin)
        log.warning("Diagnostika: %s", e)
        return default if default is not None else {"xato": f"{e.__class__.__name__}: {str(e)[:200]}"}


def info() -> dict:
    from . import pilot, reports, sync
    from .config import APP_VERSION, BASE_DIR, DATA_DIR
    from .ai.manager import get_manager
    from .telegram_bot import get_bot

    now = time.time()
    out: dict = {"created_at": now, "created": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(now)),
                 "app_version": APP_VERSION, "python": sys.version.split()[0],
                 "os": f"{platform.system()} {platform.release()} ({platform.version()})",
                 "machine": platform.machine(), "base_dir": str(BASE_DIR), "data_dir": str(DATA_DIR)}

    def sysinfo():
        import psutil

        vm = psutil.virtual_memory()
        return {"cpu_count": psutil.cpu_count(), "cpu_pct": psutil.cpu_percent(interval=0.2),
                "ram_total_gb": round(vm.total / 1e9, 1), "ram_pct": vm.percent,
                "boot_time": psutil.boot_time(), "proc_rss_mb": round(psutil.Process().memory_info().rss / 2 ** 20, 1)}
    out["system"] = _safe(sysinfo)

    def disk():
        du = shutil.disk_usage(DATA_DIR)
        return {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                "db_mb": round((DATA_DIR / "hushyor.db").stat().st_size / 2 ** 20, 1)
                if (DATA_DIR / "hushyor.db").exists() else None}
    out["disk"] = _safe(disk)

    def libs():
        res = {}
        for m in ("cv2", "onnxruntime", "numpy", "fastapi", "httpx", "openvino"):
            try:
                mod = __import__(m)
                res[m] = getattr(mod, "__version__", "?")
            except Exception:  # noqa: BLE001
                res[m] = None
        try:
            import onnxruntime as ort

            res["ort_providers"] = ort.get_available_providers()
        except Exception:  # noqa: BLE001
            pass
        return res
    out["libs"] = _safe(libs)
    private = _private_values()
    out["settings"] = _safe(lambda: {k: redact(_mask_value(k, v), private) if k != "pilot_poll_state" else "<yashirilgan>"
                                     for k, v in sorted(db.get_settings().items())})
    out["env"] = _safe(_env_keys)
    out["cameras"] = _safe(lambda: [{k: (redact(str(v)) if isinstance(v, str) else v) for k, v in s.items()}
                                    for s in get_manager().source_info()], [])
    out["camera_health"] = _safe(lambda: {str(k): v for k, v in get_manager().health.items()}, {})
    out["zones"] = _safe(lambda: db.rows("SELECT source_id, kind, COUNT(*) AS n FROM zones WHERE enabled=1 "
                                         "GROUP BY source_id, kind"), [])
    out["telegram"] = _safe(lambda: {**{k: v for k, v in (get_bot().info() or {}).items() if k != "username"},
                                     "subscribers": db.scalar("SELECT COUNT(*) FROM subscribers WHERE active=1")})
    out["sync"] = _safe(lambda: {k: (redact(str(v)) if isinstance(v, str) else v)
                                 for k, v in sync.get_worker().status().items()})
    out["readiness"] = _safe(lambda: pilot.readiness(force=True))
    out["gates"] = _safe(lambda: pilot.gates(now), [])

    def last7():
        d = reports.compute("daily", now - 7 * 86400, now, now)
        return {"visitors": d["visitors"].get("in"), "theft": d["theft"], "safety": d["safety"],
                "cameras": d["cameras"], "queues": {k: d["queues"].get(k) for k in ("has", "served", "avg_wait")}}
    out["last_7_days"] = _safe(last7)
    out["events"] = _safe(lambda: {
        "total": db.scalar("SELECT COUNT(*) FROM events"),
        "unreviewed": db.scalar("SELECT COUNT(*) FROM events WHERE status='yangi'"),
        "last_created": db.scalar("SELECT MAX(created_at) FROM events")})
    out["reports"] = _safe(lambda: db.rows("SELECT id, kind, t0, sent_at, sent_n, error FROM reports "
                                           "ORDER BY id DESC LIMIT 20"), [])
    out["outbox"] = _safe(lambda: sync.pending())
    return out


def build() -> tuple[bytes, str]:
    """ZIP baytlari va fayl nomi."""
    from .config import LOGS_DIR

    now = time.time()
    store = re.sub(r"[^A-Za-z0-9_-]+", "_", db.get_setting("store_name", "") or "dokon").strip("_")[:30] or "dokon"
    name = f"hushyor_diagnostika_{store}_{time.strftime('%Y%m%d_%H%M', time.localtime(now))}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info(), ensure_ascii=False, indent=2, default=str))
        logs = []
        private = _private_values()
        for p in sorted(LOGS_DIR.glob("*.log*")):
            if not p.is_file():
                continue
            raw = _tail(p)
            z.writestr(f"logs/{p.name}", redact(raw.decode("utf-8", errors="replace"), private))
            logs.append(p.name)
        z.writestr("README.txt",
                   "Hushyor AI diagnostika paketi\n"
                   f"Yaratildi: {time.strftime('%d.%m.%Y %H:%M', time.localtime(now))}\n\n"
                   "info.json - versiyalar, tizim, kameralar, sozlamalar (maxfiy qiymatlar yashirilgan), tayyorlik va\n"
                   "darvozalar holati; logs/ - jurnallar (har biri oxirgi 2 MB; tokenlar, parollar, obunachilar\n"
                   "chat raqami va ismi, telefonlar yashirilgan). Rasm, klip va obunachilar ro'yxati kirmaydi.\n"
                   f"Jurnallar: {', '.join(logs) or 'yo`q'}\n")
    data = buf.getvalue()
    log.info("Diagnostika paketi: %s (%.1f MB)", name, len(data) / 1e6)
    return data, name

