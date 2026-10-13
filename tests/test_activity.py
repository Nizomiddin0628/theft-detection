"""Kamera faolligi va "buyumni faqat kerakda qidirish" sinovlari (soxta model bilan, tez).

Ishga tushirish:  python -m tests.test_activity
"""
from __future__ import annotations

import sys
import threading
import time

import numpy as np

from app import db
from app.ai import worker
from app.ai.engine import Obj, PriorityLock
from app.ml import synthetic
from tests.eval_pipeline import FRAME, FakeEngine

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def feed(proc, eng, frames, items=None, static=None, t_shift=0.0, item_cls=39, name="shisha"):
    """Kadrlarni 15 kadr/s da beradi, Processor o'zi qaysi kadrda ishlashni tanlaydi."""
    by_t = {round(t, 3): b for t, b in (items or [])}
    last = -1e9
    out = []
    for t, k, box in frames:
        tt = t + t_shift
        if tt - last < proc.interval(8, 4) - 0.5 / synthetic.FPS:
            continue
        last = tt
        eng.persons = [(k, box)] if k is not None else []
        ib = by_t.get(round(t, 3))
        eng.objects = [Obj(ib.astype(np.float32), 0.8, item_cls, name, "item")] if ib is not None else []
        if static is not None:
            eng.objects.append(Obj(static.astype(np.float32), 0.8, 45, "kosa", "item"))
        res, ann, alerts, status = proc.step(FRAME, tt)
        out.append((tt, proc.mode, ann, alerts, status))
    return out


def test_priority_lock() -> None:
    lock = PriorityLock()
    order: list[str] = []
    gate = threading.Event()

    def holder():
        with lock.hold(1):
            gate.wait(2)

    def req(name, prio, delay):
        time.sleep(delay)
        with lock.hold(prio):
            order.append(name)

    th = [threading.Thread(target=holder)]
    th += [threading.Thread(target=req, args=a) for a in
           (("bo'sh", 2, 0.05), ("tinch", 1, 0.10), ("faol", 0, 0.15))]
    for x in th:
        x.start()
    time.sleep(0.4)
    gate.set()
    for x in th:
        x.join(3)
    ok("Navbat: faol → tinch → bo'sh (kelish tartibi teskari bo'lsa ham)", order == ["faol", "tinch", "bo'sh"],
       str(order))
    # Uzoq kutgan bo'sh so'rov yangi faol so'rovdan oldin o'tadi (hech kim abadiy kutmaydi)
    order.clear()
    gate.clear()
    th = [threading.Thread(target=holder), threading.Thread(target=req, args=("bo'sh", 2, 0.05)),
          threading.Thread(target=req, args=("faol", 0, 1.25))]
    for x in th:
        x.start()
    time.sleep(1.35)
    gate.set()
    for x in th:
        x.join(3)
    ok("1 s dan ko'p kutgan bo'sh kamera yangi faoldan oldin o'tadi", order == ["bo'sh", "faol"], str(order))


def test_modes(eng: FakeEngine) -> None:
    proc = worker.Processor("test")
    ok("Rejim oralig'i: faol 1/8, tinch 1/4, bo'sh 1/4 s",
       abs(worker.Processor("a", mode=worker.ACTIVE).interval(8, 4) - 0.125) < 1e-9 and
       abs(worker.Processor("a", mode=worker.CALM).interval(8, 4) - 0.25) < 1e-9 and
       abs(worker.Processor("a", mode=worker.IDLE).interval(8, 4) - 0.25) < 1e-9)
    # 1) Bo'sh kamera: 20 s davomida odam yo'q
    empty = [(i / synthetic.FPS, None, None) for i in range(int(20 * synthetic.FPS))]
    eng.calls.clear()
    out = feed(proc, eng, empty)
    ok("Bo'sh kamera: model 4 s da bir marta (harakat yo'q)", eng.calls["pose"] <= 6 and eng.calls["obj"] == 0,
       f"20 s da pose {eng.calls['pose']}, buyum {eng.calls['obj']}, qadamlar {len(out)}")
    ok("Bo'sh kamerada ustuvorlik eng past", eng.calls["pose_p2"] == eng.calls["pose"])
    # 2) Tinch: odam turibdi, qo'llar pastda — buyum detektori ishlamaydi
    rng = np.random.default_rng(5)
    seq = synthetic.generate("idle", rng)
    eng.calls.clear()
    out = feed(proc, eng, seq["frames"], t_shift=20.0)
    modes = {m for _, m, *_ in out[2:]}
    dur = seq["frames"][-1][0]
    ok("Qo'llar pastda: rejim 'tinch', buyum deyarli qidirilmaydi (≤0.5/s nazorat)",
       modes == {worker.CALM} and eng.calls["obj"] <= 0.5 * dur,
       f"rejimlar {modes}, {dur:.0f} s da buyum chaqiruvi {eng.calls['obj']}")
    calm_rate = eng.calls["pose"] / max(seq["frames"][-1][0], 1e-3)
    ok("Tinch kamera ~4 kadr/s", 3.0 <= calm_rate <= 4.6, f"{calm_rate:.1f}/s")
    # 3) Odam ketdi → 2 s dan keyin yana bo'sh
    t_end = 20.0 + seq["frames"][-1][0]
    out = feed(proc, eng, [(i / synthetic.FPS, None, None) for i in range(int(4 * synthetic.FPS))],
               t_shift=t_end + 0.1)
    ok("Odam ketgach rejim 'bo'sh'", out[-1][1] == worker.IDLE, out[-1][1])


