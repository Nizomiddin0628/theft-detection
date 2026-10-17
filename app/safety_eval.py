"""Yiqilish darvozasi (6-bosqich) — toza hisob, baza va kutubxonalarsiz.

Panel (app/safety_web.py) ham, buyruq qatoridagi darvoza (tests/safety_gate.py) ham shu funksiyalardan foydalanadi.

Reja: "yiqilish recall ≥ 85%, xato signal ≤ 1 / kamera / hafta". Mezonlar:

  * Recall — belgilangan videolar (jadval `safety_videos`): har videoda yiqilish lahzalari (odam yerga tekkan soniya).
    Video jonli kameradagi kabi tahlil qilinadi (evaluation.analyze; oxirgi kadr HOLD_SEC takrorlanadi — ko'p videolar
    yiqilishdan keyin darhol tugaydi, haqiqatda odam yerda qoladi). Har belgiga [belgi − WIN_BEFORE, belgi + WIN_AFTER]
    oynadagi birinchi "Yiqilish" signali. Kerak: jami ≥ MIN_FALLS ta yiqilish (do'konda sahnalashtirilgan — gilam
    ustida, turli joyda, turli yo'nalishda), recall ≥ RECALL_MIN.
  * Xato signal — jonli kameralardagi "Yiqilish" va "Odam yotibdi" hodisalariga berilgan ❌ javoblar / kamera-hafta.
    Kamera ishlagan vaqt — metrics_minute (up_pct ≥ 0,5 bo'lgan daqiqalar). Har kamerada ≥ MIN_DAYS kun ishlagan
    bo'lishi kerak; har biri ≤ FP_MAX_WEEK. Javobsiz (tekshirilmagan) hodisalar alohida ko'rsatiladi — ularga javob
    bermasdan darvoza "o'tdi" deyilmaydi.
"""
from __future__ import annotations

WIN_BEFORE = 1.0
WIN_AFTER = 10.0
RECALL_MIN = 0.85
MIN_FALLS = 20
FP_MAX_WEEK = 1.0
MIN_DAYS = 7
HOLD_SEC = 3.5
FALL_KINDS = ("fall",)
LIVE_KINDS = ("fall", "lying")
WEEK_MIN = 7 * 24 * 60


def live_window(safety_since, retention_days, now: float) -> float:
    """Jonli xato hisobi oynasining boshi: 2.2.0 o'rnatilgan vaqt va hodisalar saqlanadigan muddat (retention_days,
    ko'pi bilan 90 kun — metrics_minute muddati) dan qaysi biri keyinroq. Kamera daqiqalari ham, ❌ javoblar ham shu
    oynadan olinadi (aks holda eski daqiqalar yoki o'chirilgan hodisalar natijani buzadi)."""
    try:
        since = float(safety_since or 0)
    except (TypeError, ValueError):
        since = 0.0
    try:
        days = float(retention_days or 0)
    except (TypeError, ValueError):
        days = 30.0
    days = 90.0 if days <= 0 else min(days, 90.0)
    return max(since, now - days * 86400)


def clean_falls(raw) -> list[float]:
    """"3.5, 12; 1:05" → [3.5, 12.0, 65.0] (soniya yoki m:ss)."""
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = str(raw or "").replace(";", ",").replace(" ", ",").split(",")
    out = []
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        try:
            if ":" in s:
                m, sec = s.split(":", 1)
                v = int(m) * 60 + float(sec)
            else:
                v = float(s)
        except ValueError:
            continue
        if 0 <= v < 24 * 3600:
            out.append(round(v, 2))
    return sorted(set(out))[:200]


