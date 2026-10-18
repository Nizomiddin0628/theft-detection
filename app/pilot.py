"""9-bosqich (2.5.0): pilotga tayyorgarlik.

  * Tayyorlik ro'yxati — do'konga o'rnatilgach nima tayyor, nima yo'q (parol, Telegram, kameralar, zonalar, ish vaqti,
    avtomatik ishga tushish, kompyuter o'chib qolishi, disk, saqlash muddati, hisobotlar, bulut). Har band — holat
    (ok / warn / bad), izoh va tuzatish sahifasi. Bulutga hello bilan qisqasi ketadi (summary()).
  * Darvozalar jadvali — 3–8-bosqich darvozalari bitta joyda (har biri o'z modulidan: count_eval, queue_web, flow_web,
    safety_web, theft_eval, report_eval) va 9-bosqich — pilot darvozasi.
  * Pilot — boshlangan vaqt (settings.pilot_since; ixtiyoriy tugash pilot_until), kalendar haftalari bo'yicha
    ko'rsatkichlar, egasining haftalik bahosi (Telegram: dushanba, haftalik hisobotdan keyin "1–5"), PDF hisobot.

Pilot darvozasi (reja: "1–3 do'kon, 4–6 hafta"): kamida PILOT_DAYS kun va butun pilot davomida — dastur ish vaqtining
≥ 98% ida ishlagan, kameralar ≥ 95%, o'g'irlik xato signali har kamerada ≤ 3 / kun, javobsiz signallar ≤ 10%, kunlik
hisobot kunlarning ≥ 90% ida o'z vaqtida, egasining o'rtacha bahosi ≥ 4 (kamida 3 ta baho).
"""
from __future__ import annotations

import json
import math
import platform
import shutil
import statistics
import subprocess
import threading
import time

from . import db
from .logutil import get_logger

log = get_logger("pilot")

PILOT_DAYS = 28
UPTIME_MIN = 0.98
CAM_UP_MIN = 0.95
FP_MAX_DAY = 3.0
UNREV_MAX = 0.10
REPORT_MIN = 0.90
RATING_MIN, RATING_N = 4.0, 3
ON_TIME_SEC = 1800
POLL_DELAY_SEC = 600          # haftalik baho so'rovi: report_time + 10 daqiqa (haftalik hisobotdan keyin)
DISK_BAD_GB, DISK_WARN_GB = 5.0, 20.0
APP_OFF_WARN_MIN, APP_OFF_BAD_MIN = 60, 8 * 60     # oxirgi 7 kun ish vaqtida
READY_TTL = 600.0
CALC_TTL = 60.0               # pilot darvozasi / haftalar — sahifa bir vaqtda bir necha so'rov yuborganda qayta hisoblanmasin
RETENTION_PILOT_DAYS = 60     # pilot davomida hodisalar shundan kam saqlansa — haftalik raqamlar kamayib ketadi
POLL_TRIES = 3                # haftalik so'rov: yetmagan chatlarga shuncha urinish (10 daqiqada bir)
START_MAX_AGE_DAYS = 400
WEEKDAYS = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")

_ready_cache: dict = {"t": 0.0, "data": None}
_ready_lock = threading.Lock()
_calc_cache: dict = {}
_calc_lock = threading.Lock()
_task_cache: dict = {"t": 0.0, "v": None}


