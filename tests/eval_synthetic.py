"""Realistik sintetik baholash: tahlilchini haqiqiy tizim sharoitiga yaqin holatda sinaydi.

`test_analyzer` dan farqi:
  * kadr tezligi ~7.5 kadr/s (haqiqiy AI tezligi), 15 emas;
  * buyum detektori har ikkinchi kadrda ishlaydi (Processor kabi), oraliqda eski natija;
  * "sumka" xatti-harakati uchun haqiqatan sumka ramkasi beriladi;
  * qo'shimcha holatlar: o'z telefonini cho'ntakka qaytarish, savat "sumka" deb aniqlanishi,
    bir kadrda ikki odamning bir vaqtda yashirishi.

Ishga tushirish:  python -m tests.eval_synthetic [N] [normal|strict|demo] [ML 0/1]
Natija: ushlash (recall) va xato signal ulushlari. Bu raqamlar sintetik ma'lumotda —
haqiqiy do'kondagi aniqlik emas, lekin o'zgarishlarni bir-biri bilan solishtirish uchun yaroqli.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from app import db
from app.ai.analyzer import Analyzer
from app.ai.engine import Obj, Person
from app.ml import synthetic

STEP = 2   # 15 kadr/s -> 7.5 kadr/s
PRESETS = {"demo": (35, 0.3), "normal": (60, 0.45), "strict": (75, 0.6)}


def _bag_box_at_hand(seq: dict) -> np.ndarray:
    fr = seq["frames"]
    wi = 9 if seq["active"] == "L" else 10
    d0, d1 = seq["dwell"] if seq["dwell"][1] > 0 else (0, fr[-1][0])
    cand = [f for f in fr if d0 <= f[0] <= d1] or fr
    k = max(cand, key=lambda f: f[1][wi, 1])[1]
    c = k[wi, :2]
    r = 0.12 * float(fr[0][2][3] - fr[0][2][1])
    return np.array([c[0] - r, c[1] - r * 0.5, c[0] + r, c[1] + r * 1.2], dtype=np.float32)


def run_seq(frames, items, item_cls=39, item_name="shisha", bags=None, an=None, tid=1, shift=0.0):
    an = an or Analyzer("eval")
    by_t = {round(t, 3): b for t, b in items}
    last: list = []
    alerts = []
    for j, (t, k, box) in enumerate(frames[::STEP]):
        if j % 2 == 0:   # buyum detektori har 2-kadrda
            ib = by_t.get(round(t, 3))
            last = [Obj(ib, 0.8, item_cls, item_name, "item")] if ib is not None else []
            last += [Obj(b, 0.8, 26, "sumka", "bag") for b in (bags or [])]
        a, _ = an.update([Person(box=box, score=0.9, kpts=k, track_id=tid)], last, t + shift,
                         synthetic.W, synthetic.H)
        alerts += a
    return alerts


def phone_back_to_pocket(rng):
    """Xaridor o'z telefonini cho'ntakdan olib, qarab, yana cho'ntakka soladi (oddiy holat)."""
    b = synthetic.Body(rng, upper_only=False)
    active = "L" if rng.random() < 0.5 else "R"
    other = "R" if active == "L" else "L"
    s = b.side_sign(active)
    rest_a, rest_o = b.rest(active), b.rest(other)
    pocket = np.array([s * b.hw_hip * rng.uniform(0.7, 1.05), rng.uniform(-0.05, 0.12)])
    chest = np.array([s * rng.uniform(0.05, 0.25), rng.uniform(-0.75, -0.5)])
    t = 0.0
    keys = [(0.0, rest_a, 1.0)]

    def add(dt, tgt, c=1.0):
        nonlocal t
        t += dt
        keys.append((t, tgt, c))

    add(rng.uniform(0.3, 0.5), pocket)
    add(0.12, pocket, 0.5)
    add(rng.uniform(0.3, 0.5), pocket, 0.5)
    h0 = t
    add(rng.uniform(0.35, 0.5), chest)
    add(rng.uniform(1.5, 3.0), chest)
    add(rng.uniform(0.3, 0.5), pocket)
    h1 = t
    add(0.12, pocket, rng.uniform(0.25, 0.65))
    add(rng.uniform(0.5, 1.2), pocket, rng.uniform(0.25, 0.65))
    add(rng.uniform(0.3, 0.5), rest_a)
    frames, items = [], []
    origin = np.array([b.cx, b.hip_y])
    for i in range(int((t + 0.6) * synthetic.FPS)):
        tt = i / synthetic.FPS
        wa, ca = synthetic._track(keys, tt)
        head = 0.02 * np.sin(tt)
        k, box = (b.pose(wa, rest_o, origin, head, ca, 1.0) if active == "L"
                  else b.pose(rest_o, wa, origin, head, 1.0, ca))
        frames.append((tt, k, box))
        if h0 <= tt <= h1:
            wi = 9 if active == "L" else 10
            c = k[wi, :2]
            r = 0.10 * b.L
            items.append((tt, np.array([c[0] - r, c[1] - r * 1.6, c[0] + r, c[1] + r * 0.4])))
    return frames, items


