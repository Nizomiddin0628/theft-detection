"""Kassa navbati (4-bosqich): aniqlik darvozasi va kassir jadvali tahlili — toza hisob, baza va kutubxonalarsiz.

Panel (app/queue_web.py), buyruq qatoridagi darvoza (tests/queue_gate.py) va qoidalar (app/ai/queues.py) shu bir
funksiyalardan foydalanadi — hammasi bir xil natija beradi.

Darvoza (reja): **navbat soni ±1 odam aniqlikda namunalarning ≥ 90% ida; kutish vaqti xatosi ≤ 20%**.

  * Navbat soni. Panelda "Namuna olish": tizim shu lahzadagi kadrni (navbat zonasi atrofi) va o'z hisobini birga
    saqlaydi; egasi kadrdagi navbatda turganlarni sanab kiritadi (tizim soni oldindan ko'rsatilmaydi — ta'sir qilmasin).
    |avto − qo'lda| ≤ 1 — to'g'ri. Kamida SAMPLE_MIN namuna, ulardan kamida SAMPLE_MIN_BUSY tasida qo'lda ≥ 2 kishi:
    bo'sh kassada "0 = 0" oson, darvoza haqiqiy navbatda o'tishi kerak.
  * Kutish vaqti. "Sekundomer": odam navbatga turganda bosiladi, navbatdan (to'lab) chiqqanda yana. Avtomatik yozuv
    bilan vaqt oraliqlarining kesishuvi (IoU ≥ TIMING_IOU) bo'yicha juftlanadi; xato = |avto − qo'lda| / qo'lda.
    Juftlanmagan o'lchov — 100% xato (tizim odamni yo'qotgan yoki kutishini bo'lib yuborgan). Kamida TIMING_MIN
    o'lchov, o'rtacha xato ≤ 20%.
  * Little qonuni (L = λ·W) — qo'shimcha tekshiruv (darvozaga kirmaydi): kadrlardagi o'rtacha navbat uzunligi (L) va
    xizmat oqimidan (λ — soatiga navbatdan chiqqanlar) kutilgan kutish vaqti L/λ o'lchangan o'rtacha kutish bilan
    solishtiriladi. Katta farq — izlar uzilyapti (kutish bo'linib ketyapti) yoki odamlar sanalmayapti.
"""
from __future__ import annotations

import time

SAMPLE_TOL = 1
GATE_SAMPLE_ACC = 0.90
SAMPLE_MIN = 50
SAMPLE_MIN_BUSY = 15
SAMPLE_GAP_SEC = 10        # bir zonadan namunalar orasida kamida shuncha (ketma-ket bir xil kadr bo'lmasin)
TIMING_IOU = 0.5
GATE_WAIT_ERR = 0.20
TIMING_MIN = 10
TIMING_PENDING_SEC = 15    # sekundomer to'xtatilgandan keyin avtomatik yozuv shu vaqt ichida paydo bo'ladi
LITTLE_OK = 0.25
LITTLE_MIN_OBS = 600       # kamida 10 daqiqa kuzatuv, 5 ta xizmat
LITTLE_MIN_SERVED = 5

WEEKDAYS = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")
WEEKDAYS_SHORT = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")
SCHED_DAYS = 28            # kassir jadvali — oxirgi 4 hafta
SCHED_MIN_DAYS = 14        # kamida 2 hafta tarix (kuzatilgan kunlar)
SCHED_DAY_OBS_MIN = 60     # kun "kuzatilgan" — kamida 60 daqiqa kamera ishlagan
OBS_MIN = 20               # soat hisobga olinadi — shu soatda kamida 20 daqiqa kuzatilgan
BUSY_MIN = 5               # "navbatli soat" — soat ichida kamida 5 daqiqa uzun navbat (yoki uzoq kutish)
LONG_MIN_SEC = 30          # daqiqa "uzun navbatli" — kamida 30 s navbat ≥ me'yor
REC_MIN_DAYS = 2           # takrorlanadi — bir xil hafta kuni va soatda kamida 2 kun
REC_FRAC = 0.5             # ... va o'sha hafta kuni kuzatilgan kunlarning kamida yarmida


