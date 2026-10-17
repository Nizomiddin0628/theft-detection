"""2.2.0 xavfsizlik va salomatlik: skelet holati, yiqilish (tez, cho'kib, detektor yo'qotgan), salbiy harakatlar
(cho'kkalash, egilish, tiz cho'kish, polga o'tirish, uzoqlashish), "odam yotibdi", ID almashishi va past ishonchli
detektsiyalar, tungi kirish, gavjumlik (kadr va do'kon), to'silgan chiqish, olov (tajriba), Processor ulanishi,
Telegram matni va /xavfsizlik, o'qitish/baholashdan ajratish, SMS (Eskiz soxta serveri, HTTP shlyuz, chegara), panel
API va sahifa, darvoza hisobi va buyruq qatori, bulut matni va min_score.

    python -m tests.test_safety

Eslatma: sintetik skeletlar — mantiq sinovi. Haqiqiy detektor bilan: tests/test_safety_video.py (ambianic videolari);
do'kondagi aniqlik faqat Xavfsizlik sahifasidagi darvoza bilan o'lchanadi.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_safety_")   # hech qachon jonli data/ emas
os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_safety_cloud_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402  (soxta SMS serverlari: izohlar modul darajasida topilsin)
from fastapi.responses import JSONResponse  # noqa: E402

from app import config, db, safety_eval as se  # noqa: E402
from app.ai import safety  # noqa: E402
from tests import safety_sim as sim  # noqa: E402

results: list[bool] = []
ESKIZ_PORT, HTTPGW_PORT = 8781, 8782


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def run_falls(segs, fps=8.0, **kw) -> list[tuple[float, str]]:
    fd = safety.FallDetector()
    got = []
    for t, ps in sim.sequence(segs, fps=fps, **kw):
        got += [(round(a["t"], 2), a["kind"]) for a in fd.update(ps, t, 720)]
    got += [(round(a["t"], 2), a["kind"]) for a in fd.flush(1e12)]
    return got


def local_ts(h: int, m: int, day_offset: int = 0) -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, h, m, 0, 0, 0, -1))


# ============================================================================ 1. skelet holati
def pose_suite() -> None:
    print("\n--- Skelet holati (tik / yotgan / oraliq) ---")
    H = 400.0

    def state(body, h_ref=H, **kw):
        k, b = sim.to_image(body, 640, 600, H, **kw)
        return safety.classify(b, k, h_ref, 720)

    ok("Tik turgan — tik", state(sim.STAND).state == safety.UP)
    ok("Yonboshlab yotgan — yotgan", state(sim.LYING).state == safety.DOWN)
    ok("Cho'kkalagan (tik gavda) — yotgan emas", state(sim.SQUAT).state != safety.DOWN)
    ok("Egilgan (son baland) — yotgan emas", state(sim.BEND).state != safety.DOWN)
    ok("Tiz cho'kkan — yotgan emas", state(sim.KNEEL).state != safety.DOWN)
    ok("Polda o'tirgan (tik gavda) — yotgan emas, tik ham emas", state(sim.SIT_FLOOR).state == safety.MID)
    ok("45° qiyshaygan — hali yotgan emas", state(sim.rotated(45)).state != safety.DOWN)
    k, b = sim.to_image(sim.LYING, 640, 600, H)
    ok("Yotgan odam: skeletdan tik bo'y ≈ ramka balandligidan ancha katta (yangi trek uchun H_ref)",
       safety.skeleton_height(k) > 2.0 * float(b[3] - b[1]), f"{safety.skeleton_height(k):.0f} vs {b[3] - b[1]:.0f}")
    # Kadr pastiga tegib turgan tik odam (oyoqlari kadrdan tashqarida): son "pastda" ko'rinadi, lekin yotgan emas
    k, b = sim.to_image(sim.STAND, 640, 900, 600, frame_h=720)
    p = safety.classify(b, k, 600, 720)
    ok("Kadr pastida kesilgan tik odam — yotgan emas", p.truncated and p.state != safety.DOWN, p.state)
    # Son ko'rinmasa: faqat ramka shakli
    k, b = sim.to_image(sim.LYING, 640, 600, H)
    k[11:13, 2] = 0.1
    ok("Son ko'rinmaydi, ramka yassi va past — yotgan", safety.classify(b, k, H, 720).state == safety.DOWN)
    k, b = sim.to_image(sim.STAND, 640, 600, H)
    k[11:13, 2] = 0.1
    ok("Son ko'rinmaydi, tik ramka — tik", safety.classify(b, k, H, 720).state == safety.UP)


# ============================================================================ 2. ssenariylar
def scenario_suite() -> None:
    print("\n--- Yiqilish va yotish ssenariylari (4 va 8 kadr/s, shovqin bilan) ---")
    for name, (segs, exp) in sim.SCENARIOS.items():
        bad = []
        for fps, noise in itertools.product((4.0, 8.0), (0.0, 0.012)):
            got = [k for _, k in run_falls(segs, fps=fps, noise=noise, seed=5)]
            if not ((exp is None and not got) or (exp is not None and got == [exp])):
                bad.append(f"{fps:g} kadr/s, shovqin {noise}: {got}")
        ok(f"{name} → {exp or 'signal yo`q'}", not bad, "; ".join(bad))
    fd = safety.FallDetector()
    al = []
    for t, ps in sim.sequence(sim.SCENARIOS["oldinga yiqilish"][0], fps=8):
        al += fd.update(ps, t, 720)
    ok("Yiqilish signali yotgan holatdan 2 s keyin (tasdiq), boshlanishi — oxirgi tik kadr",
       len(al) == 1 and abs(al[0]["t"] - al[0]["susp_end"] - 2.0) < 0.2 and 2.8 <= al[0]["susp_start"] <= 3.6,
       str([(a["t"], a["susp_start"], a["susp_end"]) for a in al]))


def sweep_suite() -> None:
    print("\n--- Keng sinov: davomiylik, o'lcham, yo'nalish, shovqin, yo'qolish ---")
    n = miss = 0
    for fps, dur, hpx, sign, noise, lost in itertools.product((4, 8), (0.5, 0.9, 1.4, 1.8), (140, 260, 450), (1, -1),
                                                             (0.0, 0.015), (False, True)):
        segs = [(3, sim.const(sim.STAND)), (dur, sim.rot(0, 90 * sign))]
        segs += ([(0.3, sim.const(sim.rotated(90 * sign))), (5, sim.gone())] if lost
                 else [(6, sim.const(sim.rotated(90 * sign)))])
        got = [k for _, k in run_falls(segs, fps=fps, hpx=hpx, noise=noise, seed=int(dur * 10 + hpx))]
        n += 1
        miss += got != ["fall"]
    ok(f"Yiqilishlar: {n - miss}/{n} topildi (har biri aynan bitta signal)", miss == 0)
    n = fp = 0
    for fps, sp, hpx, noise, pose in itertools.product((4, 8), (0.4, 0.8, 1.5), (140, 260, 450), (0.0, 0.015),
                                                       ("SQUAT", "BEND", "KNEEL", "SIT_FLOOR")):
        P = getattr(sim, pose)
        segs = [(2, sim.const(sim.STAND)), (sp, sim.morph(sim.STAND, P)), (8, sim.const(P)),
                (sp, sim.morph(P, sim.STAND)), (2, sim.const(sim.STAND))]
        got = run_falls(segs, fps=fps, hpx=hpx, noise=noise, seed=int(sp * 10 + hpx))
        n += 1
        fp += bool(got)
    for fps, fn in itertools.product((4, 8), (lambda x: max(0.4, 1 - 0.12 * x), lambda x: min(1.6, 0.6 + 0.12 * x))):
        n += 1
        fp += bool(run_falls([(8, sim.const(sim.STAND))], fps=fps, hpx=300, noise=0.01, scale_fn=fn))
    ok(f"Salbiy harakatlar (cho'kkalash, egilish, tiz cho'kish, polga o'tirish, uzoqlashish/yaqinlashish): "
       f"{fp}/{n} xato signal", fp == 0)


def track_suite() -> None:
    print("\n--- ID almashishi, past ishonchli detektsiya, bir nechta odam ---")
    fd = safety.FallDetector()
    alerts = []
    seq = sim.sequence([(3, sim.const(sim.STAND)), (0.8, sim.rot(0, 90)), (6, sim.const(sim.LYING))], fps=8)
    for t, ps in seq:
        for p in ps:
            if t > 4.2:
                p.track_id = 7               # to'silishdan keyin kuzatuvchi yangi ID berdi
        alerts += fd.update(ps, t, 720)
    ok("Yiqilgandan keyin ID almashdi — bitta yiqilish signali, 'yotibdi' takrorlanmaydi",
       [a["kind"] for a in alerts] == ["fall"], str([a["kind"] for a in alerts]))
    # Kuzatuvchi yerdagi odamni yangi trek qilmadi (past ishonch) — loose detektsiya o'sha odamning davomi
    fd = safety.FallDetector()
    alerts = []
    for t, ps in sim.sequence([(3, sim.const(sim.STAND)), (0.5, sim.rot(0, 50)), (0.4, sim.gone()),
                               (4, sim.const(sim.LYING))], fps=8):
        if ps and t > 3.4:
            alerts += fd.update([], t, 720, loose=[sim.Pp(-1, ps[0].box, ps[0].kpts, 0.3)])
        else:
            alerts += fd.update(ps, t, 720)
    ok("Past ishonchli (trekka ulanmagan) yotgan odam detektsiyasi yo'qolgan trek davomi — yiqilish",
       [a["kind"] for a in alerts] == ["fall"], str([a["kind"] for a in alerts]))
    # Yordamchi (tik odam) yiqilgan joyga keldi, yiqilgan odamni detektor ko'rmayapti — signal bekor (reja qoidasi)
    fd = safety.FallDetector()
    alerts = []
    for t, ps in sim.sequence([(3, sim.const(sim.STAND)), (0.8, sim.rot(0, 90)), (0.3, sim.const(sim.LYING)),
                               (4, sim.gone())], fps=8):
        if 4.3 < t < 5.0:
            k, b = sim.to_image(sim.STAND, 700, 600, 400)
            ps = [sim.Pp(9, b, k)]
        alerts += fd.update(ps, t, 720)
    ok("Yiqilgan joyda (o'sha bo'yda) tik odam paydo bo'ldi — signal bekor (turib ketgan deb hisoblanadi)",
       not alerts, str([a["kind"] for a in alerts]))
    # Yonidan o'tgan bola (yiqilgan odamning yarim bo'yi) bekor qilmaydi
    fd = safety.FallDetector()
    alerts = []
    for t, ps in sim.sequence([(3, sim.const(sim.STAND)), (0.8, sim.rot(0, 90)), (0.3, sim.const(sim.LYING)),
                               (4, sim.gone())], fps=8):
        if 4.3 < t < 5.0:
            k, b = sim.to_image(sim.STAND, 700, 600, 180)
            ps = [sim.Pp(9, b, k)]
        alerts += fd.update(ps, t, 720)
    ok("Yonida kichik (bo'yi 45%) odam — bekor qilinmaydi, yiqilish signali bor", [a["kind"] for a in alerts] == ["fall"],
       str([a["kind"] for a in alerts]))
    # Ikki odam: biri yiqildi, ikkinchisi yurib turibdi
    fd = safety.FallDetector()
    alerts = []
    a_seq = sim.sequence([(3, sim.const(sim.STAND)), (0.8, sim.rot(0, 90)), (6, sim.const(sim.LYING))], fps=8, cx=300)
    b_seq = sim.sequence([(9.8, sim.const(sim.STAND))], fps=8, tid=2, cx=1000)
    for (t, pa), (_, pb) in zip(a_seq, b_seq):
        alerts += fd.update(pa + pb, t, 720)
    ok("Ikki odam: yiqilgani uchun bitta signal, track_id to'g'ri", [(a["kind"], a["track_id"]) for a in alerts] ==
       [("fall", 1)], str([(a["kind"], a["track_id"]) for a in alerts]))
    a = alerts[0] if alerts else {}
    ok("Signal formati: ball 100, daraja yuqori, pose_seq bo'sh, sabablar, ramka",
       a.get("score") == 100 and a.get("level") == "yuqori" and a.get("pose_seq") == [] and len(a.get("reasons", [])) >= 2
       and a.get("box") and a.get("kind_label") == "Yiqilish" and a["susp_start"] <= a["susp_end"] <= a["t"])
    # Review (2.2.0): o'sha ID qaytdi va tik turdi — signal bekor bo'lishi kerak
    fd = safety.FallDetector()
    alerts = []
    for t, ps in sim.sequence([(3, sim.const(sim.STAND)), (0.8, sim.rot(0, 90)), (0.3, sim.const(sim.LYING)),
                               (0.5, sim.gone()), (5, sim.const(sim.STAND))], fps=8):
        alerts += fd.update(ps, t, 720)
    ok("Yiqilib, 0,5 s ko'rinmadi, o'sha ID bilan tik turdi — signal yo'q", not alerts, str([a["kind"] for a in alerts]))
    # Detektor yotgan odamni >6 s yo'qotib, qayta topadi — "yotibdi" har safar takrorlanmasin
    fd = safety.FallDetector()
    alerts = []
    segs = [(24, sim.const(sim.LYING)), (7, sim.gone())] * 6
    for t, ps in sim.sequence(segs, fps=8):
        for p in ps:
            p.track_id = 100 + int(t // 31)                # har qaytishda yangi ID
        alerts += fd.update(ps, t, 720)
    ok("Yotgan odam 3 daqiqa (goh ko'rinmaydi, ID almashadi) — 'yotibdi' 10 daqiqada bir marta",
       [a["kind"] for a in alerts] == ["lying"], str([(a["kind"], round(a["t"])) for a in alerts]))
    for lsec, fps in ((60, 8), (120, 4), (300, 4)):
        fd = safety.FallDetector()
        al = []
        for t, ps in sim.sequence([(lsec + 15, sim.const(sim.LYING))], fps=fps):
            al += fd.update(ps, t, 720, lying_sec=lsec)
        ok(f"'Yotibdi' chegarasi {lsec} s ({fps} kadr/s) — ishlaydi", [a["kind"] for a in al] == ["lying"] and
           abs(al[0]["t"] - lsec) < 1.0, str([(a["kind"], a["t"]) for a in al]))
    # O'chiq qoida
    fd = safety.FallDetector()
    out = []
    for t, ps in sim.sequence(sim.SCENARIOS["oldinga yiqilish"][0], fps=8):
        out += fd.update(ps, t, 720, fall_on=False, lying_on=False)
    ok("Yiqilish va yotish o'chiq — signal yo'q", not out)


# ============================================================================ 3. boshqa qoidalar
class FakeZones:
    def __init__(self, zs=None):
        self.zones = zs or []

    def __bool__(self):
        return bool(self.zones)

    def of(self, kind):
        return [z for z in self.zones if z.kind == kind]

    def motion_mask(self, w, h):
        return None


def night_suite() -> None:
    print("\n--- Tungi kirish ---")
    db.set_settings({"work_hours": "09:00-21:00", "safety_night_hours": "", "safety_night_grace_min": "20"})
    s = db.get_settings(force=True)
    sch = safety.night_schedule(s)
    ok("Jadval ish vaqtidan: 21:10 (yopilgandan 10 daqiqa) — hali tun emas", not safety.is_night(local_ts(21, 10), sch))
    ok("21:25 — tun", safety.is_night(local_ts(21, 25), sch))
    ok("03:00 — tun", safety.is_night(local_ts(3, 0), sch))
    ok("08:45 (ochilishdan 15 daqiqa oldin) — tun emas", not safety.is_night(local_ts(8, 45), sch))
    ok("12:00 — ish vaqti", not safety.is_night(local_ts(12, 0), sch))
    own = safety.night_schedule({**s, "safety_night_hours": "23:00-06:00"})
    ok("O'z jadvali 23:00-06:00: 22:30 emas, 23:30 va 05:59 — tun",
       own["mode"] == "own" and not safety.is_night(local_ts(22, 30), own) and safety.is_night(local_ts(23, 30), own)
       and safety.is_night(local_ts(5, 59), own))
    ok("Jadval yo'q (ish vaqti kiritilmagan) — qoida ishlamaydi",
       not safety.is_night(local_ts(3, 0), safety.night_schedule({"work_hours": "", "safety_night_hours": ""})))
    mon = safety.SafetyMonitor(1, "Zal")
    k, b = sim.to_image(sim.STAND, 640, 600, 400)
    p = [sim.Pp(3, b, k)]
    t0 = local_ts(23, 30)
    out = []
    for i in range(40):
        out += mon.update(p, t0 + i * 0.25, 1280, 720, None, None)
    ok("Tunda 10 s odam — aynan bitta signal (≥ 2 s dan keyin)", [a["kind"] for a in out] == ["night"] and
       out[0]["t"] - t0 >= 2.0, str([(a["kind"], round(a["t"] - t0, 2)) for a in out]))
    out = []
    for i in range(10):                                  # 2 daqiqadan kam yo'q bo'ldi — o'sha tashrif
        out += mon.update(p, t0 + 60 + i * 0.25, 1280, 720, None, None)
    ok("O'sha tashrif davomida takror signal yo'q", not out)
    cams = [safety.SafetyMonitor(10 + i, f"K{i}") for i in range(3)]
    out = []
    t1 = t0 + 3600
    for i in range(40):
        for m in cams:
            out += m.update(p, t1 + i * 0.25, 1280, 720, None, None)
    ok("Bitta kirgan odamni 3 kamera ko'rdi — bitta tungi signal (bitta SMS)", [a["kind"] for a in out] == ["night"],
       str(len(out)))
    mon2 = safety.SafetyMonitor(2, "Zal")
    out = mon2.update(p, t0, 1280, 720, None, None) + mon2.update([], t0 + 0.5, 1280, 720, None, None)
    ok("Bitta kadrdagi detektsiya (xato bo'lishi mumkin) — signal yo'q", not out)
    mon3 = safety.SafetyMonitor(3, "Zal")
    out = []
    for i in range(40):
        out += mon3.update(p, local_ts(12, 0) + i * 0.25, 1280, 720, None, None)
    ok("Kunduzi odam — tungi signal yo'q", not out)
    bg = safety.SafetyMonitor(None, "Video", background=True)
    out = []
    for i in range(40):
        out += bg.update(p, t0 + i * 0.25, 1280, 720, None, None)
    ok("Video tahlilida tungi qoida ishlamaydi", not out)


def crowd_suite() -> None:
    print("\n--- Gavjumlik ---")
    db.set_settings({"safety_crowd_cam": "3", "safety_crowd_sec": "60", "safety_cooldown_min": "10"})
    mon = safety.SafetyMonitor(1, "Zal")
    people = []
    for i in range(5):
        k, b = sim.to_image(sim.STAND, 150 + i * 220, 600, 300)
        people.append(sim.Pp(10 + i, b, k))
    out = []
    t0 = local_ts(12, 0)                                 # kunduzi (tungi qoida aralashmasin)
    for i in range(0, 300):                              # 75 s, har 0,25 s; 30–35 s oralig'ida 3 kishi (qisqa tushish)
        t = t0 + i * 0.25
        ps = people[:3] if 120 <= i < 140 else people
        out += mon.update(ps, t, 1280, 720, None, None)
    ok("5 kishi > 3 chegara, 60 s (5 s qisqa tushish bilan) — aynan bitta signal ~60 s da",
       [a["kind"] for a in out] == ["crowd"] and 59 <= out[0]["t"] - t0 <= 62,
       str([(a["kind"], round(a["t"] - t0, 1)) for a in out]))
    mon = safety.SafetyMonitor(1, "Zal")
    out = []
    for i in range(0, 280):
        t = t0 + i * 0.25
        ps = people[:2] if (i // 160) % 2 == 1 or 100 <= i < 160 else people   # 25 s da 15 s tushish — qaytadan
        out += mon.update(ps, t, 1280, 720, None, None)
    ok("Uzoq tushish (15 s) hisobni qaytadan boshlaydi — 60 s yetmadi, signal yo'q", not out)
    db.set_settings({"safety_crowd_cam": "0"})
    mon = safety.SafetyMonitor(1, "Zal")
    out = []
    for i in range(300):
        out += mon.update(people, t0 + i * 0.25, 1280, 720, None, None)
    ok("Chegara 0 — qoida o'chiq", not out)
    # Do'kon bo'yicha (sanash: "hozir ichkarida")
    db.set_settings({"safety_crowd_inside": "40", "safety_crowd_sec": "60", "safety_crowd": "1"})
    sc = safety.StoreCrowd()

    class M:
        got = []

        def inject_alert(self, a):
            self.got.append(a)

    m = M()
    res = [sc.check(m, now=t0 + i * 10, inside=45) for i in range(8)]
    ok("Do'konda 45 > 40 kishi, 60 s — bitta signal kameraga beriladi", len(M.got) == 1 and M.got[0]["kind"] == "crowd"
       and sum(r is not None for r in res) == 1, str(len(M.got)))
    sc.check(m, now=t0 + 200, inside=30)
    ok("Kamaygach darhol qayta signal yo'q", sc.check(m, now=t0 + 260, inside=50) is None)
    db.set_settings({"safety_crowd_inside": "0"})


def door_frame(blocked: float = 0.0, light: float = 0.0, dark: bool = False, person: bool = False) -> np.ndarray:
    rng = np.random.default_rng(1)
    img = np.full((360, 640, 3), 120, np.uint8)
    img[60:340, 400:560] = (60, 90, 140)                     # eshik
    for y in range(80, 330, 25):
        cv2.line(img, (410, y), (550, y), (200, 200, 200), 2)  # naqsh (chegaralar)
    cv2.rectangle(img, (470, 180), (490, 230), (30, 30, 30), -1)
    img = cv2.add(img, rng.integers(0, 12, img.shape, dtype=np.uint8))
    if blocked:
        h = int(280 * blocked)
        cv2.rectangle(img, (395, 340 - h), (565, 340), (40, 160, 200), -1)   # quti
        cv2.rectangle(img, (395, 340 - h), (565, 340), (20, 60, 90), 3)
    if person:
        cv2.rectangle(img, (440, 90), (520, 340), (90, 70, 60), -1)
    if light:
        img = cv2.add(img, np.full(img.shape, light, np.uint8))
    if dark:
        img = (img * 0.08).astype(np.uint8)
    return img


def exit_suite() -> None:
    print("\n--- Favqulodda chiqish to'silgan ---")
    from app.ai import zones as zones_mod

    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Eshik kamerasi", "rtsp", "rtsp://x", 0, 1, time.time()))
    pts = [[390 / 640, 50 / 360], [570 / 640, 50 / 360], [570 / 640, 345 / 360], [390 / 640, 345 / 360]]
    zid = db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
                     (sid, "Zaxira eshik", "favqulodda", json.dumps(pts), "", 1, time.time()))
    zone = db.row("SELECT * FROM zones WHERE id=?", (zid,))
    try:
        safety.remember_exit(zone, door_frame(), [np.array([450, 100, 500, 330])])
        ok("Zonada odam bo'lsa eslab qolinmaydi", False)
    except ValueError as e:
        ok("Zonada odam bo'lsa eslab qolinmaydi", "odam" in str(e))
    safety.remember_exit(zone, door_frame(), [])
    ok("Bo'sh holati eslab qolindi (rasm + baza)", safety.exit_ref_path(zid).exists() and
       db.row("SELECT * FROM safety_exits WHERE zone_id=?", (zid,)) is not None)
    zs = zones_mod.load(sid)
    z = zs.of("favqulodda")[0]
    ref = safety._exit_ref(z)
    g0, mask, _ = safety._zone_crop(door_frame(), z.pts)

    def frac(fr):
        g, m, _ = safety._zone_crop(fr, z.pts)
        return safety.exit_compare(ref, g, m)

    r0, r1, r2, r3 = frac(door_frame()), frac(door_frame(0.6)), frac(door_frame(light=25)), frac(door_frame(dark=True))
    ok("O'sha holat — farq ≈ 0", r0["ok"] and r0["frac"] < 0.05, f"{r0['frac']:.2f}")
    ok("Quti (60% balandlik) — farq ≥ 35%", r1["ok"] and r1["frac"] >= 0.35, f"{r1['frac']:.2f}")
    ok("Yorug'lik biroz oshdi (+25) — to'silgan emas", r2["ok"] and r2["frac"] < 0.2, f"{r2['frac']:.2f}")
    ok("Chiroq o'chgan (juda qorong'i) — solishtirilmaydi", not r3["ok"])
    db.set_settings({"work_hours": "", "safety_blocked": "1", "safety_blocked_min": "10", "safety_blocked_frac": "0.35"})
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    t0 = 5000.0
    for i in range(0, 140):                              # har 5 s, ~11,7 daqiqa to'silgan
        out += mon.update([], t0 + i * 5, 640, 360, door_frame(0.6), zs, detected=True)
    ok("10 daqiqa to'silgan — aynan bitta signal (≥ 600 s)", [a["kind"] for a in out] == ["blocked"] and
       out[0]["t"] - t0 >= 600, str([(a["kind"], a["t"] - t0) for a in out]))
    ok("Signal ramkasi — zona atrofi", out and out[0]["box"][0] >= 380 and out[0]["box"][2] <= 580)
    st = safety.EXIT_STATE.get(zid, {})
    ok("Panel uchun joriy holat (farq, qachondan)", st.get("frac") is not None and st.get("blocked_since") is not None)
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    for i in range(0, 140):
        k, b = sim.to_image(sim.STAND, 480, 340, 250, frame_h=360)
        ps = [sim.Pp(1, b, k)]
        out += mon.update(ps, t0 + i * 5, 640, 360, door_frame(0.6, person=True), zs)
    ok("Zonada odam turibdi (ko'rinishni to'sgan) — hisobga olinmaydi, signal yo'q", not out)
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    for i in range(0, 140):
        fr = door_frame(0.6) if i < 119 else door_frame()            # 9 daqiqa 55 s to'silgan, keyin bo'shatildi
        out += mon.update([], t0 + i * 5, 640, 360, fr, zs)
    ok("10 daqiqa to'lmasdan bo'shatildi — signal yo'q (review: 0% farq bilan signal)", not out,
       str([(a["kind"], a["t"] - t0) for a in out]))
    db.set_settings({"work_hours": "09:00-21:00"})
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    tn = local_ts(20, 50)
    for i in range(0, 50):                               # 20:50 dan to'silgan (yopilishdan oldin)
        out += mon.update([], tn + i * 5, 640, 360, door_frame(0.6), zs)
    tm = local_ts(9, 0, 1)
    for i in range(0, 10):                               # ertalab bo'sh
        out += mon.update([], tm + i * 5, 640, 360, door_frame(), zs)
    ok("Kechqurun qo'yilgan, ertalabgacha olingan quti — signal yo'q", not out)
    db.set_settings({"work_hours": ""})
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    for i in range(0, 60):
        out += mon.update([], t0 + i * 5, 640, 360, door_frame(0.6), zs)
    safety.remember_exit(zone, door_frame(), [])         # qayta eslab qolindi — hisob yangidan
    for i in range(60, 200):
        out += mon.update([], t0 + i * 5, 640, 360, door_frame(0.6), zs)
    ok("Bo'sh holati qayta eslab qolinsa — to'silish hisobi yangidan boshlanadi", [a["t"] - t0 for a in out] == [900],
       str([(a["kind"], a["t"] - t0) for a in out]))
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    for i in range(0, 140):
        fr = door_frame(0.6) if (i // 30) % 2 == 0 else door_frame()   # 2,5 daqiqa to'silgan / bo'sh almashib
        out += mon.update([], t0 + i * 5, 640, 360, fr, zs)
    ok("Qisqa to'silishlar (2,5 daqiqa) — signal yo'q", not out)
    db.set_settings({"work_hours": "09:00-10:00"})
    mon = safety.SafetyMonitor(sid, "Eshik kamerasi")
    out = []
    for i in range(0, 140):
        out += mon.update([], local_ts(3, 0) + i * 5, 640, 360, door_frame(0.6), zs)
    ok("Ish vaqtidan tashqari — tekshirilmaydi", not out)
    db.execute("UPDATE zones SET points=? WHERE id=?", (json.dumps([[0.5, 0.1], [0.9, 0.1], [0.9, 0.9], [0.5, 0.9]]), zid))
    zones_mod.invalidate(sid)
    safety._exit_cache.clear()
    z2 = zones_mod.load(sid).of("favqulodda")[0]
    ok("Zona o'zgartirilsa eslab qolingan rasm eskiradi (qayta eslab qolish kerak)", safety._exit_ref(z2) is None)
    db.set_settings({"work_hours": ""})


def fire_frames(n: int, static: bool = False, seed: int = 2):
    rng = np.random.default_rng(seed)
    for i in range(n):
        img = np.full((360, 640, 3), 90, np.uint8)
        if static:
            cv2.rectangle(img, (100, 200), (160, 280), (30, 140, 245), -1)          # to'q sariq quti, qimirlamaydi
        else:
            for _ in range(6):
                x, y = int(rng.integers(100, 160)), int(rng.integers(200, 280))
                r = int(rng.integers(6, 22))
                cv2.circle(img, (x, y), r, (40, int(rng.integers(150, 220)), 250), -1)
        yield img


def fire_suite() -> None:
    print("\n--- Olov (tajriba) ---")
    db.set_settings({"safety_fire": "1", "safety_fire_sec": "10", "safety_cooldown_min": "10"})
    mon = safety.SafetyMonitor(1, "Ombor")
    out = []
    for i, fr in enumerate(fire_frames(60)):            # 15 s, har 0,25 s
        out += mon.update([], 100 + i * 0.25, 640, 360, fr, None, detected=False)
    ok("Miltillovchi olov rangi 15 s — bitta signal (≥ 10 s)", [a["kind"] for a in out] == ["fire"] and out[0]["t"] - 100 >= 10,
       str([(a["kind"], a["t"] - 100) for a in out]))
    mon = safety.SafetyMonitor(1, "Ombor")
    out = []
    for i, fr in enumerate(fire_frames(60, static=True)):
        out += mon.update([], 100 + i * 0.25, 640, 360, fr, None, detected=False)
    ok("Qimirlamaydigan to'q sariq narsa — signal yo'q", not out)
    mon = safety.SafetyMonitor(1, "Ombor")
    out = []
    k, b = sim.to_image(sim.STAND, 130, 320, 200, frame_h=360)
    for i, fr in enumerate(fire_frames(60)):
        out += mon.update([sim.Pp(1, b, k)], 100 + i * 0.25, 640, 360, fr, None)
    ok("Rang odam ustida (kiyim) — hisobga olinmaydi", not out)
    db.set_settings({"safety_fire": "0"})
    mon = safety.SafetyMonitor(1, "Ombor")
    out = []
    for i, fr in enumerate(fire_frames(60)):
        out += mon.update([], 100 + i * 0.25, 640, 360, fr, None, detected=False)
    ok("Sukut bo'yicha (o'chiq) — signal yo'q", not out and config.DEFAULT_SETTINGS["safety_fire"] == "0")


def processor_suite() -> None:
    print("\n--- Processor ulanishi ---")
    from app.ai import worker

    p = worker.Processor("Sinov", source_id=None, background=True)
    ok("Processor ichida SafetyMonitor", isinstance(p.safety, safety.SafetyMonitor) and p.safety.background)
    fr = np.full((360, 640, 3), 100, np.uint8)
    try:
        res = [p.step(fr, i * 0.25)[2] for i in range(12)]
        ok("Bo'sh kadrlar: step() xatosiz, signal yo'q", all(r == [] for r in res))
    except Exception as e:  # noqa: BLE001
        ok("Bo'sh kadrlar: step() xatosiz, signal yo'q", False, repr(e))
    a = safety._alert("fall", 3, 100.0, [1, 2, 3, 4], ["x"])
    ok("Klip/rasm banneri xavfsizlik uchun (ball emas)", worker.alert_banner(a, True).startswith("XAVFSIZLIK: Yiqilish"))
    mon = safety.SafetyMonitor(1, "x")
    mon._update = lambda *a, **k: 1 / 0                   # qoidadagi xato o'g'irlik tahlilini to'xtatmasin
    safety.log.disabled = True                            # kutilgan xato — jurnalni to'ldirmasin
    ok("Xavfsizlik qoidasidagi xato — bo'sh ro'yxat, istisno tashqariga chiqmaydi", mon.update([], 1.0, 640, 360, fr) == [])
    safety.log.disabled = False
    import inspect

    src = inspect.getsource(worker.Processor.step)
    ok("step(): ikkala yo'lda ham (jim kadr va to'liq tahlil) safety.update", src.count("self.safety.update(") == 2)
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "apply_counting_hook.py"), "--check"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(ROOT))
    ok("apply_counting_hook.py --check: worker.py ulangan (2.2.0 o'zgarishlari buzmagan)", r.returncode == 0,
       (r.stdout + r.stderr)[-200:])


# ============================================================================ 4. Telegram, o'qitish, baholash
def make_event(kind: str, status: str = "yangi", score: float = 100.0, source_id=None, job_id=None, seq=None,
               created_at=None) -> int:
    from app.config import POSES_DIR

    ev = db.insert_event({"source_id": source_id, "source_name": "Zal", "job_id": job_id,
                          "created_at": created_at or time.time(), "video_time": None, "score": score,
                          "level": "yuqori", "kind": kind, "reasons": ["Sabab 1", "Sabab 2"], "breakdown": [["x", 100]],
                          "track_id": 1, "status": status, "susp_start": time.time() - 3, "susp_end": time.time()})
    name = f"event_{ev:05d}.json"
    (POSES_DIR / name).write_text(json.dumps({"event_id": ev, "t_event": 10.0, "kind": kind,
                                              "seq": seq or []}), encoding="utf-8")
    db.update_event(ev, pose_path=name)
    return ev


def telegram_suite() -> None:
    print("\n--- Telegram matni, /xavfsizlik, o'qitish va baholashdan ajratish ---")
    from app import telegram_bot

    bot = telegram_bot.TelegramBot(token="")
    ev = db.get_event(make_event("fall"))
    cap = bot._caption(ev, clip_pending=True)
    ok("Yiqilish: maxsus sarlavha, 'Bu ayblov emas' yo'q, ball qatori yo'q",
       "Yiqilish — yordam kerak bo'lishi mumkin" in cap and "ayblov" not in cap and "Ball:" not in cap, cap[:120])
    ev2 = db.get_event(make_event("pocket", score=80))
    cap2 = bot._caption(ev2)
    ok("O'g'irlik matni o'zgarmagan (sarlavha, ball, ayblov emas)",
       "Shubhali harakat" in cap2 and "Ball:" in cap2 and "ayblov emas" in cap2)
    for k in safety.SAFETY_KINDS:
        e = db.get_event(make_event(k))
        ok(f"Telegram sarlavhasi: {safety.KIND_LABELS[k]}", safety.HEADLINES[k] in bot._caption(e))
    ok("KIND_UZ: barcha yangi turlar", all(telegram_bot.KIND_UZ.get(k) == safety.KIND_LABELS[k] for k in safety.SAFETY_KINDS))
    txt = safety.status_text()
    ok("Bot /xavfsizlik matni (bugungi soni, oxirgilari)", "Xavfsizlik" in txt and "Yiqilish" in txt, txt[:120])
    ok("/yordam ichida /xavfsizlik", "/xavfsizlik" in telegram_bot.HELP)
    db.set_settings({"telegram_min_score": "90"})
    bot2 = telegram_bot.TelegramBot(token="x")
    ok("Telegram min ball (90) xavfsizlik signalini to'smaydi (ball 100)", bot2._enabled_for(ev["id"]) is not None)
    db.set_settings({"telegram_min_score": "0"})
    # O'qitish: xavfsizlik hodisalari o'g'irlik modeliga aralashmaydi
    from app.ml import train

    seq = [[10.0 - 0.3 * i, np.zeros((17, 3)).tolist(), [0, 0, 10, 10]] for i in range(8)]
    a = make_event("fall", status="tasdiqlangan", seq=seq)
    b = make_event("pocket", status="tasdiqlangan", seq=seq)
    groups = {w["group"] for w in train.feedback_windows()}
    ok("O'qitish: tasdiqlangan 'Yiqilish' olinmaydi, o'g'irlik olinadi", f"ev{b}" in groups and f"ev{a}" not in groups,
       str(groups))
    from app.ai.manager import archive_feedback
    from app.config import FEEDBACK_DIR

    e = db.get_event(a)
    archive_feedback(e)
    db.execute("DELETE FROM events WHERE id=?", (a,))
    groups = {w["group"] for w in train.feedback_windows()}
    ok("O'qitish: arxivdagi (o'chirilgan) yiqilish ham olinmaydi", (FEEDBACK_DIR / f"ev{a:05d}.json").exists()
       and f"ev{a}" not in groups)
    from app import evaluation
    import inspect

    src = inspect.getsource(evaluation.analyze)
    ok("evaluation.analyze: xavfsizlik signallari alohida ('safety'), o'g'irlik 'alerts' ga aralashmaydi",
       "safety_alerts" in src and "is_safety" in src)


# ============================================================================ 5. SMS
ESKIZ = {"logins": 0, "sent": [], "token": "tok1", "expire_next": False, "fail_text": None}


def eskiz_app():
    app = FastAPI()

    @app.post("/api/auth/login")
    async def login(request: Request):
        f = await request.form()
        ESKIZ["logins"] += 1
        if f.get("email") != "a@b.uz" or f.get("password") != "pw":
            return JSONResponse({"message": "invalid"}, status_code=401)
        ESKIZ["token"] = f"tok{ESKIZ['logins']}"
        return {"message": "token_generated", "data": {"token": ESKIZ["token"]}}

    @app.post("/api/message/sms/send")
    async def send(request: Request):
        f = await request.form()
        if ESKIZ["expire_next"] or request.headers.get("authorization") != f"Bearer {ESKIZ['token']}":
            ESKIZ["expire_next"] = False
            return JSONResponse({"message": "Expired"}, status_code=401)
        if ESKIZ["fail_text"] and ESKIZ["fail_text"] in f.get("message", ""):
            return JSONResponse({"status": "error", "message": "Matn shabloni moderatsiyadan o'tmagan"}, status_code=200)
        ESKIZ["sent"].append({"phone": f.get("mobile_phone"), "text": f.get("message"), "from": f.get("from")})
        return {"id": f"m{len(ESKIZ['sent'])}", "status": "waiting", "message": "Waiting for SMS provider"}

    return app


HTTPGW: list = []


def httpgw_app():
    app = FastAPI()

    @app.post("/sms")
    async def sms_(request: Request):
        if request.headers.get("authorization") != "Bearer gwtok":
            return JSONResponse({"error": "auth"}, status_code=403)
        HTTPGW.append(await request.json())
        return {"ok": True}

    return app


def serve(app, port):
    import uvicorn

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    threading.Thread(target=srv.run, daemon=True).start()
    t0 = time.time()
    while not srv.started and time.time() - t0 < 10:
        time.sleep(0.05)
    return srv


def sms_suite() -> None:
    print("\n--- Kritik SMS (Eskiz soxta serveri, HTTP shlyuz) ---")
    from app import sms

    serve(eskiz_app(), ESKIZ_PORT)
    serve(httpgw_app(), HTTPGW_PORT)
    config.SMS_ESKIZ_BASE = f"http://127.0.0.1:{ESKIZ_PORT}/api"
    config.SMS_ESKIZ_EMAIL, config.SMS_ESKIZ_PASSWORD, config.SMS_FROM = "a@b.uz", "pw", "4546"
    ok("Telefon: '+998 90 123-45-67' → 998901234567", sms.normalize_phone("+998 90 123-45-67") == "998901234567")
    ok("Telefon: 9 raqam → 998 qo'shiladi; 12 raqamli boshqa davlat Eskiz uchun rad",
       sms.normalize_phone("901234567") == "998901234567" and sms.normalize_phone("+7 999 123 45 67") is None)
    t = sms.ascii_text("Yiqilish — do‘kon «Markaz» " + "x" * 300)
    ok("Matn ASCII va ≤ 160 belgi (o‘ → o')", t.isascii() and len(t) <= 160 and "do'kon" in t, t[:40])
    db.set_settings({"sms_enabled": "1", "sms_provider": "eskiz", "sms_phones": "998901234567, +998 91 111 22 33",
                     "sms_kinds": "fall,lying,night", "sms_max_per_hour": "5", "store_name": "Markaz do‘koni"})
    ok("Sozlangan: 2 ta raqam", sms.configured()[0] and sms.phones() == ["998901234567", "998911112233"])
    e1 = make_event("fall")
    n = sms.notify_event(e1, sync=True)
    ok("Yiqilish → 2 ta SMS (login bir marta, from=4546, ASCII)", n == 2 and len(ESKIZ["sent"]) == 2 and
       ESKIZ["logins"] == 1 and ESKIZ["sent"][0]["from"] == "4546" and all(x["text"].isascii() and len(x["text"]) <= 160
                                                                        for x in ESKIZ["sent"]), str(ESKIZ["sent"][:1]))
    ok("SMS matnida tur va do'kon", "YIQILISH" in ESKIZ["sent"][0]["text"] and "Markaz" in ESKIZ["sent"][0]["text"])
    ok("Takror (shu hodisa, shu raqam) yuborilmaydi", sms.notify_event(e1, sync=True) == 0 and len(ESKIZ["sent"]) == 2)
    ESKIZ["expire_next"] = True
    e2 = make_event("night")
    n = sms.notify_event(e2, sync=True)
    ok("Token eskirdi (401) — qayta login va yuborish", n == 2 and ESKIZ["logins"] == 2, f"n={n} logins={ESKIZ['logins']}")
    ok("Tanlanmagan tur (gavjumlik) — SMS yo'q", sms.notify_event(make_event("crowd"), sync=True) == 0)
    ok("Video tahlili hodisasi — SMS yo'q", sms.notify_event(make_event("fall", job_id=5), sync=True) == 0)
    ok("O'g'irlik hodisasi — SMS yo'q", sms.notify_event(make_event("pocket"), sync=True) == 0)
    e3 = make_event("lying")
    n = sms.notify_event(e3, sync=True)
    logs = db.rows("SELECT * FROM sms_log WHERE event_id=?", (e3,))
    ok("Soatlik chegara (5): 4 tadan keyin 1 ta yuborildi, 1 tasi jurnalda 'chegara'", n == 1 and
       any("chegara" in (r["error"] or "") for r in logs), f"n={n}")
    db.execute("DELETE FROM sms_log")
    ESKIZ["fail_text"] = "ODAM YOTIBDI"
    n = sms.notify_event(make_event("lying"), sync=True)
    errs = [r["error"] for r in db.rows("SELECT error FROM sms_log WHERE ok=0")]
    ok("Eskiz 'status: error' (shablon tasdiqlanmagan) — xato jurnalda", n == 0 and errs and "moderatsiya" in errs[0],
       str(errs[:1]))
    ESKIZ["fail_text"] = None
    rec = sms.recent()
    ok("Panel jurnalida raqam yashirilgan (oxirgi 4 raqam)", rec and rec[0]["phone"].endswith("2233") and "99891" not in rec[0]["phone"])
    res = sms.send_test()
    ok("Sinov SMS — har raqamga", res["ok"] and len(res["results"]) == 2)
    db.set_settings({"sms_enabled": "0"})
    ok("SMS o'chiq — hech narsa", sms.notify_event(make_event("fall"), sync=True) == 0)
    config.SMS_HTTP_URL, config.SMS_HTTP_TOKEN = f"http://127.0.0.1:{HTTPGW_PORT}/sms", "gwtok"
    db.set_settings({"sms_enabled": "1", "sms_provider": "http", "sms_phones": "+7 999 123 45 67"})
    db.execute("DELETE FROM sms_log")
    n = sms.notify_event(make_event("fall"), sync=True)
    ok("Umumiy HTTP shlyuz: JSON {phone, text}, Bearer token", n == 1 and HTTPGW and HTTPGW[-1]["phone"] == "79991234567",
       str(HTTPGW[-1:]))
    config.SMS_ESKIZ_EMAIL = ""
    db.set_settings({"sms_provider": "eskiz", "sms_phones": "998901234567"})
    ok("Eskiz login .env da yo'q — sozlanmagan (sabab ko'rsatiladi)", not sms.configured()[0] and "SMS_ESKIZ" in sms.configured()[1])
    config.SMS_ESKIZ_EMAIL = "a@b.uz"
    # manager.create_event → SMS (alohida oqimda)
    from app.ai.manager import Manager

    db.set_settings({"sms_enabled": "1", "sms_kinds": "fall"})
    before = len(ESKIZ["sent"])
    mgr = Manager.__new__(Manager)                         # oqimlarsiz: faqat create_event
    mgr.listeners, mgr.notifier = [], None
    a = safety._alert("fall", 1, time.time(), [1, 2, 30, 40], ["test"])
    Manager.create_event(mgr, a, source={"id": None, "name": "Zal"}, job_id=None, video_time=None)
    t0 = time.time()
    while len(ESKIZ["sent"]) == before and time.time() - t0 < 10:
        time.sleep(0.05)
    ok("manager.create_event: xavfsizlik hodisasi → SMS (kamera oqimini kutdirmaydi)", len(ESKIZ["sent"]) == before + 1)
    mgr.workers = {}
    Manager.inject_alert(mgr, safety._alert("crowd", -1, time.time(), None, ["x"]))
    last = db.get_event(db.scalar("SELECT MAX(id) FROM events"))
    ok("Do'kon gavjumligi, jonli kamera yo'q — rasmsiz hodisa, 'klip tayyorlanmoqda' deyilmaydi",
       last["kind"] == "crowd" and last["clip_error"] == 1)
    db.set_settings({"sms_enabled": "0"})
    ok("Maxfiy qiymatlar bazada emas", not any(k.startswith("SMS_") or "password" in k.lower() for k in db.get_settings()))


# ============================================================================ 6. panel API, darvoza
def login(client) -> bool:
    r = client.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"},
                    follow_redirects=False)
    return r.status_code in (302, 303)


def tiny_video(path: Path, frames: int = 30, fn=None) -> Path:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 360))
    for i in range(frames):
        w.write(fn(i) if fn else door_frame())
    w.release()
    return path


def panel_suite() -> None:
    print("\n--- Panel: sahifa, API, chiqish zonasi, darvoza videolari ---")
    from fastapi.testclient import TestClient

    from app import web

    c = TestClient(web.app)
    ok("Panelga kirish", login(c))
    r = c.get("/xavfsizlik")
    ok("Sahifa /xavfsizlik ochiladi", r.status_code == 200 and "Favqulodda chiqishlar" in r.text and "Kritik SMS" in r.text)
    ok("Menyuda 'Xavfsizlik'", 'href="/xavfsizlik"' in r.text)
    r = c.get("/api/safety/summary")
    j = r.json()
    ok("summary: turlar, bugungi sonlar, sozlamalar, SMS holati", r.status_code == 200 and set(j["kinds"]) ==
       set(safety.SAFETY_KINDS) and "safety_fall" in j["settings"] and "configured" in j["sms"])
    ok("summary: SMS maxfiy qiymatlarini bermaydi", "pw" not in json.dumps(j) and "SMS_ESKIZ_PASSWORD" not in json.dumps(j))
    ok("Noto'g'ri tungi jadval — 400", c.post("/api/safety/settings", json={"safety_night_hours": "25:00-07:00"}).status_code == 400)
    ok("Tungi jadval boshi = oxiri — 400", c.post("/api/safety/settings", json={"safety_night_hours": "07:00-07:00"}).status_code == 400)
    r = c.post("/api/safety/settings", json={"safety_night_hours": "22:30-07:30", "safety_lying_sec": 5,
                                             "safety_blocked_frac": "0.5", "safety_fire": True, "safety_fall_sens": "high"})
    st = r.json()["settings"] if r.status_code == 200 else {}
    ok("Saqlash: jadval, chegaralar (10 s dan kam emas), olov yoqildi, sezgirlik",
       st.get("safety_night_hours") == "22:30-07:30" and st.get("safety_lying_sec") == "10" and
       st.get("safety_blocked_frac") == "0.5" and st.get("safety_fire") == "1" and st.get("safety_fall_sens") == "high",
       str(r.status_code))
    ok("Noto'g'ri sezgirlik — 400", c.post("/api/safety/settings", json={"safety_fall_sens": "max"}).status_code == 400)
    ok("Noto'g'ri raqam — 400", c.post("/api/safety/settings", json={"sms_phones": "123, 998901234567"}).status_code == 400)
    r = c.post("/api/safety/settings", json={"sms_phones": "+998 90 123 45 67\n998911112233", "sms_kinds": ["fall", "x", "fire"],
                                             "sms_max_per_hour": 500})
    st = r.json()["settings"]
    ok("SMS sozlamasi: raqamlar tozalandi, turlar filtrlandi, chegara ≤ 60",
       st["sms_phones"] == "998901234567, 998911112233" and st["sms_kinds"] == "fall,fire" and st["sms_max_per_hour"] == "60")
    c.post("/api/safety/settings", json={"safety_fire": "0", "safety_fall_sens": "normal", "safety_night_hours": ""})
    ok("JSON bo'lmagan so'rov — 400", c.post("/api/safety/settings", content=b"[1,2]",
                                            headers={"content-type": "application/json"}).status_code == 400)
    # Chiqish zonasi: video fayl manba (kamera ishlamayapti — qisqa ochib kadr olinadi)
    vid = tiny_video(Path(config.DATA_DIR) / "eshik.mp4")
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Eshik video", "file", str(vid), 0, 1, time.time()))
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "favqulodda", "name": "Orqa eshik",
                                                   "points": [[0.6, 0.15], [0.9, 0.15], [0.9, 0.95], [0.6, 0.95]]})
    ok("Zona turi 'favqulodda' saqlanadi", r.status_code == 200, r.text[:120])
    zid = r.json()["zone"]["id"]
    ok("Zonalar sahifasida 'Favqulodda chiqish' turi", "Favqulodda chiqish" in c.get(f"/sources/{sid}/zones").text)
    ex = c.get("/api/safety/exits").json()["exits"]
    ok("Chiqishlar ro'yxatida, hali eslab qolinmagan", any(e["id"] == zid and e["remembered_at"] is None for e in ex))
    r = c.post(f"/api/safety/exits/{zid}/remember")
    ok("Bo'sh holatini eslab qolish (kamera o'chiq — kadr qisqa ochib olindi, ogohlantirish bilan)",
       r.status_code == 200 and r.json().get("note"), r.text[:200])
    ok("Eslab qolingan rasm beriladi", c.get(f"/api/safety/exits/{zid}/ref.png").status_code == 200)
    ok("Boshqa tur zonasi uchun — 404", c.post("/api/safety/exits/99999/remember").status_code == 404)
    r = c.post(f"/api/safety/exits/{zid}/forget")
    ok("Eslab qolinganni o'chirish", r.status_code == 200 and c.get(f"/api/safety/exits/{zid}/ref.png").status_code == 404)
    # Hodisa sahifasi
    e = make_event("fall")
    r = c.get(f"/events/{e}")
    ok("Hodisa sahifasi (yiqilish): 'Nima aniqlandi', ball tarkibi yo'q", r.status_code == 200 and "Nima aniqlandi" in r.text
       and "Ball tarkibi" not in r.text)
    j = c.get("/api/summary").json()
    ok("Boshqaruv paneli: hodisa kartasida safety belgisi", any(x.get("safety") for x in j["latest"]))
    # Darvoza videolari
    v2 = tiny_video(Path(config.DATA_DIR) / "yiqilish.mp4", 40)
    with open(v2, "rb") as f:
        r = c.post("/api/safety/videos", files={"file": ("yiqilish.mp4", f, "video/mp4")}, data={"falls": "1.5, 0:03"})
    ok("Yiqilish videosi yuklandi (soniyalar: 1.5 va 0:03 → 3)", r.status_code == 200 and r.json()["video"]["falls"] == [1.5, 3.0],
       r.text[:200])
    vid_id = r.json()["video"]["id"]
    with open(v2, "rb") as f:
        r = c.post("/api/safety/videos", files={"file": ("y.mp4", f, "video/mp4")}, data={"falls": ""})
    ok("Soniyasiz (va salbiy emas) video — 400", r.status_code == 400)
    with open(v2, "rb") as f:
        r = c.post("/api/safety/videos", files={"file": ("salbiy.mp4", f, "video/mp4")}, data={"negative": "1"})
    ok("Salbiy video (yiqilish yo'q) yuklandi", r.status_code == 200 and r.json()["video"]["negative"] == 1)
    ok("Video emas fayl — 400", c.post("/api/safety/videos", files={"file": ("a.txt", b"x", "text/plain")},
                                       data={"falls": "1"}).status_code == 400)
    g = c.get("/api/safety/gate").json()
    ok("Darvoza: tahlil qilinmagan — 'yetarli emas'", g["state"] == "yetarli emas" and g["recall"]["pending"] == 2)
    r = c.post("/api/safety/gate/run", json={})
    ok("Tahlil boshlandi", r.status_code == 200 and r.json()["started"])
    t0 = time.time()
    while time.time() - t0 < 180:
        g = c.get("/api/safety/gate").json()
        if not g["runner"]["running"]:
            break
        time.sleep(0.5)
    ok("Tahlil tugadi, natija saqlandi (videoda odam yo'q — yiqilish topilmadi)", g["recall"]["pending"] == 0 and
       g["recall"]["fn"] == 2 and not g["runner"]["error"], json.dumps(g["recall"])[:200])
    ok("Takror bosilganda — hammasi tahlil qilingan", c.post("/api/safety/gate/run", json={}).json()["started"] is False)
    r = c.post(f"/api/safety/videos/{vid_id}", json={"falls": "2"})
    ok("Belgini tuzatish", r.status_code == 200 and r.json()["video"]["falls"] == [2.0])
    ok("Video o'chirildi", c.post(f"/api/safety/videos/{vid_id}/delete").status_code == 200)
    # CSRF: boshqa saytdan
    ok("Boshqa saytdan so'rov — 403 (CSRF)", c.post("/api/safety/settings", json={"safety_fall": "0"},
                                                     headers={"Origin": "http://evil.example"}).status_code == 403)
    c2 = TestClient(web.app)
    ok("Kirmagan foydalanuvchi — 401", c2.get("/api/safety/summary").status_code == 401)


def gate_math_suite() -> None:
    print("\n--- Darvoza hisobi (app/safety_eval.py) ---")
    m = se.match([10.0, 30.0], [{"t": 12.5, "kind": "fall"}, {"t": 13.0, "kind": "fall"}, {"t": 50, "kind": "fall"},
                                 {"t": 31, "kind": "lying"}])
    ok("Juftlash: 10 → 12,5 (TP), 13 takror, 50 — xato, 30 — topilmadi ('yotibdi' hisoblanmaydi)",
       len(m["tp"]) == 1 and m["dup"] == 1 and len(m["fp"]) == 1 and len(m["fn"]) == 1)
    ok("Oyna: belgidan 1 s oldin — TP, 10 s dan keyin — yo'q",
       len(se.match([10], [{"t": 9.0, "kind": "fall"}])["tp"]) == 1 and not se.match([10], [{"t": 20.5, "kind": "fall"}])["tp"])
    ok("Soniyalar: '3.5, 1:05; 12' → [3.5, 12, 65]", se.clean_falls("3.5, 1:05; 12") == [3.5, 12.0, 65.0])
    vids = [{"id": i, "filename": f"v{i}", "falls": [5.0], "result": {"safety": [{"t": 7.0, "kind": "fall"}] if i < 18 else []}}
            for i in range(20)]
    r = se.recall_gate(vids)
    ok("20 yiqilish, 18 topildi → recall 90% ≥ 85% — o'tdi", r["ok"] and abs(r["recall"] - 0.9) < 1e-9)
    r = se.recall_gate(vids[:10])
    ok("10 ta yiqilish — yetarli emas (kerak ≥ 20)", not r["ok"] and not r["enough"])
    r = se.recall_gate(vids + [{"id": 99, "filename": "x", "falls": [1.0], "result": None}])
    ok("Tahlil qilinmagan video bor — yetarli emas", not r["enough"])
    cams = [{"source_id": 1, "name": "A", "minutes": 7 * 1440, "rejected": 1, "confirmed": 2, "unreviewed": 0},
            {"source_id": 2, "name": "B", "minutes": 14 * 1440, "rejected": 2, "confirmed": 0, "unreviewed": 0},
            {"source_id": 3, "name": "C", "minutes": 2 * 1440, "rejected": 0, "confirmed": 0, "unreviewed": 0}]
    f = se.live_gate(cams)
    ok("Xato: A 1/hafta, B 1/hafta — o'tdi; C 2 kun — hisobga olinmaydi", f["ok"] and f["ready"] == 2 and not f["cams"][2]["enough"])
    f = se.live_gate([{**cams[0], "rejected": 3}])
    ok("7 kunda 3 ta ❌ — o'tmadi", not f["ok"] and f["cams"][0]["rate"] == 3.0)
    f = se.live_gate([{**cams[0], "unreviewed": 1}])
    ok("Javobsiz signal qolgan — o'tmadi (avval javob bering)", not f["ok"])
    g = se.gate(vids, cams)
    ok("Umumiy: ikkalasi o'tdi — 'otdi'", g["state"] == "otdi")
    ok("Umumiy: recall 50% — 'otmadi'", se.gate([{**v, "result": {"safety": []}} if v["id"] % 2 else v for v in vids],
                                                 cams)["state"] == "otmadi")


def cli_suite() -> None:
    print("\n--- Buyruq qatori darvozasi ---")
    import subprocess

    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Kamera G", "rtsp", "rtsp://g", 0, 1, time.time()))
    m0 = int(time.time() // 60 * 60)
    with db.tx() as con:
        con.executemany("INSERT OR IGNORE INTO metrics_minute(minute, source_id, source_name, up_pct) VALUES(?,?,?,?)",
                        [(m0 - i * 60, sid, "Kamera G", 1.0) for i in range(8 * 1440)])
    make_event("fall", status="rad_etilgan", source_id=sid)
    db.set_settings({"safety_since": str(m0 - 10 * 86400), "retention_days": "30"})
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "safety_gate.py"), "--db", str(db.DB_PATH)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("tests/safety_gate.py ishlaydi (bazani faqat o'qiydi)", r.returncode in (1, 2) and "Kamera G" in r.stdout and
       "8.0 kun" in r.stdout, (r.stdout + r.stderr)[-400:])
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "safety_gate.py"), "--db", "/yoq/baza.db"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    ok("Baza yo'q — kod 2", r.returncode == 2)
    db.set_settings({"safety_since": str(m0 - 2 * 86400)})
    from app import safety_web

    cams = {c["source_id"]: c for c in safety_web.live_cams()}
    ok("Darvoza oynasi: 2.2.0 dan oldingi kamera daqiqalari hisobga olinmaydi (8 kun → ~2 kun)",
       1.9 <= cams[sid]["minutes"] / 1440 <= 2.1, str(cams[sid]["minutes"] / 1440))
    ok("live_window: retention 30 kun — 30 kundan eski hisoblanmaydi; 0 (cheksiz) — 90 kun",
       abs(se.live_window(0, 30, 1e9) - (1e9 - 30 * 86400)) < 1 and abs(se.live_window(0, 0, 1e9) - (1e9 - 90 * 86400)) < 1)


# ============================================================================ 7. bulut
def cloud_suite() -> None:
    print("\n--- Bulut: matn va egasining min_score chegarasi ---")
    from cloud import db as cdb, notify

    cdb.init_db()
    cid = cdb.create_company("Do'kon MChJ")
    owner = cdb.create_user(cid, "ega@x.uz", "Juda-uzun-parol-123", "Ega", "owner")
    bid, _ = cdb.create_branch(cid, "Chilonzor", "dev1")
    notify.add_target(owner, "email", "ega@x.uz")
    notify.set_prefs({"id": owner, "role": "owner"}, {"min_score": 100})
    now = time.time()

    def put(uid, kind, score):
        with cdb.engine().begin() as con:
            con.execute(cdb.events.insert().values(uid=uid, branch_id=bid, device_id="dev1", local_id=1, created_at=now,
                                                   updated_at=now, kind=kind, score=score, status="yangi",
                                                   source_name="Zal", data={"level": "yuqori", "reasons": ["r"]},
                                                   received_at=now))

    put("dev1:1", "fall", 60)
    put("dev1:2", "pocket", 60)
    ok("Egasi min_score=100: xavfsizlik signali baribir yuboriladi", notify.on_new_event("dev1:1") == 1)
    ok("... o'g'irlik signali (ball 60) — yo'q", notify.on_new_event("dev1:2") == 0)
    p = {"kind": "fall", "branch": "Chilonzor", "camera": "Zal", "score": 100, "reasons": ["Odam yiqildi"], "t_event": now}
    title, text, _ = notify.render("event", p)
    ok("Bulut matni: maxsus sarlavha, ball va 'ayblov emas' yo'q", "Yiqilish — yordam kerak" in text and "ball" not in text
       and "ayblov" not in text and title.startswith("Yiqilish"), text[:100])
    title, text, _ = notify.render("event", {**p, "kind": "bag", "score": 70})
    ok("Bulut: o'g'irlik matni o'zgarmagan", "Shubhali harakat" in text and "ayblov emas" in text and "ball" in title)
    ok("Bulut KIND_UZ: barcha yangi turlar", all(k in notify.KIND_UZ for k in safety.SAFETY_KINDS))
    api_ts = (ROOT / "cloud" / "panel" / "lib" / "api.ts").read_text(encoding="utf-8")
    ok("Bulut paneli (api.ts): yangi turlar va isSafety", all(f"{k}:" in api_ts for k in safety.SAFETY_KINDS)
       and "isSafety" in api_ts)


def main() -> int:
    t = time.time()
    db.init_db()
    pose_suite()
    scenario_suite()
    sweep_suite()
    track_suite()
    night_suite()
    crowd_suite()
    exit_suite()
    fire_suite()
    processor_suite()
    telegram_suite()
    sms_suite()
    panel_suite()
    gate_math_suite()
    cli_suite()
    cloud_suite()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