def two_people(rng, n):
    """Ikki odam bir kadrda 1 s farq bilan yashiradi. Har biri alohida signal beradigan juftliklar olinadi."""
    both = caught = 0
    done = tries = 0
    while done < n and tries < n * 10:
        tries += 1
        a = synthetic.generate("shirt", rng, with_item=True)
        b = synthetic.generate("shirt", rng, with_item=True)
        if not run_seq(a["frames"], a["items"]) or not run_seq(b["frames"], b["items"]):
            continue
        done += 1
        an = Analyzer("eval2")
        dx = 420.0 if a["frames"][0][2][0] < synthetic.W / 2 else -420.0
        ia = {round(t, 3): x for t, x in a["items"]}
        ib = {round(t, 3): x for t, x in b["items"]}
        fa = {round(t, 3): (k, bx) for t, k, bx in a["frames"][::STEP]}
        fb = {round(t + 1.0, 3): (t, k, bx) for t, k, bx in b["frames"][::STEP]}
        ids = set()
        last: list = []
        for j, t in enumerate(sorted(set(fa) | set(fb))):
            persons = []
            cur = []
            if t in fa:
                persons.append(Person(box=fa[t][1], score=0.9, kpts=fa[t][0], track_id=1))
                if ia.get(t) is not None:
                    cur.append(Obj(ia[t], 0.8, 39, "shisha", "item"))
            if t in fb:
                tb, k, bx = fb[t]
                k = k.copy()
                bx = bx.copy()
                k[:, 0] += dx
                bx[[0, 2]] += dx
                persons.append(Person(box=bx, score=0.9, kpts=k, track_id=2))
                ob = ib.get(round(tb, 3))
                if ob is not None:
                    ob = ob.copy()
                    ob[[0, 2]] += dx
                    cur.append(Obj(ob, 0.8, 39, "shisha", "item"))
            if j % 2 == 0:
                last = cur
            al, _ = an.update(persons, last, t, synthetic.W, synthetic.H)
            ids |= {x["track_id"] for x in al}
        caught += len(ids)
        both += len(ids) == 2
    return both, done, caught


def main(n: int = 40, mode: str = "normal", use_ml: str = "1") -> dict:
    db.init_db()
    th, dw = PRESETS[mode]
    db.set_settings({"threshold": th, "dwell_sec": dw, "use_ml": use_ml, "cooldown_sec": 15})
    t0 = time.time()
    rng = np.random.default_rng(2026)
    rates = {}
    for beh in synthetic.POSITIVE + synthetic.NEGATIVE:
        hits = 0
        for _ in range(n):
            seq = synthetic.generate(beh, rng, with_item=True)
            bags = [_bag_box_at_hand(seq)] if beh == "bag" else None
            hits += bool(run_seq(seq["frames"], seq["items"], bags=bags))
        rates[beh] = hits / n
    # Savat COCO'da "sumka" deb aniqlansa
    hits = 0
    for _ in range(n):
        seq = synthetic.generate("basket", rng, with_item=True)
        hits += bool(run_seq(seq["frames"], seq["items"], bags=[_bag_box_at_hand(seq)]))
    rates["basket_as_bag"] = hits / n
    # O'z telefonini cho'ntakka qaytarish
    hits = 0
    for _ in range(n):
        fr, it = phone_back_to_pocket(rng)
        hits += bool(run_seq(fr, it, item_cls=67, item_name="telefon"))
    rates["own_phone"] = hits / n
    both, pairs, caught = two_people(rng, max(10, n // 2))

    pos = float(np.mean([rates[b] for b in synthetic.POSITIVE]))
    neg = float(np.mean([rates[b] for b in synthetic.NEGATIVE]))
    print(f"Rejim: {mode}, ML: {use_ml}, har holatga {n} ta ketma-ketlik, 7.5 kadr/s, buyum har 2-kadrda")
    for b in synthetic.POSITIVE:
        print(f"  SHUBHALI  {b:18s} ushlandi {rates[b]:5.0%}")
    for b in synthetic.NEGATIVE + ["basket_as_bag", "own_phone"]:
        print(f"  oddiy     {b:18s} xato signal {rates[b]:5.0%}")
    print(f"  Ikki odam bir vaqtda: {both}/{pairs} juftlikda ikkalasi ham ushlandi ({caught}/{pairs * 2} odam)")
    print(f"  JAMI: shubhali ushlandi {pos:.0%} | oddiy xato signal {neg:.1%} | "
          f"o'z telefoni {rates['own_phone']:.0%} | savat=sumka {rates['basket_as_bag']:.0%}  "
          f"({time.time() - t0:.0f} s)")
    return {"mode": mode, "ml": use_ml, "pos": pos, "neg": neg, "rates": rates,
            "two_people": {"both": both, "pairs": pairs, "caught": caught}}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    mode = sys.argv[2] if len(sys.argv) > 2 else "normal"
    ml = sys.argv[3] if len(sys.argv) > 3 else "1"
    res = main(n, mode, ml)
    if len(sys.argv) > 4:
        with open(sys.argv[4], "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