def match(falls: list[float], alerts: list[dict]) -> dict:
    """Belgilar va "Yiqilish" signallari: har belgiga oynasidagi birinchi bo'sh signal (TP); qolgan signallar biror
    belgi oynasiga tushsa — takror, aks holda — xato signal (FP)."""
    al = sorted([a for a in alerts if a.get("kind") in FALL_KINDS], key=lambda a: float(a["t"]))
    used: set[int] = set()
    tp, fn = [], []
    for f in sorted(falls):
        cand = [i for i, a in enumerate(al) if i not in used and f - WIN_BEFORE <= float(a["t"]) <= f + WIN_AFTER]
        if cand:
            used.add(cand[0])
            tp.append({"fall": f, "t": float(al[cand[0]]["t"]), "delay": round(float(al[cand[0]]["t"]) - f, 2)})
        else:
            fn.append({"fall": f})
    fp, dup = [], 0
    for i, a in enumerate(al):
        if i in used:
            continue
        if any(f - WIN_BEFORE <= float(a["t"]) <= f + WIN_AFTER for f in falls):
            dup += 1
        else:
            fp.append({"t": float(a["t"])})
    other = [a for a in alerts if a.get("kind") not in FALL_KINDS]
    return {"tp": tp, "fn": fn, "fp": fp, "dup": dup, "other": len(other)}


def recall_gate(videos: list[dict]) -> dict:
    """videos: [{"id", "filename", "falls": [..], "negative": 0|1, "result": {"safety": [...]} | None}]."""
    per, tp, fn, fp, falls, pending = [], 0, 0, 0, 0, 0
    delays = []
    for v in videos:
        fl = clean_falls(v.get("falls"))
        res = v.get("result")
        if not res:
            pending += 1
            per.append({"id": v.get("id"), "filename": v.get("filename"), "falls": len(fl), "analyzed": False})
            continue
        m = match(fl, res.get("safety") or [])
        tp += len(m["tp"])
        fn += len(m["fn"])
        fp += len(m["fp"])
        falls += len(fl)
        delays += [x["delay"] for x in m["tp"]]
        per.append({"id": v.get("id"), "filename": v.get("filename"), "falls": len(fl), "analyzed": True,
                    "tp": len(m["tp"]), "fn": len(m["fn"]), "fp": len(m["fp"]), "dup": m["dup"],
                    "missed": [x["fall"] for x in m["fn"]], "negative": int(v.get("negative") or 0)})
    recall = tp / (tp + fn) if tp + fn else None
    enough = falls >= MIN_FALLS and pending == 0
    ok = bool(enough and recall is not None and recall >= RECALL_MIN)
    delays.sort()
    return {"falls": falls, "tp": tp, "fn": fn, "fp": fp, "recall": recall, "pending": pending, "enough": enough,
            "ok": ok, "need_falls": MIN_FALLS, "min_recall": RECALL_MIN,
            "delay_med": delays[len(delays) // 2] if delays else None, "videos": per}


def live_gate(cams: list[dict]) -> dict:
    """cams: [{"source_id", "name", "minutes" (ishlagan daqiqalar), "rejected", "confirmed", "unreviewed"}]."""
    out, ok_all, ready = [], True, 0
    for c in cams:
        minutes = float(c.get("minutes") or 0)
        weeks = minutes / WEEK_MIN
        rej = int(c.get("rejected") or 0)
        rate = rej / weeks if weeks > 0 else None
        enough = minutes >= MIN_DAYS * 24 * 60
        unrev = int(c.get("unreviewed") or 0)
        good = enough and rate is not None and rate <= FP_MAX_WEEK and unrev == 0
        if enough:
            ready += 1
            ok_all &= good
        out.append({**c, "days": round(minutes / 1440, 1), "weeks": round(weeks, 2), "rate": None if rate is None
                    else round(rate, 2), "enough": enough, "ok": good})
    ok = ready > 0 and ok_all
    return {"cams": out, "ready": ready, "ok": ok, "max_week": FP_MAX_WEEK, "min_days": MIN_DAYS}


def gate(videos: list[dict], cams: list[dict]) -> dict:
    r = recall_gate(videos)
    f = live_gate(cams)
    if r["ok"] and f["ok"]:
        state = "otdi"
    elif (r["enough"] and not r["ok"]) or any(c["enough"] and c["rate"] is not None and c["rate"] > FP_MAX_WEEK
                                               for c in f["cams"]):
        state = "otmadi"
    else:
        state = "yetarli emas"
    return {"state": state, "recall": r, "fp": f}
