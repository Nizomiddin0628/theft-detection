"""1.9.0 odamlarni sanash: chiziqdan o'tish mantig'i, xodim va ish vaqti, saqlash, API, daqiqalik yig'indi.

    python -m tests.test_counting
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_count_")   # hech qachon jonli data/ emas
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app import db  # noqa: E402
from app.ai import counting, zones as zones_mod  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""))


class P:
    def __init__(self, tid: int, box):
        self.track_id = tid
        self.box = np.array(box, dtype=np.float32)
        self.score = 0.9


W, H = 1280, 720
# Gorizontal chiziq kadr o'rtasida (y = 0.5), chapdan o'ngga; ichkari — pastki tomon (kameraga yaqin)
LINE = zones_mod.ZoneSet([zones_mod.Zone(1, "Eshik", "chiziq", np.array([[0.2, 0.5], [0.8, 0.5]], dtype=np.float32))])


def person_at(tid: int, x: float, foot_y: float, h: float = 260) -> P:
    return P(tid, [x - 50, foot_y - h, x + 50, foot_y])


def walk(c: counting.LineCounter, tid: int, x0: float, y0: float, x1: float, y1: float, n: int, t0: float,
         fps: float = 8.0, jitter: float = 0.0, rng=None, zs=LINE, status=None) -> tuple[list, float]:
    out = []
    for i in range(n):
        f = i / max(n - 1, 1)
        x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        if jitter and rng is not None:
            x += rng.normal(0, jitter)
            y += rng.normal(0, jitter)
        out += c.update([person_at(tid, x, y)], t0 + i / fps, W, H, zs, status or {}, work_hours="")
    return out, t0 + n / fps


def logic_suite() -> None:
    print("\n--- Chiziqdan o'tish mantig'i ---")
    rng = np.random.default_rng(7)
    c = counting.LineCounter(None, persist=False)
    ev, t = walk(c, 1, 640, 200, 640, 650, 16, 0.0)
    ok("Tashqaridan ichkariga o'tdi → bitta 'kirdi'", [e.dir for e in ev] == ["in"], str([e.dir for e in ev]))
    ev, t = walk(c, 2, 640, 650, 640, 200, 16, t + 1)
    ok("Ichkaridan tashqariga → bitta 'chiqdi'", [e.dir for e in ev] == ["out"])
    # Chiziq ustida turib oyoq silkinishi (±10 px) — sanalmaydi
    ev, t = walk(c, 3, 600, 340, 600, 360, 40, t + 1, jitter=8, rng=rng)
    ok("Chiziq ustida turib qimirlash (gisterezis) — sanalmaydi", ev == [], str([e.dir for e in ev]))
    # Chiziq bo'ylab yurish
    ev, t = walk(c, 4, 300, 330, 1000, 330, 30, t + 1)
    ok("Chiziq bo'ylab yurish — sanalmaydi", ev == [])
    # Chiziq uchidan tashqarida (x = 0.95 W) yuqoridan pastga
    ev, t = walk(c, 5, 1220, 200, 1220, 650, 16, t + 1)
    ok("Chiziq kesmasidan tashqarida o'tish — sanalmaydi", ev == [])
    # Kirib, ostonada orqaga qaytib, yana kirish: kirdi, chiqdi, kirdi
    ev1, t = walk(c, 6, 640, 200, 640, 600, 10, t + 1)
    ev2, t = walk(c, 6, 640, 600, 640, 200, 10, t)
    ev3, t = walk(c, 6, 640, 200, 640, 600, 10, t)
    ok("Kirdi → qaytib chiqdi → yana kirdi: 3 ta o'tish", [e.dir for e in ev1 + ev2 + ev3] == ["in", "out", "in"])
    # Tez yurish: 4 kadr/s, bir qadamda chiziqdan sakrab o'tadi
    ev, t = walk(c, 7, 640, 150, 640, 700, 4, t + 1, fps=4)
    ok("Past kadr tezligi (4/s), chiziqdan bir qadamda o'tish ham sanaladi", [e.dir for e in ev] == ["in"])
    # Kuzatuv uzilishi: ID 8 chiziq oldida yo'qoladi, 0.5 s dan keyin yaqin joyda ID 9 chiziq ortida paydo bo'ladi
    ev_a, t = walk(c, 8, 640, 150, 640, 330, 8, t + 1)
    t += 0.5
    ev_b, t = walk(c, 9, 645, 420, 645, 650, 8, t)
    ok("Kuzatuv uzildi (yangi ID chiziq ortida) — baribir bitta 'kirdi'", [e.dir for e in ev_a + ev_b] == ["in"],
       str([e.dir for e in ev_a + ev_b]))
    # Kuzatuvchi ID ni almashtirdi: eski ID yo'qoldi, yangisi keyingi kadrdayoq (0 s) yoki bir kadr o'tib paydo bo'ldi
    for gap in (0.0, 0.125):
        cc = counting.LineCounter(None, persist=False)
        ev_a, t2 = walk(cc, 20, 640, 150, 640, 340, 10, 0.0)
        ev_b, _ = walk(cc, 21, 640, 400, 640, 650, 8, t2 + gap)
        ok(f"ID chiziq ustida almashdi (oraliq {gap} s) — bitta 'kirdi'", [e.dir for e in ev_a + ev_b] == ["in"],
           str([e.dir for e in ev_a + ev_b]))
    # Xodim chiqib ketdi, 0.6 s dan keyin o'sha joyda xaridor kirdi — xodim belgisi xaridorga o'tmaydi
    cs = counting.LineCounter(None, persist=False)
    ev1, t2 = walk(cs, 30, 640, 650, 640, 150, 16, 0.0, status={30: {"staff": True}})
    ev2, _ = walk(cs, 31, 650, 160, 650, 650, 16, t2 + 0.6)
    ok("Xodim chiqdi, ortidan kelgan xaridor kirdi — xaridor xodim deb belgilanmadi",
       [(e.dir, e.staff) for e in ev1 + ev2] == [("out", True), ("in", False)],
       str([(e.dir, e.staff) for e in ev1 + ev2]))
    # Detektor A ni bir kadr ko'rmadi, o'sha kadrda ichkarida turgan B birinchi marta topildi; A qaytib kirdi.
    # B A ning holatini vaqtincha olgan bo'lsa ham — A qaytgach bekor; ro'yxat tartibi ([B, A] yoki [A, B]) muhim emas
    for new_first in (False, True):
        cf2 = counting.LineCounter(None, persist=False)
        evs, tt = [], 0.0
        for y in (250, 270, 290):
            evs += cf2.update([person_at(1, 640, y)], tt, W, H, LINE, {}, work_hours="")
            tt += 0.125
        evs += cf2.update([person_at(2, 720, 420)], tt, W, H, LINE, {}, work_hours="")
        tt += 0.125
        for y in (300, 330, 370, 410, 450, 470, 480, 490, 495, 500):
            ps2 = [person_at(2, 720, 425), person_at(1, 640, y)]
            evs += cf2.update(ps2 if new_first else ps2[::-1], tt, W, H, LINE, {}, work_hours="")
            tt += 0.125
        ok(f"Detektor bir kadr ko'rmadi + yangi odam paydo bo'ldi ({'[B, A]' if new_first else '[A, B]'}) — faqat A kirdi",
           [(e.track_id, e.dir) for e in evs] == [(1, "in")], str([(e.track_id, e.dir) for e in evs]))
    # Xodim bir kadr ko'rinmadi, yonida xaridor paydo bo'ldi va chiqib ketdi — xaridor xodim bo'lib qolmaydi
    cs2 = counting.LineCounter(None, persist=False)
    evs, tt = [], 0.0
    for y in (520, 515, 510):
        evs += cs2.update([person_at(5, 640, y)], tt, W, H, LINE, {5: {"staff": True}}, work_hours="")
        tt += 0.125
    evs += cs2.update([person_at(6, 690, 500)], tt, W, H, LINE, {}, work_hours="")
    tt += 0.125
    for y in (470, 420, 360, 300, 250, 220, 200, 190, 180):
        evs += cs2.update([person_at(5, 640, 510), person_at(6, 690, y)], tt, W, H, LINE, {5: {"staff": True}},
                          work_hours="")
        tt += 0.125
    ok("Xodim bir kadr ko'rinmadi, yonidagi xaridor chiqdi — xaridor sifatida sanaldi",
       [(e.track_id, e.dir, e.staff) for e in evs] == [(6, "out", False)], str([(e.track_id, e.dir, e.staff) for e in evs]))
    # Chiziq yo'nalishi almashtirildi (tahrir), odamlar joyida turibdi — soxta o'tish yo'q
    cf = counting.LineCounter(None, persist=False)
    ps = [person_at(40 + k, 300 + 300 * k, 600) for k in range(3)]
    evf = []
    for i in range(8):
        evf += cf.update(ps, i / 8, W, H, LINE, {}, work_hours="")
    flipped = zones_mod.ZoneSet([zones_mod.Zone(1, "Eshik", "chiziq", np.array([[0.8, 0.5], [0.2, 0.5]], dtype=np.float32))])
    for i in range(8):
        evf += cf.update(ps, 1 + i / 8, W, H, flipped, {}, work_hours="")
    evg, _ = walk(cf, 41, 600, 600, 600, 150, 16, 2.5, zs=flipped)
    ok("Chiziq tahrirlandi — joyida turganlar sanalmadi; keyin o'tgan odam yangi yo'nalishda sanaldi",
       evf == [] and [e.dir for e in evg] == ["in"], str([e.dir for e in evf + evg]))

    class _Bad:
        track_id = 1
        box = None
    counting.log.disabled = True        # kutilgan xato — jurnalga yoziladi, sinov chiqishini ifloslamasin
    try:
        bad_res = counting.LineCounter(None, persist=False).update([_Bad()], 0, W, H, LINE, {}, work_hours="")
    finally:
        counting.log.disabled = False
    ok("Sanashdagi xato kamera tahlilini to'xtatmaydi (update [] qaytaradi)", bad_res == [])
    # Uzoqda paydo bo'lgan boshqa odam meros olmaydi
    ev_a, t = walk(c, 10, 300, 150, 300, 330, 8, t + 3)
    t += 0.4
    ev_b, t = walk(c, 11, 1000, 420, 1000, 650, 8, t)
    ok("Boshqa joyda paydo bo'lgan yangi odam (uzoq) — sanalmaydi (u kadrga ichkarida kirgan)", ev_a + ev_b == [])
    # Ko'p odam bir vaqtda (yonma-yon): 3 kirdi, 2 chiqdi
    c2 = counting.LineCounter(None, persist=False)
    evs = []
    for i in range(20):
        f = i / 19
        ps = [person_at(100 + k, 400 + 150 * k, 150 + 500 * f) for k in range(3)]
        ps += [person_at(200 + k, 500 + 200 * k, 650 - 500 * f) for k in range(2)]
        evs += c2.update(ps, 100 + i / 8, W, H, LINE, {}, work_hours="")
    ok("Bir vaqtda 3 kishi kirdi, 2 kishi chiqdi", sorted(e.dir for e in evs) == ["in"] * 3 + ["out"] * 2,
       str(sorted(e.dir for e in evs)))
    # Yo'nalish: nuqtalar almashsa — kirdi/chiqdi almashadi
    rev = zones_mod.ZoneSet([zones_mod.Zone(2, "Teskari", "chiziq", np.array([[0.8, 0.5], [0.2, 0.5]], dtype=np.float32))])
    c3 = counting.LineCounter(None, persist=False)
    ev, _ = walk(c3, 1, 640, 200, 640, 650, 16, 0.0, zs=rev)
    ok("'Yo'nalishni almashtirish' (nuqtalar teskari) — o'sha harakat 'chiqdi'", [e.dir for e in ev] == ["out"])
    # Xodim: o'tgandan keyin xodim zonasiga borsa — avvalgi o'tishi qayta belgilanadi
    c4 = counting.LineCounter(None, persist=False)
    ev, t = walk(c4, 1, 640, 200, 640, 650, 16, 0.0)
    ok("Xodim (keyin aniqlandi): avval xaridor deb sanaldi", ev and not ev[0].staff)
    c4.update([person_at(1, 640, 660)], t + 3, W, H, LINE, {1: {"staff": True}}, work_hours="")
    ok("Xodim zonasida turgach — o'sha o'tish xodimga o'tdi", ev[0].staff)
    ev, _ = walk(c4, 2, 640, 650, 640, 200, 16, t + 5, status={2: {"staff": True}})
    ok("Xodim deb belgilangan odamning chiqishi — xodim", ev and ev[0].staff)
    # Ish vaqti
    lt = time.localtime()
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 8, 30, 0, 0, 0, -1))
    ok("Ish vaqti: 08:30 — '09:00-22:00' dan tashqari", not counting.in_work_hours(base, "09:00-22:00"))
    ok("Ish vaqti: 12:00 — ichida", counting.in_work_hours(base + 3.5 * 3600, "09:00-22:00"))
    ok("Tungi smena '20:00-02:00': 01:00 — ichida, 03:00 — tashqari",
       counting.in_work_hours(base - 7.5 * 3600, "20:00-02:00") and not counting.in_work_hours(base - 5.5 * 3600,
                                                                                               "20:00-02:00"))
    ok("Bo'sh yoki noto'g'ri sozlama — har doim ish vaqti", counting.in_work_hours(base, "") and
       counting.in_work_hours(base, "abc") and counting.in_work_hours(base, "09:00-09:00"))
    d0 = counting.day_start(base)
    os_ = counting.occupancy_start
    ok("'Ichkarida' boshi — yopiq payt o'rtasi: 09:00-22:00 → 03:30 (12:00 da bugungi, 02:00 da kechagi)",
       os_(d0 + 12 * 3600, "09:00-22:00") == d0 + 3.5 * 3600
       and os_(d0 + 2 * 3600, "09:00-22:00") == counting.day_start(d0 - 3600) + 3.5 * 3600)
    ok("Tungi do'kon 20:00-02:00 → 11:00; ish vaqti bo'sh — yarim tun",
       os_(d0 + 12 * 3600, "20:00-02:00") == d0 + 11 * 3600 and os_(d0 + 12 * 3600, "") == d0
       and os_(d0 + 12 * 3600, "09:00-09:00") == d0)
    c5 = counting.LineCounter(None, persist=False)
    ev = []
    for i in range(16):
        ev += c5.update([person_at(1, 640, 200 + 30 * i)], base + i / 8, W, H, LINE, {}, work_hours="09:00-22:00")
    ok("Ish vaqtidan tashqari o'tish belgilanadi", ev and ev[0].offhours)
    ok("Zonasiz kamera — hech narsa sanalmaydi, xato yo'q",
       counting.LineCounter(None, persist=False).update([person_at(1, 640, 300)], 0, W, H, zones_mod.ZoneSet(), {}) == [])


def store_suite() -> None:
    print("\n--- Saqlash, yig'indi, daqiqa va bulut ---")
    db.init_db()
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Kirish', 'rtsp', 'rtsp://x', 0, ?)",
                     (time.time(),))
    db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
               (sid, "Eshik", "chiziq", "[[0.2,0.5],[0.8,0.5]]", "", 1, time.time()))
    zones_mod.invalidate(sid)
    zs = zones_mod.load(sid)
    ok("Chiziq zonasi bazadan o'qiladi (2 nuqta)", len(counting.lines_from(zs)) == 1)
    c = counting.LineCounter(sid, "Kirish", persist=True)
    t0 = int(time.time() // 60) * 60 - 600       # 10 daqiqa oldin boshlangan daqiqa
    n_in = n_out = 0
    for k in range(6):
        ev, _ = walk(c, 10 + k, 640, 200, 640, 650, 16, t0 + k * 5, zs=zs)
        n_in += len(ev)
    for k in range(2):
        ev, _ = walk(c, 30 + k, 640, 650, 640, 200, 16, t0 + 40 + k * 5, zs=zs)
        n_out += len(ev)
    ok("Har o'tish counts jadvaliga yozildi", db.scalar("SELECT COUNT(*) FROM counts WHERE source_id=?", (sid,)) == 8)
    mc = counting.minute_counts(sid, t0)
    ok("Daqiqalik yig'indi: 6 kirdi, 2 chiqdi", mc.get("in") == 6 and mc.get("out") == 2, str(mc))
    s = counting.summary(t0 - 60, t0 + 3600, sid)
    ok("Oraliq yig'indisi: ichkarida 4, siljish %", s["in"] == 6 and s["out"] == 2 and s["inside"] == 4
       and s["drift_pct"] == round(4 / 6 * 100, 1), str(s)[:120])
    # metrics: daqiqa yozuvi kirdi/chiqdi bilan (bulutga ham)
    from app import metrics, sync

    sent = []
    real_enq = sync.enqueue_metrics
    sync.enqueue_metrics = lambda rows: sent.extend(rows)
    try:
        class M:
            def source_info(self):
                return [{"id": sid, "name": "Kirish", "running": True, "persons": 1, "fps_ai": 8, "mode": "tinch",
                         "alerts": 0}]
        ms = metrics.MetricsSampler(M())
        ms.sample()
        rows = ms.flush(t0)
        cam = [r for r in rows if r["source_id"] == sid][0]
        ok("Daqiqalik ko'rsatkichda kirdi/chiqdi (bulutga ketadigan yozuvda)", cam.get("in") == 6 and cam.get("out") == 2
           and any(r.get("in") == 6 for r in sent), str({k: cam.get(k) for k in ("in", "out", "extra")}))
        # Odam o'tgach peshtaxta ortiga borib xodim deb aniqlandi (o'sha iz uzluksiz kuzatilmoqda) → o'tgan daqiqa
        # qayta hisoblanadi va bulutga qayta yuboriladi
        sent.clear()
        ev, t = walk(c, 50, 640, 200, 640, 650, 16, t0 + 50, zs=zs)
        ms.sample()
        ms.flush(t0)
        ok("Yangi kirgan odam avval xaridor (7 kirdi)", counting.minute_counts(sid, t0).get("in") == 7)
        sent.clear()
        for i in range(20):        # 2.5 s davomida xodim zonasida (analyzer belgisi)
            c.update([person_at(50, 640, 660)], t + i / 8, W, H, zs, {50: {"staff": True}} if i >= 10 else {},
                     work_hours="")
        mc2 = counting.minute_counts(sid, t0)
        ok("Xodim qayta belgilandi → daqiqa yig'indisi: 6 kirdi, 1 xodim", mc2.get("in") == 6 and mc2.get("staff_in") == 1,
           str(mc2))
        ok("Tuzatilgan daqiqa bulut navbatiga qayta qo'yildi", any(r.get("in") == 6 and r["minute"] == t0 for r in sent),
           str(sent)[:120])
        # AI kechikdi: o'tish yozilganda uning daqiqasi allaqachon yopilgan — daqiqa qatori darhol to'g'rilanadi
        sent.clear()
        walk(c, 60, 640, 200, 640, 650, 16, t0 + 52, zs=zs)
        row = db.row("SELECT extra FROM metrics_minute WHERE minute=? AND source_id=?", (t0, sid))
        ok("Kechikkan o'tish: yopilgan daqiqa (lokal va bulut) 7 kirdi", '"in": 7' in (row or {}).get("extra", "")
           and any(r.get("in") == 7 and r["minute"] == t0 for r in sent), str(row))
        sent.clear()
        ok("O'zgarmagan daqiqa qayta yuborilmaydi", metrics.recount(sid, t0) is False and not sent)
        # Dastur daqiqa o'rtasida to'xtadi: o'tish yozilgan, daqiqa qatori yo'q → ishga tushishda tiklanadi
        m_lost = t0 - 1200
        db.execute("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours, x, y)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (m_lost + 30, sid, "Kirish", 1, "Eshik", "in", 99, 0, 0, 0.5, 0.5))
        sent.clear()
        n_fix = metrics.backfill_counts()
        row = db.row("SELECT * FROM metrics_minute WHERE minute=? AND source_id=?", (m_lost, sid))
        ok("Yozilmay qolgan daqiqa tiklandi (1 kirdi, bulutga ham)", n_fix == 1 and row is not None
           and '"in": 1' in (row["extra"] or "") and row["persons_avg"] is None
           and any(r["minute"] == m_lost and r.get("in") == 1 for r in sent), f"{n_fix} {row}")
        sent.clear()
        ok("Qayta ishga tushishda takror yuborilmaydi", metrics.backfill_counts() == 0 and not sent)
    finally:
        sync.enqueue_metrics = real_enq
    # Ish vaqti tugagach chiqqan xaridor "ichkarida" dan ayiriladi (kirdi/chiqdi xaridorlar soniga esa kirmaydi)
    s9 = 9000
    lt = time.localtime()
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 8, 30, 0, 0, 0, -1))
    with db.tx() as cn:
        for tt, d, oh in ((base + 13.4 * 3600, "in", 0), (base + 13.6 * 3600, "out", 1)):
            cn.execute("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours,"
                       " x, y) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (tt, s9, "Kech", 1, "Eshik", d, 1, 0, oh, 0.5, 0.5))
    sm = counting.summary(base - 3600, base + 86400, s9)
    ok("21:54 kirdi, 22:06 chiqdi: kirdi 1, chiqdi 0 (xaridor), ichkarida 0, kun oxiri farqi 0%",
       sm["in"] == 1 and sm["out"] == 0 and sm["inside"] == 0 and sm["drift_pct"] == 0.0, str(sm)[:160])
    mc9 = counting.minute_counts(s9, int((base + 13.6 * 3600) // 60 * 60))
    ok("Daqiqalik yig'indida ish vaqtidan tashqari yo'nalish bo'yicha (bulut uchun)", mc9.get("off_out") == 1
       and mc9.get("offhours") == 1 and mc9.get("out") == 0, str(mc9))
    db.execute("DELETE FROM counts WHERE source_id=?", (s9,))
    # Kun oxiri farqi biznes kunida: kecha 21:55 kirgan, bugun 00:30 da chiqqan — kechagi farq 0% (yarim tun emas)
    d_y = counting.day_start() - 86400
    with db.tx() as cn:
        for tt, d in ((d_y + 21.9 * 3600, "in"), (d_y + 24.5 * 3600, "out")):
            cn.execute("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours,"
                       " x, y) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (tt, s9, "Kech", 1, "Eshik", d, 1, 0, 0, 0.5, 0.5))
    db.set_settings({"work_hours": "09:00-22:00"})
    yrow = [r for r in counting.daily(3, s9) if r["day"] == d_y][0]
    db.set_settings({"work_hours": ""})
    ok("Kun oxiri farqi biznes kunida (03:30–03:30): yarim tundan keyin chiqqan ham hisobda — 0%",
       yrow["drift_pct"] == 0.0 and yrow["in"] == 1, str(yrow))
    db.execute("DELETE FROM counts WHERE source_id=?", (s9,))
    ok("Video tahlili (persist=False) bazaga yozmaydi", (lambda cc: (walk(cc, 1, 640, 200, 640, 650, 16, t0, zs=zs),
                                                                     db.scalar("SELECT COUNT(*) FROM counts"))[1])(
        counting.LineCounter(sid, "x", persist=False)) == 11)


def web_suite() -> None:
    print("\n--- Panel: zonalar, Tashriflar API, qo'lda sanash sinovi ---")
    from fastapi.testclient import TestClient

    from app import config, visits, web

    c = TestClient(web.app)
    r = c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"}, follow_redirects=False)
    ok("Kirish", r.status_code == 303, r.headers.get("location", ""))
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES('Eshik kamera', 'rtsp', 'rtsp://y', 0, ?)",
                     (time.time(),))
    bad = [c.post(f"/api/sources/{sid}/zones", json={"kind": "chiziq", "points": pts}).status_code
           for pts in ([[0.1, 0.5]], [[0.1, 0.5], [0.9, 0.5], [0.5, 0.9]], [[0.5, 0.5], [0.51, 0.5]])]
    ok("Chiziq: 1 nuqta, 3 nuqta, juda qisqa — rad (400)", bad == [400, 400, 400], str(bad))
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "chiziq", "points": [[0.1, 0.6], [0.9, 0.6]], "name": "Asosiy eshik"})
    ok("Chiziq (2 nuqta) saqlandi va qaytarildi", r.status_code == 200 and len(r.json()["zone"]["points"]) == 2, r.text[:100])
    zid = r.json()["zone"]["id"]
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "polka", "points": [[0.1, 0.5], [0.9, 0.5]]})
    ok("Ko'pburchak zona 2 nuqta bilan — rad (400)", r.status_code == 400)
    r = c.get(f"/sources/{sid}/zones")
    ok("Zonalar sahifasi chiziq turini taklif qiladi", r.status_code == 200 and "Sanash chizig" in r.text)
    zones_mod.invalidate(sid)
    cnt = counting.LineCounter(sid, "Eshik kamera", persist=True)
    zs = zones_mod.load(sid)
    t0 = time.time() - 120
    for k in range(4):
        walk(cnt, 500 + k, 640, 250, 640, 600, 16, t0 + k * 4, zs=zs)
    walk(cnt, 600, 640, 600, 640, 250, 16, t0 + 30, zs=zs)
    r = c.get("/api/counts/today").json()
    ok("Bugun: kirdi/chiqdi, ichkarida, soatlik, oxirgilar, chiziqlar", r["in"] >= 4 and r["out"] >= 1
       and r["inside"] == r["in"] - r["out"] and sum(r["hours"]) == r["in"] and r["last"] and r["lines"], str(
           {k: r[k] for k in ("in", "out", "inside")}))
    s_ = c.get("/api/summary").json()
    ok("Boshqaruv paneli: tashriflar kartasi ma'lumoti", s_.get("visits") and s_["visits"]["in"] == r["in"], str(s_.get("visits")))
    d = c.get("/api/counts/daily?days=7").json()["days"]
    ok("Kunlik jadval: 7 kun, bugun birinchi", len(d) == 7 and d[0]["in"] == r["in"])
    csv_ = c.get("/api/export/counts.csv?days=7")
    ok("CSV eksport (UTF-8 BOM, sarlavha, qatorlar)", csv_.status_code == 200 and csv_.text.startswith("\ufeffvaqt")
       and csv_.text.count("kirdi") >= 4)
    ok("Ish vaqti: noto'g'ri ko'rinish — 400; to'g'ri — saqlanadi",
       c.post("/api/counts/work-hours", json={"work_hours": "9-22"}).status_code == 400 and
       c.post("/api/counts/work-hours", json={"work_hours": "09:00-22:00"}).json()["work_hours"] == "09:00-22:00")
    c.post("/api/counts/work-hours", json={"work_hours": ""})
    c.post("/api/counts/reset-inside")
    ok("'Hozir ichkarida'ni 0 qilish", c.get("/api/counts/today").json()["inside"] == 0)
    # Qo'lda sanash sinovi: 6 kirdi va 2 chiqdi bosiladi; avtomatik: 5 kirdi (bittasi o'tkazib yuborilgan), 2 chiqdi
    r = c.post("/api/counts/sessions", json={"source_id": str(sid)}).json()
    ss = r["session"]["id"]
    ok("Ikkinchi sinovni bir vaqtda boshlab bo'lmaydi (409)", c.post("/api/counts/sessions", json={}).status_code == 409)
    base = time.time()
    for _ in range(6):
        c.post(f"/api/counts/sessions/{ss}/tap", json={"dir": "in"})
    c.post(f"/api/counts/sessions/{ss}/tap", json={"dir": "out"})
    c.post(f"/api/counts/sessions/{ss}/tap", json={"dir": "out"})
    c.post(f"/api/counts/sessions/{ss}/tap", json={"dir": "out"})
    u = c.post(f"/api/counts/sessions/{ss}/undo").json()
    ok("Bosish va bekor qilish: 6 kirdi, 2 chiqdi", u["n_in"] == 6 and u["n_out"] == 2, str(u))
    for k in range(5):
        walk(cnt, 700 + k, 640, 250, 640, 600, 8, base - 0.4 + k * 0.01, zs=zs)
    for k in range(2):
        walk(cnt, 800 + k, 640, 600, 640, 250, 8, base - 0.4 + k * 0.01, zs=zs)
    res = c.post(f"/api/counts/sessions/{ss}/stop").json()["session"]
    ok("Solishtirish: kirdi 6/5 → 83.3%, chiqdi 2/2, jami 87.5%; 5 juftlangan, 1 o'tkazib yuborilgan",
       res["in"]["manual"] == 6 and res["in"]["auto"] == 5 and abs(res["in"]["accuracy"] - 5 / 6) < 1e-3
       and res["out"]["accuracy"] == 1.0 and abs(res["accuracy"] - 0.875) < 1e-3 and res["in"]["matched"] == 5
       and res["in"]["missed"] == 1, str(res)[:200])
    ok("Qisqa sinov darvozaga kirmaydi", res["gate_ok"] is False)
    lst = c.get("/api/counts/sessions").json()
    ok("Sinovlar ro'yxati va darvoza holati", lst["sessions"][0]["id"] == ss and lst["gate"]["ok"] is False)
    ok("Tugagan sinovga bosib bo'lmaydi (409)", c.post(f"/api/counts/sessions/{ss}/tap", json={"dir": "in"}).status_code == 409)
    from app import count_eval as ce

    taps = [[1000 + k * 20, "in"] for k in range(100)]
    autos = [{"t": 1001 + k * 20, "dir": "in"} for k in range(85)] + [{"t": 3500 + k * 20, "dir": "in"} for k in range(15)]
    cm = ce.compare(taps, autos)
    fake = {**cm, "ended_at": 1, "duration": 3600}
    ok("Son bir xil (100/100), lekin 15 ta o'tkazib yuborilgan + 15 ortiqcha: aniqlik 100%, juftlash 85% — darvozadan o'tmaydi",
       cm["accuracy"] == 1.0 and cm["match_rate"] == 0.85 and not ce.session_ok(fake), str({k: cm[k] for k in ("accuracy", "match_rate")}))
    autos90 = [{"t": 1001 + k * 20, "dir": "in"} for k in range(90)] + [{"t": 3500 + k * 20, "dir": "in"} for k in range(10)]
    autos97 = [{"t": 1002.5 + k * 20, "dir": "in"} for k in range(97)] + [{"t": 3500 + k * 20, "dir": "in"} for k in range(3)]
    c90, c97 = ce.compare(taps, autos90), ce.compare(taps, autos97)
    ok("10 ta o'tkazib yuborilgan + 10 ortiqcha (juftlash 90%) — o'tmaydi; 3+3, 2.5 s kechikish bilan (97%) — o'tadi",
       not ce.session_ok({**c90, "ended_at": 1, "duration": 3600})
       and ce.session_ok({**c97, "ended_at": 1, "duration": 3600}), f"{c90['match_rate']} / {c97['match_rate']}")
    cmp = visits.compare([[100, "in"], [200, "in"], [300, "out"]], [{"t": 101, "dir": "in"}, {"t": 260, "dir": "in"},
                                                                  {"t": 300.5, "dir": "out"}, {"t": 400, "dir": "out"}])
    ok("Juftlash: vaqt farqi > 3 s — juft emas (son bir xil bo'lsa ham)", cmp["in"]["matched"] == 1 and
       cmp["in"]["accuracy"] == 1.0 and cmp["out"]["extra"] == 1, str(cmp))
    today = c.get("/api/counts/today").json()
    occ = visits.occupancy()
    ok("Bulutga hello: hozir ichkarida va bugungi kirdi/chiqdi", occ is not None and occ["inside"] == today["inside"]
       and occ["in"] == today["in"] and occ["since"] == today["inside_since"], str(occ))
    from app import sync

    sent: dict = {}
    sw = sync.SyncWorker(url="http://127.0.0.1:9", token="sinov-tokeni", manager=None)
    sw._post = lambda path, payload, timeout=15: (sent.update(payload), (500, {}))[1]
    try:
        sw.hello()
    except Exception as e:  # noqa: BLE001
        sent["error"] = repr(e)
    finally:
        sw.client.close()
    ok("sync.hello yuboradigan ma'lumotda occupancy bor (vaqt ham)", sent.get("occupancy", {}).get("inside") == today["inside"]
       and isinstance(sent.get("time"), float), str({k: sent.get(k) for k in ("occupancy", "error")}))
    r = c.get("/visits")
    ok("Tashriflar sahifasi ochiladi, menyuda bor", r.status_code == 200 and "Qo'lda sanash sinovi" in r.text
       and 'href="/visits"' in r.text)
    c.post(f"/api/zones/{zid}/delete")


def gate_suite() -> None:
    """tests/count_gate.py (buyruq qatori) va panel darvozasi bir xil mezon bilan ishlaydi."""
    print("\n--- Darvoza: tests/count_gate.py ---")
    import subprocess

    from fastapi.testclient import TestClient

    from app import config, visits, web
    from app.config import DB_PATH

    gate_py = Path(__file__).resolve().parent / "count_gate.py"

    def run_gate() -> tuple[int, str]:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, str(gate_py), "--db", str(DB_PATH)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    code, out = run_gate()
    ok("Ma'lumot yetarli emas — chiqish kodi 2", code == 2, out.strip().splitlines()[-1] if out.strip() else out)
    today = counting.day_start()
    rows = []

    def session(t0: float, n_in: int, n_out: int, miss_in: int = 0, extra_out: int = 0) -> None:
        taps = [[t0 + 30 + k * 50, "in"] for k in range(n_in)] + [[t0 + 55 + k * 50, "out"] for k in range(n_out)]
        for t, d in taps[miss_in:]:
            rows.append((t + 1.2, d))
        for k in range(extra_out):
            rows.append((t0 + 40 + k * 300, "out"))
        db.execute("INSERT INTO count_sessions(started_at, ended_at, source_id, taps, note) VALUES(?,?,?,?,?)",
                   (t0, t0 + 3600, None, __import__("json").dumps(taps), "darvoza sinovi"))

    session(today - 2 * 86400 + 11 * 3600, 60, 58)
    session(today - 86400 + 11 * 3600, 70, 70, miss_in=2)          # 68/70 kirdi → 97.1% jami
    for i in range(1, 5):                                            # 4 kun: kunduzi qo'shimcha 100 kirdi / 99 chiqdi
        base = today - i * 86400 + 14 * 3600
        rows.extend((base + k * 30, "in") for k in range(100))
        rows.extend((base + 15 + k * 30, "out") for k in range(99))
    with db.tx() as c:
        c.executemany("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours, "
                      "x, y) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [(t, 1, "Eshik", 1, "Eshik", d, 0, 0, 0, 0.5, 0.5) for t, d in rows])
    code, out = run_gate()
    ok("2 ta 1 soatlik sinov ≥95% va kunlik og'ish ≤5% — O'TDI (kod 0)", code == 0 and "O'TDI" in out,
       out.strip().splitlines()[-1])
    cl = TestClient(web.app)
    cl.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"})
    g = cl.get("/api/counts/sessions").json()["gate"]
    ok("Panel darvozasi ham o'tdi (bir xil mezon)", g["ok"] is True and g["passed"] >= 2, str(g))
    rows.clear()
    session(today - 3600 * 2 + 1, 50, 50, extra_out=8)              # eng yangisi: chiqdi 58/50 → 92%
    with db.tx() as c:
        c.executemany("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, offhours, "
                      "x, y) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [(t, 1, "Eshik", 1, "Eshik", d, 0, 0, 0, 0.5, 0.5) for t, d in rows])
    code, out = run_gate()
    ok("Yangi sinovda aniqlik 92% — O'TMADI (kod 1), maslahat bilan", code == 1 and "O'TMADI" in out
       and "Ko'pi ortiqcha" in out, out.strip().splitlines()[-1])
    g = cl.get("/api/counts/sessions").json()["gate"]
    ok("Panel ham o'tmadi deydi", g["ok"] is False, str(g))
    ok("count_gate bazani faqat o'qiydi (yozuvlar o'zgarmadi)",
       db.scalar("SELECT COUNT(*) FROM count_sessions WHERE note='darvoza sinovi'") == 3)
    assert visits  # import ishlatiladi


def hook_suite() -> None:
    """scripts/apply_counting_hook.py: egasining asl worker.py siga sanashni ulash (fayl almashtirilmaydi)."""
    print("\n--- worker.py ga ulash: scripts/apply_counting_hook.py ---")
    import importlib.util
    import subprocess

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("ach", root / "scripts" / "apply_counting_hook.py")
    ach = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ach)
    cur = (root / "app" / "ai" / "worker.py").read_text(encoding="utf-8")
    ok("Joriy worker.py ulangan (counting.hook_ok)", counting.hook_ok() and ach.MARK in cur)
    # Ulashdan oldingi holatni tiklash: 1.9.0 qatorlarini olib tashlash → qayta ulash → aynan joriy fayl
    lines = cur.split("\n")
    plain, skip = [], 0
    for i, ln in enumerate(lines):
        if skip:
            skip -= 1
            continue
        if ln.strip() == 'if getattr(self, "counter", None) is None:':
            skip = 2
            continue
        if ln.strip() == "if counting.lines_from(self.zones):":
            skip = 1
            continue
        if ln.strip() == 'if getattr(self.proc, "counter", None) is not None:':
            skip = 1
            continue
        if "# 1.9.0" in ln:
            continue
        plain.append(ln)
    plain_txt = "\n".join(plain)
    new, msgs = ach.patch_text(plain_txt)
    ok("Ulanmagan nusxaga qo'shimchalar (6 majburiy + ixtiyoriy) kiradi va natija joriy fayl bilan bir xil",
       new == cur and sum(m.startswith("OK") for m in msgs) >= 6, "; ".join(msgs))
    no_loop = plain_txt.replace("self.loop_no += 1", "self.loop_no = self.loop_no + 1")
    new_nl, msgs_nl = ach.patch_text(no_loop)
    ok("Ixtiyoriy joy (video aylanishi) topilmasa — qolganlari ulanadi, ogohlantirish bilan",
       new_nl is not None and ach.MARK in new_nl and any(m.startswith("!") for m in msgs_nl), "; ".join(msgs_nl)[-160:])
    broken = plain_txt.replace("if self.mode == CALM:", "if self.mode in (CALM,):")
    new2, msgs2 = ach.patch_text(broken)
    ok("Tuzilishi boshqacha fayl — o'zgartirilmaydi (None, sababi bilan)", new2 is None and "interval" in msgs2[0], msgs2[0])
    tmp = Path(tempfile.mkdtemp(prefix="hook_")) / "app" / "ai"
    tmp.mkdir(parents=True)
    f = tmp / "worker.py"
    f.write_bytes(plain_txt.replace("\n", "\r\n").encode("utf-8"))
    r = subprocess.run([sys.executable, str(root / "scripts" / "apply_counting_hook.py"), "--file", str(f),
                        "--no-import-check"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    after = f.read_bytes()
    ok("CRLF fayl: ulanadi, satr oxirlari CRLF qoladi", r.returncode == 0 and b"\r\n" in after
       and after.decode("utf-8").replace("\r\n", "\n") == cur, r.stdout[-200:])
    r2 = subprocess.run([sys.executable, str(root / "scripts" / "apply_counting_hook.py"), "--file", str(f),
                         "--no-import-check"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=60)
    ok("Qayta ishga tushirish zararsiz (allaqachon ulangan)", r2.returncode == 0 and "allaqachon" in r2.stdout
       and f.read_bytes() == after)
    f.write_text(broken, encoding="utf-8")
    r3 = subprocess.run([sys.executable, str(root / "scripts" / "apply_counting_hook.py"), "--file", str(f),
                         "--no-import-check"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=60)
    ok("Ulab bo'lmasa — chiqish kodi 1, fayl o'zgarmaydi", r3.returncode == 1 and f.read_text(encoding="utf-8") == broken)
    f.write_bytes(b"\xef\xbb\xbf" + plain_txt.encode("utf-8"))           # Notepad BOM bilan saqlagan
    r4 = subprocess.run([sys.executable, str(root / "scripts" / "apply_counting_hook.py"), "--file", str(f),
                         "--no-import-check"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=60)
    b4 = f.read_bytes()
    ok("BOM bilan saqlangan fayl ham ulanadi, BOM saqlanadi", r4.returncode == 0 and b4.startswith(b"\xef\xbb\xbf")
       and b4[3:].decode("utf-8") == cur, r4.stdout[-160:])
    ok("Vaqtinchalik fayl qolmadi", not list(tmp.glob("*.yangi")))


def main() -> int:
    logic_suite()
    store_suite()
    web_suite()
    gate_suite()
    hook_suite()
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