# ============================================================================= yordamchilar
def _f(v, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def since_until() -> tuple[float | None, float | None]:
    s = db.get_settings()
    t0 = _f(s.get("pilot_since"))
    t1 = _f(s.get("pilot_until"))
    return (t0 if t0 and t0 > 0 else None), (t1 if t1 and t1 > 0 else None)


def day_start(t: float) -> float:
    from .reports import day_start as ds

    return ds(t)


def week_start(t: float) -> float:
    from .reports import week_start as ws

    return ws(t)


def autostart_task() -> bool | None:
    """Windows: "HushyorAI" rejalashtirilgan vazifasi bormi (install_windows.bat). Boshqa tizim — None. 10 daqiqa kesh."""
    if platform.system() != "Windows":
        return None
    now = time.time()
    if now - _task_cache["t"] < READY_TTL and _task_cache["v"] is not None:
        return _task_cache["v"]
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", "HushyorAI"], capture_output=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        v = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        v = None
    _task_cache.update(t=now, v=v)
    return v


# ============================================================================= tayyorlik ro'yxati
def _items() -> list[dict]:
    from . import reports, sync
    from .ai.counting import LINE_KIND
    from .config import ADMIN_PASSWORD, DATA_DIR
    from .ai.manager import get_manager
    from .telegram_bot import get_bot

    out: list[dict] = []
    s = db.get_settings()

    def add(key: str, title: str, state: str, detail: str, link: str | None = None, optional: bool = False) -> None:
        out.append({"key": key, "title": title, "state": state, "detail": detail, "link": link, "optional": optional})

    # 1. Panel paroli
    if ADMIN_PASSWORD == "admin123":
        add("password", "Panel paroli", "bad", "Standart parol (admin123). .env faylida ADMIN_PASSWORD= ni o'zgartiring va "
                                               "dasturni qayta ishga tushiring.")
    else:
        add("password", "Panel paroli", "ok", "Standart emas")
    # 2. Do'kon nomi
    store = (s.get("store_name") or "").strip()
    add("store", "Do'kon nomi", "ok" if store else "warn",
        store or "Kiritilmagan — Telegram xabarlari va hisobotlarda ko'rinadi", "/settings")
    # 3. Telegram
    bot = get_bot()
    subs = int(db.scalar("SELECT COUNT(*) FROM subscribers WHERE active=1") or 0)
    if not getattr(bot, "token", ""):
        add("telegram", "Telegram bot", "bad", ".env da TELEGRAM_BOT_TOKEN berilmagan — signallar hech kimga bormaydi",
            "/telegram")
    else:
        st = (bot.info() or {}).get("status") or ""
        if subs == 0:
            add("telegram", "Telegram bot", "bad", "Bot ulangan, lekin hech kim obuna bo'lmagan (ulanish kodini yuboring)",
                "/telegram")
        elif st and st not in ("ishlayapti", "ulangan"):
            add("telegram", "Telegram bot", "warn", f"Holat: {st}; obunachilar: {subs}", "/telegram")
        else:
            add("telegram", "Telegram bot", "ok", f"Obunachilar: {subs}", "/telegram")
    # 4. Kameralar
    srcs = [x for x in db.rows("SELECT id, name, kind, enabled FROM sources ORDER BY id")
            if x["enabled"] and x["kind"] != "file"]
    if not srcs:
        add("cameras", "Kameralar", "bad", "Hech qanday kamera qo'shilmagan (yoki hammasi o'chirilgan)", "/sources")
    else:
        try:
            info = {x["id"]: x for x in get_manager().source_info()}
        except Exception:  # noqa: BLE001
            info = {}
        # Ishlayapti = oqim ochiq va kadr kelyapti (ishga tushayotgan / qayta ulanayotgan / xato — yo'q)
        down = [x["name"] for x in srcs if not ((info.get(x["id"]) or {}).get("running")
                                                and (info.get(x["id"]) or {}).get("state") == "ishlayapti")]
        if down:
            add("cameras", "Kameralar", "bad", f"{len(srcs)} tadan {len(down)} tasi ishlamayapti: " + ", ".join(down[:5]),
                "/sources")
        else:
            add("cameras", "Kameralar", "ok", f"{len(srcs)} ta kamera ishlayapti", "/sources")
    # 5. Zonalar (kassa/xodim joyi — xato signal kamayadi)
    if srcs:
        nz = {r["source_id"]: r["n"] for r in db.rows("SELECT source_id, COUNT(*) AS n FROM zones WHERE enabled=1 "
                                                       "GROUP BY source_id")}
        bare = [x["name"] for x in srcs if not nz.get(x["id"])]
        add("zones", "Zonalar", "warn" if bare else "ok",
            ("Zonasiz kamera: " + ", ".join(bare[:5]) + " — kassa va xodimlar joyini belgilang (xato signallar kamayadi)")
            if bare else "Har kamerada zona bor", "/sources")
    # 6. Sanash chizig'i (ixtiyoriy)
    has_line = bool(db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (LINE_KIND,)))
    add("line", "Sanash chizig'i", "ok" if has_line else "warn",
        "Bor" if has_line else "Yo'q — tashriflar hisoblanmaydi (Kameralar → Zonalar → \"Sanash chizig'i\")",
        "/sources", optional=True)
    # 7. Ish vaqti
    wh = (s.get("work_hours") or "").strip()
    add("work_hours", "Ish vaqti", "ok" if wh else "warn",
        wh or "Kiritilmagan — hisobot va kamera nazorati kecha-kunduz deb hisoblanadi, tungi kirish ishlamaydi",
        "/visits")
    # 8. Avtomatik ishga tushish
    task = autostart_task()
    if task is True:
        add("autostart", "Avtomatik ishga tushish", "ok", "HushyorAI vazifasi o'rnatilgan")
    elif task is False:
        add("autostart", "Avtomatik ishga tushish", "bad",
            "O'rnatilmagan — kompyuter qayta yoqilsa dastur ishlamaydi. install_windows.bat ni yurgizing")
    else:
        add("autostart", "Avtomatik ishga tushish", "warn", "Faqat Windows'da tekshiriladi", optional=True)
    # 9. Kompyuter o'chib / uxlab qolmayaptimi (oxirgi 7 kun, ish vaqtida)
    now = time.time()
    try:
        cams = reports.cameras(now - 7 * 86400, now, now)
        off = int(cams.get("app_off_min") or 0)
        if not cams.get("work_min"):
            add("app_off", "Dastur uzluksiz ishlashi", "warn", "Hali ma'lumot yo'q", optional=True)
        else:
            stt = "bad" if off >= APP_OFF_BAD_MIN else ("warn" if off >= APP_OFF_WARN_MIN else "ok")
            add("app_off", "Dastur uzluksiz ishlashi", stt,
                f"Oxirgi 7 kunda ish vaqtida o'chiq: {reports.fmt_dur(off * 60)}"
                + (" — uyqu rejimini o'chiring, quvvat va avtomatik ishga tushishni tekshiring" if stt != "ok" else ""))
    except Exception:  # noqa: BLE001
        log.exception("Tayyorlik: dastur ishlashi")
    # 10. Disk
    try:
        free = shutil.disk_usage(DATA_DIR).free / 1e9
        stt = "bad" if free < DISK_BAD_GB else ("warn" if free < DISK_WARN_GB else "ok")
        add("disk", "Diskda bo'sh joy", stt, f"{free:.1f} GB" + (" — kliplar uchun joy kam" if stt != "ok" else ""),
            "/settings")
    except OSError:
        pass
    # 11. Saqlash muddati (darvozalar 14 kungacha, pilot 4–6 hafta ma'lumot talab qiladi)
    ret = _f(s.get("retention_days"), 30) or 0
    p_on = since_until()[0] is not None and since_until()[1] is None
    need = RETENTION_PILOT_DAYS if p_on else 30
    stt = "ok" if ret == 0 or ret >= need else ("bad" if p_on else "warn")
    add("retention", "Hodisalarni saqlash muddati", stt,
        ("cheksiz" if ret == 0 else f"{int(ret)} kun") + ("" if stt == "ok" else
                                                         (f" — pilot davomida kamida {need} kun bo'lsin (eski hodisalar "
                                                          f"o'chsa haftalik raqamlar kamayadi)" if p_on else
                                                          " — pilot va darvozalar uchun kamida 30 kun tavsiya etiladi")),
        "/settings")
    # 12. Hisobotlar
    rep_subs = int(db.scalar("SELECT COUNT(*) FROM subscribers WHERE active=1 AND COALESCE(reports, 1)=1") or 0)
    on = str(s.get("report_daily", "1")) in ("1", "true", "on")
    add("reports", "Kunlik hisobot", "ok" if on and rep_subs else "warn",
        (f"Har kuni {s.get('report_time') or '09:00'} da, {rep_subs} ta chatga") if on and rep_subs else
        ("O'chirilgan" if not on else "Hisobot oladigan obunachi yo'q"), "/reports")
    # 13. Bulut (ixtiyoriy)
    if not sync.configured():
        add("cloud", "Bulut", "warn", "Ulanmagan (ixtiyoriy: bir nechta filial va masofadan ko'rish uchun)", optional=True)
    else:
        try:
            w = sync.get_worker().status()
        except Exception:  # noqa: BLE001
            w = {}
        last = _f(w.get("last_ok"), 0) or 0
        if last and now - last < 900:
            skew = abs(_f(w.get("clock_skew"), 0) or 0)
            add("cloud", "Bulut", "warn" if skew > 120 else "ok",
                f"Aloqa bor; navbatda {((w.get('pending') or {}).get('total') or 0)} ta yozuv"
                + (f"; soat farqi {int(skew)} s — kompyuter vaqtini to'g'rilang" if skew > 120 else ""))
        else:
            add("cloud", "Bulut", "bad", f"Aloqa yo'q: {w.get('last_error') or w.get('state') or 'noma`lum'}")
    return out


