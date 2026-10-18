"""Demo rejimi (2.6.0, 10-bosqich): namuna do'kon — 30 kunlik ma'lumot, haqiqiy kamerasiz.

`scripts/demo.py` (yoki `demo_windows.bat`) alohida papkada (data/demo) ishga tushiradi: haqiqiy baza, Telegram bot,
bulut va SMS ga TEGMAYDI (muhit o'zgaruvchilari bo'sh qilinadi). Kameralar o'rnida samples/ videolari aylanadi —
AI jonli ishlaydi (Jonli kuzatuv). O'tgan 30 kun uchun:
  * tashriflar (soat va hafta kuni bo'yicha tabiiy taqsimot), kassa navbati (17:00–19:00 gavjum), ko'rsatkichlar
    (dastur va kameralar ishlagani; bir kun qisqa uzilish — hisobotda tavsiya chiqsin);
  * o'g'irlik signallari (klip va rasm — namuna videodan), ✅/❌ javoblar va javob vaqti; xavfsizlik signallari;
  * do'kon rejasi va bo'lim tashriflari, heatmap;
  * kunlik/haftalik hisobotlar (yuborilgan), tavsiyalarga baho; pilot (3 hafta oldin boshlangan) va egasining bahosi.
Hammasi tasodifiy, lekin har ishga tushirishda bir xil (seed) — demo ssenariysi takrorlanadi.
"""
from __future__ import annotations

import json
import random
import shutil
import time

from . import db
from .config import BASE_DIR, CLIPS_DIR, DATA_DIR
from .logutil import get_logger

log = get_logger("demo")

DAYS = 30
STORE = "Namuna do'kon (DEMO)"
SAMPLES = BASE_DIR / "samples"
HOUR_W = {9: 3, 10: 5, 11: 7, 12: 8, 13: 9, 14: 9, 15: 8, 16: 8, 17: 11, 18: 13, 19: 12, 20: 8, 21: 5}
DAY_F = (1.0, 0.95, 0.97, 1.0, 1.1, 1.35, 1.2)          # dushanba … yakshanba
SECTIONS = [("Kassa", "#ef4444", (1, 9, 6, 11.5), 1.6), ("Sut mahsulotlari", "#3b82f6", (1, 1, 6, 4), 1.2),
            ("Non", "#f59e0b", (7, 1, 11, 4), 1.0), ("Meva-sabzavot", "#22c55e", (12, 1, 17, 5), 1.3),
            ("Ichimliklar", "#8b5cf6", (7, 6, 11, 8.5), 0.9), ("Maishiy kimyo", "#64748b", (12, 6.5, 17, 8.5), 0.5)]
REASONS = {"pocket": ["Qo'l cho'ntakka kirdi", "Buyum qo'ldan yo'qoldi"],
           "torso": ["Qo'l kiyim ichiga kirdi", "Atrofga qarab oldi"],
           "bag": ["Buyum sumkaga tushdi", "Polka oldida uzoq turdi"]}
REVIEWERS = ("Qorovul Aziz", "Qorovul Bekzod", "Menejer Dilnoza")


def _day0(t: float) -> float:
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def is_empty() -> bool:
    return not db.scalar("SELECT COUNT(*) FROM sources")


