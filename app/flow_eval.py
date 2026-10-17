"""Oqim aniqligini baholash (5-bosqich darvozasi) — toza hisob, baza va kutubxonalarsiz.

Panel (app/flow_web.py) ham, buyruq qatoridagi darvoza (tests/flow_gate.py) ham shu funksiyalardan foydalanadi.

Reja: "belgilangan nuqtalarda rejadagi xato ≤ 0,5 m; bo'lim tashriflari qo'lda tekshiruvga mos". Mezonlar:

  * Nuqtada turish (har kalibrlangan kamera): odam polda belgilangan joyda (masalan, skotch bilan belgi, joyi rejada
    o'lchangan) turadi, tizim POINT_SAMPLE_SEC davomida uning oyog'ini rejaga o'tkazib, medianasini oladi. Faqat
    kameraning oxirgi kalibrlashidan keyingi o'lchovlar. Kerak: ≥ POINT_MIN ta o'lchov, ulardan ≥ POINT_SPOTS ta
    turli joyda (bir-biridan ≥ SPOT_SEP m) — kadrning bir burchagidagi 5 ta o'lchov butun kamerani isbotlamaydi.
    O'tish: 90% o'lchovda xato ≤ POINT_MAX_ERR (P90). Bu sinov kalibrlash xatosi + detektor oyoq nuqtasi xatosini
    birga o'lchaydi (haqiqatda shu kerak).
  * Bo'lim tashriflari: egasi telefonda kuzatayotgan bo'limiga kirgan har odamda "+1" bosadi; tizimning o'sha bo'lim
    tashriflari (kirgan vaqti) bilan solishtiriladi. Oxirgi VISIT_SESSIONS ta, har biri ≥ VISIT_MIN_SEC davom etgan
    sinovning har birida:
    - son aniqligi ≥ VISIT_ACC: 1 − Σ|avto − qo'lda| / Σ qo'lda (bo'limlar alohida — bir bo'limdagi ortiqcha
      boshqasidagi kamchilikni yashirmaydi);
    - juftlash ≥ VISIT_MATCH: juftlangan / max(qo'lda, avto) — har bosish ±MATCH_TOL ichidagi shu bo'lim tashrifi
      bilan. Ruxsat ±5 s: tizim odam bo'limda 2 s turgandan keyin sanaydi, vaqti esa kirgan payt; odam tugmani 1–3 s
      kechikib bosadi;
    va ikkala sinovda jami ≥ VISIT_MIN_MANUAL ta qo'lda bosish (oz sonda foiz tasodifiy).
"""
from __future__ import annotations

import math

POINT_SAMPLE_SEC = 3.0
POINT_MIN = 5
POINT_SPOTS = 3
SPOT_SEP = 1.0
POINT_MAX_ERR = 0.5
POINT_PCT = 0.90

MATCH_TOL = 5.0
VISIT_ACC = 0.90
VISIT_MATCH = 0.85
VISIT_MIN_SEC = 30 * 60
VISIT_SESSIONS = 2
VISIT_MIN_MANUAL = 30


def percentile(vals: list[float], q: float) -> float | None:
    v = sorted(float(x) for x in vals)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def spots(points: list[tuple[float, float]], sep: float = SPOT_SEP) -> int:
    """Turli joylar soni: bir-biridan sep m dan uzoq nuqtalar (ochko'z guruhlash)."""
    centers: list[tuple[float, float]] = []
    for x, y in points:
        if all(math.hypot(x - a, y - b) >= sep for a, b in centers):
            centers.append((x, y))
    return len(centers)


def point_gate(tests: list[dict]) -> dict:
    """tests: bitta kameraning (oxirgi kalibrlashdan keyingi) o'lchovlari [{"x", "y", "err"}]."""
    errs = [float(t["err"]) for t in tests if t.get("err") is not None]
    p90 = percentile(errs, POINT_PCT)
    sp = spots([(float(t["x"]), float(t["y"])) for t in tests if t.get("err") is not None])
    enough = len(errs) >= POINT_MIN and sp >= POINT_SPOTS
    return {"n": len(errs), "spots": sp, "p90": round(p90, 3) if p90 is not None else None,
            "mean": round(sum(errs) / len(errs), 3) if errs else None,
            "max": round(max(errs), 3) if errs else None,
            "within": round(sum(1 for e in errs if e <= POINT_MAX_ERR) / len(errs), 3) if errs else None,
            "enough": enough, "ok": bool(enough and p90 is not None and p90 <= POINT_MAX_ERR)}


def compare(taps: list, autos: list[dict], sections: list[int] | None = None) -> dict:
    """taps: [[t, bo'lim id], ...] (qo'lda); autos: [{"t", "section_id"}] — shu oraliqdagi avtomatik tashriflar.
    sections — kuzatilgan bo'limlar (boshqalari hisobga olinmaydi). Bo'lim bo'yicha ochko'z juftlash ±MATCH_TOL."""
    secs = sorted({int(s) for s in (sections or [])} or {int(s) for _, s in taps})
    per = {}
    tot_err = tot_m = tot_a = tot_match = 0
    for sid in secs:
        m = sorted(float(t) for t, s in taps if int(s) == sid)
        a = sorted(float(x["t"]) for x in autos if int(x["section_id"]) == sid)
        used = [False] * len(a)
        matched = 0
        for t in m:
            best, bd = None, None
            for i, ta in enumerate(a):
                if used[i]:
                    continue
                dt = abs(ta - t)
                if dt <= MATCH_TOL and (bd is None or dt < bd):
                    best, bd = i, dt
            if best is not None:
                used[best] = True
                matched += 1
        per[sid] = {"manual": len(m), "auto": len(a), "matched": matched, "missed": len(m) - matched,
                    "extra": len(a) - matched,
                    "accuracy": round(1 - abs(len(a) - len(m)) / len(m), 4) if m else None}
        tot_err += abs(len(a) - len(m))
        tot_m += len(m)
        tot_a += len(a)
        tot_match += matched
    return {"sections": per, "manual": tot_m, "auto": tot_a, "matched": tot_match,
            "accuracy": round(1 - tot_err / tot_m, 4) if tot_m else None,
            "match_rate": round(tot_match / max(tot_m, tot_a), 4) if tot_m else None}


def session_ok(res: dict) -> bool:
    return bool(res.get("ended_at") and res.get("duration", 0) >= VISIT_MIN_SEC and res.get("accuracy") is not None
                and res["accuracy"] >= VISIT_ACC and (res.get("match_rate") or 0) >= VISIT_MATCH)


def sessions_gate(results: list[dict]) -> dict:
    """results — yangisi birinchi. Hisobga faqat tugagan va yetarlicha uzun sinovlar."""
    done = [x for x in results if x.get("ended_at") and x.get("duration", 0) >= VISIT_MIN_SEC]
    last = done[:VISIT_SESSIONS]
    manual = sum(int(x.get("manual") or 0) for x in last)
    enough = len(last) >= VISIT_SESSIONS and manual >= VISIT_MIN_MANUAL
    return {"sessions": len(done), "last_passed": sum(1 for x in last if session_ok(x)), "manual": manual,
            "enough": enough, "ok": enough and all(session_ok(x) for x in last)}
