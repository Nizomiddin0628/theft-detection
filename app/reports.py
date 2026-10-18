"""Kunlik va haftalik hisobot, tavsiyalar (2.4.0, 8-bosqich).

Har kuni belgilangan vaqtda (sukut 09:00) kechagi kun, har dushanba — o'tgan hafta (dushanba–yakshanba) hisobi:
  * tashriflar (sanash chizig'i) — o'tgan haftaning shu kuni / o'tgan hafta bilan solishtirib, eng gavjum soat;
  * navbat — o'rtacha va 90% kutish, uzun navbat soatlari (har kassa);
  * oqim — eng ko'p kirilgan bo'limlar;
  * o'g'irlik signallari — ✅ / ❌ / javobsiz, kamera bo'yicha; xavfsizlik signallari — tur bo'yicha;
  * kameralar — ish vaqtida necha daqiqa ishlamagani; dastur o'zi o'chiq bo'lgan vaqt.
Tavsiyalar — oddiy, tushunarli qoidalar ("aqlli" emas): har biri raqamga asoslanadi va kalit (key) bilan saqlanadi —
egasi Telegram'da yoki panelda "👍 foydali / 👎 foydasiz" deb belgilaydi (8-bosqich darvozasi: ≥ 3 ta foydali).

Raqamlar paneldagi bilan bir xil manbadan: counting.summary, queues.summary, flow.visits_summary, events jadvali,
metrics_minute. Hisobot yaratilganda ma'lumot reports jadvalida saqlanadi; Telegram'ga yetgandan keyin u qayta
hisoblanmaydi (tarix va tavsiya tugmalari o'zgarmaydi). Yetkazish har chat bo'yicha (report_sends): matn yetgan chat —
yetkazilgan (PDF alohida), qayta urinish faqat yetmagan chatlarga.
"""
from __future__ import annotations

import hashlib
import html
import json
import threading
import time

from . import db
from .logutil import get_logger

log = get_logger("reports")

KINDS = ("daily", "weekly")
LABEL = {"daily": "Kunlik", "weekly": "Haftalik"}
WEEKDAYS = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")
CAM_DOWN_MIN = 30          # ish vaqtida shuncha daqiqadan ko'p ishlamagan kamera — tavsiya
APP_OFF_MIN = 30           # dastur ish vaqtida shuncha daqiqa o'chiq — tavsiya
FALSE_SHARE = 0.6          # xato signal ulushi (≥ FALSE_MIN_REVIEWED javobdan)
FALSE_MIN_REVIEWED = 5
UNREV_MIN, UNREV_SHARE = 3, 0.3
VISIT_BASE = 50            # tashriflar o'zgarishi shundan ko'p bazada
VISIT_DROP, VISIT_RISE = -20.0, 25.0
DRIFT_PCT, DRIFT_BASE = 10.0, 50
LONG_SHARE = 0.25          # soatning shuncha ulushida navbat ≥ queue_max_len — "uzun navbat soati"
LONG_MIN_OBS = 600.0       # ... kamida 10 daqiqa kuzatilgan soat
MAX_RECS = 6
ON_TIME_SEC = 1800         # darvoza: hisobot rejadagi vaqtdan ≤ 30 daqiqa kechikib yetsa — o'z vaqtida
PERM_TRIES = 2             # doimiy xato (bot bloklangan, chat yo'q — 400/403) bo'lgan chatga shuncha urinish; tarmoq /
RETRY_SEC = 600            # Telegram xatosida — o'sha kun oxirigacha 10 daqiqada bir qayta
_gen_lock = threading.Lock()     # generate: rejalashtiruvchi, panel va Telegram tugmasi bir vaqtda
_send_lock = threading.Lock()    # rejadagi / paneldan hammaga yuborish — bir vaqtda bittasi (takror yo'q)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def fmt_dur(sec: float | None) -> str:
    if sec is None:
        return "—"
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} daq {s} s" if s and m < 10 else f"{m} daq"
    h, m = divmod(m, 60)
    return f"{h} soat {m} daq" if m else f"{h} soat"


