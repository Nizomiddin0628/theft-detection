"""Tahlilchi qoidalarini sintetik ketma-ketliklarda sinash.

Ishga tushirish:  python -m tests.test_analyzer
"""
from __future__ import annotations

import collections
import sys

import numpy as np

from app import db
from app.ai.analyzer import Analyzer
from app.ai.engine import Obj, Person
from app.ml import synthetic


def run(behavior: str, rng: np.random.Generator, with_item: bool) -> bool:
    seq = synthetic.generate(behavior, rng, with_item=with_item)
    an = Analyzer("test")
    items_by_t = {round(t, 3): box for t, box in seq["items"]}
    fired = False
    for t, k, box in seq["frames"]:
        p = Person(box=box, score=0.9, kpts=k, track_id=1)
        objs = []
        ib = items_by_t.get(round(t, 3))
        if ib is not None:
            objs.append(Obj(ib, 0.8, 39, "shisha", "item"))
        alerts, _ = an.update([p], objs, t, synthetic.W, synthetic.H)
        if alerts:
            fired = True
    return fired


def main(n: int = 60, mode: str = "normal", use_ml: str = "0") -> dict:
    db.init_db()
    preset = {"demo": (35, 0.3), "normal": (60, 0.45), "strict": (75, 0.6)}[mode]
    db.set_settings({"threshold": preset[0], "dwell_sec": preset[1], "use_ml": use_ml, "cooldown_sec": 15})
    rng = np.random.default_rng(42)
    rates = collections.OrderedDict()
    for beh in synthetic.POSITIVE + synthetic.NEGATIVE:
        hits = sum(run(beh, rng, with_item=True) for _ in range(n))
        rates[beh] = hits / n
    pos = np.mean([rates[b] for b in synthetic.POSITIVE])
    neg = np.mean([rates[b] for b in synthetic.NEGATIVE])
    print(f"Rejim: {mode}, ML: {use_ml}")
    for b, r in rates.items():
        tag = "SHUBHALI" if b in synthetic.POSITIVE else "oddiy   "
        print(f"  {tag} {b:18s} signal ulushi: {r:5.0%}")
    print(f"  O'rtacha: shubhali {pos:.0%}, oddiy (xato signal) {neg:.0%}")
    return {"pos": pos, "neg": neg, "rates": rates}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    ml = sys.argv[2] if len(sys.argv) > 2 else "0"
    main(mode=mode, use_ml=ml)