def readiness(force: bool = False) -> dict:
    """Tayyorlik ro'yxati (10 daqiqa kesh — bulutga har hello'da ketadi)."""
    with _ready_lock:
        if not force and _ready_cache["data"] is not None and time.time() - _ready_cache["t"] < READY_TTL:
            return _ready_cache["data"]
        try:
            items = _items()
        except Exception as e:  # noqa: BLE001  (xato bo'lsa ham keshlanadi — har hello'da qayta urinmasin)
            log.exception("Tayyorlik ro'yxati")
            items = [{"key": "error", "title": "Tayyorlik ro'yxati", "state": "warn", "optional": False, "link": None,
                      "detail": f"Tekshirib bo'lmadi: {e.__class__.__name__}"}]
        need = [x for x in items if not x["optional"]]
        data = {"items": items, "ok": sum(1 for x in need if x["state"] == "ok"), "total": len(need),
                "bad": [x["title"] for x in items if x["state"] == "bad"],
                "warn": [x["title"] for x in items if x["state"] == "warn"],
                "ready": not any(x["state"] == "bad" for x in items), "at": time.time()}
        _ready_cache.update(t=time.time(), data=data)
        return data


# ============================================================================= darvozalar jadvali
def _count_gate() -> dict:
    from . import count_eval as ce
    from . import visits

    sg = visits.sessions_list()["gate"]
    spec = db.get_setting("work_hours", "") or ""
    now = time.time()
    today = day_start(now)
    days = []
    for i in range(0, 15):
        t0, t1 = ce.business_day(day_start(today - i * 86400 + 43200), spec)
        if t1 > now:
            continue
        if len(days) >= 14:
            break
        r = db.row("SELECT SUM(dir='in' AND staff=0) AS ain, SUM(dir='out' AND staff=0) AS aout FROM counts "
                   "WHERE t>=? AND t<?", (t0, t1)) or {}
        days.append({"day": t0, "in": int(r.get("ain") or 0), "out": int(r.get("aout") or 0)})
    dg = ce.drift_gate(days)
    ok = sg["ok"] and dg["ok"]
    bad = (sg["enough"] and not sg["ok"]) or (dg["enough"] and not dg["ok"])
    det = (f"Qo'lda sanash: {sg['last_passed']}/{ce.GATE_SESSIONS} o'tdi; kunlik og'ish: "
           + (f"{dg['median'] * 100:.1f}% ({dg['days']} kun)" if dg["median"] is not None else "ma'lumot yo'q"))
    return {"state": "otdi" if ok else ("otmadi" if bad else "yetarli emas"), "detail": det}


def _queue_gate() -> dict:
    from . import queue_web
    from .ai import queues

    if not queues.has_queues():
        return {"state": "qo'llanmaydi", "detail": "Kassa navbati zonasi yo'q"}
    g = queue_web.gate()
    s, t = g["samples_gate"], g["timings_gate"]
    ok = s["ok"] and t["ok"]
    bad = (s["enough"] and not s["ok"]) or (t["enough"] and not t["ok"])
    det = (f"Namunalar: {s['n']}" + (f", ±1 aniqlik {s['accuracy'] * 100:.0f}%" if s["accuracy"] is not None else "")
           + f"; sekundomer: {t['n']}" + (f", xato {t['mean_err'] * 100:.0f}%" if t["mean_err"] is not None else ""))
    return {"state": "otdi" if ok else ("otmadi" if bad else "yetarli emas"), "detail": det}


def _flow_gate() -> dict:
    from . import flow_web
    from .ai import flow

    if not flow.has_plans():
        return {"state": "qo'llanmaydi", "detail": "Do'kon rejasi yo'q"}
    g = flow_web._gate()
    vg = g["visits_gate"]
    bad = any(c["enough"] and not c["ok"] for c in g["cams"]) or (vg["enough"] and not vg["ok"])
    det = f"Nuqta sinovi: {sum(1 for c in g['cams'] if c['ok'])}/{len(g['cams'])} kamera; qo'lda sanash: {vg['last_passed']}"
    return {"state": "otdi" if g["ok"] else ("otmadi" if bad else "yetarli emas"), "detail": det}


def _safety_gate() -> dict:
    from . import safety_web

    g = safety_web.gate_data()
    r, f = g["recall"], g["fp"]
    det = ((f"Yiqilish topish: {r['recall'] * 100:.0f}%" if r.get("recall") is not None else "Yiqilish videolari yo'q")
           + f"; jonli kameralar: {sum(1 for c in f.get('cams', []) if c.get('enough'))} tasida yetarli kun")
    return {"state": g["state"], "detail": det}