# ----------------------------------------------------------------------------- navbat soni
def samples_gate(samples: list[dict]) -> dict:
    """samples: [{"auto": int, "manual": int | None}] — javob berilmaganlari hisobga olinmaydi."""
    done = [s for s in samples if s.get("manual") is not None and s.get("auto") is not None]
    n = len(done)
    good = sum(1 for s in done if abs(int(s["auto"]) - int(s["manual"])) <= SAMPLE_TOL)
    exact = sum(1 for s in done if int(s["auto"]) == int(s["manual"]))
    busy = [s for s in done if int(s["manual"]) >= 2]
    busy_good = sum(1 for s in busy if abs(int(s["auto"]) - int(s["manual"])) <= SAMPLE_TOL)
    acc = good / n if n else None
    bias = sum(int(s["auto"]) - int(s["manual"]) for s in done) / n if n else None
    enough = n >= SAMPLE_MIN and len(busy) >= SAMPLE_MIN_BUSY
    return {"n": n, "good": good, "exact": exact, "busy": len(busy),
            "accuracy": round(acc, 4) if acc is not None else None,
            "busy_accuracy": round(busy_good / len(busy), 4) if busy else None,
            "bias": round(bias, 3) if bias is not None else None,   # manfiy — tizim kam sanayapti
            "enough": enough, "ok": bool(enough and acc is not None and acc >= GATE_SAMPLE_ACC)}


# ----------------------------------------------------------------------------- kutish vaqti
def iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = min(a1, b1) - max(a0, b0)
    union = max(a1, b1) - min(a0, b0)
    return max(0.0, inter) / union if union > 0 else 0.0


def match_timings(timings: list[dict], autos: list[dict], now: float | None = None) -> list[dict]:
    """timings: [{"id", "zone_id", "t0", "t1"}] (t1 None — hali davom etyapti); autos: [{"zone_id", "t_in", "t_out",
    "wait"}]. Har avtomatik yozuv ko'pi bilan bitta o'lchov bilan juftlanadi (eng katta kesishuv avval)."""
    now = time.time() if now is None else now
    pairs = []
    for i, m in enumerate(timings):
        if m.get("t1") is None:
            continue
        for j, a in enumerate(autos):
            if int(a["zone_id"]) != int(m["zone_id"]):
                continue
            v = iou(float(m["t0"]), float(m["t1"]), float(a["t_in"]), float(a["t_out"]))
            if v >= TIMING_IOU:
                pairs.append((v, i, j))
    used_m: dict[int, tuple] = {}
    used_a: set = set()
    for v, i, j in sorted(pairs, reverse=True):
        if i in used_m or j in used_a:
            continue
        used_m[i] = (j, v)
        used_a.add(j)
    out = []
    for i, m in enumerate(timings):
        r = {"id": m.get("id"), "zone_id": m["zone_id"], "t0": m["t0"], "t1": m.get("t1"),
             "manual": (float(m["t1"]) - float(m["t0"])) if m.get("t1") is not None else None,
             "auto": None, "err": None, "iou": None, "pending": False, "note": m.get("note") or "",
             "zone_name": m.get("zone_name") or ""}
        if m.get("t1") is None:
            r["running"] = True
        elif i in used_m:
            a = autos[used_m[i][0]]
            r["auto"] = float(a["wait"])
            r["iou"] = round(used_m[i][1], 3)
            r["err"] = round(abs(r["auto"] - r["manual"]) / max(r["manual"], 1.0), 4)
        elif now - float(m["t1"]) < TIMING_PENDING_SEC:
            r["pending"] = True                       # avtomatik yozuv hali yopilmagan (odam endi chiqdi)
        out.append(r)
    return out


def timings_gate(results: list[dict]) -> dict:
    fin = [r for r in results if r.get("manual") is not None and not r.get("pending") and not r.get("running")]
    errs = [(r["err"] if r.get("err") is not None else 1.0) for r in fin]
    matched = sum(1 for r in fin if r.get("err") is not None)
    mean = sum(errs) / len(errs) if errs else None
    srt = sorted(errs)
    med = None
    if srt:
        k = len(srt) // 2
        med = srt[k] if len(srt) % 2 else (srt[k - 1] + srt[k]) / 2
    enough = len(fin) >= TIMING_MIN
    return {"n": len(fin), "matched": matched, "mean_err": round(mean, 4) if mean is not None else None,
            "median_err": round(med, 4) if med is not None else None, "enough": enough,
            "ok": bool(enough and mean is not None and mean <= GATE_WAIT_ERR)}


def little(t_obs: float, len_sum: float, served: int, wait_sum: float, wait_n: int) -> dict | None:
    """L = λ·W tekshiruvi. Ma'lumot kam bo'lsa — None."""
    if t_obs < LITTLE_MIN_OBS or served < LITTLE_MIN_SERVED or wait_n < LITTLE_MIN_SERVED or wait_sum <= 0:
        return None
    L = len_sum / t_obs
    lam = served / t_obs
    W = wait_sum / wait_n
    w_l = L / lam
    diff = abs(w_l - W) / W
    return {"L": round(L, 3), "per_hour": round(lam * 3600, 1), "W": round(W, 1), "W_little": round(w_l, 1),
            "diff": round(diff, 4), "ok": diff <= LITTLE_OK}


# ----------------------------------------------------------------------------- kassir jadvali (4-qoida)
def _hours_label(h0: int, h1: int) -> str:
    return f"{h0:02d}:00–{h1:02d}:00"


