"""7-bosqich darvozasi (2.3.0): o'g'irlik signallarida xato ≤ 3 / kamera / kun — toza hisob, bazasiz.

Panel (Aniqlik sinovi sahifasi) ham, buyruq qatoridagi darvoza (tests/theft_gate.py) ham shu funksiyalardan foydalanadi.

Mezonlar (har jonli kamera):
  * ❌ (xato signal deb belgilangan) o'g'irlik hodisalari / ishlagan kun ≤ FP_MAX_DAY. Ishlagan vaqt — metrics_minute
    (up_pct ≥ 0,5 daqiqalar). Kamera daqiqalari va hodisalar bir oynada: hodisalar saqlanadigan muddat (retention_days,
    ≤ 90 kun — metrics_minute muddati).
  * kamida MIN_DAYS kun ishlagan;
  * javob berilmagan (tekshirilmagan) hodisalar ulushi ≤ MAX_UNREVIEWED — aks holda xato soni noma'lum.
Xavfsizlik signallari (yiqilish va h.k.) bu hisobga kirmaydi — ularning darvozasi alohida (app/safety_eval.py).
Recall (o'tkazib yuborilgan o'g'irliklar) bu yerda o'lchanmaydi — Aniqlik sinovidagi belgilangan videolar bilan.
"""
from __future__ import annotations

import json
from pathlib import Path

FP_MAX_DAY = 3.0
MIN_DAYS = 7
MAX_UNREVIEWED = 0.10


def window(retention_days, now: float) -> float:
    try:
        days = float(retention_days or 0)
    except (TypeError, ValueError):
        days = 30.0
    days = 90.0 if days <= 0 else min(days, 90.0)
    return now - days * 86400


def add_archived(ev: dict, feedback_dir, t0: float, live_ids: set, name_to_id: dict, safety) -> int:
    """O'chirilgan hodisalarning javoblari (data/feedback/ev*.json) — ❌ belgilangan (yoki javobsiz) signalni o'chirib,
    darvozani "aldab" bo'lmasin; javobsiz o'chirilgani "javobsiz" bo'lib sanaladi. ev: {source_id: {"rejected", "confirmed", "unreviewed"}} — joyida to'ldiriladi. Bazada bor
    hodisalar (live_ids) qayta sanalmaydi. Qaytaradi: qo'shilgan javoblar soni."""
    d = Path(feedback_dir)
    if not d.is_dir():
        return 0
    added = 0
    for f in d.glob("ev*.json"):
        try:
            eid = int(f.stem[2:])
            if eid in live_ids or f.stat().st_mtime < t0:     # arxiv vaqti ≥ hodisa vaqti: eski fayl o'qilmaydi
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if data.get("job_id") is not None or data.get("kind") in safety:
            continue
        if float(data.get("created_at") or 0) < t0:
            continue
        sid = data.get("source_id")
        if sid is None:
            sid = name_to_id.get(data.get("source_name"))         # 2.3.0 dan oldingi arxiv: kamera nomi bo'yicha
        if sid is None or int(sid) <= 0:
            continue
        key = {"rad_etilgan": "rejected", "tasdiqlangan": "confirmed", "yangi": "unreviewed"}.get(data.get("status"))
        if key is None:
            continue
        e = ev.setdefault(int(sid), {"rejected": 0, "confirmed": 0, "unreviewed": 0})
        e[key] += 1
        added += 1
    return added


def gate(cams: list[dict]) -> dict:
    """cams: [{"source_id", "name", "minutes", "rejected", "confirmed", "unreviewed"}]."""
    out, ready, ok_all = [], 0, True
    for c in cams:
        minutes = float(c.get("minutes") or 0)
        days = minutes / 1440.0
        rej, conf, unrev = int(c.get("rejected") or 0), int(c.get("confirmed") or 0), int(c.get("unreviewed") or 0)
        total = rej + conf + unrev
        share = unrev / total if total else 0.0
        rate = rej / days if days > 0 else None
        enough = days >= MIN_DAYS
        # 7 kun to'lmasa ham: xatolar butun sinov muddati chegarasidan (3 × 7 = 21) oshib ketgan bo'lsa — o'tmadi
        early_bad = not enough and rej > FP_MAX_DAY * MIN_DAYS
        good = enough and rate is not None and rate <= FP_MAX_DAY and share <= MAX_UNREVIEWED
        failed = (enough and rate is not None and rate > FP_MAX_DAY) or early_bad
        if enough:
            ready += 1
            ok_all &= good
        out.append({**c, "days": round(days, 1), "rate": None if rate is None else round(rate, 2),
                    "unreviewed_share": round(share, 3), "enough": enough, "ok": good, "failed": failed})
    ok = ready > 0 and ok_all and not any(c["failed"] for c in out)
    bad = any(c["failed"] for c in out)
    state = "otdi" if ok else ("otmadi" if bad else "yetarli emas")
    return {"cams": out, "ready": ready, "ok": ok, "state": state, "max_day": FP_MAX_DAY, "min_days": MIN_DAYS,
            "max_unreviewed": MAX_UNREVIEWED}
