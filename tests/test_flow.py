"""2.1.0 oqim va heatmap: gomografiya (kalibrlash), katak/bo'lim egalari, bo'lim tashriflari, yo'nalishlar, heatmap,
ID uzilishi, xodimlar, saqlash, bulut yig'indisi, panel API, darvoza (nuqtada turish + qo'lda sanash), Telegram /oqim.

    python -m tests.test_flow

Sahna: sintetik pinhole kamera (tests/flow_sim.py) do'kon poliga qaraydi; odamlar rejada yuradi, ularning ramkalari
kadrga proyeksiyalanadi (oyoq — pol nuqtasi, bosh — 1,7 m). Eslatma: bu mantiq sinovi — do'koningizdagi aniqlik faqat
Oqim sahifasidagi darvoza (nuqtada turish va qo'lda sanash) bilan o'lchanadi.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_flow_")   # hech qachon jonli data/ emas
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app import db, flow_eval as fe  # noqa: E402
from app.ai import flow, homography as hg  # noqa: E402
from tests.flow_sim import SimCamera, box_for  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


class Pp:
    def __init__(self, tid: int, box, kpts=None):
        self.track_id = tid
        self.box = np.asarray(box, dtype=np.float32)
        self.score = 0.9
        self.kpts = kpts


W, H = 1280, 720
CAM = SimCamera(pos=(10.0, -1.0), height=3.2, look=(10.0, 6.0), fov_deg=95, w=W, h=H)


def floor_pairs(cam: SimCamera, n: int, rng, click=0.0, plan_noise=0.0, extent=((1, 19), (0.5, 11))) -> list[dict]:
    g = np.mgrid[extent[0][0]:extent[0][1]:0.05, extent[1][0]:extent[1][1]:0.05].reshape(2, -1).T
    uv, vis = cam.project(g)
    g, uv = g[vis], uv[vis]
    targets = [(a, b) for a in np.linspace(0.06, 0.94, 5) for b in np.linspace(0.42, 0.95, 4)]
    rng.shuffle(targets)
    out = []
    for t in targets[:n]:
        i = int(np.argmin(np.hypot(*(uv - t).T)))
        out.append({"img": list(uv[i] + rng.normal(0, click, 2)) if click else list(uv[i]),
                    "plan": list(g[i] + rng.normal(0, plan_noise, 2)) if plan_noise else list(g[i])})
    return out


# ============================================================================ 1. gomografiya
def homography_suite() -> None:
    print("\n--- Gomografiya va linza egriligi ---")
    rng = np.random.default_rng(3)
    q = rng.uniform(-0.9, 0.9, (200, 2))
    for k in ((0.0, 0.0), (0.5, 0.0), (0.4, 0.8), (-0.15, 0.0)):
        qu, d = hg.undistort(q, k)
        good = d > hg.MIN_SCALE
        back = hg.distort(qu[good], k)
        ok(f"distort(undistort(q)) = q, k={k}", np.nanmax(np.abs(back - q[good])) < 1e-6,
           f"{np.nanmax(np.abs(back - q[good])):.2e}")
    pairs = floor_pairs(CAM, 10, rng)
    res = hg.fit(pairs, CAM.aspect, W, H)
    ok("Xatosiz 10 nuqta: model topildi, LOO ≈ 0", res["ok"] and res["loo_rms"] < 0.01, str(res["loo_rms"]))
    c = res["calib"]
    g = np.mgrid[2:18:0.5, 1:10:0.5].reshape(2, -1).T
    uv, vis = CAM.project(g)
    xy, good = c.to_plan(uv[vis])
    e = np.hypot(*(xy[good] - g[vis][good]).T)
    ok("Rejadagi ixtiyoriy nuqta (yaroqli sohada) — xato < 2 sm", len(e) > 100 and e.max() < 0.02,
       f"n={len(e)}, max {e.max():.4f} m")
    back, okb = c.to_image(g[vis][good])
    ok("to_image — to_plan ning teskarisi", okb.all() and np.abs(back - uv[vis][good]).max() < 1e-6)
    c2 = hg.Calib.from_json(json.loads(json.dumps(c.to_json())))
    ok("Kalibrlash JSON orqali saqlanib qayta o'qiladi", c2 is not None and np.allclose(c2.to_plan(uv[vis])[0][good], xy[good]))
    # Ufq orti va yaroqli sohadan tashqari
    far, _ = CAM.project([[10.0, 60.0]])
    _, okf = c.to_plan(far)
    ok("Juda uzoq joy (ufqqa yaqin, 1 m < 18 piksel) rejaga tushirilmaydi", not okf[0])
    _, oks = c.to_plan([[0.5, 0.05]])
    ok("Kadr tepasi (yaroqli sohadan tashqari) rejaga tushirilmaydi", not oks[0])
    ok("Kadr ustidagi 1 m to'r chiziqlari bor", len(hg.grid_lines(c, 20, 12)) > 10)
    cov = hg.coverage_polygon(c)
    ok("Qamrov chegarasi rejada", len(cov) > 10 and cov[:, 1].min() >= -1)
    # Kam / yomon nuqtalar
    r4 = hg.fit(pairs[:4], CAM.aspect)
    ok("4 nuqta: model bor, lekin xato baholanmaydi (ogohlantirish)", r4["ok"] and r4["loo_rms"] is None and
       any("baholab bo'lmaydi" in w for w in r4["warnings"]))
    r3 = hg.fit(pairs[:3], CAM.aspect)
    ok("3 nuqta — kalibrlash yo'q", not r3["ok"] and r3["warnings"])
    line = [{"img": [0.1 + 0.2 * i, 0.6], "plan": [2.0 + 3 * i, 5.0]} for i in range(5)]
    ok("Bir chiziqdagi nuqtalar rad etiladi", not hg.fit(line, CAM.aspect)["ok"])
    bad = [dict(p) for p in pairs]
    bad[3] = dict(bad[3], plan=[bad[3]["plan"][0] + 3.0, bad[3]["plan"][1]])
    rb = hg.fit(bad, CAM.aspect)
    ok("Noto'g'ri bosilgan nuqta (3 m siljigan) topiladi", rb["suspect"] == 3 and rb["points"][3].get("suspect") and
       any("#4" in w for w in rb["warnings"]), str(rb["suspect"]))
    ok("Yaxshi nuqtalarda gumonli nuqta yo'q", res["suspect"] is None)
    wall = [{"img": list(CAM.project([[x, 0.6]])[0][0]), "plan": [x, 0.6]} for x in (3.0, 6.0, 9.0, 12.0, 15.0)]
    rw = hg.fit(wall + pairs[:3], CAM.aspect)
    ok("Nuqtalarning ko'pi bir chiziqda (devor tagi) — ogohlantirish", any("bir chiziqda" in w for w in rw["warnings"]),
       str(rw["warnings"]))
    chk = [dict(p, check=True) for p in floor_pairs(CAM, 3, np.random.default_rng(11))]
    rc = hg.fit(pairs + chk, CAM.aspect)
    ok("Tekshiruv nuqtalari moslashga kirmaydi, xatosi alohida", rc["n"] == 10 and rc["n_check"] == 3 and
       rc["check_max"] is not None and rc["check_max"] < 0.02)
    # Shovqin va linza egriligi (statistika)
    for k1, n, lim in ((0.0, 8, 0.5), (-0.12, 12, 0.5)):
        p90s = []
        for seed in range(8):
            r_ = np.random.default_rng(100 + seed)
            cam = SimCamera(pos=(10 + r_.uniform(-2, 2), -1), height=r_.uniform(2.7, 3.5), look=(10, r_.uniform(5, 7)),
                            fov_deg=r_.uniform(75, 100), w=W, h=H, k1=k1)
            fr = hg.fit(floor_pairs(cam, n, r_, click=0.002, plan_noise=0.05), cam.aspect, W, H)
            if not fr["ok"]:
                p90s.append(9.9)
                continue
            uv2, v2 = cam.project(g)
            xy2, g2 = fr["calib"].to_plan(uv2[v2])
            p90s.append(float(np.percentile(np.hypot(*(xy2[g2] - g[v2][g2]).T), 90)))
        ok(f"Shovqinli bosish (2,5 px, rejada 5 sm), k1={k1}, {n} nuqta: 8 sahnaning ≥ 6 tasida 90% xato ≤ {lim} m",
           sum(1 for v in p90s if v <= lim) >= 6, ", ".join(f"{v:.2f}" for v in p90s))


# ============================================================================ 2. sahna
def make_plan(sections: list[tuple], w_m=20.0, h_m=12.0, mpp=0.05) -> flow.Plan:
    p = flow.Plan(1, "Sinov", mpp, int(w_m / mpp), int(h_m / mpp))
    for i, (name, x0, y0, x1, y1) in enumerate(sections):
        poly = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
        p.sections.append(flow.Section(i + 1, name, flow.PALETTE[i], poly, (x1 - x0) * (y1 - y0)))
    return p


SECS = [("Sut", 2, 2, 7, 6), ("Non", 13, 2, 18, 6), ("Ichimlik", 7.5, 7, 12.5, 10)]


def scene(cams=None, sections=SECS):
    plan = make_plan(sections)
    cams = cams or [(1, CAM)]
    out = []
    for sid, sc in cams:
        res = hg.fit(floor_pairs(sc, 12, np.random.default_rng(sid)), sc.aspect, W, H)
        out.append(flow.Cam(sid, plan.id, res["calib"]))
    flow._assign(plan, out)
    return plan, out


def monitor(plan, cam) -> flow.FlowMonitor:
    m = flow.FlowMonitor(cam.source_id, "sinov", persist=False)
    m.calib_override = (plan, cam)
    return m


def run_paths(mons: dict, paths: list, fps=8.0, t0=1000.0, staff=(), drop=None, rng=None, swap=None, hide_legs=()):
    """paths: [(tid, [(x, y, sekund_to'xtash), ...], tezlik m/s, boshlanish vaqti)]. mons: {kamera: (FlowMonitor,
    SimCamera)}. drop: (tid, t0, t1) — shu oraliqda detektor ko'rmaydi; swap: {tid: (t, new_tid)} — ID almashadi."""
    tl = []
    for tid, wps, speed, start in paths:
        t = start
        pos = np.array(wps[0][:2], dtype=float)
        tl.append((tid, t, pos.copy()))
        for x, y, stay in wps[1:]:
            tgt = np.array([x, y], dtype=float)
            d = float(np.hypot(*(tgt - pos)))
            n = max(1, int(d / speed * fps))
            for i in range(1, n + 1):
                t += 1 / fps
                tl.append((tid, t, pos + (tgt - pos) * i / n))
            pos = tgt
            for _ in range(int(stay * fps)):
                t += 1 / fps
                tl.append((tid, t, pos.copy()))
    tl.sort(key=lambda x: x[1])
    frames: dict[float, list] = {}
    for tid, t, p in tl:
        frames.setdefault(round(t * fps), []).append((tid, p))
    keys = sorted(frames)
    visits = []
    for k in range(keys[0], keys[-1] + 1):
        t = t0 + k / fps
        for sid, (m, sc) in mons.items():
            ppl = []
            for tid, p in frames.get(k, []):
                if drop and tid == drop[0] and drop[1] <= k / fps < drop[2]:
                    continue
                b = box_for(sc, p)
                if b is None:
                    continue
                if rng is not None:
                    b = b + rng.normal(0, 1.5, 4).astype(np.float32)
                ot = tid
                if swap and tid in swap and k / fps >= swap[tid][0]:
                    ot = swap[tid][1]
                kp = None
                if tid in hide_legs:
                    bh = b[3] - b[1]
                    kp = np.zeros((17, 3), dtype=np.float32)
                    for i, fy in ((5, 0.18), (6, 0.18), (11, 0.47), (12, 0.47)):
                        kp[i] = [(b[0] + b[2]) / 2, b[1] + fy * bh, 0.9]
                    b = b.copy()
                    b[3] = b[1] + 0.55 * bh            # polka oyoqlarni to'sgan: ramka belgacha
                ppl.append(Pp(ot, b, kp))
            st = {tid: {"staff": True} for tid in staff}
            visits += m.update(ppl, t, W, H, None, st)
    return visits


def logic_suite() -> None:
    print("\n--- Bo'lim tashriflari va yo'nalishlar ---")
    plan, (cam,) = scene()
    ok("Bitta kamera — hamma bo'limlar egasi", all(s.owner == 1 for s in plan.sections),
       str([(s.name, s.owner, round(s.coverage, 2)) for s in plan.sections]))
    m = monitor(plan, cam)
    # 1: Sut (4 s) → Non (4 s) → chiqib ketadi
    v = run_paths({1: (m, CAM)}, [(1, [(1, 9, 0), (4.5, 4, 4), (15.5, 4, 4), (19, 9, 0)], 1.2, 0.0)])
    ok("Sut → Non: ikki tashrif, ikkinchisi 'Sut'dan", [(x.section_name, x.from_id) for x in v] == [("Sut", None), ("Non", 1)],
       str([(x.section_name, x.from_id) for x in v]))
    m = monitor(plan, cam)
    # O'tib ketish: Ichimlik burchagini kesib o'tadi (≈ 0,5 s), keyin butun bo'lim bo'ylab to'xtamay (4 s)
    v = run_paths({1: (m, CAM)}, [(2, [(6.5, 8.5, 0), (8.5, 6.5, 0), (7, 8.5, 0), (13.5, 8.5, 0)], 1.4, 0.0)],
                  rng=np.random.default_rng(2))
    ok("Burchakni kesib o'tish va bo'lim bo'ylab to'xtamay yurish (4,6 s) — tashrif emas", [x.section_name for x in v] == [],
       str([x.section_name for x in v]))
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(2, [(6.5, 8.5, 0), (13.5, 8.5, 0)], 0.35, 0.0)], rng=np.random.default_rng(3))
    ok("Bo'lim bo'ylab sekin (polkalarga qarab) yurish — tashrif", [x.section_name for x in v] == ["Ichimlik"],
       str([x.section_name for x in v]))
    m = monitor(plan, cam)
    # Bo'limda turib, chegarada u yoq-bu yoq (titrash)
    path = [(4.5, 7.5, 0), (4.5, 5.7, 3)] + [(4.5, 6.3 if i % 2 else 5.8, 0.3) for i in range(12)] + [(4.5, 9, 0)]
    v = run_paths({1: (m, CAM)}, [(3, path, 1.0, 0.0)], rng=np.random.default_rng(1))
    ok("Bo'lim chegarasida u yoq-bu yoq yurish — bitta tashrif", [x.section_name for x in v] == ["Sut"],
       str([x.section_name for x in v]))
    m = monitor(plan, cam)
    # Chiqib 6 s dan keyin qaytdi (REENTRY_SEC ichida) — yangi tashrif emas; 30 s dan keyin — yangi
    v = run_paths({1: (m, CAM)}, [(4, [(4.5, 8, 0), (4.5, 4, 4), (4.5, 7.8, 4), (4.5, 4, 4), (4.5, 7.8, 30), (4.5, 4, 4),
                                       (4.5, 9, 0)], 1.2, 0.0)])
    ok("Chiqib 15 s ichida qaytish — o'sha tashrif; 30 s dan keyin — yangi", [x.section_name for x in v] == ["Sut", "Sut"],
       str([x.section_name for x in v]))
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(5, [(4.5, 8, 0), (4.5, 4, 5), (4.5, 9, 0)], 1.2, 0.0)], staff=(5,))
    ok("Xodim (xodim zonasida turgan) — sanalmaydi", v == [])
    # ID uzilishi: to'silish 1 s (ID yangisi bilan qaytadi) — ikki marta sanalmaydi
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(6, [(4.5, 8, 0), (4.5, 4, 6), (15, 4, 5), (16, 9, 0)], 1.2, 0.0)], swap={6: (6.5, 60)},
                  drop=(6, 5.8, 6.5))
    ok("Bo'limda to'silib ID almashdi — bitta tashrif, keyingi bo'lim yo'nalishi saqlanadi",
       [(x.section_name, x.from_id) for x in v] == [("Sut", None), ("Non", 1)], str([(x.section_name, x.from_id) for x in v]))
    # 2,5 s to'silish (odam polka orqasida) — yangi ID baribir o'sha odam
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(9, [(4.5, 8, 0), (4.5, 4, 10), (4.5, 9, 0)], 1.2, 0.0)], swap={9: (8.5, 90)},
                  drop=(9, 6.0, 8.5))
    ok("2,5 s to'silishdan keyin yangi ID — o'sha odam, bitta tashrif", [x.section_name for x in v] == ["Sut"],
       str([x.section_name for x in v]))
    # Uzoq to'silish (5 s) — yangi odam (ulanmaydi, lekin u bo'limda allaqachon sanalgan)
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(7, [(4.5, 8, 0), (4.5, 4, 14), (4.5, 9, 0)], 1.2, 0.0)], swap={7: (11.0, 70)},
                  drop=(7, 6.0, 11.0))
    ok("5 s to'silishdan keyin yangi ID — eslab bo'lmaydi (bo'lim ikkinchi marta sanaladi, halol cheklov)",
       [x.section_name for x in v] == ["Sut", "Sut"], str([x.section_name for x in v]))
    # Oyoqlar ko'rinmaydi (polka to'sgan): skelet bo'yicha taxmin — joy to'g'ri, tashrif sanaladi
    m = monitor(plan, cam)
    v = run_paths({1: (m, CAM)}, [(8, [(9.5, 11, 0), (10, 8.5, 5), (10, 11.5, 0)], 1.0, 0.0)], hide_legs=(8,))
    ok("Oyog'i ko'rinmagan odam — skeletdan taxmin, 'Ichimlik' tashrifi", [x.section_name for x in v] == ["Ichimlik"],
       str([x.section_name for x in v]))
    pp = Pp(1, [100, 100, 160, 210], np.zeros((17, 3), dtype=np.float32))
    for i, fy in ((5, 120), (6, 120), (11, 160), (12, 160)):
        pp.kpts[i] = [130, fy, 0.9]
    fx, fy, est = flow.foot_point(pp)
    ok("foot_point: son + 1,7 × gavda", est and abs(fy - (160 + 1.7 * 40)) < 1e-3, f"{fy}")
    pp2 = Pp(1, [100, 100, 160, 300], None)
    ok("foot_point: skelet yo'q — ramka pasti", flow.foot_point(pp2) == (130.0, 300.0, False))
    # Ko'p odam bir vaqtda: 12 kishi, tasodifiy yo'llar; har biri aniq bitta bo'limda to'xtaydi
    m = monitor(plan, cam)
    rng = np.random.default_rng(5)
    paths, expect = [], {"Sut": 0, "Non": 0, "Ichimlik": 0}
    centers = {"Sut": (4.5, 4), "Non": (15.5, 4), "Ichimlik": (10, 8.5)}
    for i in range(12):
        nm = list(centers)[i % 3]
        cx, cy = centers[nm]
        while True:                     # to'xtash joyi kameraga ko'rinadigan katakda bo'lsin
            sx, sy = cx + rng.uniform(-1.5, 1.5), cy + rng.uniform(-1, 1)
            if plan.owner[int(sy // flow.CELL), int(sx // flow.CELL)] == 1:
                break
        paths.append((100 + i, [(rng.uniform(3, 17), 11.5, 0), (sx, sy, 4),
                                (rng.uniform(3, 17), 11.5, 0)], 1.1, i * 3.0))
        expect[nm] += 1
    v = run_paths({1: (m, CAM)}, paths, rng=np.random.default_rng(9))
    got = {k: sum(1 for x in v if x.section_name == k) for k in expect}
    ok("12 kishi (shovqinli ramka) — har bo'lim tashrifi aniq", got == expect, f"{got} (kutilgan {expect})")
    # Heatmap: bandlik yig'indisi ≈ kuzatilgan odam × soniya
    occ = sum(sum(a.occ.values()) for a in m.local.values())
    pas = sum(sum(a.pas.values()) for a in m.local.values())
    vec = sum(sum(x[0] for x in a.vec.values()) for a in m.local.values())
    ok("Heatmap: bandlik (odam × s), o'tganlar va yo'nalish strelkalari yig'ildi", occ > 100 and pas > 50 and vec > 20,
       f"occ {occ:.0f}, pas {pas}, vec {vec}")


def multicam_suite() -> None:
    print("\n--- Bir nechta kamera: katak va bo'lim egasi (ikki marta sanalmaydi) ---")
    cam2 = SimCamera(pos=(10.0, 13.0), height=3.2, look=(10.0, 5.0), fov_deg=95, w=W, h=H)
    plan, (c1, c2) = scene(cams=[(1, CAM), (2, cam2)])
    own = {s.name: s.owner for s in plan.sections}
    ok("Bo'lim egasi — uni yaqinroqdan ko'radigan kamera", own["Ichimlik"] == 2 and own["Sut"] == 1, str(own))
    o = plan.owner
    ok("Kataklar ikki kamera orasida bo'lingan", (o == 1).sum() > 50 and (o == 2).sum() > 50,
       f"1: {(o == 1).sum()}, 2: {(o == 2).sum()}, hech kim: {(o == 0).sum()}")
    m1, m2 = monitor(plan, c1), monitor(plan, c2)
    v = run_paths({1: (m1, CAM), 2: (m2, cam2)}, [(1, [(1, 11, 0), (4.5, 4, 4), (10, 8.5, 4), (15.5, 4, 4), (19, 11, 0)],
                                                   1.2, 0.0)])
    names = sorted(x.section_name for x in v)
    ok("Ikki kamera ko'radigan odam — har bo'lim bir marta", names == ["Ichimlik", "Non", "Sut"], str(names))
    occ1 = sum(sum(a.occ.values()) for a in m1.local.values())
    occ2 = sum(sum(a.occ.values()) for a in m2.local.values())
    tot_t = (len(v) and 1) and (occ1 + occ2)
    ok("Heatmap: ikkala kamera yig'indisi ≈ odamning haqiqiy vaqti (ikki marta emas)", 0.8 * 25 < tot_t < 1.2 * 33,
       f"{occ1:.1f} + {occ2:.1f} = {tot_t:.1f} s")


# ============================================================================ 3. saqlash, API, bulut
def login(client):
    from app import config

    r = client.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"},
                    follow_redirects=False)
    return r.status_code in (302, 303)


def storage_api_suite() -> None:
    print("\n--- Saqlash, panel API, darvoza ---")
    from fastapi.testclient import TestClient

    from app import web

    db.init_db()
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Zal", "rtsp", "rtsp://x", 0, 1, time.time()))
    c = TestClient(web.app)
    ok("Panelga kirish", login(c))
    for url in ("/oqim", "/oqim/sozlash", f"/oqim/kamera/{sid}"):
        r = c.get(url)
        ok(f"Sahifa ochiladi: {url}", r.status_code == 200 and "plan.js" in r.text, str(r.status_code))
    r = c.get("/api/flow/summary")
    ok("Reja yo'q: summary bo'sh", r.status_code == 200 and r.json()["plan"] is None)
    # Rasmsiz reja
    r = c.post("/api/flow/plans", json={"name": "Asosiy zal", "w_m": 20, "h_m": 12})
    ok("Rasmsiz reja (20 × 12 m) yaratildi", r.status_code == 200 and abs(r.json()["plan"]["w_m"] - 20) < 0.1, r.text[:200])
    pid = r.json()["plan"]["id"]
    mpp = r.json()["plan"]["m_per_px"]
    ok("Reja rasmi beriladi", c.get(f"/media/plans/{pid}").status_code == 200)
    ok("Noto'g'ri o'lcham rad etiladi", c.post("/api/flow/plans", json={"w_m": 500, "h_m": 5}).status_code == 400)
    # Rasm yuklash (PNG) va masshtab
    import cv2

    im = np.full((400, 600, 3), 255, np.uint8)
    cv2.rectangle(im, (10, 10), (590, 390), (0, 0, 0), 3)
    _, png = cv2.imencode(".png", im)
    r = c.post("/api/flow/plans", files={"file": ("reja.png", png.tobytes(), "image/png")}, data={"name": "Chizma"})
    ok("Reja rasmi yuklandi", r.status_code == 200 and r.json()["plan"]["w_px"] == 600, r.text[:200])
    pid2 = r.json()["plan"]["id"]
    r = c.post(f"/api/flow/plans/{pid2}", json={"scale": {"p1": [10, 10], "p2": [590, 10], "meters": 29}})
    ok("Masshtab: 580 px = 29 m → 0,05 m/px", r.status_code == 200 and abs(r.json()["plan"]["m_per_px"] - 0.05) < 1e-9)
    big = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (20000).to_bytes(4, "big") + (20000).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    r = c.post("/api/flow/plans", files={"file": ("x.png", big, "image/png")})
    ok("400 megapikselli rasm (kichik fayl) yechilmasdan rad etiladi", r.status_code == 400 and "juda katta" in r.text, r.text[:120])
    _, jpg = cv2.imencode(".jpg", im)
    from app.flow_web import image_size
    ok("JPEG/PNG sarlavhasidan o'lcham", image_size(jpg.tobytes()) == (600, 400) and image_size(png.tobytes()) == (600, 400))
    ok("Masshtab: reja 150 m dan katta chiqsa rad etiladi",
       c.post(f"/api/flow/plans/{pid2}", json={"m_per_px": 5}).status_code == 400)
    ok("Rasm emas fayl rad etiladi", c.post("/api/flow/plans", files={"file": ("x.png", b"not an image", "image/png")}).status_code == 400)
    ok("Reja o'chirildi", c.post(f"/api/flow/plans/{pid2}/delete").status_code == 200)
    # Bo'limlar (reja pikselida)
    ids = {}
    for name, x0, y0, x1, y1 in SECS:
        pts = [[x0 / mpp, y0 / mpp], [x1 / mpp, y0 / mpp], [x1 / mpp, y1 / mpp], [x0 / mpp, y1 / mpp]]
        r = c.post(f"/api/flow/plans/{pid}/sections", json={"name": name, "points": pts})
        ids[name] = r.json()["section"]["id"]
    ok("3 bo'lim saqlandi, yuzasi to'g'ri", r.status_code == 200 and abs(r.json()["section"]["area_m2"] - 15.0) < 0.2,
       r.text[:200])
    ok("2 nuqtali bo'lim rad etiladi", c.post(f"/api/flow/plans/{pid}/sections", json={"points": [[1, 1], [5, 5]]}).status_code == 400)
    # Kalibrlash: preview + saqlash (reja pikselida)
    pairs = floor_pairs(CAM, 10, np.random.default_rng(2))
    body = {"plan_id": pid, "frame_w": W, "frame_h": H,
            "pairs": [{"img": p["img"], "plan": [p["plan"][0] / mpp, p["plan"][1] / mpp]} for p in pairs]}
    r = c.post(f"/api/flow/calib/{sid}/preview", json=body)
    ok("Kalibrlash oldindan ko'rish: xato, to'r, qamrov", r.status_code == 200 and r.json()["ok"] and r.json()["grid"] and
       r.json()["coverage_px"] and r.json()["loo_rms"] < 0.05, r.text[:200])
    ok("Oldindan ko'rish bazaga yozmaydi", not db.row("SELECT 1 FROM cam_calib"))
    r = c.post(f"/api/flow/calib/{sid}", json=body)
    ok("Kalibrlash saqlandi", r.status_code == 200 and r.json()["ok"])
    lay = flow.layout(force=True)
    ok("Joylashuv: kamera rejada, bo'limlar egasi", sid in lay.cams and all(s.owner == sid for s in lay.plans[pid].sections),
       str([(s.name, s.owner) for s in lay.plans[pid].sections]))
    g = c.get(f"/api/flow/calib/{sid}").json()
    ok("Kalibrlash qayta o'qiladi (juftliklar, xatolar)", g["calib"] and len(g["calib"]["pairs"]) == 10 and g["calib"]["fit"]["ok"])
    ok("Noto'g'ri juftlik rad etiladi", c.post(f"/api/flow/calib/{sid}", json={"plan_id": pid, "pairs": [{"img": [2, 2], "plan": [1, 1]}]}).status_code == 400)
    # Jonli kamera: LineCounter orqali (worker.py ulanishi)
    from app.ai import counting

    lc = counting.LineCounter(sid, "Zal", persist=True)
    t0 = time.time() - 120
    paths = [(1, [(1, 11, 0), (4.5, 4, 4), (15.5, 4, 4), (19, 11, 0)], 1.2, 0.0),
             (2, [(19, 11, 0), (15.5, 4, 3), (10, 8.5, 3), (1, 11, 0)], 1.2, 2.0)]

    class Adapter:
        def __init__(self, lc):
            self.lc = lc

        def update(self, ppl, t, w, h, zs, st):
            self.lc.update(ppl, t, w, h, None, st, work_hours="")
            return []
    run_paths({sid: (Adapter(lc), CAM)}, paths, t0=t0)
    ok("LineCounter oqimni o'zi ulaydi (kalibrlangan kamera)", lc.flow is not None)
    rows = db.rows("SELECT section_name, from_id FROM section_visits ORDER BY t")
    ok("Tashriflar bazaga yozildi (4 ta, 2 yo'nalish)", len(rows) == 4 and sum(1 for r in rows if r["from_id"]) == 2,
       str([(r["section_name"], r["from_id"]) for r in rows]))
    ok("Jonli holat: odamlar rejada", isinstance(flow.live_people(sid), list))
    # Bazaga yozib bo'lmasa (masalan, "database is locked") — yig'indi yo'qolmaydi, keyingi daqiqada yoziladi
    n_acc = len(flow._acc)
    orig_tx = db.tx

    def bad_tx():
        raise RuntimeError("database is locked (sinov)")
    db.tx = bad_tx
    try:
        flow.flush()
        failed = False
    except RuntimeError:
        failed = True
    db.tx = orig_tx
    ok("Yozish xatosida yig'indi xotiraga qaytadi", failed and len(flow._acc) == n_acc and n_acc > 0, f"{n_acc}")
    touched, _ = flow.flush()
    ok("Soatlik heatmap bazaga yozildi", touched and db.scalar("SELECT COUNT(*) FROM flow_hour") >= 1, str(touched))
    occ1 = flow.heat(pid, t0 - 3600, time.time() + 1, "occ")["total"]
    # Ikkinchi marta (keyingi daqiqa) — mavjud qatorga qo'shiladi
    lc2 = counting.LineCounter(sid, "Zal", persist=True)
    run_paths({sid: (Adapter(lc2), CAM)}, [(5, [(1, 11, 0), (4.5, 4, 5), (1, 11, 0)], 1.2, 0.0)], t0=t0 + 40)
    flow.flush()
    h = flow.heat(pid, t0 - 3600, time.time() + 1, "occ")
    ok("Keyingi daqiqa yig'indisi qo'shildi (ustiga yozilmadi)", h["total"] > occ1 + 4, f"{occ1} → {h['total']}")
    arr = np.frombuffer(base64.b64decode(h["data"]), dtype=np.uint8)
    ok("Heatmap katakchalari (base64) to'g'ri o'lchamda", len(arr) == h["nx"] * h["ny"] and arr.max() == 255)
    r = c.get(f"/api/flow/summary?plan_id={pid}&period=today").json()
    tot_today = r["visits"]["total"]
    ok("Summary API: bo'limlar, yo'nalishlar, vaqt qatori", tot_today == 5 and r["visits"]["moves"] and r["series"],
       f"jami {tot_today}")
    for k in ("occ", "pas"):
        rr = c.get(f"/api/flow/heat?plan_id={pid}&period=7&kind={k}")
        ok(f"Heat API ({k})", rr.status_code == 200 and rr.json()["total"] > 0)
    csvt = c.get("/api/export/flow.csv?days=7").text
    ok("CSV eksport", "Sut" in csvt and "tashriflar" in csvt)
    # Masshtab o'zgarsa: kalibrlash qayta moslanadi, to'r kaliti yangi
    gk = flow.layout(force=True).plans[pid].grid_key
    c.post(f"/api/flow/plans/{pid}", json={"m_per_px": mpp * 1.5})
    lay = flow.layout(force=True)
    ok("Masshtab o'zgardi: kalibrlash qayta moslandi, to'r yangi (eski heatmap aralashmaydi)",
       sid in lay.cams and lay.plans[pid].grid_key != gk and flow.heat(pid, t0 - 3600, time.time() + 1)["total"] == 0)
    c.post(f"/api/flow/plans/{pid}", json={"m_per_px": mpp})
    flow.layout(force=True)
    # Bulut yig'indisi
    item = flow.hour_item(pid, flow._hour(t0 + 60))
    ok("Bulut soatlik yig'indisi: tashriflar, yo'nalishlar, heatmap", item and sum(item["visits"].values()) >= 1 and
       item["occ"] and "grid" in item, str({k: (v if k in ('visits', 'moves') else '…') for k, v in (item or {}).items()})[:200])
    ok("Bot /oqim matni", "Sut" in flow.status_text())
    # Bulutga yuborish tartibi: joriy soat — yangi tashrif bo'lsa yoki 10 daqiqada bir; yopilgan soat — albatta;
    # bulut "flow" ni birinchi e'lon qilganda oxirgi 7 kun bir marta
    from app import sync
    sent: list = []
    o_sup, o_enq = sync.cloud_supports, sync.enqueue_flow_hour
    sync.cloud_supports = lambda f: True
    sync.enqueue_flow_hour = lambda it: sent.append((it["hour"], sum(it["visits"].values())))
    try:
        hnow = flow._hour(time.time())
        m_odd = hnow + 7 * 60                          # 10 daqiqa chegarasida emas
        flow.on_minute(m_odd)
        nb = len(sent)
        ok("Bulut: birinchi marta — oxirgi kunlar bir marta (backfill)", nb >= 1 and db.get_setting("flow_backfill_done") == "1",
           str(sent))
        sent.clear()
        with flow._acc_lock:
            flow._acc[(hnow, pid, sid)] = flow._Acc(hnow, pid, sid, flow.layout().plans[pid].grid_key)
            flow._acc[(hnow, pid, sid)].obs = 30
        flow.on_minute(m_odd)
        ok("Joriy soat, yangi tashrif yo'q, 10 daqiqa chegarasi emas — yuborilmaydi", sent == [], str(sent))
        with flow._acc_lock:
            flow._acc[(hnow, pid, sid)] = flow._Acc(hnow, pid, sid, flow.layout().plans[pid].grid_key)
            flow._dirty_hours.add((pid, hnow))
        flow.on_minute(m_odd)
        ok("Yangi tashrif bo'lsa — darhol yuboriladi", [h for h, _ in sent] == [hnow], str(sent))
        sent.clear()
        with flow._acc_lock:
            flow._acc[(hnow - 3600, pid, sid)] = flow._Acc(hnow - 3600, pid, sid, flow.layout().plans[pid].grid_key)
        flow.on_minute(m_odd)
        ok("Yopilgan soat — oxirgi marta yuboriladi", [h for h, _ in sent] == [hnow - 3600], str(sent))
    finally:
        sync.cloud_supports, sync.enqueue_flow_hour = o_sup, o_enq

    # Darvoza: nuqtada turish (jonli monitor taqlidi)
    print("\n--- Darvoza ---")
    mon = flow.FlowMonitor(sid, "Zal", persist=True)
    flow._live[sid] = mon
    import threading

    stop = threading.Event()

    def feeder():
        rr = np.random.default_rng(4)
        while not stop.is_set():
            b = box_for(CAM, (6.0 + rr.normal(0, 0.03), 5.0 + rr.normal(0, 0.03)))
            mon.update([Pp(1, b)], time.time(), W, H, None, {})
            time.sleep(0.1)
    th = threading.Thread(target=feeder, daemon=True)
    th.start()
    r = c.post("/api/flow/points", json={"source_id": sid, "px": 6.0 / mpp, "py": 5.0 / mpp})
    ok("Nuqtada turish: o'lchandi, xato kichik", r.status_code == 200 and r.json()["err"] < 0.2 and r.json()["n"] >= 10,
       r.text[:200])
    stop.set()
    th.join()
    for (x, y) in ((3, 4), (15, 3), (10, 9), (8, 2)):
        db.execute("INSERT INTO flow_points(t, source_id, plan_id, x, y, mx, my, err, n) VALUES(?,?,?,?,?,?,?,?,?)",
                   (time.time(), sid, pid, x, y, x + 0.2, y, 0.2, 20))
    g = c.get("/api/flow/gate").json()
    ok("Darvoza (nuqtalar): 5 o'lchov, 5 joy, 90% ≤ 0,5 m — o'tdi", g["points_ok"] and g["cams"][0]["spots"] >= 3, str(g["cams"]))
    flow._live.pop(sid, None)
    r = c.post("/api/flow/points", json={"source_id": sid, "px": 10, "py": 10})
    ok("Kamerada odam yo'q — 409", r.status_code == 409)
    # Qo'lda sanash sessiyasi
    r = c.post("/api/flow/sessions", json={"plan_id": pid, "sections": [ids["Sut"], ids["Non"]]})
    ok("Qo'lda sanash boshlandi", r.status_code == 200, r.text[:200])
    ses = r.json()["session"]["id"]
    ok("Ikkinchi sinov bir vaqtda — 409", c.post("/api/flow/sessions", json={"plan_id": pid, "sections": [ids["Sut"]]}).status_code == 409)
    c.post(f"/api/flow/sessions/{ses}/tap", json={"section_id": ids["Sut"]})
    c.post(f"/api/flow/sessions/{ses}/tap", json={"section_id": ids["Sut"]})
    r = c.post(f"/api/flow/sessions/{ses}/undo")
    ok("Bosish va bekor qilish", r.json()["counts"] == {str(ids["Sut"]): 1})
    ok("Kuzatilmayotgan bo'lim — 400", c.post(f"/api/flow/sessions/{ses}/tap", json={"section_id": ids["Ichimlik"]}).status_code == 400)
    r = c.post(f"/api/flow/sessions/{ses}/stop").json()["session"]
    ok("Sinov tugadi — qisqa sinov darvozaga kirmaydi", not r["gate_ok"] and r["ended_at"])
    ok("CSRF: boshqa saytdan so'rov rad etiladi",
       c.post("/api/flow/sections/1/delete", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}).status_code == 403)
    c2 = TestClient(web.app)
    ok("Kirishsiz API — 401", c2.get("/api/flow/plans").status_code == 401 and c2.get(f"/media/plans/{pid}").status_code in (401, 303))