def _days_label(wds: list[int]) -> str:
    s = sorted(wds)
    if len(s) == 7:
        return "Har kuni"
    if s == [0, 1, 2, 3, 4]:
        return "Ish kunlari (Du–Ju)"
    if s == [5, 6]:
        return "Dam olish kunlari (Sh–Ya)"
    return ", ".join(WEEKDAYS[d] for d in s)


def schedule(rows: list[dict], max_len: int, max_wait_sec: float, now: float | None = None,
             days: int = SCHED_DAYS) -> dict:
    """Takrorlanadigan navbat soatlari (bir xil hafta kuni va soatda). rows: queue_minute qatorlari
    [{"minute", "t_obs", "len_max", "long_sec", "wait_max"}] — barcha zonalar (bir daqiqada bir nechta qator bo'lishi
    mumkin). max_len hozir faqat ma'lumot uchun (long_sec allaqachon shu me'yor bilan yig'ilgan).

    Natija: {"enough", "days" (kuzatilgan kunlar), "items": [{"weekdays", "label", "hours", "h0", "h1",
    "days_busy", "days_obs", "max_len"}]} — eng ko'p takrorlanadigani birinchi."""
    now = time.time() if now is None else now
    since = now - days * 86400
    mins: dict[int, list] = {}
    for r in rows:
        m = int(r["minute"])
        if m < since or m > now:
            continue
        cur = mins.setdefault(m, [False, False, 0])
        if float(r.get("t_obs") or 0) >= 20:
            cur[0] = True
        lm = int(r.get("len_max") or 0)
        # Uzun navbatli daqiqa: daqiqaning kamida yarmida navbat ≥ me'yor (bir lahzalik sakrash emas) yoki
        # kimdir me'yordan uzoq kutgan
        if float(r.get("long_sec") or 0) >= LONG_MIN_SEC or float(r.get("wait_max") or 0) >= max_wait_sec:
            cur[1] = True
        cur[2] = max(cur[2], lm)
    cells: dict[tuple, list] = {}                     # (sana, soat) -> [kuzatilgan daq, navbatli daq, eng uzun]
    day_obs: dict[tuple, int] = {}
    for m, (obs, long_, lm) in mins.items():
        lt = time.localtime(m)
        key = ((lt.tm_year, lt.tm_mon, lt.tm_mday), lt.tm_hour)
        c = cells.setdefault(key, [0, 0, 0, lt.tm_wday])
        if obs:
            c[0] += 1
            day_obs[key[0]] = day_obs.get(key[0], 0) + 1
            if long_:
                c[1] += 1
                c[2] = max(c[2], lm)
    n_days = sum(1 for v in day_obs.values() if v >= SCHED_DAY_OBS_MIN)
    if n_days < SCHED_MIN_DAYS:
        return {"enough": False, "days": n_days, "items": []}
    grid: dict[tuple, list] = {}                      # (hafta kuni, soat) -> [kuzatilgan kunlar, navbatli kunlar, eng uzun]
    for (_d, hour), (obs_m, long_m, lm, wd) in cells.items():
        if obs_m < OBS_MIN:
            continue
        g = grid.setdefault((wd, hour), [0, 0, 0])
        g[0] += 1
        if long_m >= BUSY_MIN:
            g[1] += 1
            g[2] = max(g[2], lm)
    rec = {k for k, (o, b, _lm) in grid.items() if b >= REC_MIN_DAYS and b / o >= REC_FRAC}
    ranges: dict[tuple, list[int]] = {}
    for wd in range(7):
        hrs = sorted(h for (d, h) in rec if d == wd)
        i = 0
        while i < len(hrs):
            j = i
            while j + 1 < len(hrs) and hrs[j + 1] == hrs[j] + 1:
                j += 1
            ranges.setdefault((hrs[i], hrs[j] + 1), []).append(wd)
            i = j + 1
    items = []
    for (h0, h1), wds in ranges.items():
        cs = [grid[(wd, h)] for wd in wds for h in range(h0, h1)]
        # kunlar soni — sanalar bo'yicha (bir necha soatlik oraliq bir kunni ko'p marta sanamasin)
        busy_d = sum(max(grid[(wd, h)][1] for h in range(h0, h1)) for wd in wds)
        obs_d = sum(max(grid[(wd, h)][0] for h in range(h0, h1)) for wd in wds)
        items.append({"weekdays": sorted(wds), "label": _days_label(wds), "hours": _hours_label(h0, h1), "h0": h0,
                      "h1": h1, "days_busy": busy_d, "days_obs": obs_d, "max_len": max((c[2] for c in cs), default=0)})
    items.sort(key=lambda x: (-len(x["weekdays"]) * (x["h1"] - x["h0"]), x["weekdays"][0], x["h0"]))
    return {"enough": True, "days": n_days, "items": items}
