"""Kamera zonalari sinovi: geometriya, API va har zona qoidasi (sintetik skeletlar bilan).

Ishga tushirish:  python -m tests.test_zones
"""
from __future__ import annotations

import sys
import time

import numpy as np

from app import db
from app.ai import worker
from app.ai.analyzer import Analyzer
from app.ai.engine import Obj, Person
from app.ai.zones import Zone, ZoneSet
from app.ml import synthetic
from tests.eval_pipeline import FRAME, FakeEngine
from tests.eval_synthetic import _bag_box_at_hand

W, H = synthetic.W, synthetic.H
results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def rect(x1, y1, x2, y2) -> np.ndarray:
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


def run(frames, items=None, bags=None, zones=None, an=None, step=2, t0=0.0):
    """Tahlilchini to'g'ridan-to'g'ri ishlatish (7.5 kadr/s, buyum har kadrda qidirilgan)."""
    an = an or Analyzer("zona")
    by_t = {round(t, 3): b for t, b in (items or [])}
    alerts, last_status = [], {}
    for t, k, box in frames[::step]:
        objs = []
        ib = by_t.get(round(t, 3))
        if ib is not None:
            objs.append(Obj(ib.astype(np.float32), 0.8, 39, "shisha", "item"))
        objs += [Obj(np.asarray(b, dtype=np.float32), 0.8, 26, "sumka", "bag") for b in (bags or [])]
        a, last_status = an.update([Person(box=box, score=0.9, kpts=k, track_id=1)], objs, t + t0, W, H,
                                   searched=None, zones=zones)
        alerts += a
    return alerts, last_status, an


def first_alerting(beh: str, rng, **kw):
    for _ in range(60):
        seq = synthetic.generate(beh, rng, with_item=True)
        bags = [_bag_box_at_hand(seq)] if beh == "bag" else None
        al, _, _ = run(seq["frames"], seq["items"], bags)
        if al:
            return seq, bags
    raise RuntimeError(f"{beh}: signal beradigan ketma-ketlik topilmadi")


def foot_of(seq) -> np.ndarray:
    b = seq["frames"][len(seq["frames"]) // 2][2]
    return np.array([(b[0] + b[2]) / 2 / W, b[3] / H])


def test_geometry() -> None:
    z = Zone(1, "Kassa", "kassa", rect(0.1, 0.1, 0.5, 0.5))
    ok("Nuqta zonada / tashqarida", z.contains((0.3 * W, 0.3 * H), W, H) and not z.contains((0.6 * W, 0.3 * H), W, H))
    zs = ZoneSet([Zone(2, "TV", "etiborsiz", rect(0.0, 0.0, 0.5, 1.0))])
    m = zs.motion_mask(96, 54)
    ok("E'tiborsiz zona harakat niqobidan chiqariladi", m is not None and not m[:, :40].any() and m[:, 60:].all())
    ok("Zonasiz kamerada niqob yo'q", ZoneSet().motion_mask(96, 54) is None)


def test_api() -> None:
    from fastapi.testclient import TestClient

    from app.web import app

    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"})
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Sinov kamera", "file", "samples/dokon_namuna_20s.mp4", 0, 1, time.time()))
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "kassa", "name": "Kassa 1",
                                                  "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.6]]})
    ok("Zona yaratildi", r.status_code == 200 and r.json()["zone"]["kind"] == "kassa", r.text[:120])
    zid = r.json()["zone"]["id"]
    r = c.post(f"/api/sources/{sid}/zones", json={"id": zid, "kind": "polka", "name": "Ichimliklar",
                                                  "sens": "yuqori", "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.6],
                                                                                [0.1, 0.6]]})
    z = c.get(f"/api/sources/{sid}/zones").json()
    ok("Zona tahrirlandi", len(z) == 1 and z[0]["kind"] == "polka" and z[0]["sens"] == "yuqori" and
       len(z[0]["points"]) == 4, str(z)[:160])
    ok("2 nuqtali zona rad etiladi", c.post(f"/api/sources/{sid}/zones", json={
        "kind": "kassa", "points": [[0.1, 0.1], [0.2, 0.2]]}).status_code == 400)
    ok("Noto'g'ri tur rad etiladi", c.post(f"/api/sources/{sid}/zones", json={
        "kind": "xyz", "points": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.1]]}).status_code == 400)
    r = c.get(f"/api/sources/{sid}/frame.jpg")
    ok("Zona chizish uchun toza kadr", r.status_code == 200 and r.content[:2] == b"\xff\xd8", f"{len(r.content)} bayt")
    ok("Zona sahifasi ochiladi", c.get(f"/sources/{sid}/zones").status_code == 200)
    ok("Kameralar ro'yxatida zona soni", next(s for s in c.get("/api/sources").json() if s["id"] == sid)["zones"] == 1)
    ok("Zona o'chirildi", c.post(f"/api/zones/{zid}/delete").status_code == 200 and
       not c.get(f"/api/sources/{sid}/zones").json())
    c.post(f"/api/sources/{sid}/zones", json={"kind": "eshik", "points": [[0, 0], [1, 0], [1, 1]]})
    c.post(f"/api/sources/{sid}/delete")
    ok("Manba o'chirilganda zonalari ham o'chadi", not db.rows("SELECT id FROM zones WHERE source_id=?", (sid,)))