def gate_math_suite() -> None:
    print("\n--- Darvoza hisobi (flow_eval) ---")
    tests = [{"x": 1, "y": 1, "err": 0.2}, {"x": 1.3, "y": 1, "err": 0.3}, {"x": 5, "y": 5, "err": 0.1},
             {"x": 9, "y": 2, "err": 0.4}, {"x": 9, "y": 2.5, "err": 0.35}]
    g = fe.point_gate(tests)
    ok("5 o'lchov, 3 joy, P90 ≤ 0,5 → o'tdi", g["ok"] and g["spots"] == 3, str(g))
    g = fe.point_gate([dict(t, x=1, y=1) for t in tests])
    ok("Hammasi bir joyda — yetarli emas", not g["ok"] and not g["enough"] and g["spots"] == 1)
    g = fe.point_gate(tests[:4] + [{"x": 3, "y": 8, "err": 1.4}])
    ok("Bitta katta xato (1,4 m) P90 ni oshiradi → o'tmadi", not g["ok"] and g["enough"], str(g["p90"]))
    taps = [[100, 1], [130, 1], [200, 2], [260, 2], [300, 1]]
    autos = [{"t": 102, "section_id": 1}, {"t": 128, "section_id": 1}, {"t": 203, "section_id": 2},
             {"t": 400, "section_id": 2}, {"t": 301, "section_id": 1}, {"t": 150, "section_id": 3}]
    r = fe.compare(taps, autos, [1, 2])
    ok("Juftlash ±5 s, bo'limlar alohida, kuzatilmagan bo'lim e'tiborsiz",
       r["manual"] == 5 and r["auto"] == 5 and r["matched"] == 4 and r["accuracy"] == 1.0 and r["match_rate"] == 0.8, str(r))
    base = {"ended_at": 1, "duration": 1900, "accuracy": 0.95, "match_rate": 0.9, "manual": 20}
    ok("2 sinov × 20 bosish, mos → darvoza o'tdi", fe.sessions_gate([base, base])["ok"])
    ok("Bitta sinov — yetarli emas", not fe.sessions_gate([base])["enough"])
    ok("Juftlash 80% — o'tmadi", not fe.sessions_gate([dict(base, match_rate=0.8), base])["ok"])
    ok("Jami bosish < 30 — yetarli emas", not fe.sessions_gate([dict(base, manual=10), dict(base, manual=10)])["enough"])


def cli_gate_suite() -> None:
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "tests" / "flow_gate.py"), "--db", str(db.DB_PATH)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("tests/flow_gate.py ishlaydi (bazani o'qiydi)", r.returncode in (0, 1, 2) and "Nuqtada turish" in r.stdout,
       (r.stdout + r.stderr)[-300:])


def main() -> int:
    t = time.time()
    homography_suite()
    logic_suite()
    multicam_suite()
    gate_math_suite()
    storage_api_suite()
    cli_gate_suite()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