def _theft_gate() -> dict:
    from . import theft_eval
    from .web import theft_live_cams

    cams, _t0 = theft_live_cams()
    g = theft_eval.gate(cams)
    rates = [c["rate"] for c in g["cams"] if c["rate"] is not None]
    det = (f"Eng ko'p xato: {max(rates):.1f} / kamera / kun" if rates else "Jonli ma'lumot yo'q") + \
        f"; {g['ready']} ta kamerada yetarli kun"
    return {"state": g["state"], "detail": det}


def _report_gate() -> dict:
    from . import report_eval

    g = report_eval.gate(lambda sql, p=(): db.rows(sql, p), _f(db.get_setting("report_since", "0"), 0) or 0)
    det = f"O'z vaqtida: {g['on_time']}/{len(g['days'])} kun; foydali tavsiyalar: {g['useful']}"
    return {"state": g["state"], "detail": det}


GATES = [
    (3, "Odamlarni sanash", "/visits", _count_gate),
    (4, "Kassa navbati", "/navbat", _queue_gate),
    (5, "Oqim va heatmap", "/oqim", _flow_gate),
    (6, "Xavfsizlik", "/xavfsizlik", _safety_gate),
    (7, "O'g'irlik signallari", "/eval", _theft_gate),
    (8, "Hisobotlar", "/reports", _report_gate),
]


def gates(now: float | None = None) -> list[dict]:
    out = []
    for stage, name, link, fn in GATES:
        try:
            g = fn()
        except Exception as e:  # noqa: BLE001  (bitta darvoza xatosi jadvalni buzmasin)
            log.exception("Darvoza %s", stage)
            g = {"state": "xato", "detail": f"{e.__class__.__name__}: {str(e)[:120]}"}
        out.append({"stage": stage, "name": name, "link": link, **g})
    try:
        pg = pilot_gate(now)
    except Exception as e:  # noqa: BLE001
        log.exception("Pilot darvozasi")
        pg = {"state": "xato", "detail": f"{e.__class__.__name__}: {str(e)[:120]}"}
    out.append({"stage": 9, "name": "Pilot", "link": "/pilot", "state": pg["state"], "detail": pg["detail"]})
    return out


# ============================================================================= pilot ko'rsatkichlari
def _window_stats(t0: float, t1: float, now: float) -> dict:
    """Bitta oyna: dastur va kameralar ishlashi, signallar, javob vaqti, tashriflar, navbat, hisobotlar."""
    from . import reports
    from .ai.safety import SAFETY_KINDS

    end = min(t1, now)
    cams = reports.cameras(t0, end, now)
    work = int(cams.get("work_min") or 0)
    off = int(cams.get("app_off_min") or 0)
    on_sum = sum(int(c.get("on_min") or 0) for c in cams.get("cams", []))
    down_sum = sum(int(c.get("down_min") or 0) for c in cams.get("cams", []))
    sig = reports.signals(t0, end)
    th = sig["theft"]
    marks = ",".join("?" for _ in SAFETY_KINDS)
    rv = [float(r["d"]) for r in db.rows(
        "SELECT reviewed_at - created_at AS d FROM events WHERE job_id IS NULL AND created_at >= ? AND created_at < ? "
        "AND reviewed_at IS NOT NULL AND status IN ('tasdiqlangan', 'rad_etilgan') AND reviewed_at >= created_at",
        (t0, end))]
    safety_n = int(db.scalar(f"SELECT COUNT(*) FROM events WHERE job_id IS NULL AND created_at >= ? AND created_at < ? "
                             f"AND kind IN ({marks})", (t0, end, *SAFETY_KINDS)) or 0)
    out = {"t0": t0, "t1": t1, "partial": t1 > now, "work_min": work, "app_off_min": off,
           "cam_on_min": on_sum, "cam_down_min": down_sum,
           "uptime": round(1 - off / work, 4) if work else None,
           "cam_up": round(1 - down_sum / on_sum, 4) if on_sum else None,
           "theft": {k: th[k] for k in ("total", "confirmed", "rejected", "unreviewed")},
           "safety": safety_n,
           "react_med": round(statistics.median(rv)) if rv else None,
           "visitors": None, "avg_wait": None}
    try:
        from .ai import counting

        if db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (counting.LINE_KIND,)):
            out["visitors"] = int(counting.summary(t0, end)["in"])
    except Exception:  # noqa: BLE001
        log.exception("Pilot: tashriflar")
    try:
        from .ai import queues

        if queues.has_queues():
            out["avg_wait"] = queues.summary(t0, end).get("avg_wait")
    except Exception:  # noqa: BLE001
        log.exception("Pilot: navbat")
    days, on_time = _report_days(t0, t1, now)
    out["report_days"], out["report_on_time"] = days, on_time
    return out


def _report_days(t0: float, t1: float, now: float) -> tuple[int, int]:
    """Oynadagi tugagan kunlar soni va kunlik hisoboti rejalashtiruvchi tomonidan o'z vaqtida (≤ 30 daqiqa) yetgan kunlar.
    Kunlik hisobot ertasi kuni keladi: kun "tugagan" — ertasi kungi rejadagi vaqt (+30 daq) o'tgan bo'lsa."""
    from .reports import scheduled_due

    days, ok = 0, 0
    d = day_start(t0)                        # pilot boshlangan kun ham (uning hisoboti ertasi kuni keladi)
    reps = {round(r["t0"]): r for r in db.rows("SELECT t0, sent_at, due_at FROM reports WHERE kind='daily' AND t0 >= ? "
                                               "AND t0 < ?", (d - 1, t1))}
    while d < t1 - 1:
        d1 = day_start(d + 36 * 3600)
        if scheduled_due("daily", d1) + ON_TIME_SEC > now:
            break
        days += 1
        r = reps.get(round(d))
        if r and r["sent_at"] and float(r["sent_at"]) - float(r["due_at"] or r["sent_at"]) <= ON_TIME_SEC:
            ok += 1
        d = d1
    return days, ok