def _clips(n: int = 4) -> list[tuple[str, str]]:
    """Namuna videodan n ta qisqa klip va rasm (data/demo/clips)."""
    import cv2

    from .ai.clipper import write_mp4

    src = SAMPLES / "dokon_namuna_20s.mp4"
    out = []
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    k = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if k % max(1, int(round(fps / 10))) == 0:          # ~10 kadr/s
            frames.append(cv2.resize(fr, (640, int(fr.shape[0] * 640 / fr.shape[1])) if fr.shape[1] > 640 else fr))
        k += 1
    cap.release()
    if not frames:
        return out
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    seg = max(20, len(frames) // n)
    for i in range(n):
        part = frames[i * seg // 2: i * seg // 2 + 60] or frames[:60]
        clip, thumb = f"demo_{i + 1}.mp4", f"demo_{i + 1}.jpg"
        if not write_mp4(part, CLIPS_DIR / clip, 10.0):
            clip = ""
        mid = part[len(part) // 2].copy()
        h, w = mid.shape[:2]
        cv2.rectangle(mid, (int(w * 0.35), int(h * 0.2)), (int(w * 0.62), int(h * 0.95)), (40, 40, 230), 3)
        cv2.imwrite(str(CLIPS_DIR / thumb), mid, [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append((clip, thumb))
    return out


def seed(days: int = DAYS, now: float | None = None, clips: bool = True, live_sources: bool = True) -> dict:
    """Bo'sh demo bazani to'ldiradi. Qaytaradi: qo'shilgan yozuvlar soni (tur bo'yicha)."""
    from . import reports
    from .ai import counting

    now = time.time() if now is None else now
    rnd = random.Random(20261018)
    today = _day0(now)
    start = _day0(today - days * 86400 + 43200)
    stats: dict = {}
    db.set_settings({"store_name": STORE, "work_hours": "09:00-22:00", "retention_days": "0", "queue_max_len": "4",
                     "report_daily": "1", "report_weekly": "1", "report_time": "09:00",
                     "report_since": str(start - 86400), "counting_since": str(start - 86400),
                     # Namuna videolar istalgan soatda aylanadi: kechqurun demo ko'rsatilsa "tungi kirish" signallari
                     # ro'yxatni to'ldirib yubormasin
                     "safety_night": "0"})
    # --- kameralar (samples/ videolari, aylanib) va zonalar
    cams = [("Savdo zali", "dokon_namuna_20s.mp4", [("Polkalar", "polka", [[0.0, 0.15], [0.42, 0.15], [0.42, 0.9],
                                                                             [0.0, 0.9]])]),
            ("1-kassa navbati", "navbat_namuna.mp4", [("1-kassa", "navbat", [[0.40, 0.70], [0.78, 0.70], [0.84, 1.0],
                                                                               [0.34, 1.0]]),
                                                       ("Kassir", "kassir", [[0.42, 0.38], [0.76, 0.38], [0.76, 0.66],
                                                                             [0.42, 0.66]])]),
            ("Kirish eshigi", "sanash_namuna.mp4", [("Eshik", counting.LINE_KIND, [[0.0, 0.72], [1.0, 0.72]])])]
    sids = []
    for name, f, zones in cams:
        sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,1,?)",
                         (name, "file", str(SAMPLES / f), int(live_sources), start - 30 * 86400))
        for zname, kind, pts in zones:
            db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,1,?)",
                       (sid, zname, kind, json.dumps(pts), "", start - 30 * 86400))
        sids.append(sid)
    s_hall, s_queue, s_door = sids
    zq = db.scalar("SELECT id FROM zones WHERE kind='navbat'")
    days_list = []
    d = start
    while d < today:
        days_list.append(d)
        d = _day0(d + 36 * 3600)
    # --- ko'rsatkichlar: har daqiqa tizim va kamera qatori (bitta qisqa uzilish va bitta kamera uzilishi)
    outage_day, cam_day = days_list[max(0, len(days_list) - 21)], days_list[max(0, len(days_list) - 13)]
    mm = []
    end_m = int(now // 60 * 60) - 60
    m = int(start)
    while m < end_m:
        off = outage_day + 14 * 3600 <= m < outage_day + 14 * 3600 + 25 * 60
        if not off:
            mm.append((m, 0, "tizim", None, None, None, None, None, None, round(rnd.uniform(18, 45), 1),
                       round(rnd.uniform(40, 60), 1), None))
            for sid, nm in ((s_hall, "Savdo zali"), (s_queue, "1-kassa navbati"), (s_door, "Kirish eshigi")):
                up = 0.0 if (sid == s_hall and cam_day + 11 * 3600 <= m < cam_day + 11 * 3600 + 40 * 60) else 1.0
                mm.append((m, sid, nm, round(rnd.uniform(0.5, 3.5), 2), rnd.randint(1, 6), 8.0, 0.6, up, 0,
                           None, None, None))
        m += 60
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO metrics_minute(minute, source_id, source_name, persons_avg, persons_max, "
                      "fps_ai, active_pct, up_pct, alerts, cpu, ram, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", mm)
    stats["metrics"] = len(mm)
    # --- tashriflar va navbat
    counts, qrows = [], []
    for d in days_list + [today]:
        wd = time.localtime(d + 43200).tm_wday
        for h, w in HOUR_W.items():
            n = int(round(w * 5 * DAY_F[wd] * rnd.uniform(0.85, 1.15)))
            for i in range(n):
                t = d + h * 3600 + rnd.uniform(0, 3600)
                if t >= now - 60:
                    continue
                counts.append((t, s_door, "Kirish eshigi", 1, "Eshik", "in", i, 0, 0))
                to = t + rnd.uniform(6, 30) * 60
                if to < now - 60:
                    counts.append((to, s_door, "Kirish eshigi", 1, "Eshik", "out", i, 0, 0))
            for mi in range(60):
                minute = int(d + h * 3600 + mi * 60)
                if minute >= now - 120:
                    break
                busy = h in (17, 18, 19)
                served = 1 if rnd.random() < (0.9 if busy else 0.5) * DAY_F[wd] else 0
                wait = rnd.uniform(140, 260) if busy else rnd.uniform(40, 110)
                ln = rnd.randint(3, 6) if busy else rnd.randint(0, 2)
                long_ = rnd.uniform(25, 55) if busy and wd >= 4 else (rnd.uniform(5, 30) if busy else 0.0)
                qrows.append((minute, zq, "1-kassa", s_queue, "1-kassa navbati", 60.0, ln * 60.0, ln, round(long_, 1),
                              60.0 if ln else 20.0, 55.0, 1, served, round(wait * served, 1), served,
                              round(wait, 1) if served else 0.0))
        for k in range(3):                                   # xodimlar o'tishi
            t = d + (8.7 + k * 5) * 3600
            if t < now:
                counts.append((t, s_door, "Kirish eshigi", 1, "Eshik", "in", 900 + k, 1, 0))
    with db.tx() as c:
        c.executemany("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", counts)
        c.executemany("INSERT OR REPLACE INTO queue_minute(minute, zone_id, zone_name, source_id, source_name, t_obs, "
                      "len_sum, len_max, long_sec, busy_sec, cashier_sec, cashier_known, served, wait_sum, wait_n, "
                      "wait_max) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", qrows)
    stats["counts"], stats["queue_minutes"] = len(counts), len(qrows)
    # --- signallar
    clip_files = _clips() if clips else []
    evs = []
    for d in days_list + [today]:
        recent = d >= today                                  # bugungilarning yarmi hali ko'rilmagan
        for _ in range(rnd.choice((1, 2, 2, 3, 4))):
            t = d + rnd.uniform(10, 21.5) * 3600
            if t >= now - 600:
                continue
            kind = rnd.choice(("pocket", "pocket", "torso", "bag"))
            r = rnd.random()
            # Javobsiz — faqat oxirgi kunlarda (bugun yarmi, kecha-o'tgan kuni chorak); tekshirilganlarning ~62% tasdiqlangan
            if (recent and r < 0.5) or (d >= today - 2 * 86400 and r < 0.25):
                st = "yangi"
            else:
                st = "tasdiqlangan" if rnd.random() < 0.62 else "rad_etilgan"
            score = round(rnd.uniform(62, 94), 1)
            ev = {"source_id": s_hall, "source_name": "Savdo zali", "created_at": t, "score": score,
                  "level": "yuqori" if score >= 75 else "o'rta", "kind": kind, "reasons": REASONS[kind],
                  "breakdown": [], "status": st}
            if st != "yangi":
                ev.update(reviewed_by=rnd.choice(REVIEWERS), reviewed_at=t + rnd.uniform(40, 420))
            evs.append(ev)
    safety = [(0.4, "fall", "tasdiqlangan", "Odam yiqildi"), (0.55, "night", "tasdiqlangan", "Ish vaqtidan tashqari odam"),
              (0.7, "fall", "rad_etilgan", "Odam yiqildi"), (0.85, "crowd", "tasdiqlangan", "Kassa oldida gavjumlik")]
    for frac, kind, st, reason in safety:
        d = days_list[int(frac * (len(days_list) - 1))]
        t = d + (23.2 if kind == "night" else 15.5) * 3600
        evs.append({"source_id": s_hall, "source_name": "Savdo zali", "created_at": t, "score": 100,
                    "level": "yuqori", "kind": kind, "reasons": [reason], "breakdown": [], "status": st,
                    "reviewed_by": REVIEWERS[2], "reviewed_at": t + 95})
    n_ev = 0
    for ev in sorted(evs, key=lambda e: e["created_at"]):    # id tartibi = vaqt tartibi (ro'yxatda aralashmasin)
        cf = clip_files[n_ev % len(clip_files)] if clip_files else ("", "")
        ev.update(track_id=100 + n_ev, clip_path=cf[0] or None, thumb_path=cf[1] or None)
        db.insert_event(ev)
        n_ev += 1
    stats["events"] = n_ev
    # --- do'kon rejasi va bo'limlar (oqim)
    stats.update(_flow(rnd, days_list, now, s_hall))
    # --- hisobotlar (yuborilgan) va baholar
    # Kunlik hisobotlar — pilot boshidan (23 kun) beri; yuborish vaqti hali kelmagani (bugun 09:00 gacha — kechagisi)
    # yaratilmaydi: rejalashtiruvchi o'zi yaratadi (kelajakdagi "yuborilgan" vaqt chiqmasin)
    n_rep = 0
    for d in days_list[-25:]:
        d1 = _day0(d + 36 * 3600)
        due = reports.scheduled_due("daily", d1)
        if due + 90 > now:
            continue
        rid = reports.generate("daily", d, d1, due)
        db.execute("UPDATE reports SET sent_at=?, sent_n=2, frozen=1 WHERE id=?", (due + rnd.uniform(20, 90), rid))
        n_rep += 1
        recs = (reports.get(rid) or {}).get("data", {}).get("recs") or []
        for i, rec in enumerate(recs[:2]):
            db.execute("INSERT OR REPLACE INTO report_feedback(report_id, rec_key, useful, who, t) VALUES(?,?,?,?,?)",
                       (rid, rec["key"], 0 if (i == 1 and rnd.random() < 0.3) else 1, "tg:demo", due + 600))
    w = reports.week_start(today)
    for k in (1, 2, 3):
        w0 = _day0(w - k * 7 * 86400 + 43200)
        if w0 < start:
            continue
        w1 = _day0(w0 + 7 * 86400 + 43200)
        if reports.scheduled_due("weekly", w1) + 60 > now:
            continue
        rid = reports.generate("weekly", w0, w1, reports.scheduled_due("weekly", w1))
        db.execute("UPDATE reports SET sent_at=?, sent_n=2, frozen=1 WHERE id=?",
                   (reports.scheduled_due("weekly", w1) + 60, rid))
        n_rep += 1
    stats["reports"] = n_rep
    # --- pilot
    from . import pilot

    p0 = _day0(today - 23 * 86400 + 43200)
    pilot.start(p0)
    for i, (r, com) in enumerate(((4, "Kassa navbati bo'yicha tavsiya foydali bo'ldi"), (5, None),
                                  (4, "Xato signallar kamaydi, zal kamerasi ba'zan uziladi"))):
        wk = pilot.week_start(p0 + (i + 1) * 7 * 86400 + 43200) - 7 * 86400 if i else pilot.week_start(p0)
        if wk + 7 * 86400 > now:
            continue
        db.execute("INSERT OR REPLACE INTO pilot_feedback(week_t0, who, chat_id, rating, comment, t) VALUES(?,?,?,?,?,?)",
                   (wk, "tg:egasi", None, r, com, wk + 7 * 86400 + 9.5 * 3600))
    pilot._reset_caches()
    db.set_settings({"demo_seeded_at": str(now)})
    log.info("Demo ma'lumot yaratildi: %s", stats)
    return stats


def _calib(rnd: random.Random, pid: int, m_per_px: float, source_id: int) -> None:
    """"Savdo zali" kamerasi rejaga bog'langan (kalibrlangan) bo'lsin: kadrdagi trapetsiya ↔ zalning 1–17 × 1–11.5 m
    qismi. 10 ta kalibrlash va 2 ta tekshiruv nuqtasi (bosish xatosi ~5 sm) — panelda haqiqiy kalibrlash kabi ko'rinadi."""
    import cv2
    import numpy as np

    from .ai import flow
    from .ai import homography as hg

    src = np.float32([[0.04, 0.97], [0.96, 0.97], [0.80, 0.32], [0.20, 0.32]])
    dst = np.float32([[4.0, 11.5], [14.0, 11.5], [17.0, 1.0], [1.0, 1.0]])
    H = cv2.getPerspectiveTransform(src, dst)
    pts = [(0.1, 0.92), (0.5, 0.95), (0.9, 0.92), (0.25, 0.7), (0.5, 0.68), (0.75, 0.7), (0.3, 0.45), (0.7, 0.45),
           (0.22, 0.36), (0.78, 0.36), (0.4, 0.82), (0.62, 0.55)]
    pairs = []
    for i, (u, v) in enumerate(pts):
        x, y = cv2.perspectiveTransform(np.float32([[[u, v]]]), H)[0, 0]
        x, y = float(x) + rnd.gauss(0, 0.04), float(y) + rnd.gauss(0, 0.04)
        pairs.append({"img": [u, v], "plan": [round(x / m_per_px, 2), round(y / m_per_px, 2)], "check": int(i >= 10)})
    fw, fh = 1280, 720
    res = hg.fit(flow.pairs_m(pairs, m_per_px), fw / fh, fw, fh)
    if not res.get("ok"):
        log.warning("Demo kalibrlash chiqmadi: %s", res.get("warnings"))
        return
    model = res["calib"].to_json()
    model["m_per_px"] = m_per_px
    db.execute("INSERT OR REPLACE INTO cam_calib(source_id, plan_id, pairs, model, stats, frame_w, frame_h, enabled, "
               "updated_at) VALUES(?,?,?,?,?,?,?,1,?)",
               (source_id, pid, json.dumps(pairs), json.dumps(model), json.dumps(flow.stats_of(res)), fw, fh,
                time.time()))


def _flow(rnd: random.Random, days_list: list[float], now: float, cam: int) -> dict:
    """Bo'sh reja (18 × 12 m), 6 ta bo'lim, kalibrlangan zal kamerasi, bo'lim tashriflari (yo'nalishlar bilan) va
    14 kunlik heatmap."""
    import cv2

    from .ai import flow
    from .flow_web import PLANS_DIR, blank_image

    img, m_per_px = blank_image(18.0, 12.0)
    h, w = img.shape[:2]
    t0 = time.time()
    pid = db.execute("INSERT INTO floor_plans(name, img, w_px, h_px, m_per_px, blank, created_at, updated_at) "
                     "VALUES(?,?,?,?,?,1,?,?)", ("Savdo zali", "", w, h, m_per_px, t0, t0))
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"plan_{pid}_demo.png"
    cv2.imwrite(str(PLANS_DIR / name), img)
    db.execute("UPDATE floor_plans SET img=? WHERE id=?", (name, pid))
    secs = []
    for nm, color, (x0, y0, x1, y1), wgt in SECTIONS:
        pts = [[x0 / m_per_px, y0 / m_per_px], [x1 / m_per_px, y0 / m_per_px], [x1 / m_per_px, y1 / m_per_px],
               [x0 / m_per_px, y1 / m_per_px]]
        sid = db.execute("INSERT INTO plan_sections(plan_id, name, color, points, enabled, created_at) VALUES(?,?,?,?,1,?)",
                         (pid, nm, color, json.dumps(pts), t0))
        secs.append((sid, nm, (x0, y0, x1, y1), wgt))
    _calib(rnd, pid, m_per_px, cam)
    flow.bump()
    visits, hours = [], []
    names = {s[0]: s[1] for s in secs}
    weights = [s[3] for s in secs]
    p = flow.layout(force=True).plans.get(pid)
    nx, ny = (p.nx, p.ny) if p else (36, 24)
    grid = p.grid_key if p else f"{nx}x{ny}@0.5"
    heat0 = days_list[max(0, len(days_list) - 13)] if days_list else 0      # heatmap: oxirgi 13 kun + bugun
    for d in days_list + [_day0(now)]:
        wd = time.localtime(d + 43200).tm_wday
        for hr, wt in HOUR_W.items():
            if d + hr * 3600 >= now - 60:
                break
            n = int(round(wt * 4 * DAY_F[wd] * rnd.uniform(0.8, 1.2)))
            occ: dict[str, float] = {}
            pas: dict[str, float] = {}
            for i in range(n):
                t = d + hr * 3600 + rnd.uniform(0, 3500)
                if t >= now - 60:
                    continue
                path = rnd.choices(secs[1:], weights=weights[1:], k=rnd.randint(1, 3)) + [secs[0]]
                prev = None
                for k, s in enumerate(path):
                    visits.append((t + k * 90, pid, s[0], names[s[0]], cam, 1000 + i, prev))
                    prev = s[0]
                    if d >= heat0:
                        x0, y0, x1, y1 = s[2]
                        for _ in range(6):                   # bo'lim o'rtasi gavjumroq
                            cx = int(min(nx - 1, max(0, min(x1, max(x0, rnd.gauss((x0 + x1) / 2, (x1 - x0) / 4))) / 0.5)))
                            cy = int(min(ny - 1, max(0, min(y1, max(y0, rnd.gauss((y0 + y1) / 2, (y1 - y0) / 4))) / 0.5)))
                            key = str(cy * nx + cx)
                            occ[key] = round(occ.get(key, 0.0) + rnd.uniform(4, 15), 1)
                            pas[key] = pas.get(key, 0) + 1
            if occ and d + hr * 3600 + 3600 <= now:         # joriy soat — jonli oqim yozadi
                hours.append((int(d + hr * 3600), pid, cam, grid, 3600.0, json.dumps(occ), json.dumps(pas), "{}"))
    with db.tx() as c:
        c.executemany("INSERT INTO section_visits(t, plan_id, section_id, section_name, source_id, track_id, from_id) "
                      "VALUES(?,?,?,?,?,?,?)", visits)
        c.executemany("INSERT OR REPLACE INTO flow_hour(hour, plan_id, source_id, grid, obs, occ, pas, vec) "
                      "VALUES(?,?,?,?,?,?,?,?)", hours)
    return {"section_visits": len(visits), "flow_hours": len(hours)}


def reset() -> None:
    """Demo papkasini tozalash (faqat HUSHYOR_DEMO=1 va data/demo da — haqiqiy ma'lumotga hech qachon tegmaydi)."""
    from .config import DEMO

    if not DEMO or DATA_DIR.name != "demo":
        raise RuntimeError("reset faqat demo papkasida")
    for p in DATA_DIR.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass

