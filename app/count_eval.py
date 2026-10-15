"""Odam sanash aniqligini baholash (3-bosqich darvozasi) — toza hisob, baza va kutubxonalarsiz.

Panel (app/visits.py) ham, buyruq qatoridagi darvoza (tests/count_gate.py) ham shu bir funksiyalardan foydalanadi,
shuning uchun ikkalasi har doim bir xil natija beradi.

Mezonlar:
  * qo'lda sanash sinovi: kamida GATE_MIN_SEC davom etgan oxirgi 2 ta sinovning har birida
    - son aniqligi ≥ GATE_ACC: 1 − (|avto kirdi − qo'lda kirdi| + |avto chiqdi − qo'lda chiqdi|) / (qo'lda jami) —
      yo'nalishlar alohida, shuning uchun "kirdi"dagi ortiqcha "chiqdi"dagi kamchilikni yashirmaydi;
    - juftlash ≥ GATE_MATCH: juftlangan / max(qo'lda, avto) — har bosish ±MATCH_TOL ichidagi avtomatik o'tish bilan.
      Son bir xil chiqsa ham, o'tkazib yuborilgan va ortiqcha o'tishlar bir-birini "yopib" qo'ymasin (10 ta o'tkazib
      yuborilgan + 10 ta ortiqcha = son aniqligi 100%, juftlash 90% — o'tmaydi). Ruxsat ±4 s: odam tugmani 1–3 s
      kechikib bosadi;
  * kunlik muvozanat: yopilishda ichkarida hech kim qolmaydi, demak kirdi ≈ chiqdi. Og'ish = |kirdi − chiqdi| / kirdi.
    Kamida DRIFT_MIN_DAYS kunlik (har biri ≥ DRIFT_MIN_IN kishi) medianasi ≤ DRIFT_MAX.
"""
from __future__ import annotations

import time

MATCH_TOL = 4.0            # qo'lda bosish va avtomatik o'tish orasidagi ruxsat (soniya)
GATE_ACC = 0.95
GATE_MATCH = 0.95
GATE_MIN_SEC = 60 * 60     # sinov kamida 1 soat davom etsin (reja: "2 × 1 soat qo'lda sanash")
GATE_SESSIONS = 2
DRIFT_MAX = 0.05
DRIFT_MIN_IN = 50          # bundan kam kirgan kun (dam olish, yarim kun) og'ishga qo'shilmaydi — tasodif katta
DRIFT_MIN_DAYS = 3


def compare(taps: list, autos: list[dict]) -> dict:
    """taps: [[t, "in"|"out"], ...] (qo'lda); autos: [{"t", "dir"}] — shu oraliqdagi barcha avtomatik o'tishlar
    (xodim ham — qo'lda hammani sanaydi). Yo'nalish bo'yicha ochko'z juftlash ±MATCH_TOL: juftlanmagan qo'lda
    bosish — "o'tkazib yuborilgan", juftlanmagan avtomatik — "ortiqcha"."""
    out: dict = {}
    tot_err = tot_manual = tot_auto = tot_matched = 0
    for d in ("in", "out"):
        m = sorted(float(t) for t, dd in taps if dd == d)
        a = sorted(float(x["t"]) for x in autos if x["dir"] == d)
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
        n_m, n_a = len(m), len(a)
        acc = (1 - abs(n_a - n_m) / n_m) if n_m else None
        out[d] = {"manual": n_m, "auto": n_a, "matched": matched, "missed": n_m - matched, "extra": n_a - matched,
                  "accuracy": round(acc, 4) if acc is not None else None}
        tot_err += abs(n_a - n_m)
        tot_manual += n_m
        tot_auto += n_a
        tot_matched += matched
    out["accuracy"] = round(1 - tot_err / tot_manual, 4) if tot_manual else None
    out["match_rate"] = round(tot_matched / max(tot_manual, tot_auto), 4) if tot_manual else None
    return out


def session_ok(res: dict) -> bool:
    return bool(res.get("ended_at") and res.get("duration", 0) >= GATE_MIN_SEC and res.get("accuracy") is not None
                and res["accuracy"] >= GATE_ACC and (res.get("match_rate") or 0) >= GATE_MATCH)


def sessions_gate(results: list[dict]) -> dict:
    """results — yangisi birinchi. Hisobga faqat tugagan va yetarlicha uzun sinovlar olinadi."""
    done = [x for x in results if x.get("ended_at") and x.get("duration", 0) >= GATE_MIN_SEC]
    last = done[:GATE_SESSIONS]
    return {"sessions": len(done), "passed": sum(1 for x in done if session_ok(x)),
            "last_passed": sum(1 for x in last if session_ok(x)),
            "ok": len(last) >= GATE_SESSIONS and all(session_ok(x) for x in last),
            "enough": len(last) >= GATE_SESSIONS}


def drift_gate(days: list[dict]) -> dict:
    """days: [{"day", "in", "out"}, ...] — xodimsiz, ish vaqtidan qat'i nazar (yopilgandan keyin chiqqanlar ham).
    Bugun (hali yopilmagan) chaqiruvchi tomonidan chiqarib tashlanadi."""
    use = [d for d in days if int(d.get("in") or 0) >= DRIFT_MIN_IN]
    vals = sorted(abs(int(d["in"]) - int(d["out"] or 0)) / int(d["in"]) for d in use)
    med = None
    if vals:
        k = len(vals) // 2
        med = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2
    return {"days": len(use), "median": round(med, 4) if med is not None else None,
            "worst": round(vals[-1], 4) if vals else None,
            "enough": len(use) >= DRIFT_MIN_DAYS,
            "ok": len(use) >= DRIFT_MIN_DAYS and med is not None and med <= DRIFT_MAX}


# ----------------------------------------------------------------------------- vaqt: "ichkarida" boshi, biznes kuni
def local_day_start(t: float) -> float:
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _hm(spec: str) -> tuple[int, int] | None:
    try:
        a, b = [x.strip() for x in (spec or "").strip().split("-", 1)]
        ah, am = (int(v) for v in a.split(":"))
        bh, bm = (int(v) for v in b.split(":"))
    except ValueError:
        return None
    s_, e_ = ah * 60 + am, bh * 60 + bm
    if not (0 <= s_ < 1440 and 0 <= e_ < 1440) or s_ == e_:
        return None
    return s_, e_


def occupancy_start(t: float | None = None, spec: str = "") -> float:
    """"Hozir ichkarida" hisobining boshi: do'kon yopiq paytining o'rtasi (09:00-22:00 → 03:30, 20:00-02:00 → 11:00) —
    yopilgandan keyin chiqib ketganlar ham ayiriladi; ish vaqti berilmasa (yoki kecha-kunduz) — yarim tun."""
    t = time.time() if t is None else t
    hm = _hm(spec)
    if hm is None:
        return local_day_start(t)
    s_, e_ = hm
    closed = (s_ - e_) % 1440                         # yopiq davr uzunligi (daqiqa)
    mid = (e_ + closed // 2) % 1440
    d0 = local_day_start(t)
    b = d0 + mid * 60
    if b > t:
        b = local_day_start(d0 - 43200) + mid * 60
    return b


def business_day(day0: float, spec: str = "") -> tuple[float, float]:
    """Kalendar kuni (day0 — yarim tun) uchun "biznes kuni": shu kun tushidan oldingi eng yaqin "ichkarida" boshidan
    24 soat (09:00-22:00 → 03:30 dan ertasi 03:30 gacha; tungi 20:00-02:00 → 11:00 dan ertasi 11:00 gacha).
    Kun oxiri farqi (kirdi ≈ chiqdi) shu oraliqda o'lchanadi — yarim tundan keyin chiqqanlar ham kiradi."""
    b0 = occupancy_start(day0 + 12 * 3600, spec)
    b1 = occupancy_start(day0 + 36 * 3600, spec)
    return b0, b1