def _cached(key: tuple, fn):
    now = time.time()
    with _calc_lock:
        hit = _calc_cache.get(key)
        if hit and now - hit[0] < CALC_TTL:
            return hit[1]
    v = fn()
    with _calc_lock:
        if len(_calc_cache) > 32:
            _calc_cache.clear()
        _calc_cache[key] = (now, v)
    return v


def _week_done(b: float, now: float) -> bool:
    """Hafta (oyna oxiri b) yopildimi: oxirgi kunining kunlik hisoboti rejadagi vaqti (+30 daq) o'tgan."""
    from .reports import scheduled_due

    return now >= scheduled_due("daily", day_start(b - 1 + 43200)) + ON_TIME_SEC


def weeks(now: float | None = None) -> list[dict]:
    """Pilot kalendar haftalari (dushanba–yakshanba; birinchisi pilot boshlangan paytdan). Yopilgan hafta raqamlari
    pilot_weeks jadvalida saqlanadi: keyin eski hodisalar o'chirilsa ham (saqlash muddati) o'zgarmaydi va qayta
    hisoblanmaydi. Baho — har safar jonli."""
    explicit = now is not None
    now = time.time() if now is None else now
    t_start, t_end = since_until()
    if not t_start:
        return []
    if explicit:
        return _weeks(now, t_start, t_end)
    return _cached(("weeks", t_start, t_end), lambda: _weeks(now, t_start, t_end))


def _weeks(now: float, t_start: float, t_end: float | None) -> list[dict]:
    end = min(now, t_end) if t_end else now
    arch = _archived(t_start, end)                   # o'chirilgan hodisalar arxivi — bir marta o'qiladi
    out = []
    w = week_start(t_start)
    while w < end:
        w1 = day_start(w + 7 * 86400 + 43200)
        a, b = max(w, t_start), min(w1, t_end or w1)
        st = None
        done = b <= end and _week_done(b, now)
        if done:
            row = db.row("SELECT data FROM pilot_weeks WHERE week_t0=? AND t0=? AND t1=?", (w, a, b))
            if row:
                try:
                    st = json.loads(row["data"])
                except ValueError:
                    st = None
        if st is None:
            st = _window_stats(a, b, end)
            if done and abs(now - time.time()) < 300:   # "hozir" bo'yicha yopilgan hafta (sinovdagi now — saqlanmaydi)
                try:
                    db.execute("INSERT OR REPLACE INTO pilot_weeks(week_t0, t0, t1, data, t) VALUES(?,?,?,?,?)",
                               (w, a, b, json.dumps(st), time.time()))
                except Exception as e:  # noqa: BLE001  (faqat o'qiladigan baza — tests/pilot_gate.py)
                    log.debug("Pilot haftasi saqlanmadi: %s", e)
        st.update(_live_signals(a, min(b, end), arch))   # ✅/❌ keyin ham berilishi mumkin — har safar jonli (arxiv bilan)
        st.update({"week": w, "label": f"{time.strftime('%d.%m', time.localtime(a))}–"
                                       f"{time.strftime('%d.%m', time.localtime(min(b, end) - 1))}"})
        st["rating"], st["ratings"] = _rating(w)
        out.append(st)
        w = w1
    return out


def _archived(t0: float, t1: float) -> list[tuple[float, str]]:
    """O'chirilgan o'g'irlik hodisalari (data/feedback/ev*.json, bazada yo'q): [(created_at, holat kaliti)] — 7-bosqich
    arxivi bilan bir xil qoidalar (theft_eval.add_archived)."""
    from .ai.safety import SAFETY_KINDS
    from .config import FEEDBACK_DIR

    out: list[tuple[float, str]] = []
    if not FEEDBACK_DIR.is_dir():
        return out
    live = {int(r["id"]) for r in db.rows("SELECT id FROM events WHERE created_at >= ?", (t0,))}
    key = {"rad_etilgan": "rejected", "tasdiqlangan": "confirmed", "yangi": "unreviewed"}
    for f in FEEDBACK_DIR.glob("ev*.json"):
        try:
            if int(f.stem[2:]) in live or f.stat().st_mtime < t0:
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        ca = _f(d.get("created_at"), 0) or 0
        if d.get("job_id") is not None or d.get("kind") in SAFETY_KINDS or not t0 <= ca < t1 or d.get("status") not in key:
            continue
        if d.get("source_id") is None and not d.get("source_name"):
            continue
        out.append((ca, key[d["status"]]))
    return out


def _live_signals(t0: float, t1: float, arch: list[tuple[float, str]]) -> dict:
    """Haftadagi o'g'irlik signallari (o'chirilganlari arxivdan) va javob vaqti — keyingi ✅/❌ ham hisobga olinadi."""
    from .ai.safety import SAFETY_KINDS

    marks = ",".join("?" for _ in SAFETY_KINDS)
    th = {"confirmed": 0, "rejected": 0, "unreviewed": 0}
    for r in db.rows(f"SELECT status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND source_id IS NOT NULL AND "
                     f"(kind IS NULL OR kind NOT IN ({marks})) AND created_at >= ? AND created_at < ? GROUP BY status",
                     (*SAFETY_KINDS, t0, t1)):
        th[{"tasdiqlangan": "confirmed", "rad_etilgan": "rejected"}.get(r["status"], "unreviewed")] += int(r["n"])
    for ca, k in arch:
        if t0 <= ca < t1:
            th[k] += 1
    th["total"] = th["confirmed"] + th["rejected"] + th["unreviewed"]
    rv = [float(r["d"]) for r in db.rows(
        "SELECT reviewed_at - created_at AS d FROM events WHERE job_id IS NULL AND created_at >= ? AND created_at < ? "
        "AND reviewed_at IS NOT NULL AND status IN ('tasdiqlangan', 'rad_etilgan') AND reviewed_at >= created_at",
        (t0, t1))]
    return {"theft": th, "react_med": round(statistics.median(rv)) if rv else None}