def test_grab_and_hide(eng: FakeEngine) -> None:
    rng = np.random.default_rng(11)
    seq = None
    for _ in range(50):   # buyumi aniq ko'rinadigan "cho'ntakka solish" ketma-ketligi
        s = synthetic.generate("pocket", rng, with_item=True)
        if len(s["items"]) >= 8:
            seq = s
            break
    proc = worker.Processor("test")
    db.set_settings({"show_objects": "0"})
    eng.calls.clear()
    out = feed(proc, eng, seq["frames"], seq["items"])
    modes = [m for _, m, *_ in out]
    ok("Qo'l polkaga uzatilganda rejim 'faol'", worker.ACTIVE in modes, " ".join(m[0] for m in modes))
    hand_calls = eng.calls["obj_hand"]
    ok("Buyum faqat qo'l/odam atrofida qidirildi (butun kadr emas)", eng.calls["obj"] > 0 and
       eng.calls["obj"] == hand_calls + eng.calls["obj_person"], dict(eng.calls))
    held = [h for _, _, ann, _, _ in out for st in ann[2].values() for h in (st.get("held") or [])]
    names = {h["name"] for h in held}
    states = {h["state"] for h in held}
    ok("Qo'ldagi mahsulot nomi bilan belgilandi", "shisha" in names, f"{names} {states}")
    ok("Yashirilgandan keyin 'yashirildi?' holati", "yashirildi?" in states, str(states))
    ok("Rasmda polkadagi buyumlar chizilmaydi (show_objects=0)", all(not ann[1] for _, _, ann, _, _ in out))
    alerts = [a for *_, al, _ in out for a in al]
    ok("Signal chiqdi va sabablarida buyum bor", alerts and any("Qo'lda" in r for r in alerts[0]["reasons"]),
       (alerts[0]["breakdown"] if alerts else "signal yo'q"))
    # 4) show_objects=1 — topilgan buyumlar ramkasi ham chiziladi
    db.set_settings({"show_objects": "1"})
    proc2 = worker.Processor("test2")
    out2 = feed(proc2, eng, seq["frames"], seq["items"])
    ok("show_objects=1 bo'lsa buyum ramkalari chiziladi", any(ann[1] for _, _, ann, _, _ in out2))
    db.set_settings({"show_objects": "0"})


def test_static_item(eng: FakeEngine) -> None:
    """Polkada qimirlamay turgan buyum yonida qo'l uzatilib qaytarilsa — 'qo'lda' deb belgilanmaydi."""
    rng = np.random.default_rng(3)
    seq = synthetic.generate("reach_return", rng)
    side = seq["active"]
    wi = 9 if side == "L" else 10
    k_far = max(seq["frames"], key=lambda f: abs(f[1][wi, 0] - f[2][0]))[1]   # eng uzoq uzatilgan payt
    c = k_far[wi, :2]
    static = np.array([c[0] - 25, c[1] - 25, c[0] + 25, c[1] + 25])
    proc = worker.Processor("test")
    out = feed(proc, eng, seq["frames"], static=static)
    held = [h for _, _, ann, _, _ in out for st in ann[2].values() for h in (st.get("held") or [])]
    ok("Polkadagi qimirlamas buyum 'qo'lda' deb olinmaydi", not held, str(held[:1]))


def main() -> int:
    db.init_db()
    db.set_settings({"threshold": 60, "dwell_sec": 0.45, "use_ml": "0", "cooldown_sec": 15,
                     "ai_fps": 8, "calm_fps": 4, "show_objects": "0"})
    eng = FakeEngine()
    orig = worker.Engine.get
    worker.Engine.get = staticmethod(lambda: eng)  # type: ignore[method-assign]
    try:
        test_priority_lock()
        test_modes(eng)
        test_grab_and_hide(eng)
        test_static_item(eng)
    finally:
        worker.Engine.get = orig  # type: ignore[method-assign]
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