def day_start(t: float | None = None) -> float:
    lt = time.localtime(time.time() if t is None else t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def week_start(t: float | None = None) -> float:
    d0 = day_start(t)
    return day_start(d0 - time.localtime(d0).tm_wday * 86400 + 43200)


def period(kind: str, now: float | None = None) -> tuple[float, float]:
    """Oxirgi tugagan davr: kunlik — kecha (00:00–24:00), haftalik — o'tgan dushanba–yakshanba."""
    now = time.time() if now is None else now
    if kind == "weekly":
        w1 = week_start(now)
        return day_start(w1 - 7 * 86400 + 43200), w1
    d1 = day_start(now)
    return day_start(d1 - 43200), d1


def prev_period(t0: float, t1: float) -> tuple[float, float]:
    """Solishtirish: bir hafta oldingi xuddi shunday davr (kunlik — o'tgan haftaning shu kuni: do'konda hafta kunlari
    farq qiladi, "kecha" bilan solishtirish dushanbani yakshanba bilan solishtirardi). Tugamagan davr (/hisobot —
    "bugun hozirgacha") — o'sha kunning xuddi shu soatigacha."""
    p0 = day_start(t0 - 7 * 86400 + 43200)
    if abs(day_start(t1) - t1) > 1:
        return p0, p0 + (t1 - t0)
    return p0, day_start(t1 - 7 * 86400 + 43200)


def period_label(kind: str, t0: float, t1: float) -> str:
    if kind == "weekly":
        return f"{time.strftime('%d.%m', time.localtime(t0))}–{time.strftime('%d.%m.%Y', time.localtime(t1 - 1))}"
    lt = time.localtime(t0)
    return f"{time.strftime('%d.%m.%Y', lt)}, {WEEKDAYS[lt.tm_wday].lower()}"


def pct_change(cur: float, prev: float, base: float = 20) -> float | None:
    if prev is None or prev < base:
        return None
    return round((cur - prev) / prev * 100, 1)


def _ranges(hours: list[int]) -> list[tuple[int, int]]:
    """[9, 10, 11, 17, 18] → [(9, 12), (17, 19)] (oxiri — ochiq chegara)."""
    out: list[list[int]] = []
    for h in sorted(set(hours)):
        if out and h == out[-1][1]:
            out[-1][1] = h + 1
        else:
            out.append([h, h + 1])
    return [(a, b) for a, b in out]


def _hh(h: int) -> str:
    return f"{h % 24:02d}:00"


# ============================================================================= hisob
def visitors(t0: float, t1: float, kind: str) -> dict:
    from .ai import counting

    has = bool(db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (counting.LINE_KIND,)))
    s = counting.summary(t0, t1)
    p0, p1 = prev_period(t0, t1)
    p = counting.summary(p0, p1)
    out = {"has": has, "in": s["in"], "out": s["out"], "all_in": s["all_in"], "all_out": s["all_out"],
           "staff": s["staff"], "drift_pct": s["drift_pct"], "busiest_hour": s["busiest_hour"],
           "prev_in": p["in"], "change_pct": pct_change(s["in"], p["in"])}
    if kind == "weekly":
        days = []
        d = t0
        while d < t1 - 1:
            d1 = day_start(d + 36 * 3600)
            days.append({"day": d, "in": counting.summary(d, d1)["in"]})
            d = d1
        out["days"] = days
        best = max(days, key=lambda x: x["in"]) if days else None
        out["busiest_day"] = best["day"] if best and best["in"] else None
    else:
        arr = [0] * 24
        for r in s["hourly"]:
            k = int((r["hour"] - t0) // 3600)
            if 0 <= k < 24:
                arr[k] = r["in"]
        out["hours"] = arr
    return out


def queue_part(t0: float, t1: float) -> dict:
    from .ai import queues

    if not queues.has_queues():
        return {"has": False}
    s = queues.summary(t0, t1)
    p0, p1 = prev_period(t0, t1)
    p = queues.summary(p0, p1)
    max_len = int(float(db.get_setting("queue_max_len", "4") or 4))
    # Uzun navbat soatlari (haftalikda — kun soati bo'yicha 7 kun yig'indisi): har kassa
    rows = db.rows("SELECT zone_id, MAX(zone_name) AS name, CAST((minute - ?) / 3600 AS INTEGER) % 24 AS h, "
                   "SUM(t_obs) AS obs, SUM(long_sec) AS lng FROM queue_minute WHERE minute >= ? AND minute < ? "
                   "GROUP BY zone_id, h", (t0, t0, t1))
    long_by: dict[int, dict] = {}
    for r in rows:
        obs = float(r["obs"] or 0)
        if obs >= LONG_MIN_OBS and float(r["lng"] or 0) / obs >= LONG_SHARE:
            z = long_by.setdefault(int(r["zone_id"]), {"zone_id": int(r["zone_id"]), "name": r["name"], "hours": [],
                                                       "share": {}})
            z["hours"].append(int(r["h"]))
            z["share"][int(r["h"])] = round(float(r["lng"]) / obs, 2)
    long_zones = []
    for z in long_by.values():
        rng = _ranges(z["hours"])
        long_zones.append({"zone_id": z["zone_id"], "name": z["name"], "ranges": rng,
                           "share": max(z["share"].values())})
    alerts = db.scalar("SELECT COUNT(*) FROM queue_alerts WHERE t >= ? AND t < ?", (t0, t1)) or 0
    return {"has": True, "served": s["served"], "avg_wait": s["avg_wait"], "p90_wait": s["p90_wait"],
            "max_wait": s["max_wait"], "max_len": s["max_len"], "long_sec": s["long_sec"],
            "prev_avg_wait": p["avg_wait"], "prev_served": p["served"], "queue_max_len": max_len,
            "zones": [{"name": z["name"], "served": z["served"], "avg_wait": z["avg_wait"], "max_wait": z["max_wait"],
                       "max_len": z["max_len"], "long_sec": z["long_sec"]} for z in s["zones"]],
            "long_zones": long_zones, "alerts": int(alerts)}


def flow_part(t0: float, t1: float) -> dict:
    try:
        from .ai import flow

        if not flow.has_plans():
            return {"has": False}
        p0, p1 = prev_period(t0, t1)
        plans = []
        for p in flow.layout().plans.values():
            s = flow.visits_summary(p.id, t0, t1)
            ps = flow.visits_summary(p.id, p0, p1)
            plans.append({"name": p.name, "total": s["total"], "prev_total": ps["total"],
                          "top": [{"name": x["name"], "visits": x["visits"], "share": x["share"]}
                                  for x in s["sections"][:5] if x["visits"]],
                          "moves": s["moves"][:3]})
        return {"has": True, "plans": plans}
    except Exception:  # noqa: BLE001
        log.exception("Hisobot: oqim qismi")
        return {"has": False}


def signals(t0: float, t1: float) -> dict:
    from .ai.safety import KIND_LABELS as SAFE_LABELS, SAFETY_KINDS

    marks = ",".join("?" for _ in SAFETY_KINDS)
    rows = db.rows(f"SELECT source_id, MAX(source_name) AS name, status, COUNT(*) AS n FROM events WHERE job_id IS NULL "
                   f"AND created_at >= ? AND created_at < ? AND (kind IS NULL OR kind NOT IN ({marks})) "
                   f"GROUP BY source_id, status", (t0, t1, *SAFETY_KINDS))
    cams: dict = {}
    tot = {"total": 0, "confirmed": 0, "rejected": 0, "unreviewed": 0}
    key = {"tasdiqlangan": "confirmed", "rad_etilgan": "rejected"}
    for r in rows:
        k = key.get(r["status"], "unreviewed")
        c = cams.setdefault(r["source_id"], {"source_id": r["source_id"], "name": r["name"] or "—", "total": 0,
                                             "confirmed": 0, "rejected": 0, "unreviewed": 0})
        c[k] += int(r["n"])
        c["total"] += int(r["n"])
        tot[k] += int(r["n"])
        tot["total"] += int(r["n"])
    reviewed = tot["confirmed"] + tot["rejected"]
    p0, p1 = prev_period(t0, t1)
    prev = db.scalar(f"SELECT COUNT(*) FROM events WHERE job_id IS NULL AND created_at >= ? AND created_at < ? "
                     f"AND (kind IS NULL OR kind NOT IN ({marks}))", (p0, p1, *SAFETY_KINDS)) or 0
    srows = db.rows(f"SELECT kind, status, COUNT(*) AS n FROM events WHERE job_id IS NULL AND created_at >= ? AND "
                    f"created_at < ? AND kind IN ({marks}) GROUP BY kind, status", (t0, t1, *SAFETY_KINDS))
    safety: dict = {}
    for r in srows:
        s = safety.setdefault(r["kind"], {"kind": r["kind"], "label": SAFE_LABELS.get(r["kind"], r["kind"]), "n": 0,
                                          "unreviewed": 0})
        s["n"] += int(r["n"])
        if r["status"] not in key:
            s["unreviewed"] += int(r["n"])
    return {"theft": {**tot, "false_share": round(tot["rejected"] / reviewed, 3) if reviewed else None,
                      "prev_total": int(prev),
                      "cams": sorted(cams.values(), key=lambda c: -c["total"])},
            "safety": [safety[k] for k in SAFETY_KINDS if k in safety],
            "safety_unreviewed": sum(s["unreviewed"] for s in safety.values())}


def cameras(t0: float, t1: float, now: float | None = None) -> dict:
    """Ish vaqtida (work_hours) har kamera necha daqiqa ishlamagani. Dastur o'chiq bo'lgan daqiqalar alohida: u paytda
    "kamera ishlamadi" emas, "dastur o'chiq" (kompyuter o'chgan / uxlagan)."""
    from .ai.counting import in_work_hours

    now = time.time() if now is None else now
    spec = db.get_setting("work_hours", "")
    end = min(t1, now)
    # Dastur birinchi marta ishga tushgan daqiqadan oldingi vaqt "o'chiq" emas (yangi o'rnatilgan kompyuter)
    first = db.scalar("SELECT minute FROM metrics_minute WHERE source_id=0 ORDER BY minute LIMIT 1")
    start = max(t0, float(first)) if first is not None else end
    work = [m for m in range(int(start // 60 * 60), int(end), 60) if in_work_hours(m + 30, spec)]
    ws = set(work)
    srcs = db.rows("SELECT id, name, kind, created_at FROM sources WHERE enabled=1 AND kind != 'file' ORDER BY id")
    if not work:
        return {"work_min": 0, "app_off_min": 0, "cams": []}
    sysm = {int(r["minute"]) for r in db.rows("SELECT minute FROM metrics_minute WHERE source_id=0 AND minute >= ? "
                                              "AND minute < ?", (t0, end))}
    on = [m for m in work if m in sysm]
    out = []
    for s in srcs:
        since = float(s["created_at"] or 0)
        mine = [m for m in on if m >= since]
        up = {int(r["minute"]): float(r["up_pct"] or 0) for r in db.rows(
            "SELECT minute, up_pct FROM metrics_minute WHERE source_id=? AND minute >= ? AND minute < ?",
            (s["id"], t0, end))}
        down = sum(1.0 - min(1.0, up.get(m, 0.0)) for m in mine)
        out.append({"source_id": s["id"], "name": s["name"], "down_min": round(down),
                    "up_pct": round(1 - down / len(mine), 3) if mine else None})
    return {"work_min": len(work), "app_off_min": len(work) - len(on),
            "cams": sorted(out, key=lambda c: -c["down_min"]), "work_hours": spec}


def compute(kind: str, t0: float, t1: float, now: float | None = None) -> dict:
    store = db.get_setting("store_name", "") or "Do'kon"
    data = {"kind": kind, "t0": t0, "t1": t1, "label": period_label(kind, t0, t1), "store": store,
            "generated_at": time.time(),
            "visitors": visitors(t0, t1, kind), "queues": queue_part(t0, t1), "flow": flow_part(t0, t1),
            **signals(t0, t1), "cameras": cameras(t0, t1, now)}
    data["recs"] = recommend(data)
    return data


# ============================================================================= tavsiyalar
def recommend(d: dict) -> list[dict]:
    """Qoidalar → [{"key", "level" (warn | info), "text"}], muhimlari oldin, ko'pi bilan MAX_RECS ta."""
    recs: list[dict] = []
    q = d.get("queues") or {}
    for z in q.get("long_zones", []) if q.get("has") else []:
        for a, b in z["ranges"]:
            recs.append({"key": f"queue:{z['zone_id']}:{a}-{b}", "level": "warn",
                         "text": f"🧾 {z['name']}: {_hh(a)}–{_hh(b)} da navbat uzun (soatning {round(z['share'] * 100)}% "
                                 f"ida ≥ {q['queue_max_len']} kishi) — shu soatlarda qo'shimcha kassa oching yoki "
                                 f"kassirni oldindan chaqiring."})
    wait_lim = float(db.get_setting("queue_max_wait_min", "3") or 3) * 60
    if q.get("has") and q.get("p90_wait") and q["p90_wait"] >= wait_lim and q.get("served", 0) >= 20:
        recs.append({"key": "queue:p90", "level": "warn",
                     "text": f"⏱ Xaridorlarning 10 foizi navbatda {fmt_dur(q['p90_wait'])} dan ko'p kutdi (chegara "
                             f"{fmt_dur(wait_lim)}) — gavjum soatlarda kassalar sonini ko'paytiring."})
    cams = d.get("cameras") or {}
    for c in cams.get("cams", []):
        if c["down_min"] >= CAM_DOWN_MIN:
            recs.append({"key": f"cam:{c['source_id']}", "level": "warn",
                         "text": f"📷 {c['name']}: ish vaqtida {fmt_dur(c['down_min'] * 60)} ishlamadi — kabel, quvvat "
                                 f"yoki tarmoqni tekshiring (shu vaqtda hech narsa kuzatilmadi)."})
    if cams.get("app_off_min", 0) >= APP_OFF_MIN:
        recs.append({"key": "app_off", "level": "warn",
                     "text": f"💻 Dastur ish vaqtida {fmt_dur(cams['app_off_min'] * 60)} o'chiq bo'ldi — kompyuter "
                             f"o'chgan yoki uxlab qolgan. Uyqu rejimini o'chiring va avtomatik ishga tushish "
                             f"(install_windows.bat) yoqilganini tekshiring."})
    th = d.get("theft") or {}
    reviewed = th.get("confirmed", 0) + th.get("rejected", 0)
    if reviewed >= FALSE_MIN_REVIEWED and (th.get("false_share") or 0) >= FALSE_SHARE:
        worst = max(th.get("cams") or [{}], key=lambda c: c.get("rejected", 0))
        where = f" (eng ko'p: {worst['name']} — {worst['rejected']} ta)" if worst.get("rejected") else ""
        recs.append({"key": "false", "level": "warn",
                     "text": f"⚙️ O'g'irlik signallarining {round(th['false_share'] * 100)}% xato bo'ldi{where} — shu "
                             f"kamerada kassa/xodim joyiga \"e'tiborsiz\" zona chizing yoki sezgirlikni pasaytiring."})
    unrev = th.get("unreviewed", 0)
    if unrev >= UNREV_MIN and th.get("total") and unrev / th["total"] >= UNREV_SHARE:
        recs.append({"key": "unreviewed", "level": "info",
                     "text": f"🟡 {unrev} ta signal javobsiz qoldi — har signalga ✅ yoki ❌ bosing: tizim shu "
                             f"javoblardan o'rganadi va xato signallar kamayadi."})
    if d.get("safety_unreviewed"):
        recs.append({"key": "safety_unrev", "level": "warn",
                     "text": f"🆘 {d['safety_unreviewed']} ta xavfsizlik signali (yiqilish va h.k.) javobsiz qoldi — "
                             f"ularni ko'rib chiqing."})
    v = d.get("visitors") or {}
    ch = v.get("change_pct")
    if v.get("has") and ch is not None and (v.get("prev_in") or 0) >= VISIT_BASE:
        what = "kun" if d.get("kind") == "daily" else "hafta"
        if ch <= VISIT_DROP:
            recs.append({"key": "visitors:drop", "level": "info",
                         "text": f"📉 Tashriflar o'tgan {what}ga nisbatan {abs(ch):.0f}% kam ({v['in']} / "
                                 f"{v['prev_in']}) — sabab (ob-havo, aksiya tugashi, yopiq soatlar)ni tekshiring."})
        elif ch >= VISIT_RISE:
            recs.append({"key": "visitors:rise", "level": "info",
                         "text": f"📈 Tashriflar o'tgan {what}ga nisbatan {ch:.0f}% ko'p ({v['in']} / {v['prev_in']}) — "
                                 f"shu kunlarga xodim va tovar zaxirasini rejalang."})
    if v.get("has") and (v.get("all_in") or 0) >= DRIFT_BASE and (v.get("drift_pct") or 0) > DRIFT_PCT:
        recs.append({"key": "drift", "level": "info",
                     "text": f"🚪 Kirdi va chiqdi soni {v['drift_pct']:.0f}% farq qiladi — sanash chizig'i joyini "
                             f"tekshiring (odamlar chiziqni chetlab o'tmayaptimi, eshik to'liq ko'rinadimi)."})
    if d.get("kind") == "weekly" and not v.get("has") and cams.get("cams"):
        recs.append({"key": "no_lines", "level": "info",
                     "text": "🚪 Sanash chizig'i chizilmagan — tashriflar hisoblanmayapti. Kameralar → Zonalar → "
                             "\"Sanash chizig'i\"."})
    recs.sort(key=lambda r: 0 if r["level"] == "warn" else 1)
    return recs[:MAX_RECS]


# ============================================================================= matn (Telegram)
def _chg(p: float | None) -> str:
    if p is None:
        return ""
    return f" ({'+' if p > 0 else ''}{p:.0f}%)"


def text(d: dict, report_id: int | None = None) -> str:
    """Telegram xabari (HTML, ≤ 4096 belgi): eng muhim raqamlar va tavsiyalar. Batafsili — PDF'da."""
    kind = d["kind"]
    lines = [f"📊 <b>{LABEL[kind]} hisobot — {esc(d['store'])}</b>", f"🗓 {esc(d['label'])}", ""]
    v = d["visitors"]
    if v.get("has"):
        cmp_ = "o'tgan haftaning shu kuni" if kind == "daily" else "o'tgan hafta"
        lines.append(f"👥 Tashriflar: <b>{v['in']}</b>{_chg(v.get('change_pct'))}"
                     + (f" — {cmp_}: {v['prev_in']}" if v.get("prev_in") else ""))
        if kind == "daily" and v.get("busiest_hour"):
            h = time.localtime(v["busiest_hour"]).tm_hour
            lines.append(f"   eng gavjum soat: {_hh(h)}–{_hh(h + 1)}")
        if kind == "weekly" and v.get("busiest_day"):
            lines.append(f"   eng gavjum kun: {WEEKDAYS[time.localtime(v['busiest_day']).tm_wday]}")
    q = d["queues"]
    if q.get("has"):
        lines.append(f"🧾 Navbat: {q['served']} xaridor, o'rtacha kutish {fmt_dur(q['avg_wait'])}"
                     + (f", 90% — {fmt_dur(q['p90_wait'])} gacha" if q.get("p90_wait") is not None else "")
                     + f", eng uzun navbat {q['max_len']} kishi")
    f = d["flow"]
    if f.get("has"):
        for p in f["plans"][:2]:
            top = ", ".join(f"{esc(x['name'])} {round(x['share'] * 100)}%" for x in p["top"][:3])
            lines.append(f"🗺 Bo'limlar: {top or 'tashrif yo`q'}")
    th = d["theft"]
    lines.append(f"⚠️ O'g'irlik signallari: <b>{th['total']}</b> (✅ {th['confirmed']}, ❌ {th['rejected']}, "
                 f"🟡 {th['unreviewed']})")
    if d["safety"]:
        lines.append("🛟 Xavfsizlik: " + ", ".join(f"{esc(s['label'])} {s['n']}" for s in d["safety"]))
    else:
        lines.append("🛟 Xavfsizlik signali bo'lmadi")
    cams = d["cameras"]
    down = [c for c in cams.get("cams", []) if c["down_min"] >= 5]
    if cams.get("cams"):
        if down:
            lines.append("📷 Ishlamagan vaqt (ish vaqtida): " + ", ".join(
                f"{esc(c['name'])} {fmt_dur(c['down_min'] * 60)}" for c in down[:4]))
        else:
            lines.append("📷 Kameralar ish vaqtida uzluksiz ishladi")
    if cams.get("app_off_min", 0) >= 5:
        lines.append(f"💻 Dastur o'chiq: {fmt_dur(cams['app_off_min'] * 60)} (ish vaqtida)")
    if d["recs"]:
        lines += ["", "<b>Tavsiyalar:</b>"]
        for i, r in enumerate(d["recs"], 1):
            lines.append(f"{i}. {esc(r['text'])}")
        lines.append("<i>Har tavsiyaga pastdagi 👍/👎 bilan baho bering — keraksizlari kamayadi.</i>")
    else:
        lines += ["", "✅ Alohida tavsiya yo'q — hammasi me'yorida."]
    out, n = [], 0
    for ln in lines:                       # butun qatorlar bilan qisqartiriladi — HTML teg o'rtasidan kesilmaydi
        if n + len(ln) + 1 > 3990:
            out.append("…")
            break
        out.append(ln)
        n += len(ln) + 1
    return "\n".join(out)


def rec_ref(key: str) -> str:
    """Tugmadagi tavsiya belgisi: kalit xeshi (tartib raqami emas — ro'yxat o'zgarsa ham boshqa tavsiyaga tushmaydi;
    callback_data ≤ 64 bayt)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def rec_type(key: str) -> str:
    """Darvoza uchun tavsiya turi: navbat soatlari oralig'i turlicha bo'lsa ham bitta tavsiya ("queue:3:17-19" va
    "queue:3:18-20" — "queue:3")."""
    parts = str(key).split(":")
    return ":".join(parts[:2]) if parts[0] == "queue" and len(parts) > 2 else str(key)


def markup(d: dict, report_id: int) -> dict | None:
    if not d.get("recs"):
        return None
    rows = [[{"text": f"👍 {i}", "callback_data": f"rf:{report_id}:{rec_ref(r['key'])}:1"},
             {"text": f"👎 {i}", "callback_data": f"rf:{report_id}:{rec_ref(r['key'])}:0"}]
            for i, r in enumerate(d["recs"], 1)]
    return {"inline_keyboard": rows}


def today_text() -> str:
    """Telegram /hisobot: bugun hozirgacha (qisqa)."""
    now = time.time()
    d = compute("daily", day_start(now), now + 1, now)
    d["label"] = f"bugun, {time.strftime('%H:%M', time.localtime(now))} gacha"
    s = text(d)
    return s.replace("📊 <b>Kunlik hisobot", "📊 <b>Bugungi holat", 1)


# ============================================================================= PDF
def pdf(d: dict) -> bytes:
    from . import pdfmini

    kind = d["kind"]
    doc = pdfmini.Doc(f"Hushyor AI - {LABEL[kind].lower()} hisobot")
    doc.text(f"{LABEL[kind]} hisobot: {d['store']}", size=16, bold=True)
    doc.text(d["label"], size=10, color=(0.45, 0.45, 0.5))
    doc.rule()
    v = d["visitors"]
    doc.text("Tashriflar", size=12, bold=True)
    if v.get("has"):
        cmp_ = "o'tgan haftaning shu kuni" if kind == "daily" else "o'tgan hafta"
        doc.kv("Kirdi (xaridorlar)", f"{v['in']}" + (f"   ({cmp_}: {v['prev_in']}{_chg(v.get('change_pct'))})"
                                                     if v.get("prev_in") else ""))
        doc.kv("Chiqdi", str(v["out"]))
        if v.get("staff"):
            doc.kv("Xodimlar o'tishi", str(v["staff"]))
        if kind == "daily" and v.get("hours"):
            doc.bars(v["hours"], [f"{h:02d}" for h in range(24)], title="Soatlar bo'yicha kirganlar")
        if kind == "weekly" and v.get("days"):
            doc.bars([x["in"] for x in v["days"]], [WEEKDAYS[time.localtime(x["day"]).tm_wday][:2] for x in v["days"]],
                     title="Kunlar bo'yicha kirganlar")
    else:
        doc.text("Sanash chizig'i chizilmagan.", indent=8, color=(0.4, 0.4, 0.45))
    q = d["queues"]
    if q.get("has"):
        doc.space(6)
        doc.text("Navbat", size=12, bold=True)
        doc.kv("Navbatdan o'tdi", str(q["served"]))
        doc.kv("O'rtacha kutish", fmt_dur(q["avg_wait"]) + (f"   (oldin: {fmt_dur(q['prev_avg_wait'])})"
                                                             if q.get("prev_avg_wait") else ""))
        doc.kv("90% kutish / eng uzoq", f"{fmt_dur(q['p90_wait'])} / {fmt_dur(q['max_wait'])}")
        doc.kv("Eng uzun navbat", f"{q['max_len']} kishi; uzun navbat jami {fmt_dur(q['long_sec'])}")
        if q["zones"]:
            doc.table(["Kassa", "O'tdi", "O'rt. kutish", "Eng uzoq", "Eng uzun navbat"],
                      [[z["name"], z["served"], fmt_dur(z["avg_wait"]), fmt_dur(z["max_wait"]), z["max_len"]]
                       for z in q["zones"]], [170, 60, 90, 80, 100])
    f = d["flow"]
    if f.get("has"):
        doc.space(6)
        doc.text("Bo'limlar (oqim)", size=12, bold=True)
        for p in f["plans"]:
            doc.kv(p["name"], f"{p['total']} tashrif" + (f"   (oldin: {p['prev_total']})" if p.get("prev_total") else ""))
            for x in p["top"]:
                doc.text(f"\x95 {x['name']}: {x['visits']} ({round(x['share'] * 100)}%)", indent=8)
    th = d["theft"]
    doc.space(6)
    doc.text("O'g'irlik signallari", size=12, bold=True)
    doc.kv("Jami", f"{th['total']} (oldin: {th['prev_total']})")
    doc.kv("Tasdiq / xato / javobsiz", f"{th['confirmed']} / {th['rejected']} / {th['unreviewed']}")
    if th["cams"]:
        doc.table(["Kamera", "Jami", "Tasdiq", "Xato", "Javobsiz"],
                  [[c["name"], c["total"], c["confirmed"], c["rejected"], c["unreviewed"]] for c in th["cams"][:10]],
                  [190, 70, 70, 70, 80])
    doc.space(6)
    doc.text("Xavfsizlik", size=12, bold=True)
    if d["safety"]:
        for s in d["safety"]:
            doc.kv(s["label"], f"{s['n']}" + (f" (javobsiz {s['unreviewed']})" if s["unreviewed"] else ""))
    else:
        doc.text("Xavfsizlik signali bo'lmadi.", indent=8)
    cams = d["cameras"]
    doc.space(6)
    doc.text("Kameralar (ish vaqtida)", size=12, bold=True)
    doc.kv("Ish vaqti", (cams.get("work_hours") or "kecha-kunduz") + f", {fmt_dur(cams.get('work_min', 0) * 60)}")
    doc.kv("Dastur o'chiq", fmt_dur(cams.get("app_off_min", 0) * 60))
    if cams.get("cams"):
        doc.table(["Kamera", "Ishlamagan", "Ishlagan ulushi"],
                  [[c["name"], fmt_dur(c["down_min"] * 60), "—" if c["up_pct"] is None else f"{c['up_pct'] * 100:.1f}%"]
                   for c in cams["cams"]], [220, 120, 120])
    doc.space(8)
    doc.text("Tavsiyalar", size=12, bold=True)
    if d["recs"]:
        for i, r in enumerate(d["recs"], 1):
            doc.text(f"{i}. {r['text']}", indent=8)
    else:
        doc.text("Alohida tavsiya yo'q - hammasi me'yorida.", indent=8)
    doc.space(8)
    doc.rule()
    from .config import APP_VERSION

    doc.text(f"Yaratildi: {time.strftime('%d.%m.%Y %H:%M', time.localtime(d['generated_at']))}, Hushyor AI {APP_VERSION}. "
             f"Raqamlar panelda ham shu manbadan.", size=8, color=(0.4, 0.4, 0.45))
    return doc.render()


# ============================================================================= saqlash va yuborish
def scheduled_due(kind: str, t1: float) -> float:
    """Davr hisobotining rejadagi yuborish vaqti: davr tugagan kun (kunlik — bugun, haftalik — dushanba) report_time."""
    try:
        hh, mm = (int(x) for x in str(db.get_setting("report_time", "09:00") or "09:00").split(":"))
    except ValueError:
        hh, mm = 9, 0
    return day_start(t1 + 43200) + hh * 3600 + mm * 60


def _delivered(report_id: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM report_sends WHERE report_id=? AND sent_at IS NOT NULL",
                         (report_id,)) or 0)


def generate(kind: str, t0: float, t1: float, due_at: float | None = None) -> int:
    """Hisobotni hisoblab saqlaydi. O'sha davr uchun bor bo'lsa: hali hech kimga yetmagan — qayta hisoblanadi
    (yangiroq raqamlar); yetgan — o'zgarmaydi (Telegram'dagi xabar, tugmalar va darvoza tekshiruvi o'sha nusxa bilan).
    Bir vaqtda ishlaydigan chaqiruvlar (rejalashtiruvchi, panel, /hisobot tugmasi) — qulf ostida."""
    from . import report_eval

    explicit = due_at is not None              # rejalashtiruvchi: joriy report_time bo'yicha (eskisini almashtiradi)
    if due_at is None:
        due_at = scheduled_due(kind, t1)       # qo'lda yaratilgan ham — darvozada "o'z vaqtida" shu vaqtga nisbatan
    with _gen_lock:
        row = db.row("SELECT id, sent_at, frozen FROM reports WHERE kind=? AND t0=?", (kind, t0))
        if row and (row["frozen"] or row["sent_at"] or _delivered(int(row["id"]))):
            return int(row["id"])
        d = compute(kind, t0, t1)
        # Darvoza: hisobot raqamlari shu paytdagi bazadan mustaqil SQL bilan qayta sanaladi (keyin hodisa o'chirilsa
        # ham tekshiruv o'sha paytga nisbatan — app/report_eval.py)
        chk = report_eval.recount(lambda sql, p=(): db.rows(sql, p), t0, t1)
        dj, cj = json.dumps(d, ensure_ascii=False), json.dumps(chk)
        db.execute("INSERT INTO reports(kind, t0, t1, created_at, due_at, data, checked) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(kind, t0) DO UPDATE SET data=excluded.data, checked=excluded.checked, "
                   "created_at=excluded.created_at, t1=excluded.t1, due_at="
                   + ("excluded.due_at" if explicit else "COALESCE(reports.due_at, excluded.due_at)"),
                   (kind, t0, t1, time.time(), due_at, dj, cj))
        return int(db.scalar("SELECT id FROM reports WHERE kind=? AND t0=?", (kind, t0)))


def get(report_id: int) -> dict | None:
    r = db.row("SELECT * FROM reports WHERE id=?", (report_id,))
    if not r:
        return None
    r["data"] = json.loads(r["data"] or "{}")
    fb = db.rows("SELECT rec_key, SUM(useful=1) AS up, SUM(useful=0) AS down FROM report_feedback WHERE report_id=? "
                 "GROUP BY rec_key", (report_id,))
    r["feedback"] = {x["rec_key"]: {"up": int(x["up"] or 0), "down": int(x["down"] or 0)} for x in fb}
    return r


def feedback(report_id: int, ref: str | int, useful: bool, who: str) -> dict | None:
    """ref — tavsiya kaliti, tugmadagi xesh (rec_ref) yoki (eski) tartib raqami."""
    r = get(report_id)
    recs = (r or {}).get("data", {}).get("recs") or []
    rec = None
    if isinstance(ref, int):
        rec = recs[ref] if 0 <= ref < len(recs) else None
    else:
        rec = next((x for x in recs if x["key"] == ref or rec_ref(x["key"]) == ref), None)
    if rec is None:
        return None
    db.execute("INSERT INTO report_feedback(report_id, rec_key, useful, who, t) VALUES(?,?,?,?,?) "
               "ON CONFLICT(report_id, rec_key, who) DO UPDATE SET useful=excluded.useful, t=excluded.t",
               (report_id, rec["key"], int(bool(useful)), who[:80], time.time()))
    return rec


def subscribers() -> list[int]:
    return [int(r["chat_id"]) for r in db.rows("SELECT chat_id FROM subscribers WHERE active=1 AND COALESCE(reports, 1)=1")]


def pending_chats(report_id: int) -> list[int]:
    """Rejadagi yuborishda kimga: hali urinilmagan bo'lsa — barcha obunachilar; aks holda — faqat yetmagan (hali obuna)
    chatlar: tarmoq/Telegram xatosi — o'sha kun oxirigacha, doimiy xato (bloklangan, chat yo'q) — PERM_TRIES marta.
    Keyin obuna bo'lgan chatga kechagi hisobot kelmaydi. Rejadagi vaqtdan OLDIN paneldan qo'lda yuborilgani hisobga
    olinmaydi — rejadagi nusxa baribir yuboriladi (darvoza avtomatik yetkazishni o'lchaydi); keyin yuborilgani
    (masalan, 09:00 dagi xatodan so'ng qo'lda) — yetkazilgan, takror yuborilmaydi."""
    subs = subscribers()
    due = float(db.scalar("SELECT due_at FROM reports WHERE id=?", (report_id,)) or 0)
    rows = [x for x in db.rows("SELECT chat_id, sent_at, tries, perm, auto FROM report_sends WHERE report_id=?",
                               (report_id,))
            if not (x["sent_at"] and not x["auto"] and due and float(x["sent_at"]) < due)]
    if not rows:
        return subs
    retry = {int(x["chat_id"]) for x in rows
             if not x["sent_at"] and not (x["perm"] and int(x["tries"] or 0) >= PERM_TRIES)}
    return [c for c in subs if c in retry]


def send(report_id: int, bot, chats: list[int] | None = None, mark: bool = True,
         only_missing: bool | None = None, auto: bool | None = None) -> int:
    """Telegram'ga: matn (tavsiya tugmalari bilan) va PDF. Qaytaradi: shu chaqiruvda nechta chatga yetdi.
    mark=True (rejadagi va paneldan "Telegram'ga yuborish") — hisobot muzlatiladi (qayta hisoblanmaydi) va har chat
    natijasi report_sends ga yoziladi; only_missing (sukut — chats berilmagan rejadagi yuborishda) — faqat
    yetmaganlarga. auto (sukut = only_missing) — rejalashtiruvchi yubordi: darvoza faqat shuni sanaydi (sent_at).
    Tugamagan davr (bugun / joriy hafta) hech qachon belgilanmaydi — ertangi to'liq hisobotni to'smasin.
    mark=False — /hisobot tugmasi (bitta chatga, so'rov bo'yicha): hech narsa belgilanmaydi.
    Matn yetgan chat — yetkazilgan; PDF yetmasa — faqat ogohlantirish (matn qayta yuborilmaydi)."""
    if bot is None or not getattr(bot, "token", ""):
        return 0
    head = db.row("SELECT t1 FROM reports WHERE id=?", (report_id,))
    if not head:
        return 0
    if mark and float(head["t1"]) > time.time():
        mark = False
        chats = subscribers() if chats is None else chats
    if not mark:
        r = get(report_id)
        return _send(r, bot, list(chats or []), False) if r else 0
    with _send_lock:
        with _gen_lock:                    # muzlatish: shu paytdan generate() bu hisobotni qayta hisoblamaydi
            db.execute("UPDATE reports SET frozen=1 WHERE id=?", (report_id,))
            r = get(report_id)
        if not r:
            return 0
        if only_missing is None:
            only_missing = chats is None
        if auto is None:
            auto = bool(only_missing)
        if chats is None:
            chats = pending_chats(report_id) if only_missing else subscribers()
        return _send(r, bot, chats, True, auto)


def _send(r: dict, bot, chats: list[int], mark: bool, auto: bool = False) -> int:
    report_id = int(r["id"])
    d = r["data"]
    body = text(d, report_id)
    mk = markup(d, report_id)
    try:
        pdf_bytes = pdf(d)
    except Exception:  # noqa: BLE001
        log.exception("Hisobot #%s: PDF yaratilmadi", report_id)
        pdf_bytes = None
    name = f"hisobot_{r['kind']}_{time.strftime('%Y%m%d', time.localtime(r['t0']))}.pdf"
    ok_n, errs = 0, []

    def note(c: int, ok: bool, err: str | None, perm: bool = False) -> None:
        if not mark:
            return
        if ok:                             # matn yetishi bilan darhol (PDF yuklanguncha kutmasdan)
            db.execute("INSERT INTO report_sends(report_id, chat_id, sent_at, pdf, tries, error, auto) "
                       "VALUES(?,?,?,0,1,NULL,?) ON CONFLICT(report_id, chat_id) DO UPDATE SET "
                       # yetmagan edi yoki oldin qo'lda yetgan, endi rejalashtiruvchi yetkazdi — yangisi yoziladi
                       "sent_at=CASE WHEN report_sends.sent_at IS NULL OR (excluded.auto=1 AND report_sends.auto=0) "
                       "THEN excluded.sent_at ELSE report_sends.sent_at END, "
                       "auto=CASE WHEN report_sends.sent_at IS NULL OR (excluded.auto=1 AND report_sends.auto=0) "
                       "THEN excluded.auto ELSE report_sends.auto END, tries=report_sends.tries + 1, error=NULL",
                       (report_id, c, time.time(), int(auto)))
        else:
            db.execute("INSERT INTO report_sends(report_id, chat_id, sent_at, pdf, tries, error, perm) "
                       "VALUES(?,?,NULL,0,1,?,?) ON CONFLICT(report_id, chat_id) DO UPDATE SET "
                       "tries=report_sends.tries + 1, error=excluded.error, perm=excluded.perm",
                       (report_id, c, err, int(perm)))

    for c in chats:
        try:
            data = {"chat_id": c, "text": body, "parse_mode": "HTML"}
            if mk:
                data["reply_markup"] = mk
            res = bot.api("sendMessage", data, chat_id=c)
            if not res or not res.get("ok"):
                err = str((res or {}).get("description", "xato"))[:200]
                errs.append(err)
                note(c, False, err, int((res or {}).get("error_code") or 0) in (400, 401, 403, 404))
                continue
        except Exception as e:  # noqa: BLE001
            errs.append(e.__class__.__name__)
            note(c, False, e.__class__.__name__)
            continue
        ok_n += 1
        note(c, True, None)
        pdf_ok, perr = 0, None
        if pdf_bytes:
            try:
                res = bot.api("sendDocument", {"chat_id": str(c), "caption": f"{LABEL[r['kind']]} hisobot (PDF)"},
                              files={"document": (name, pdf_bytes, "application/pdf")}, timeout=60, chat_id=c)
                pdf_ok = int(bool(res and res.get("ok")))
                if not pdf_ok:
                    perr = "PDF: " + str((res or {}).get("description", "xato"))[:180]
            except Exception as e:  # noqa: BLE001
                perr = f"PDF: {e.__class__.__name__}"
            if perr:
                log.warning("Hisobot #%s: matn yetdi, PDF yetmadi (%s) — chat %s", report_id, perr, c)
        if mark:
            db.execute("UPDATE report_sends SET pdf=MAX(pdf, ?), error=? WHERE report_id=? AND chat_id=?",
                       (pdf_ok, perr, report_id, c))
    if mark:
        n = _delivered(report_id)
        first = db.scalar("SELECT MIN(sent_at) FROM report_sends WHERE report_id=? AND sent_at IS NOT NULL AND auto=1",
                          (report_id,))
        db.execute("UPDATE reports SET sent_at=COALESCE(sent_at, ?), sent_n=?, error=? WHERE id=?",
                   (first, n, "; ".join(errs)[:300] or None, report_id))
    log.info("%s hisobot #%s: %d ta chatga yuborildi%s", LABEL[r["kind"]], report_id, ok_n,
             f" (xato: {errs[0]})" if errs else "")
    return ok_n


def _due(kind: str, now: float) -> tuple[float, float, float] | None:
    """Hozir yuborilishi kerak bo'lgan hisobot: (t0, t1, rejadagi vaqt) yoki None."""
    s = db.get_settings()
    if str(s.get(f"report_{kind}", "1")) not in ("1", "true", "on"):
        return None
    try:
        hh, mm = (int(x) for x in str(s.get("report_time", "09:00") or "09:00").split(":"))
    except ValueError:
        hh, mm = 9, 0
    d0 = day_start(now)
    if kind == "weekly" and time.localtime(now).tm_wday != 0:
        return None                    # faqat dushanba (kun davomida kechikib bo'lsa ham)
    due = d0 + hh * 3600 + mm * 60
    if now < due:
        return None
    t0, t1 = period(kind, now)
    since = float(s.get("report_since", 0) or 0)
    if t1 <= since:
        return None                    # davr hisobot yoqilishidan oldin tugagan — yuborilmaydi (o'rnatilgan kun —
                                       # qisman bo'lsa ham yuboriladi: ertasi ertalab birinchi hisobot keladi)
    if not subscribers():
        return None                    # obunachi yo'q — hisoblash ham shart emas (panelda qo'lda yaratish mumkin)
    r = db.row("SELECT id FROM reports WHERE kind=? AND t0=?", (kind, t0))
    if r and not pending_chats(int(r["id"])):
        return None                    # hammaga yetdi (yoki yetmaganlarning urinishlari tugadi)
    return t0, t1, due


class Scheduler(threading.Thread):
    """Har 30 s: vaqti kelgan (va yuborilmagan) kunlik/haftalik hisobotni yaratadi va yuboradi. Dastur 09:00 da o'chiq
    bo'lsa — yoqilganda (o'sha kun ichida) yuboradi. Telegram ishlamasa — 10 daqiqadan keyin qayta urinadi."""

    def __init__(self, bot):
        super().__init__(daemon=True, name="reports")
        self.bot = bot
        self.stop_evt = threading.Event()
        self.next_try: dict[str, float] = {}

    def tick(self, now: float | None = None) -> list[int]:
        now = time.time() if now is None else now
        done = []
        for kind in KINDS:
            if now < self.next_try.get(kind, 0):
                continue
            try:
                due = _due(kind, now)
                if due is None:
                    continue
                t0, t1, at = due
                rid = generate(kind, t0, t1, at)
                n = send(rid, self.bot)
                if n:
                    done.append(rid)
                if pending_chats(rid):
                    self.next_try[kind] = now + RETRY_SEC      # yetmagan chatlar — 10 daqiqadan keyin
            except Exception:  # noqa: BLE001  (baza band va h.k. — oqim to'xtamasin)
                log.exception("%s hisobot yaratilmadi", LABEL[kind])
                self.next_try[kind] = now + RETRY_SEC
        return done

    def run(self) -> None:
        while not self.stop_evt.wait(30):
            try:
                if getattr(self.bot, "token", ""):
                    self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Hisobot rejalashtiruvchisi")


_sched: Scheduler | None = None


def start(bot) -> Scheduler:
    global _sched
    if _sched is None or not _sched.is_alive():
        _sched = Scheduler(bot)
        _sched.start()
    return _sched