def test_rules() -> None:
    rng = np.random.default_rng(21)
    seq, _ = first_alerting("pocket", rng)
    fx, fy = foot_of(seq)
    around_foot = rect(fx - 0.25, fy - 0.1, fx + 0.25, min(1.0, fy + 0.1))

    # Xodim zonasi: shu odam xodim — signal yo'q
    al, st, _ = run(seq["frames"], seq["items"], zones=ZoneSet([Zone(1, "Peshtaxta orti", "xodim", around_foot)]))
    ok("Xodim zonasidagi odamga signal berilmaydi", not al and st.get(1, {}).get("staff"), str(st.get(1, {}))[:80])

    # Kassa: sumkaga solish — oddiy holat, signal yo'q; zonasiz esa signal bor
    bseq, bags = first_alerting("bag", rng)
    bx, by = foot_of(bseq)
    kassa = ZoneSet([Zone(2, "Kassa", "kassa", rect(bx - 0.25, by - 0.1, bx + 0.25, min(1.0, by + 0.1)))])
    al, _, _ = run(bseq["frames"], bseq["items"], bags, zones=kassa)
    ok("Kassada sumkaga solish signal bermaydi (zonasiz bergan edi)", not al)

    # Polka: qo'l polka zonasiga kirgan → +10 ball va sabab
    side = seq["active"]
    wi = 9 if side == "L" else 10
    far = max(seq["frames"], key=lambda f: -f[1][wi, 1] if f[1][wi, 2] > 0.4 else -1e9)[1][wi, :2]
    shelf = ZoneSet([Zone(3, "Ichimliklar", "polka", rect(far[0] / W - 0.06, far[1] / H - 0.06,
                                                         far[0] / W + 0.06, far[1] / H + 0.06))])
    al, _, _ = run(seq["frames"], seq["items"], zones=shelf)
    ok("Polka zonasidan olingan — sababda ko'rsatiladi (+10)",
       al and any("polka zonasi" in r for r in al[0]["reasons"]),
       str(al[0]["breakdown"]) if al else "signal yo'q")

    # Eshik: signal bo'lgan odam keyin eshik zonasiga yurib boradi → "Chiqish tomon ketmoqda"
    t_end, k_last, b_last = seq["frames"][-1]
    walk = []
    for i in range(1, 31):     # 2 s davomida o'ngga 300 px yuradi
        d = 10.0 * i
        k = k_last.copy()
        b = b_last.copy()
        k[:, 0] += d
        b[[0, 2]] += d
        walk.append((t_end + i / synthetic.FPS, k, b))
    door_x = (b_last[2] + 300) / W
    door = Zone(4, "Asosiy eshik", "eshik", rect(door_x - 0.2, fy - 0.1, min(1.0, door_x + 0.05), min(1.0, fy + 0.1)))
    zs = ZoneSet([door])
    al1, _, an = run(seq["frames"], seq["items"], zones=zs)
    al2, _, _ = run(walk, zones=zs, an=an)
    ex = [a for a in al2 if a["kind"] == "exit"]
    ok("Shubhali odam eshikka keldi — alohida signal", len(ex) == 1 and al1,
       (ex[0]["reasons"][-1] + f" | ball {ex[0]['score']:.0f}") if ex else f"{[a['kind'] for a in al2]}")
    al3, _, _ = run(walk[-10:], zones=zs, an=an)
    ok("Eshik signali bir marta", not [a for a in al3 if a["kind"] == "exit"])
    # Signal paytida allaqachon eshik zonasida turgan odam — ikkinchi ("chiqish") xabar yo'q
    al, _, _ = run(seq["frames"], seq["items"], zones=ZoneSet([Zone(6, "Eshik", "eshik", rect(0, 0, 1, 1))]))
    ok("Eshik oldida yashirgan odamga faqat bitta signal", len(al) == 1 and al[0]["kind"] != "exit",
       str([a["kind"] for a in al]))
    # Oddiy xaridor (signal ham, yashirish ham yo'q) eshikka kelsa — signal yo'q
    calm = synthetic.generate("walk", rng)
    fxw, fyw = foot_of(calm)
    al, _, _ = run(calm["frames"], zones=ZoneSet([Zone(5, "Eshik", "eshik", rect(0, 0, 1, 1))]))
    ok("Oddiy xaridor eshikdan o'tsa signal yo'q", not al)


def test_ignore_zone() -> None:
    """E'tiborsiz zonadagi odam (televizordagi) kuzatilmaydi va harakati kamerani uyg'otmaydi."""
    eng = FakeEngine()
    orig = worker.Engine.get
    orig_load = worker.zones_mod.load
    worker.Engine.get = staticmethod(lambda: eng)  # type: ignore[method-assign]
    try:
        rng = np.random.default_rng(3)
        seq = synthetic.generate("pocket", rng, with_item=True)
        b = seq["frames"][0][2]
        cx, cy = (b[0] + b[2]) / 2 / W, (b[1] + b[3]) / 2 / H
        proc = worker.Processor("tv")
        tv = ZoneSet([Zone(9, "TV", "etiborsiz", rect(cx - 0.3, cy - 0.4, cx + 0.3, cy + 0.4))])
        worker.zones_mod.load = lambda sid: tv
        n_tracked = 0
        last = -1e9
        for t, k, box in seq["frames"]:
            if t - last < proc.interval(8, 4) - 0.5 / synthetic.FPS:
                continue
            last = t
            eng.persons = [(k, box)]
            res, ann, al, st = proc.step(FRAME, t)
            n_tracked += len(res.persons)
        ok("E'tiborsiz zonadagi odam kuzatilmaydi", n_tracked == 0 and proc.mode != worker.ACTIVE,
           f"kuzatilgan {n_tracked}, rejim {proc.mode}")
    finally:
        worker.Engine.get = orig  # type: ignore[method-assign]
        worker.zones_mod.load = orig_load


def main() -> int:
    db.init_db()
    db.set_settings({"threshold": 60, "dwell_sec": 0.45, "use_ml": "0", "cooldown_sec": 15, "signal_exit": "1"})
    test_geometry()
    test_api()
    test_rules()
    test_ignore_zone()
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
