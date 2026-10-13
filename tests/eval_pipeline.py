"""To'liq quvur (Processor) bo'yicha sintetik baholash: kamera rejimlari + buyumni faqat kerakda qidirish.

`eval_synthetic` faqat tahlilchini sinaydi (buyumlar butun kadrdan har 2-kadrda beriladi). Bu yerda esa
haqiqiy ish tartibi takrorlanadi:
  * kadrlar 15 kadr/s keladi, AI qadami oralig'ini Processor o'zi tanlaydi (bo'sh / tinch / faol);
  * buyum detektori faqat Processor so'ragan sohada (qo'l yoki odam atrofi) "ishlaydi": soxta detektor
    markazi shu soha ichida bo'lgan buyum va sumkalarni qaytaradi;
  * har ketma-ketlik uchun model chaqiruvlari sanaladi (resurs sarfi).

Ishga tushirish:  python -m tests.eval_pipeline [N] [normal|strict|demo] [ML 0/1]
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter

import numpy as np

from app import db
from app.ai import worker
from app.ai.engine import Obj, Person
from app.ml import synthetic
from tests.eval_synthetic import PRESETS, _bag_box_at_hand, phone_back_to_pocket


class FakeEngine:
    """Model o'rniga: oldindan ma'lum odam va buyumlarni qaytaradi, chaqiruvlarni sanaydi."""

    def __init__(self):
        self.persons: list = []
        self.objects: list = []
        self.calls: Counter = Counter()

    def detect_persons(self, frame, size=640, conf=0.4, iou=0.5, prio=1):
        self.calls["pose"] += 1
        self.calls[f"pose_p{prio}"] += 1
        return [Person(box=b.copy(), score=0.9, kpts=k.copy()) for k, b in self.persons]

    def detect_objects_in(self, frame, box, size=320, conf=0.25, iou=0.5, prio=0, src="hand"):
        self.calls["obj"] += 1
        self.calls[f"obj_{src}"] += 1
        out = []
        for o in self.objects:
            cx, cy = (o.box[0] + o.box[2]) / 2, (o.box[1] + o.box[3]) / 2
            if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
                b = o.box.copy()
                b[[0, 2]] = b[[0, 2]].clip(box[0], box[2])
                b[[1, 3]] = b[[1, 3]].clip(box[1], box[3])
                out.append(Obj(b, o.score, o.cls, o.name, o.kind))
        return out


FRAME = np.zeros((synthetic.H, synthetic.W, 3), dtype=np.uint8)


def run_pipeline(frames, items, eng: FakeEngine, item_cls=39, item_name="shisha", bags=None) -> tuple[list, dict]:
    """Bitta ketma-ketlik: 15 kadr/s kadrlar, Processor rejimiga qarab qadam tashlanadi."""
    proc = worker.Processor("eval")
    by_t = {round(t, 3): b for t, b in items}
    alerts = []
    last = -1e9
    mode_t: Counter = Counter()
    prev_t, prev_mode = None, proc.mode
    eng.calls.clear()
    for t, k, box in frames:
        if t - last < proc.interval(8, 4) - 0.5 / synthetic.FPS:
            continue
        last = t
        if prev_t is not None:
            mode_t[prev_mode] += t - prev_t
        prev_t = t
        eng.persons = [(k, box)]
        ib = by_t.get(round(t, 3))
        eng.objects = ([Obj(ib.astype(np.float32), 0.8, item_cls, item_name, "item")] if ib is not None else [])
        eng.objects += [Obj(np.asarray(b, dtype=np.float32), 0.8, 26, "sumka", "bag") for b in (bags or [])]
        _, _, al, _ = proc.step(FRAME, t)
        prev_mode = proc.mode
        alerts += al
    dur = max(frames[-1][0] - frames[0][0], 1e-3)
    return alerts, {"calls": dict(eng.calls), "dur": dur, "modes": dict(mode_t)}


def main(n: int = 40, mode: str = "normal", use_ml: str = "1") -> dict:
    db.init_db()
    th, dw = PRESETS[mode]
    db.set_settings({"threshold": th, "dwell_sec": dw, "use_ml": use_ml, "cooldown_sec": 15,
                     "ai_fps": 8, "calm_fps": 4})
    eng = FakeEngine()
    orig_get = worker.Engine.get
    worker.Engine.get = staticmethod(lambda: eng)  # type: ignore[method-assign]
    t0 = time.time()
    rng = np.random.default_rng(2026)
    rates: dict = {}
    use: Counter = Counter()
    secs = 0.0
    mode_secs: Counter = Counter()

    def account(st):
        nonlocal secs
        secs += st["dur"]
        use.update(st["calls"])
        mode_secs.update(st["modes"])

    try:
        for beh in synthetic.POSITIVE + synthetic.NEGATIVE:
            hits = 0
            for _ in range(n):
                seq = synthetic.generate(beh, rng, with_item=True)
                bags = [_bag_box_at_hand(seq)] if beh == "bag" else None
                al, st = run_pipeline(seq["frames"], seq["items"], eng, bags=bags)
                account(st)
                hits += bool(al)
            rates[beh] = hits / n
        hits = 0
        for _ in range(n):
            seq = synthetic.generate("basket", rng, with_item=True)
            al, st = run_pipeline(seq["frames"], seq["items"], eng, bags=[_bag_box_at_hand(seq)])
            account(st)
            hits += bool(al)
        rates["basket_as_bag"] = hits / n
        hits = 0
        for _ in range(n):
            fr, it = phone_back_to_pocket(rng)
            al, st = run_pipeline(fr, it, eng, item_cls=67, item_name="telefon")
            account(st)
            hits += bool(al)
        rates["own_phone"] = hits / n
    finally:
        worker.Engine.get = orig_get  # type: ignore[method-assign]

    pos = float(np.mean([rates[b] for b in synthetic.POSITIVE]))
    neg = float(np.mean([rates[b] for b in synthetic.NEGATIVE]))
    per_s = {k: round(v / secs, 2) for k, v in sorted(use.items())}
    share = {k: f"{v / secs:.0%}" for k, v in mode_secs.items()}
    print(f"Quvur: rejim {mode}, ML {use_ml}, har holatga {n} ta; kadrlar 15/s, AI qadamini Processor tanlaydi")
    for b in synthetic.POSITIVE:
        print(f"  SHUBHALI  {b:18s} ushlandi {rates[b]:5.0%}")
    for b in synthetic.NEGATIVE + ["basket_as_bag", "own_phone"]:
        print(f"  oddiy     {b:18s} xato signal {rates[b]:5.0%}")
    print(f"  JAMI: shubhali ushlandi {pos:.0%} | oddiy xato signal {neg:.1%} | "
          f"o'z telefoni {rates['own_phone']:.0%} | savat=sumka {rates['basket_as_bag']:.0%}")
    print(f"  Resurs (1 soniya videoga): {per_s}; rejim ulushi {share}  ({time.time() - t0:.0f} s)")
    return {"mode": mode, "ml": use_ml, "pos": pos, "neg": neg, "rates": rates, "per_sec": per_s,
            "mode_share": share}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    mode = sys.argv[2] if len(sys.argv) > 2 else "normal"
    ml = sys.argv[3] if len(sys.argv) > 3 else "1"
    res = main(n, mode, ml)
    if len(sys.argv) > 4:
        with open(sys.argv[4], "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