def _rating(week: float) -> tuple[float | None, int]:
    r = db.rows("SELECT rating FROM pilot_feedback WHERE week_t0=?", (week,))
    vals = [int(x["rating"]) for x in r if x["rating"]]
    return (round(sum(vals) / len(vals), 2) if vals else None), len(vals)


def valid_week(week, now: float | None = None) -> bool:
    """Baho uchun hafta: chekli son, dushanba yarim tuni, pilot haftalaridan biri (boshidan — hozirgacha / tugashigacha)."""
    try:
        w = float(week)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(w) or w <= 0 or w > 4e9 or abs(week_start(w + 43200) - w) > 1:
        return False
    now = time.time() if now is None else now
    t_start, t_end = since_until()
    if not t_start:
        return False
    end = min(now, t_end) if t_end else now
    return week_start(t_start) <= w <= end


def pilot_gate(now: float | None = None) -> dict:
    explicit = now is not None
    t_start, t_end = since_until()
    if not t_start:
        return {"state": "boshlanmagan", "detail": "Pilot boshlanmagan (Pilot sahifasi → \"Pilotni boshlash\")",
                "days": 0, "need_days": PILOT_DAYS, "checks": []}
    if explicit:
        return _pilot_gate(now, t_start, t_end, False)
    return _cached(("gate", t_start, t_end), lambda: _pilot_gate(time.time(), t_start, t_end, True))


