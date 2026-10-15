"""2.0.0 kassa navbati: uzunlik, kutish vaqti, ID uzilishi, kassir, 5 qoida (4 tasi edge'da), saqlash, bulut navbati,
Telegram /navbat, panel API va aniqlik darvozasi (namuna + sekundomer + tests/queue_gate.py).

    python -m tests.test_queues

Sahna simulyatori: xaridorlar o'ngdan keladi, navbat oxiriga turadi, boshida to'laydi va chapga chiqib ketadi.
Detektor/kuzatuvchi xatolari taqlid qilinadi: tasodifiy o'tkazib yuborilgan kadrlar, bir necha soniyalik to'silish
(> 2 s bo'lsa — yangi ID, haqiqiy kuzatuvchidagidek), o'z-o'zidan ID almashishi, oyoq nuqtasi titrashi.
Eslatma: bu mantiq sinovi — do'koningizdagi aniqlik faqat Navbat sahifasidagi darvoza bilan o'lchanadi.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_queue_")   # hech qachon jonli data/ emas
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from app import db, queue_eval as qe  # noqa: E402
from app.ai import counting, queues, zones as zones_mod  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


class P:
    def __init__(self, tid: int, box):
        self.track_id = tid
        self.box = np.array(box, dtype=np.float32)
        self.score = 0.9
        self.kpts = np.zeros((17, 3), dtype=np.float32)


W, H = 1280, 720
QPTS = np.array([[0.35, 0.42], [0.65, 0.42], [0.65, 0.98], [0.35, 0.98]], dtype=np.float32)
KPTS = np.array([[0.36, 0.18], [0.64, 0.18], [0.64, 0.36], [0.36, 0.36]], dtype=np.float32)
ZQ = zones_mod.Zone(11, "1-kassa", "navbat", QPTS)
ZK = zones_mod.Zone(12, "Kassir", "kassir", KPTS)
ZS = zones_mod.ZoneSet([ZQ, ZK])
ZS_NOK = zones_mod.ZoneSet([ZQ])
CASHIER_POS = (0.5, 0.30)


def box_at(x: float, y: float) -> list:
    """Normallashgan oyoq nuqtasi → ramka (perspektiva: pastroqdagi odam kattaroq)."""
    hp = 150 + 250 * y
    fx, fy = x * W, y * H
    return [fx - hp * 0.2, fy - hp, fx + hp * 0.2, fy]


def inside(x: float, y: float) -> bool:
    return ZQ.contains((x * W, y * H), W, H)


# ----------------------------------------------------------------------------- simulyator
class Sim:
    SLOT0, SLOT_DY = 0.48, 0.09
    ENTRY, EXIT = (0.86, 0.90), (0.10, 0.40)

    def __init__(self, arrivals: list[float], service: list[float], fps: float = 4.0, seed: int = 1,
                 drop: float = 0.0, burst: float = 0.0, idsw: float = 0.0, jitter: float = 0.0,
                 cashier: list[tuple] | None = None, t0: float = 0.0, speed: float = 0.25):
        self.arr, self.srv, self.fps = arrivals, service, fps
        self.rng = np.random.default_rng(seed)
        self.drop, self.burst, self.idsw, self.jitter = drop, burst, idsw, jitter
        self.cashier = cashier if cashier is not None else [(-1e9, 1e9)]
        self.t0, self.speed = t0, speed

    def run(self, mon: queues.QueueMonitor, zs=ZS, until: float | None = None, staff_tids=()) -> dict:
        dt = 1.0 / self.fps
        n = len(self.arr)
        pos = {}                 # i -> [x, y]
        state = {}               # i -> "line" | "leave" | "gone"
        line: list[int] = []
        srv_start: dict[int, float] = {}
        tid_of: dict[int, int] = {}
        miss_since: dict[int, float] = {}
        occl_until: dict[int, float] = {}
        next_tid = [100]
        truth_in: dict[int, float] = {}
        truth_out: dict[int, float] = {}
        len_pairs = []
        t = 0.0
        end = until if until is not None else (max(self.arr) + sum(self.srv) + 60)

        def new_tid() -> int:
            next_tid[0] += 1
            return next_tid[0]
        nxt = 0
        while t <= end:
            while nxt < n and self.arr[nxt] <= t:
                pos[nxt] = list(self.ENTRY)
                state[nxt] = "line"
                line.append(nxt)
                tid_of[nxt] = new_tid()
                nxt += 1
            # harakat
            for i in list(state):
                if state[i] == "gone":
                    continue
                if state[i] == "line":
                    k = line.index(i)
                    tgt = (0.5, self.SLOT0 + k * self.SLOT_DY)
                else:
                    tgt = self.EXIT
                dx, dy = tgt[0] - pos[i][0], tgt[1] - pos[i][1]
                d = (dx * dx + dy * dy) ** 0.5
                step = self.speed * dt
                if d <= step:
                    pos[i] = [tgt[0], tgt[1]]
                    if state[i] == "leave":
                        state[i] = "gone"
                        continue
                else:
                    pos[i][0] += dx / d * step
                    pos[i][1] += dy / d * step
                if state[i] == "line" and line[0] == i and d <= step:
                    if i not in srv_start:
                        srv_start[i] = t
                    elif t - srv_start[i] >= self.srv[i]:
                        state[i] = "leave"
                        line.pop(0)
            # haqiqat
            n_in = 0
            for i, st in state.items():
                if st == "gone":
                    continue
                if inside(*pos[i]):
                    truth_in.setdefault(i, t)
                    truth_out[i] = t
                    n_in += 1
            # detektor + kuzatuvchi taqlidi
            tracked = []
            for i, st in state.items():
                if st == "gone":
                    continue
                if self.burst and self.rng.random() < self.burst * dt and occl_until.get(i, -1) < t:
                    occl_until[i] = t + self.rng.uniform(1.0, 3.5)
                seen = self.rng.random() >= self.drop and occl_until.get(i, -1) < t
                if not seen:
                    miss_since.setdefault(i, t)
                    continue
                if i in miss_since:
                    if t - miss_since.pop(i) > 2.0:      # kuzatuvchi izni 2 s dan keyin tashlaydi — yangi ID
                        tid_of[i] = new_tid()
                if self.idsw and self.rng.random() < self.idsw * dt:
                    tid_of[i] = new_tid()
                x = pos[i][0] + (self.rng.normal(0, self.jitter) if self.jitter else 0.0)
                y = pos[i][1] + (self.rng.normal(0, self.jitter) if self.jitter else 0.0)
                tracked.append(P(tid_of[i], box_at(x, y)))
            status = {}
            if any(a <= t < b for a, b in self.cashier):
                tracked.append(P(7, box_at(*CASHIER_POS)))
                status[7] = {"staff": True}
            for st_tid in staff_tids:
                status[st_tid] = {"staff": True}
            mon.update(tracked, self.t0 + t, W, H, zs, status)
            len_pairs.append((t, n_in))
            t += dt
        truth = [{"zone_id": ZQ.id, "t_in": self.t0 + truth_in[i], "t_out": self.t0 + truth_out[i],
                  "wait": truth_out[i] - truth_in[i]} for i in truth_in if truth_out[i] - truth_in[i] >= queues.MIN_WAIT]
        return {"truth": truth, "len": len_pairs}


def evaluate(mon: queues.QueueMonitor, res: dict, t0: float = 0.0) -> dict:
    mon.close()
    autos = [w for w in mon.waits]
    tims = [{"id": k, "zone_id": x["zone_id"], "t0": x["t_in"], "t1": x["t_out"]} for k, x in enumerate(res["truth"])]
    m = qe.match_timings(tims, autos, now=1e12)
    g = qe.timings_gate(m)
    trace = {round(t - t0, 3): n for t, z, n, raw in mon.trace}
    pairs = [(n, trace.get(round(t, 3))) for t, n in res["len"] if round(t, 3) in trace]
    good = sum(1 for a, b in pairs if b is not None and abs(a - b) <= 1)
    exact = sum(1 for a, b in pairs if b is not None and a == b)
    return {"n": len(res["truth"]), "autos": len(autos), "mean_err": g["mean_err"], "matched": g["matched"],
            "len_acc": good / len(pairs) if pairs else 0.0, "len_exact": exact / len(pairs) if pairs else 0.0}


def scene(seed: int, n: int = 30, gap: float = 25.0, srv: float = 30.0):
    rng = np.random.default_rng(seed)
    arrivals = list(np.cumsum(rng.exponential(gap, n)))
    service = list(rng.uniform(srv * 0.5, srv * 1.5, n))
    return arrivals, service


# ----------------------------------------------------------------------------- 1) o'lchash mantig'i
def logic_suite() -> None:
    print("\n--- Uzunlik va kutish: toza sahna, o'tib ketuvchi, xodim, kassir ---")
    mon = queues.QueueMonitor(None, "Kassa kamera", persist=False)
    # Bir kishi 60 s turadi
    for k in range(0, 241):
        t = k * 0.25
        tr = [P(1, box_at(0.5, 0.6))] if t < 60 else [P(1, box_at(0.9, 0.6))]
        mon.update(tr, t, W, H, ZS_NOK, {})
    mon.close()
    ok("Bir kishi ~60 s navbatda → bitta kutish ≈ 60 s", len(mon.waits) == 1 and abs(mon.waits[0]["wait"] - 59.75) < 0.6,
       str([w["wait"] for w in mon.waits]))
    # O'tib ketuvchi: 2 s zona ichida yuradi
    mon = queues.QueueMonitor(None, "x", persist=False)
    lens = []
    for k in range(0, 40):
        t = k * 0.25
        x = 0.2 + k * 0.05                           # ~1.2 m/s: zonani ~1.5 s da kesib o'tadi
        mon.update([P(5, box_at(x, 0.6))], t, W, H, ZS_NOK, {})
        lens.append(mon.queues[ZQ.id].length)
    mon.close()
    ok("O'tib ketgan odam navbat uzunligiga kirmaydi va kutish yozilmaydi", max(lens) == 0 and not mon.waits,
       f"uzunlik max {max(lens)}, kutishlar {len(mon.waits)}")
    # Xodim zonada turadi — sanalmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 80):
        mon.update([P(9, box_at(0.5, 0.6)), P(10, box_at(0.45, 0.75))], k * 0.25, W, H, ZS_NOK, {9: {"staff": True}})
    mon.close()
    ok("Xodim (staff) navbatga kirmaydi: uzunlik 1, kutish faqat xaridorniki",
       mon.queues[ZQ.id].length == 1 and len(mon.waits) == 1 and mon.waits[0]["track_id"] == 10,
       str([(w["track_id"], w["wait"]) for w in mon.waits]))
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 20):
        mon.update([P(1, box_at(0.5, 0.6)), P(2, box_at(0.45, 0.75)), P(3, box_at(0.55, 0.9))], k * 0.25, W, H,
                   ZS_NOK, {})
    ok("Uch kishi turibdi → uzunlik 3 (MIN_STAY dan keyin)", mon.queues[ZQ.id].length == 3)
    # Kassir zonasidagi odam navbat a'zosi emas; kassir holati
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 20):
        mon.update([P(7, box_at(*CASHIER_POS)), P(1, box_at(0.5, 0.6))], k * 0.25, W, H, ZS, {})
    st = mon.snapshot[0]
    ok("Kassir joyidagi odam — kassir bor, navbatda 1", st["cashier"] is True and st["length"] == 1, str(st))
    for k in range(20, 20 + 4 * 12):
        mon.update([P(1, box_at(0.5, 0.6))], k * 0.25, W, H, ZS, {})
    st = mon.snapshot[0]
    ok("Kassir 12 s ko'rinmadi → 'kassir yo'q' (8 s chidamlilikdan keyin)",
       st["cashier"] is False and st["state"] == "kassir_yoq" and st["cashier_absent"] >= 11, str(st))
    mon2 = queues.QueueMonitor(None, "x", persist=False)
    mon2.update([P(1, box_at(0.5, 0.6))], 0.0, W, H, ZS_NOK, {})
    ok("Kassir joyi chizilmagan — kassir holati noma'lum (None)", mon2.snapshot[0]["cashier"] is None)
    # To'silish: 1.2 s ko'rinmay qoladi — uzunlik o'zgarmaydi, kutish bo'linmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    lens = []
    for k in range(0, 160):
        t = k * 0.25
        tr = [P(1, box_at(0.5, 0.6))]
        if not (12 <= t < 13.2 or 25 <= t < 26):
            tr.append(P(2, box_at(0.5, 0.7)))
        mon.update(tr, t, W, H, ZS_NOK, {})
        if t > 3:
            lens.append(mon.queues[ZQ.id].length)
    mon.close()
    ok("Qisqa to'silish: uzunlik doim 2, kutishlar bo'linmaydi (2 ta)", min(lens) == 2 and len(mon.waits) == 2,
       f"min {min(lens)}, kutishlar {len(mon.waits)}")
    # ID almashishi: to'silgandan keyin yangi ID zona ichida paydo bo'ladi — kutish meros
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 240):
        t = k * 0.25
        tr = [P(1, box_at(0.5, 0.55))]
        if t < 20:
            tr.append(P(2, box_at(0.5, 0.7)))
        elif t >= 23:
            tr.append(P(3, box_at(0.5, 0.62)))      # 3 s yo'q edi, oldinga siljigan — yangi ID
        if t >= 50:
            tr = [x for x in tr if x.track_id != 3] + [P(3, box_at(0.9, 0.62))]
        mon.update(tr, t, W, H, ZS_NOK, {})
    mon.close()
    w2 = [w for w in mon.waits if w["track_id"] == 3]
    ok("Yangi ID zona ichida paydo bo'ldi (to'silish 3 s) → eski odamning davomi, kutish ≈ 50 s",
       len(mon.waits) == 2 and w2 and abs(w2[0]["wait"] - 49.75) < 0.6 and w2[0]["ids"] == 2,
       str([(w["track_id"], w["wait"], w["ids"]) for w in mon.waits]))
    # Tashqaridan kirib kelgan odam yo'qolganning o'rniga ulanmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 200):
        t = k * 0.25
        tr = []
        if t < 20:
            tr.append(P(1, box_at(0.5, 0.9)))        # 20 s turib, ko'rinmay qoladi (kadrdan yashirin ketdi)
        if 10 <= t < 21:
            tr.append(P(4, box_at(0.8, 0.9)))        # 10 s dan beri tashqarida turgan odam
        elif t >= 21:
            tr.append(P(4, box_at(0.5, 0.9)))        # 21 s da navbatga kirdi (yo'qolganning joyiga)
        mon.update(tr, t, W, H, ZS_NOK, {})
    mon.close()
    ws = sorted((w["track_id"], round(w["wait"])) for w in mon.waits)
    ok("Tashqaridan kirgan odam — yangi xaridor (yo'qolganning kutishi meros emas)",
       ws == [(1, 20), (4, 29)], str(ws))
    # Zona o'zgardi — holat tashlanadi
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 40):
        mon.update([P(1, box_at(0.5, 0.6))], k * 0.25, W, H, ZS_NOK, {})
    z2 = zones_mod.ZoneSet([zones_mod.Zone(11, "1-kassa", "navbat", QPTS * np.array([1.0, 0.99], dtype=np.float32))])
    mon.update([P(1, box_at(0.5, 0.6))], 10.0, W, H, z2, {})
    ok("Zona tahrirlansa — a'zolar qaytadan (eski kutish uzunlikka o'tmaydi)", mon.queues[ZQ.id].members[1].t_in == 10.0)
    mon.update([], 10.5, W, H, zones_mod.ZoneSet([]), {})
    ok("Navbat zonasi o'chirilsa — monitor bo'shaydi", not mon.queues and mon.snapshot == [])
    # Juftlash: ikki kassa, ikki kassir zonasi — eng yaqini
    za = zones_mod.Zone(21, "A", "navbat", np.array([[0.05, 0.5], [0.3, 0.5], [0.3, 0.9], [0.05, 0.9]], dtype=np.float32))
    zb = zones_mod.Zone(22, "B", "navbat", np.array([[0.7, 0.5], [0.95, 0.5], [0.95, 0.9], [0.7, 0.9]], dtype=np.float32))
    ka = zones_mod.Zone(23, "KA", "kassir", np.array([[0.05, 0.2], [0.3, 0.2], [0.3, 0.35], [0.05, 0.35]], dtype=np.float32))
    kb = zones_mod.Zone(24, "KB", "kassir", np.array([[0.7, 0.2], [0.95, 0.2], [0.95, 0.35], [0.7, 0.35]], dtype=np.float32))
    prs = {q.id: (k.id if k else None) for q, k in queues.pairs(zones_mod.ZoneSet([za, kb, zb, ka]))}
    ok("Ikki kassa: har navbat eng yaqin kassir joyi bilan juftlanadi", prs == {21: 23, 22: 24}, str(prs))
    # Analyzer: kassir joyi xodim belgisini BERMAYDI (kassir yo'qligida peshtaxta ortiga o'tgan begona odam kuzatilsin);
    # xodim zonasi — beradi
    from app.ai.analyzer import Analyzer

    an = Analyzer("x")
    st = {}
    for k in range(0, 14):
        _, st = an.update([P(7, box_at(*CASHIER_POS))], [], k * 0.25, W, H, searched=[], zones=ZS)
    ok("Analyzer: kassir joyida 3 s turgan odam xodim EMAS (o'g'irlik signallari o'chmaydi)",
       not (st.get(7) or {}).get("staff"), str(st.get(7)))
    zx = zones_mod.ZoneSet([ZQ, ZK, zones_mod.Zone(13, "Xodim", "xodim", KPTS)])
    an = Analyzer("x")
    for k in range(0, 14):
        _, st = an.update([P(7, box_at(*CASHIER_POS))], [], k * 0.25, W, H, searched=[], zones=zx)
    ok("Analyzer: o'sha joyga 'Xodimlar joyi' ham chizilsa — xodim", (st.get(7) or {}).get("staff") is True, str(st.get(7)))
    # Mustaqil tekshiruv topgan holatlar:
    # (a) chiqib ketayotgan xaridor kuzatuvdan yo'qoldi — zona ichida paydo bo'lgan yangi ID unga ulanmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 400):
        t = k * 0.25
        tr = []
        if t < 60:
            tr.append(P(1, box_at(0.5, 0.6)))
        elif t < 61:
            tr.append(P(1, box_at(0.9, 0.6)))        # chiqdi (zonadan tashqarida ko'rindi), keyin yo'qoldi
        if 61.5 <= t < 82:
            tr.append(P(2, box_at(0.52, 0.62)))      # A turgan joyda birdan paydo bo'lgan yangi ID — boshqa odam
        mon.update(tr, t, W, H, ZS_NOK, {})
    mon.close()
    ws = sorted((w["track_id"], round(w["wait"])) for w in mon.waits)
    ok("Chiqib ketayotganda yo'qolgan xaridorga yangi ID ulanmaydi (A ≈ 60 s, B ≈ 20 s alohida)", ws == [(1, 60), (2, 20)],
       str(ws))
    # (b) bir kadr kassir zonasiga tushgan oyoq nuqtasi xaridorning kutishini o'chirmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    for k in range(0, 300):
        t = k * 0.25
        y = 0.30 if 40 <= t < 40.5 else 0.45                  # 0.5 s oyoq nuqtasi peshtaxta (kassir zonasi) ustida
        tr = [P(1, box_at(0.5, y))] if t < 70 else [P(1, box_at(0.9, 0.6))]
        mon.update(tr, t, W, H, ZS, {})
    mon.close()
    ok("Bir lahza kassir zonasiga tushish — kutish butun (≈ 70 s), bo'linmaydi",
       len(mon.waits) == 1 and abs(mon.waits[0]["wait"] - 69.75) < 0.6, str([round(w["wait"], 1) for w in mon.waits]))
    # (c) zonada doim turgan "odam" (manekin/plakat) — 20 daqiqadan keyin hisobga olinmaydi, kutishi yozilmaydi
    mon = queues.QueueMonitor(None, "x", persist=False)
    t = 0.0
    while t < queues.STATIC_SEC + 120:
        mon.update([P(1, box_at(0.5, 0.6))], t, W, H, ZS_NOK, {})
        t += 2.0
    st = mon.snapshot[0]
    mon.close()
    ok("Doimiy 'odam' 20 daqiqadan keyin: uzunlik 0, eng uzoq kutish 0, kutish yozilmaydi",
       st["length"] == 0 and st["max_wait"] == 0 and not mon.waits, str((st["length"], st["max_wait"], len(mon.waits))))


def accuracy_suite() -> None:
    print("\n--- Sintetik sahna: aniqlik (darvoza mezoni: soni ±1 ≥ 90%, kutish xatosi ≤ 20%) ---")
    arr, srv = scene(3, n=30)
    mon = queues.QueueMonitor(None, "x", persist=False)
    res = Sim(arr, srv, seed=3).run(mon)
    e = evaluate(mon, res)
    ok("Toza sahna (xatosiz detektor): kutish xatosi < 3%, soni ±1 ≥ 99%",
       e["mean_err"] is not None and e["mean_err"] < 0.03 and e["len_acc"] >= 0.99 and e["matched"] == e["n"],
       f"{e['n']} kishi, xato {e['mean_err']}, ±1 {e['len_acc']:.3f}, aniq {e['len_exact']:.3f}")
    worst = {"err": 0.0, "acc": 1.0}
    for seed in (5, 6, 7):
        arr, srv = scene(seed, n=30, gap=20)
        mon = queues.QueueMonitor(None, "x", persist=False)
        res = Sim(arr, srv, seed=seed, drop=0.10, burst=0.03, idsw=0.01, jitter=0.004).run(mon)
        e = evaluate(mon, res)
        worst["err"] = max(worst["err"], e["mean_err"] or 1.0)
        worst["acc"] = min(worst["acc"], e["len_acc"])
        print(f"       o'rtacha shovqin, seed {seed}: {e['n']} kishi, avto {e['autos']}, juftlangan {e['matched']}, "
              f"kutish xatosi {e['mean_err']}, soni ±1 {e['len_acc']:.3f} (aniq {e['len_exact']:.3f})")
    ok("O'rtacha shovqin (10% kadr yo'q, to'silishlar, ID almashishi): kutish xatosi ≤ 10%, soni ±1 ≥ 95%",
       worst["err"] <= 0.10 and worst["acc"] >= 0.95, str(worst))
    worst = {"err": 0.0, "acc": 1.0}
    for seed in (8, 9):
        arr, srv = scene(seed, n=30, gap=18)
        mon = queues.QueueMonitor(None, "x", persist=False)
        res = Sim(arr, srv, seed=seed, drop=0.20, burst=0.06, idsw=0.02, jitter=0.006).run(mon)
        e = evaluate(mon, res)
        worst["err"] = max(worst["err"], e["mean_err"] or 1.0)
        worst["acc"] = min(worst["acc"], e["len_acc"])
        print(f"       og'ir shovqin, seed {seed}: {e['n']} kishi, avto {e['autos']}, juftlangan {e['matched']}, "
              f"kutish xatosi {e['mean_err']}, soni ±1 {e['len_acc']:.3f}")
    ok("Og'ir shovqin (20% kadr yo'q, ko'p to'silish): baribir darvoza ichida (xato ≤ 20%, ±1 ≥ 90%)",
       worst["err"] <= 0.20 and worst["acc"] >= 0.90, str(worst))
    # Little qonuni: sintetik sahnada L/λ ≈ W
    arr, srv = scene(11, n=40, gap=20)
    mon = queues.QueueMonitor(None, "x", persist=False)
    res = Sim(arr, srv, seed=11, drop=0.05).run(mon)
    mon.close()
    tr = mon.trace
    dts = np.diff([x[0] for x in tr])
    t_obs = float(np.sum(dts))
    len_sum = float(np.sum([tr[i][2] * dts[i] for i in range(len(dts))]))
    li = qe.little(t_obs, len_sum, len(mon.waits), sum(w["wait"] for w in mon.waits), len(mon.waits))
    ok("Little qonuni: L/λ o'lchangan o'rtacha kutishdan ≤ 25% farq qiladi", li is not None and li["ok"], str(li))


# ----------------------------------------------------------------------------- 2) saqlash va qoidalar
class Notifier:
    def __init__(self):
        self.texts = []

    def send_queue_alert(self, text: str) -> int:
        self.texts.append(text)
        return 1


def _source(name: str = "Kassa kamera") -> int:
    return db.execute("INSERT INTO sources(name, kind, uri, enabled, created_at) VALUES(?, 'rtsp', 'rtsp://x', 0, ?)",
                      (name, time.time()))


def _zone(sid: int, name: str, kind: str, pts) -> int:
    zid = db.execute("INSERT INTO zones(source_id, name, kind, points, sens, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
                     (sid, name, kind, json.dumps(np.asarray(pts).tolist()), "", 1, time.time()))
    zones_mod.invalidate(sid)
    return zid


def stand(mon, people: list, t0: float, sec: float, zs, fps: float = 4.0, cashier: bool = True) -> float:
    t = t0
    while t < t0 + sec:
        tr = [P(tid, box_at(x, y)) for tid, x, y in people]
        st = {}
        if cashier:
            tr.append(P(7, box_at(*CASHIER_POS)))
            st[7] = {"staff": True}
        mon.update(tr, t, W, H, zs, st)
        t += 1 / fps
    return t


def rules_suite() -> None:
    print("\n--- Qoidalar 1-2, saqlash, daqiqalik yig'indi ---")
    queues.ASYNC_ALERTS = False
    nt = Notifier()
    queues.set_notifier(nt)
    db.set_settings({"queue_max_len": "4", "queue_long_sec": "30", "queue_max_wait_min": "3", "queue_cashier_sec": "45",
                     "queue_cooldown_min": "10", "queue_tg": "1"})
    queues.reload_cfg()
    sid = _source()
    zq = _zone(sid, "1-kassa <b>A</b>", "navbat", QPTS)
    _zone(sid, "Kassir", "kassir", KPTS)
    zs = zones_mod.load(sid)
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    five = [(1, 0.5, 0.5), (2, 0.5, 0.6), (3, 0.5, 0.7), (4, 0.5, 0.8), (5, 0.5, 0.9)]
    t0 = time.time() - 3600
    t = stand(mon, five, t0, 25, zs)
    ok("Navbatda 5 kishi, 25 s — hali signal yo'q (30 s kerak)", db.scalar("SELECT COUNT(*) FROM queue_alerts") == 0)
    t = stand(mon, five, t, 10, zs)
    a = db.rows("SELECT * FROM queue_alerts")
    ok("1-qoida: 5 kishi ≥ 30 s → 'Qo'shimcha kassa oching' (bitta)", len(a) == 1 and a[0]["rule"] == "R1"
       and "5 kishi" in a[0]["text"], str([(x["rule"], x["title"]) for x in a]))
    ok("Zona nomi Telegram/HTML uchun ekranlangan", "&lt;b&gt;A&lt;/b&gt;" in a[0]["text"] and "<b>A</b>" not in a[0]["text"])
    ok("Telegram'ga yuborildi (navbat signali yoqilgan obunachilarga)", len(nt.texts) == 1 and "Qo'shimcha kassa" in nt.texts[0]
       and a[0]["tg_sent"] == 1)
    t = stand(mon, five, t, 5 * 60, zs)
    ok("Sovish vaqti (10 daq): 5 daqiqa davomida takror yo'q", db.scalar("SELECT COUNT(*) FROM queue_alerts") == 1)
    t = stand(mon, five, t, 5 * 60 + 5, zs)
    a = db.rows("SELECT * FROM queue_alerts ORDER BY id")
    ok("10 daqiqadan keyin hali ham uzun → 'Navbat hali ham uzun'", len(a) == 2 and a[1]["title"] == "Navbat hali ham uzun",
       str([x["title"] for x in a]))
    # Uzoq kutish (1 kishi, 3 daqiqadan ko'p)
    db.execute("DELETE FROM queue_alerts")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    t = stand(mon, [(31, 0.5, 0.5)], t + 60, 175, zs)
    ok("1 kishi 175 s — signal yo'q", db.scalar("SELECT COUNT(*) FROM queue_alerts") == 0)
    t = stand(mon, [(31, 0.5, 0.5)], t, 10, zs)
    a = db.rows("SELECT * FROM queue_alerts")
    ok("1-qoida (kutish): kimdir ≥ 3 daqiqa kutmoqda → signal", len(a) == 1 and a[0]["title"] == "Navbatda uzoq kutishyapti"
       and json.loads(a[0]["data"])["reason"] == "wait", str([x["title"] for x in a]))
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)        # dastur qayta ishga tushdi
    t = stand(mon, [(31, 0.5, 0.5)], t, 30, zs)
    ok("Qayta ishga tushgandan keyin sovish vaqti saqlanadi (signal darhol takrorlanmaydi)",
       db.scalar("SELECT COUNT(*) FROM queue_alerts") == 1)
    # Doimiy "odam" — 1-qoida cheksiz takrorlanmaydi
    db.execute("DELETE FROM queue_alerts")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    t0s = t + 3600
    tt = t0s
    while tt < t0s + queues.STATIC_SEC + 3600:
        tr = [P(77, box_at(0.5, 0.5)), P(7, box_at(*CASHIER_POS))]
        mon.update(tr, tt, W, H, zs, {7: {"staff": True}})
        tt += 2.0
    n77 = db.scalar("SELECT COUNT(*) FROM queue_alerts")
    ok("Zonada 80 daqiqa turgan 'odam' — signal 20 daqiqagacha (≤ 2 ta), keyin to'xtaydi", n77 <= 2, f"{n77} ta signal")
    t = tt
    # 2-qoida: kassir yo'q
    db.execute("DELETE FROM queue_alerts")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    three = [(41, 0.5, 0.5), (42, 0.5, 0.6), (43, 0.5, 0.7)]
    t = stand(mon, three, t + 60, 40, zs, cashier=False)
    ok("Navbat 3, kassir 40 s yo'q — hali signal yo'q (45 s)", db.scalar("SELECT COUNT(*) FROM queue_alerts WHERE rule='R2'") == 0)
    t = stand(mon, three, t, 8, zs, cashier=False)
    a = db.rows("SELECT * FROM queue_alerts WHERE rule='R2'")
    ok("2-qoida: navbat bor, kassir joyi ≥ 45 s bo'sh → 'Kassani tekshiring'", len(a) == 1 and "bo'sh" in a[0]["text"], str(a))
    db.execute("DELETE FROM queue_alerts")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    t = stand(mon, three, t + 60, 120, zs, cashier=True)
    ok("Kassir bor — 2-qoida ishlamaydi", db.scalar("SELECT COUNT(*) FROM queue_alerts WHERE rule='R2'") == 0)
    zs_nok = zones_mod.ZoneSet([z for z in zs.zones if z.kind != "kassir"])
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    stand(mon, three, t + 60, 120, zs_nok, cashier=False)
    ok("Kassir joyi chizilmagan — 2-qoida ishlamaydi (kassir holati noma'lum)",
       db.scalar("SELECT COUNT(*) FROM queue_alerts WHERE rule='R2'") == 0)
    db.set_settings({"queue_tg": "0"})
    queues.reload_cfg()
    n0 = len(nt.texts)
    db.execute("DELETE FROM queue_alerts")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    t = stand(mon, five, time.time() - 1800, 40, zs)
    ok("Telegram o'chirilgan (queue_tg=0) — signal faqat panelda", len(nt.texts) == n0
       and db.scalar("SELECT COUNT(*) FROM queue_alerts") == 1)
    db.set_settings({"queue_tg": "1"})
    queues.reload_cfg()
    # Daqiqalik yig'indi
    queues._acc.clear()
    db.execute("DELETE FROM queue_minute")
    db.execute("DELETE FROM queue_waits")
    mon = queues.QueueMonitor(sid, "Kassa kamera", persist=True)
    base = (int(time.time() // 60) - 10) * 60
    t = base
    # 0-60 s: 2 kishi turadi; 60-90: bittasi chiqadi
    t = stand(mon, [(51, 0.5, 0.5), (52, 0.5, 0.6)], base, 60, zs)
    t = stand(mon, [(51, 0.5, 0.5), (52, 0.9, 0.6)], t, 30, zs)
    rows = queues.flush_minutes(base + 60)
    r0 = next((r for r in rows if r["minute"] == base), None)
    ok("Daqiqalik: kuzatuv ≈ 60 s, o'rtacha uzunlik ≈ 2, kassir 100%", r0 and abs(r0["t_obs"] - 59.75) < 0.5
       and 1.8 <= r0["len_sum"] / r0["t_obs"] <= 2.0 and r0["cashier_sec"] >= 59 and r0["cashier_known"] == 1, str(r0))
    ok("Navbatdan chiqqan — xizmat 1, kutish ≈ 60 s (oxirgi marta navbatda ko'ringan daqiqasida)", r0["served"] == 1
       and abs(r0["wait_sum"] - 59.75) < 0.6 and sum(r["served"] for r in rows) == 1
       and db.scalar("SELECT COUNT(*) FROM queue_waits") == 1, str(r0))
    # Kechikkan qism mavjud daqiqaga qo'shiladi
    with queues._acc_lock:
        a = queues._acc_for(base, mon.queues[zq], sid, "Kassa kamera")
        a.t_obs += 5
        a.served += 1
    rows = queues.flush_minutes(base + 120)
    r0b = db.row("SELECT * FROM queue_minute WHERE minute=? AND zone_id=?", (base, zq))
    ok("Kechikkan yig'indi o'sha daqiqa qatoriga qo'shiladi (almashtirilmaydi)",
       abs(r0b["t_obs"] - (r0["t_obs"] + 5)) < 0.01 and r0b["served"] == r0["served"] + 1, str(dict(r0b)))
    ok("Persist=False (video tahlili) bazaga yozmaydi", (lambda m: (stand(m, five, t + 100, 40, zs),
                                                                   db.scalar("SELECT COUNT(*) FROM queue_waits"))[1])(
        queues.QueueMonitor(sid, "x", persist=False)) == 1)
    # Jonli holat (registr)
    st = queues.live_status()
    ok("Jonli holat: kassa nomi, uzunlik, kassir, holat", any(x["zone_id"] == zq and x["cashier"] is True for x in st),
       str(st[:1]))
    db.execute("UPDATE zones SET enabled=0 WHERE id=?", (zq,))
    ok("O'chirilgan zona jonli holatda ko'rinmaydi", not any(x["zone_id"] == zq for x in queues.live_status()))
    db.execute("UPDATE zones SET enabled=1 WHERE id=?", (zq,))
    # LineCounter orqali (worker.py ulanishi) — navbat ham o'lchanadi, xato sanashni buzmaydi
    lc = counting.LineCounter(sid, "Kassa kamera", persist=False)
    for k in range(20):
        lc.update([P(1, box_at(0.5, 0.6))], k * 0.25, W, H, zs, {})
    ok("LineCounter.update navbatni ham o'lchaydi (worker.py ga yangi ulanish kerak emas)",
       lc.queue is not None and lc.queue.queues[zq].length == 1)
    orig = queues.QueueMonitor.update
    queues.QueueMonitor.update = lambda *a, **k: 1 / 0
    try:
        line = zones_mod.Zone(99, "Eshik", "chiziq", np.array([[0.2, 0.5], [0.8, 0.5]], dtype=np.float32))
        zl = zones_mod.ZoneSet(zs.zones + [line])
        lc = counting.LineCounter(None, "x", persist=False)
        evs = []
        for k in range(16):
            evs += lc.update([P(1, [600, 200 + k * 30 - 260, 680, 200 + k * 30])], k * 0.125, W, H, zl, {}, work_hours="")
        ok("Navbatdagi xato sanashni buzmaydi (o'tish baribir sanaldi)", [e.dir for e in evs] == ["in"], str(evs))
    finally:
        queues.QueueMonitor.update = orig


def store_rules_suite() -> None:
    print("\n--- Qoidalar 3-4 (do'kon bo'yicha) va jadval tahlili ---")
    db.execute("DELETE FROM queue_minute")
    db.execute("DELETE FROM queue_alerts")
    db.set_settings({"queue_r3_at": "0", "queue_tg_low": "0"})
    queues.reload_cfg()
    now = time.time()
    m1 = int(now // 60) * 60

    def minutes(zone: int, name: str, busy: float, mx: int, cash: float, n: int = 30):
        with db.tx() as c:
            for k in range(1, n + 1):
                c.execute("INSERT OR REPLACE INTO queue_minute(minute, zone_id, zone_name, source_id, source_name, t_obs, "
                          "len_sum, len_max, long_sec, busy_sec, cashier_sec, cashier_known, served, wait_sum, wait_n, "
                          "wait_max) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (m1 - k * 60, zone, name, 1, "K", 60, busy * 60 * 0.6, mx, 0, busy * 60, cash * 60, 1, 1, 30, 1, 40))
    minutes(301, "1-kassa", 0.30, 1, 1.0)
    minutes(302, "2-kassa", 0.15, 2, 0.95)
    nt = Notifier()
    queues.set_notifier(nt)
    a = queues.check_r3(now)
    ok("3-qoida: 2 kassa ochiq, band 45% < 50%, navbat ≤ 2 → 'Qo'shimcha kassir kerak emas'", a is not None
       and a["rule"] == "R3" and "2 ta kassa ochiq" in a["text"], str(a and a["text"]))
    ok("3-qoida Telegram'ga sukut bo'yicha yuborilmaydi (faqat panelda)", nt.texts == [])
    ok("3-qoida 2 soatda bir marta", queues.check_r3(now + 600) is None)
    db.set_settings({"queue_r3_at": "0"})
    minutes(302, "2-kassa", 0.30, 3, 0.95)
    ok("Navbat 3 kishiga yetgan — 3-qoida ishlamaydi", queues.check_r3(now) is None)
    minutes(302, "2-kassa", 0.30, 2, 0.95)
    minutes(301, "1-kassa", 0.30, 1, 0.5)
    ok("Faqat bitta kassada kassir doim bor — 3-qoida ishlamaydi", queues.check_r3(now) is None)
    minutes(301, "1-kassa", 0.30, 1, 1.0)
    minutes(302, "2-kassa", 0.30, 2, 0.95)
    ok("Band vaqt 60% > 50% — 3-qoida ishlamaydi", queues.check_r3(now) is None)
    # 4-qoida: jadval
    rows = []
    base_day = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    for d in range(1, 29):
        day = base_day - d * 86400
        wd = time.localtime(day + 43200).tm_wday
        for hour in range(9, 22):
            for mi in range(60):
                m = int(day + hour * 3600 + mi * 60)
                busy = (wd == 4 and hour in (18, 19) and mi < 30) or (hour == 13 and mi < 10 and d != 3)
                spike = hour == 11 and mi == 7                     # bir lahzalik 5 kishi (har kuni) — jadvalga kirmaydi
                rows.append({"minute": m, "t_obs": 60, "len_max": 5 if busy or spike else 1, "long_sec": 60 if busy else 0,
                             "wait_max": 100 if busy else 30})
    sch = qe.schedule(rows, 4, 180, now)
    items = {(it["label"], it["hours"]) for it in sch["items"]}
    ok("Jadval: juma 18:00–20:00 takrorlanadi", ("Juma", "18:00–20:00") in items, str(items))
    daily = [it for it in sch["items"] if it["hours"] == "13:00–14:00"]
    ok("Jadval: har kuni 13:00–14:00 (bitta hafta kuni 4 haftadan 1 tasida yo'q — baribir takror)",
       daily and daily[0]["label"] == "Har kuni", str([(d["label"], d["days_busy"], d["days_obs"]) for d in daily]))
    ok("Bir lahzalik navbat (har kuni 11:07 da 1 daqiqa) jadvalga kirmaydi", all(it["hours"] in ("18:00–20:00", "13:00–14:00") for it in sch["items"]),
       str(items))
    short = [r for r in rows if r["minute"] > now - 10 * 86400]
    ok("Tarix < 14 kun — tavsiya yo'q (enough=False)", qe.schedule(short, 4, 180, now)["enough"] is False)
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO queue_minute(minute, zone_id, zone_name, t_obs, len_max, long_sec, wait_max) "
                      "VALUES(?,?,?,?,?,?,?)", [(r["minute"], 401, "1-kassa", r["t_obs"], r["len_max"], r["long_sec"],
                                                 r["wait_max"]) for r in rows])
    db.set_settings({"queue_r4_week": "", "queue_tg_schedule": "1"})
    queues.reload_cfg()
    lt = time.localtime(now)
    monday9 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - lt.tm_wday, 9, 30, 0, 0, 0, -1))
    ok("4-qoida dushanba 09:00 dan oldin ishlamaydi", queues.check_r4(monday9 - 3600) is None)
    a = queues.check_r4(monday9)
    ok("4-qoida dushanba ertalab: 'Kassir jadvali' tavsiyasi (Telegram'ga ham)", a is not None and "Juma 18:00–20:00" in a["text"]
       and nt.texts and "Kassir jadvali" in nt.texts[-1], str(a and a["text"][:200]))
    ok("4-qoida haftada bir marta", queues.check_r4(monday9 + 7200) is None)
    db.set_settings({"queue_r4_week": ""})
    a = queues.check_r4(monday9 + 86400)
    ok("Dushanba kompyuter o'chiq bo'lgan — seshanba kuni yuboriladi", a is not None and "Juma" in a["text"])


def sync_suite() -> None:
    print("\n--- Bulut navbati: imkoniyat e'lon qilinmaguncha yozilmaydi ---")
    from app import sync

    old = (sync.CLOUD_URL, sync.EDGE_TOKEN)
    sync.CLOUD_URL, sync.EDGE_TOKEN = "http://127.0.0.1:9", "t" * 32
    try:
        db.execute("DELETE FROM outbox")
        db.set_settings({"cloud_features": ""})
        queues.raise_alert({"t": time.time(), "rule": "R1", "level": "warning", "source_id": 1, "source_name": "K",
                            "zone_id": 1, "zone_name": "1-kassa", "title": "Qo'shimcha kassa oching", "text": "x", "data": {}})
        sync.enqueue_queue_minutes([{"minute": 60, "zone_id": 1}])
        ok("Eski bulut (features yo'q): navbat qatorlari outbox'ga yozilmaydi (hodisalar navbatini to'smasin)",
           db.scalar("SELECT COUNT(*) FROM outbox") == 0)
        db.set_settings({"cloud_features": "queues"})
        queues.raise_alert({"t": time.time(), "rule": "R2", "level": "danger", "source_id": 1, "source_name": "K",
                            "zone_id": 1, "zone_name": "1-kassa", "title": "Kassani tekshiring", "text": "x", "data": {}})
        sync.enqueue_queue_minutes([{"minute": 60, "zone_id": 1, "t_obs": 60}])
        sync.enqueue_queue_minutes([{"minute": 60, "zone_id": 1, "t_obs": 61}])
        kinds = [r["kind"] for r in db.rows("SELECT kind FROM outbox ORDER BY id")]
        ok("Bulut 'queues' ni e'lon qilgan: qalert va queue (daqiqa qayta yuborilsa — ustiga yoziladi)",
           kinds == ["qalert", "queue"], str(kinds))
        r = json.loads(db.scalar("SELECT payload FROM outbox WHERE kind='queue'"))
        ok("Daqiqa qatori yangilangan qiymat bilan", r["t_obs"] == 61)
        w = sync.SyncWorker.__new__(sync.SyncWorker)
        calls = []
        w._post = lambda path, body, **k: (calls.append(path), (200, {"ok": True}))[1]
        w._done = lambda ids: None
        w._fail = lambda ids, err, terminal=False: calls.append(("fail", err))
        w.sent, w.batch_size = {}, 10
        rows = db.rows("SELECT * FROM outbox ORDER BY id")
        w._send_batch_inner("qalert", [rows[0]["id"]], [rows[0]])
        w._send_batch_inner("queue", [rows[1]["id"]], [rows[1]])
        ok("Yo'llar: qalert → /edge/queue_alerts, queue → /edge/queues", calls == ["/api/v1/edge/queue_alerts",
                                                                                "/api/v1/edge/queues"], str(calls))
    finally:
        sync.CLOUD_URL, sync.EDGE_TOKEN = old
        db.execute("DELETE FROM outbox")


def telegram_suite() -> None:
    print("\n--- Telegram: /navbat va navbat signallarini o'chirish ---")
    from app import telegram_bot

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    sent = []
    bot.token = "x"
    bot._enqueue = lambda prio, kind, a, b=None: sent.append((kind, a, b))
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active) VALUES(1001,'A','a',?,1)",
               (time.time(),))
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active) VALUES(1002,'B','b',?,1)",
               (time.time(),))
    db.execute("UPDATE subscribers SET queue_alerts=0 WHERE chat_id=1002")
    n = bot.send_queue_alert("🧾 test")
    ok("Navbat signali faqat o'chirmaganlarga", n == 1 and [x[1] for x in sent] == [1001], str(sent))
    m = bot._queue_markup(1001)
    ok("/navbat tugmasi: 'o'chirish' (yoqilgan chat)", m["inline_keyboard"][0][0]["callback_data"] == "qa:off")
    ok("/navbat tugmasi: 'yoqish' (o'chirgan chat)", bot._queue_markup(1002)["inline_keyboard"][0][0]["callback_data"] == "qa:on")
    txt = queues.status_text()
    ok("/navbat matni: kassalar holati", "Navbat" in txt and ("kishi" in txt or "ishlamayapti" in txt), txt[:120])


def web_suite() -> None:
    print("\n--- Panel: Navbat sahifasi, sozlamalar, darvoza (namuna + sekundomer), CSV ---")
    from fastapi.testclient import TestClient

    from app import config, web

    c = TestClient(web.app)
    r = c.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"}, follow_redirects=False)
    ok("Kirish", r.status_code == 303)
    r = c.get("/navbat")
    ok("Navbat sahifasi ochiladi, menyuda bor", r.status_code == 200 and 'href="/navbat"' in r.text and "Kassa navbati" in r.text)
    sid = _source("Kassa 2")
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "navbat", "points": QPTS.tolist(), "name": "3-kassa"})
    ok("Navbat zonasi saqlanadi", r.status_code == 200, r.text[:100])
    zid = r.json()["zone"]["id"]
    r = c.post(f"/api/sources/{sid}/zones", json={"kind": "kassir", "points": KPTS.tolist(), "name": "3-kassir"})
    ok("Kassir zonasi saqlanadi", r.status_code == 200)
    r = c.get("/api/queues/status").json()
    z = next((x for x in r["zones"] if x["id"] == zid), None)
    ok("Status: zona va kassir juftligi", z and z["cashier_zone"] == "3-kassir", str(z))
    ok("Status: bugungi ko'rsatkichlar, soatlar, qoidalar", "today" in r and len(r["hours_len"]) == 24 and "R1" in r["rules"])
    bad = c.post("/api/queues/settings", json={"queue_max_len": "1"}).status_code
    bad2 = c.post("/api/queues/settings", json={"queue_max_wait_min": "abc"}).status_code
    ok("Sozlama: chegaradan tashqari / son emas — 400", bad == 400 and bad2 == 400, f"{bad} {bad2}")
    r = c.post("/api/queues/settings", json={"queue_max_len": "5", "queue_tg_low": True})
    ok("Sozlama saqlandi va qoidalarga darhol ta'sir qiladi", r.status_code == 200 and queues.cfg()["max_len"] == 5
       and queues.cfg()["tg_low"] is True, r.text[:120])
    c.post("/api/queues/settings", json={"queue_max_len": "4", "queue_tg_low": False})
    r = c.post("/api/queues/samples", json={"zone_id": zid})
    ok("Namuna: kamera ishlamasa — 409 (tushunarli xabar)", r.status_code == 409 and "Kamera" in r.json()["detail"])
    # Kamera ishlayotgandek: manager worker'ga kadr va monitor holati
    from app.ai import manager as mgr_mod

    class FakeW:
        def __init__(self):
            import threading
            self.frame_lock = threading.Lock()
            self.latest_frame = np.full((H, W, 3), 120, dtype=np.uint8)

        def is_alive(self):
            return True
    mgr = mgr_mod.get_manager()
    mgr.workers[sid] = FakeW()
    zs = zones_mod.load(sid)
    mon = queues.QueueMonitor(sid, "Kassa 2", persist=True)
    stand(mon, [(1, 0.5, 0.5), (2, 0.5, 0.6), (3, 0.5, 0.7)], time.time() - 5, 4.5, zs)
    try:
        r = c.post("/api/queues/samples", json={"zone_id": zid})
        ok("Namuna olindi: kadr (zona atrofi) + tizim soni yashirin", r.status_code == 200 and "auto" not in r.json(), r.text[:120])
        s1 = r.json()["id"]
        img = c.get(f"/api/queues/samples/{s1}.jpg")
        ok("Namuna kadri JPEG", img.status_code == 200 and img.content[:2] == b"\xff\xd8")
        r = c.post("/api/queues/samples", json={"zone_id": zid})
        ok("Ketma-ket namuna (10 s ichida) — 429", r.status_code == 429)
        r = c.post(f"/api/queues/samples/{s1}/answer", json={"manual": 3})
        ok("Javob: avto 3, qo'lda 3 → to'g'ri", r.status_code == 200 and r.json()["auto"] == 3 and r.json()["good"], r.text)
        ok("Javob: noto'g'ri qiymat — 400", c.post(f"/api/queues/samples/{s1}/answer", json={"manual": -1}).status_code == 400)
    finally:
        mgr.workers.pop(sid, None)
    # Sekundomer + avtomatik yozuv
    r = c.post("/api/queues/timings", json={"zone_id": zid, "note": "qizil kurtka"})
    tid = r.json()["id"]
    t0 = float(db.scalar("SELECT t0 FROM queue_timings WHERE id=?", (tid,)))
    c.post(f"/api/queues/timings/{tid}/stop")
    db.execute("UPDATE queue_timings SET t0=?, t1=? WHERE id=?", (t0 - 100, t0 - 10, tid))
    db.execute("INSERT INTO queue_waits(source_id, zone_id, zone_name, t_in, t_out, wait) VALUES(?,?,?,?,?,?)",
               (sid, zid, "3-kassa", t0 - 98, t0 - 9, 89))
    g = c.get("/api/queues/gate").json()
    tm = next(x for x in g["timings"] if x["id"] == tid)
    ok("Sekundomer avtomatik yozuv bilan juftlandi: qo'lda 90 s, avto 89 s, xato ≈ 1%",
       tm["auto"] == 89 and abs(tm["err"] - 1 / 90) < 0.002, str(tm))
    ok("Darvoza holati: namunalar va sekundomer (yetarli emas)", g["samples_gate"]["n"] == 1 and not g["samples_gate"]["enough"]
       and g["timings_gate"]["n"] == 1 and not g["timings_gate"]["enough"])
    r = c.get("/api/export/queues.csv?days=7")
    ok("CSV eksport (kutishlar)", r.status_code == 200 and "kutish" in r.text and "3-kassa" in r.text)
    r = c.get("/api/queues/daily?days=3").json()
    ok("Kunlar bo'yicha jadval", len(r["days"]) == 3)
    r = c.get("/api/queues/schedule").json()
    ok("Kassir jadvali API", "items" in r and "enough" in r)
    r = c.get(f"/sources/{sid}/zones")
    ok("Zonalar sahifasi 'Kassa navbati' va 'Kassir joyi' turlarini taklif qiladi",
       r.status_code == 200 and "Kassa navbati" in r.text and "Kassir joyi" in r.text)


def gate_suite() -> None:
    """tests/queue_gate.py (buyruq qatori) — panel bilan bir xil mezon."""
    print("\n--- Darvoza: tests/queue_gate.py ---")
    from app.config import DB_PATH

    gate_py = Path(__file__).resolve().parent / "queue_gate.py"

    def run() -> tuple[int, str]:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, str(gate_py), "--db", str(DB_PATH)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    db.execute("DELETE FROM queue_samples")
    db.execute("DELETE FROM queue_timings")
    code, out = run()
    ok("Ma'lumot yetarli emas — kod 2", code == 2, out.strip().splitlines()[-1] if out.strip() else out)
    now = time.time()
    rng = np.random.default_rng(1)
    with db.tx() as c:
        for k in range(60):
            man = int(rng.integers(0, 7))
            if k < 20:
                man = max(man, 2)                                       # kamida 20 ta namunada navbat bor
            auto = man + (0 if k % 12 else (2 if k % 24 else -1))      # 60 dan 2 tasi ±1 dan ko'p xato → 96.7%
            c.execute("INSERT INTO queue_samples(t, source_id, zone_id, zone_name, auto, raw, manual) VALUES(?,?,?,?,?,?,?)",
                      (now - 4000 + k * 30, 1, 11, "1-kassa", auto, auto, man))
        for k in range(12):
            t0 = now - 3000 + k * 200
            man = 60 + k * 10
            c.execute("INSERT INTO queue_timings(source_id, zone_id, zone_name, t0, t1) VALUES(?,?,?,?,?)",
                      (1, 11, "1-kassa", t0, t0 + man))
            c.execute("INSERT INTO queue_waits(source_id, zone_id, zone_name, t_in, t_out, wait) VALUES(?,?,?,?,?,?)",
                      (1, 11, "1-kassa", t0 + 2, t0 + man - 1, man * 0.95 - 3))
    code, out = run()
    ok("60 namuna (≥ 20 tasida navbat), ±1 ≥ 90% va 12 sekundomer, xato ≤ 20% — O'TDI (kod 0)", code == 0 and "O'TDI" in out,
       out.strip().splitlines()[-1])
    db.execute("UPDATE queue_waits SET wait = wait * 0.6 WHERE zone_id=11")
    code, out = run()
    ok("Kutish 40% kam o'lchansa — O'TMADI (kod 1), maslahat bilan", code == 1 and "O'TMADI" in out and "kam" in out,
       out.strip().splitlines()[-1])


def main() -> int:
    db.init_db()
    logic_suite()
    accuracy_suite()
    rules_suite()
    store_rules_suite()
    sync_suite()
    telegram_suite()
    web_suite()
    gate_suite()
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi")
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