def _pilot_gate(now: float, t_start: float, t_end: float | None, cached: bool) -> dict:
    from . import theft_eval
    from .web import theft_live_cams

    end = min(now, t_end) if t_end else now
    days = max(0, int((day_start(end) - day_start(t_start)) // 86400))
    ws = weeks() if cached else _weeks(now, t_start, t_end)
    work = sum(w["work_min"] for w in ws)
    off = sum(w["app_off_min"] for w in ws)
    on = sum(w.get("cam_on_min", 0) for w in ws)
    down = sum(w.get("cam_down_min", 0) for w in ws)
    st = {"uptime": round(1 - off / work, 4) if work else None, "cam_up": round(1 - down / on, 4) if on else None,
          "report_days": sum(w["report_days"] for w in ws), "report_on_time": sum(w["report_on_time"] for w in ws)}
    # O'g'irlik: 7-bosqich bilan bir xil manba (o'chirilgan hodisalar arxivi ham), pilot oynasida
    cams, _ = theft_live_cams(t_start, t_end)
    tg = theft_eval.gate(cams)
    rates = [c["rate"] for c in tg["cams"] if c["rate"] is not None and c["enough"]]     # ≥ 7 kun ishlagan kameralar
    failed = [c for c in tg["cams"] if c.get("failed")]      # 7 kun to'lmasdan chegaradan (21 ta ❌) oshgan ham
    th = {k: sum(int(c.get(k) or 0) for c in tg["cams"]) for k in ("rejected", "confirmed", "unreviewed")}
    th["total"] = th["rejected"] + th["confirmed"] + th["unreviewed"]
    st["theft"] = th
    unrev = th["unreviewed"] / th["total"] if th["total"] else 0.0
    rep = st["report_on_time"] / st["report_days"] if st["report_days"] else None
    fb = [int(x["rating"]) for x in db.rows("SELECT rating FROM pilot_feedback WHERE week_t0 >= ? AND week_t0 <= ? "
                                             "AND rating BETWEEN 1 AND 5", (week_start(t_start), end)) if x["rating"]]
    avg = round(sum(fb) / len(fb), 2) if fb else None

    def chk(key, title, value, ok, need, fmt):
        return {"key": key, "title": title, "value": value, "ok": ok, "need": need, "text": fmt}
    checks = [
        chk("days", "Pilot davomiyligi", days, days >= PILOT_DAYS, f"≥ {PILOT_DAYS} kun", f"{days} kun"),
        chk("uptime", "Dastur ish vaqtida ishladi", st["uptime"], st["uptime"] is not None and st["uptime"] >= UPTIME_MIN,
            f"≥ {UPTIME_MIN * 100:.0f}%", "—" if st["uptime"] is None else f"{st['uptime'] * 100:.1f}%"),
        chk("cam_up", "Kameralar ishladi", st["cam_up"], st["cam_up"] is not None and st["cam_up"] >= CAM_UP_MIN,
            f"≥ {CAM_UP_MIN * 100:.0f}%", "—" if st["cam_up"] is None else f"{st['cam_up'] * 100:.1f}%"),
        chk("false", "Xato signal (eng ko'p bergan kamera, ≥ 7 kun)", max(rates) if rates else None,
            bool(rates) and max(rates) <= FP_MAX_DAY and not failed, f"≤ {FP_MAX_DAY:g} / kamera / kun",
            (f"{max(rates):.1f} / kun" if rates else "—")
            + (f"; {failed[0]['name']}: {failed[0]['rejected']} ta ❌" if failed else "")),
        chk("unreviewed", "Javobsiz signallar", round(unrev, 3), unrev <= UNREV_MAX, f"≤ {UNREV_MAX * 100:.0f}%",
            f"{unrev * 100:.0f}% ({th['unreviewed']}/{th['total']})"),
        chk("reports", "Kunlik hisobot o'z vaqtida", rep, rep is not None and rep >= REPORT_MIN,
            f"≥ {REPORT_MIN * 100:.0f}% kun", "—" if rep is None else f"{st['report_on_time']}/{st['report_days']} kun"),
        chk("rating", "Egasining bahosi", avg, avg is not None and len(fb) >= RATING_N and avg >= RATING_MIN,
            f"o'rtacha ≥ {RATING_MIN:g} (≥ {RATING_N} ta)", "—" if avg is None else f"{avg:.1f} ({len(fb)} ta)"),
    ]
    enough = days >= PILOT_DAYS
    ok = enough and all(c["ok"] for c in checks)
    state = "otdi" if ok else ("otmadi" if enough else "yetarli emas")
    passed = sum(1 for c in checks if c["ok"])
    detail = f"{days}/{PILOT_DAYS} kun; mezonlar: {passed}/{len(checks)}"
    return {"state": state, "detail": detail, "days": days, "need_days": PILOT_DAYS, "checks": checks,
            "since": t_start, "until": t_end, "stats": st}


def summary() -> dict:
    """Bulutga hello bilan (qisqa): tayyorlik va pilot holati."""
    r = readiness()
    t_start, t_end = since_until()
    out = {"ok": r["ok"], "total": r["total"], "bad": r["bad"][:8], "warn": r["warn"][:8], "at": r["at"]}
    if t_start:
        end = min(time.time(), t_end) if t_end else time.time()
        out["pilot"] = {"since": t_start, "until": t_end,
                        "days": max(0, int((day_start(end) - day_start(t_start)) // 86400))}
    return out


def status_text() -> str:
    """Telegram /pilot."""
    from .reports import esc

    t_start, _ = since_until()
    r = readiness()
    lines = ["🧪 <b>Pilot holati</b>", f"Tayyorlik: {r['ok']}/{r['total']}" + (" ✅" if r["ready"] else "")]
    for t in r["bad"][:5]:
        lines.append(f"  ❌ {esc(t)}")
    for t in r["warn"][:3]:
        lines.append(f"  ⚠️ {esc(t)}")
    if not t_start:
        lines.append("\nPilot hali boshlanmagan (panel → Pilot).")
        return "\n".join(lines)
    g = pilot_gate()
    lines.append(f"\nBoshlangan: {time.strftime('%d.%m.%Y', time.localtime(t_start))}, {g['days']}/{PILOT_DAYS} kun")
    for c in g["checks"][1:]:
        lines.append(f"{'✅' if c['ok'] else '▫️'} {esc(c['title'])}: {esc(c['text'])} (kerak {esc(c['need'])})")
    lines.append({"otdi": "\n🏁 Pilot darvozasi o'tdi", "otmadi": "\n⛔ Pilot darvozasi o'tmadi"}.get(
        g["state"], "\n⏳ Pilot davom etyapti"))
    return "\n".join(lines)


# ============================================================================= egasining bahosi
def save_feedback(week: float, who: str, rating: int | None, comment: str | None = None,
                  chat_id: int | None = None) -> None:
    if rating is not None and not 1 <= int(rating) <= 5:
        raise ValueError("baho 1..5")
    if not valid_week(week):
        raise ValueError("Bu hafta pilotga tegishli emas")
    db.execute("INSERT INTO pilot_feedback(week_t0, who, chat_id, rating, comment, t) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(week_t0, who) DO UPDATE SET rating=COALESCE(excluded.rating, pilot_feedback.rating), "
               "comment=COALESCE(excluded.comment, pilot_feedback.comment), t=excluded.t",
               (week, who[:80], chat_id, None if rating is None else int(rating),
                None if comment is None else str(comment)[:1000], time.time()))
    _reset_caches()                                  # yangi baho sahifada va darvozada darhol ko'rinsin


def poll_week(now: float) -> float | None:
    """Hozir so'ralishi kerak bo'lgan hafta (o'tgan dushanba) yoki None."""
    t_start, t_end = since_until()
    if not t_start or (t_end and t_end <= now - 7 * 86400):
        return None
    lt = time.localtime(now)
    if lt.tm_wday != 0:
        return None
    from .reports import scheduled_due

    d0 = day_start(now)
    if now < scheduled_due("weekly", d0) + POLL_DELAY_SEC:
        return None
    w0 = day_start(d0 - 7 * 86400 + 43200)
    if t_start >= d0 or (t_end and t_end <= w0):
        return None                          # pilot o'tgan haftaga to'g'ri kelmaydi
    if db.get_setting("pilot_poll_week", "") == str(int(w0)):
        return None
    return w0


def _poll_state(w0: float) -> dict:
    try:
        st = json.loads(db.get_setting("pilot_poll_state", "") or "{}")
    except ValueError:
        st = {}
    if not isinstance(st, dict) or st.get("week") != int(w0):
        st = {"week": int(w0), "chats": [], "tries": 0}
    return st


def poll_text(w0: float) -> str:
    from .reports import esc

    store = db.get_setting("store_name", "") or "Do'kon"
    w1 = day_start(w0 + 7 * 86400 + 43200)
    return (f"📝 <b>Pilot: haftalik baho</b> — {esc(store)}\n"
            f"🗓 {time.strftime('%d.%m', time.localtime(w0))}–{time.strftime('%d.%m.%Y', time.localtime(w1 - 1))}\n\n"
            f"Hushyor AI o'tgan hafta do'koningizga qanchalik foydali bo'ldi?\n"
            f"1 — umuman foydasiz, 5 — juda foydali. Izoh bo'lsa — bahodan keyin shu chatga yozing.")


def poll_markup(w0: float) -> dict:
    return {"inline_keyboard": [[{"text": str(n), "callback_data": f"pv:{int(w0)}:{n}"} for n in range(1, 6)]]}


def poll_tick(bot, now: float | None = None) -> int:
    """Rejalashtiruvchidan (reports.Scheduler) har 30 s: dushanba haftalik baho so'rovi. Qaytaradi: nechta chatga."""
    now = time.time() if now is None else now
    w0 = poll_week(now)
    if w0 is None or bot is None or not getattr(bot, "token", ""):
        return 0
    from .reports import subscribers

    subs = subscribers()
    if not subs:
        return 0                                     # obunachi yo'q — hafta yopilmaydi (kun davomida qo'shilsa oladi)
    st = _poll_state(w0)
    pending = [c for c in subs if c not in st["chats"]]
    n = 0
    for c in pending:
        try:
            res = bot.api("sendMessage", {"chat_id": c, "text": poll_text(w0), "parse_mode": "HTML",
                                          "reply_markup": poll_markup(w0)}, chat_id=c)
            if res and res.get("ok"):
                n += 1
                st["chats"].append(c)
        except Exception as e:  # noqa: BLE001
            log.warning("Pilot so'rovi: %s", e.__class__.__name__)
    st["tries"] += 1
    left = len(pending) - n
    done = left == 0 or st["tries"] >= POLL_TRIES
    upd = {"pilot_poll_state": json.dumps(st)}
    if done:
        upd["pilot_poll_week"] = str(int(w0))           # hammaga yetdi yoki urinishlar tugadi — shu hafta yopiladi
    db.set_settings(upd)
    if n:
        log.info("Pilot: haftalik baho so'rovi %d ta chatga%s", n, f" ({left} tasiga yetmadi)" if left else "")
    return n


# ============================================================================= PDF
def pdf(now: float | None = None) -> bytes:
    from . import pdfmini
    from .config import APP_VERSION
    from .reports import fmt_dur

    now = time.time() if now is None else now
    store = db.get_setting("store_name", "") or "Do'kon"
    doc = pdfmini.Doc("Hushyor AI - pilot hisoboti")
    doc.text(f"Pilot hisoboti: {store}", size=16, bold=True)
    t_start, t_end = since_until()
    if t_start:
        doc.text(f"Boshlangan: {time.strftime('%d.%m.%Y', time.localtime(t_start))}"
                 + (f", tugagan: {time.strftime('%d.%m.%Y', time.localtime(t_end))}" if t_end else ", davom etyapti"),
                 size=10, color=(0.45, 0.45, 0.5))
    doc.rule()
    g = pilot_gate(now)
    doc.text("Pilot darvozasi", size=12, bold=True)
    doc.kv("Holat", {"otdi": "O'TDI", "otmadi": "O'TMADI", "yetarli emas": "davom etyapti",
                     "boshlanmagan": "boshlanmagan"}.get(g["state"], g["state"]))
    if g["checks"]:
        doc.table(["Mezon", "Natija", "Kerak", ""],
                  [[c["title"], c["text"], c["need"], "ha" if c["ok"] else "yo'q"] for c in g["checks"]],
                  [190, 120, 140, 40])
    ws = weeks(now)
    if ws:
        doc.space(6)
        doc.text("Haftalar", size=12, bold=True)
        doc.table(["Hafta", "Dastur", "Kameralar", "Signal (T/X/J)", "Javob", "Tashrif", "Hisobot", "Baho"],
                  [[w["label"] + (" *" if w["partial"] else ""),
                    "—" if w["uptime"] is None else f"{w['uptime'] * 100:.1f}%",
                    "—" if w["cam_up"] is None else f"{w['cam_up'] * 100:.1f}%",
                    f"{w['theft']['total']} ({w['theft']['confirmed']}/{w['theft']['rejected']}/{w['theft']['unreviewed']})",
                    fmt_dur(w["react_med"]) if w["react_med"] is not None else "—",
                    "—" if w["visitors"] is None else str(w["visitors"]),
                    f"{w['report_on_time']}/{w['report_days']}",
                    "—" if w["rating"] is None else f"{w['rating']:.1f}"] for w in ws],
                  [70, 55, 60, 95, 55, 55, 55, 45], size=8)
        doc.text("T/X/J — tasdiqlangan / xato / javobsiz; Javob — signalga javob berish vaqti (mediana); "
                 "* — hafta hali tugamagan.", size=8, color=(0.4, 0.4, 0.45))
    doc.space(6)
    doc.text("Darvozalar (3-8-bosqich)", size=12, bold=True)
    doc.table(["Bosqich", "Holat", "Izoh"], [[f"{x['stage']}. {x['name']}", x["state"], x["detail"]]
                                            for x in gates(now) if x["stage"] < 9], [130, 80, 280], size=8)
    r = readiness()
    doc.space(6)
    doc.text(f"Tayyorlik: {r['ok']}/{r['total']}", size=12, bold=True)
    for x in r["items"]:
        mark = {"ok": "+", "warn": "!", "bad": "X"}[x["state"]]
        doc.text(f"[{mark}] {x['title']}: {x['detail']}", indent=8, size=9)
    com = db.rows("SELECT week_t0, who, rating, comment FROM pilot_feedback WHERE comment IS NOT NULL AND comment != '' "
                  "ORDER BY week_t0, t")
    if com:
        doc.space(6)
        doc.text("Egasining izohlari", size=12, bold=True)
        for c in com:
            doc.text(f"{time.strftime('%d.%m', time.localtime(c['week_t0']))} ({c['rating'] or '-'}): {c['comment']}",
                     indent=8, size=9)
    doc.space(8)
    doc.rule()
    doc.text(f"Yaratildi: {time.strftime('%d.%m.%Y %H:%M', time.localtime(now))}, Hushyor AI {APP_VERSION}.",
             size=8, color=(0.4, 0.4, 0.45))
    return doc.render()


def _reset_caches() -> None:
    _ready_cache["t"] = 0.0
    with _calc_lock:
        _calc_cache.clear()


def start(t: float | None = None) -> float:
    t = time.time() if t is None else float(t)
    # pilot_poll_week saqlanadi: qayta boshlansa o'sha haftaning so'rovi takror yuborilmaydi
    db.set_settings({"pilot_since": str(t), "pilot_until": ""})
    db.execute("DELETE FROM pilot_weeks")
    _reset_caches()
    log.info("Pilot boshlandi: %s", time.strftime("%d.%m.%Y %H:%M", time.localtime(t)))
    return t


def stop(t: float | None = None) -> float:
    if since_until()[1]:
        raise ValueError("Pilot allaqachon yakunlangan")
    t = time.time() if t is None else float(t)
    db.set_settings({"pilot_until": str(t)})
    _reset_caches()
    log.info("Pilot yakunlandi: %s", time.strftime("%d.%m.%Y %H:%M", time.localtime(t)))
    return t


def feedback_rows() -> list[dict]:
    return db.rows("SELECT week_t0, who, rating, comment, t FROM pilot_feedback ORDER BY week_t0 DESC, t DESC LIMIT 200")
