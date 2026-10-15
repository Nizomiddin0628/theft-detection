"""Kassa navbati (4-bosqich, 2.0.0): navbat uzunligi, kutish vaqti, kassir bor/yo'q va 5 qoida.

Zonalar (Kameralar → Zonalar):
  navbat   Kassa oldida xaridorlar navbat kutadigan joy — to'lov joyi bilan birga (to'layotgan xaridor ham navbatda).
           Har kassaga alohida zona; nomi — kassa nomi ("1-kassa").
  kassir   Peshtaxta orti — kassir turadigan joy. Bitta kamerada bir nechta kassa bo'lsa, har navbat eng yaqin kassir
           joyi bilan juftlanadi. Kassir joyi chizilmasa — kassir bor/yo'qligi bilinmaydi (2- va 3-qoida ishlamaydi).
           Kassir joyi o'g'irlik signallarini o'chirmaydi (buning uchun — "xodim" zonasi).

O'lchash (har AI qadamida, app/ai/counting.py dagi LineCounter.update orqali — worker.py ga yangi ulanish kerak emas):
  * Navbat uzunligi — navbat zonasida oyog'i (ramka pastining o'rtasi) turgan odamlar: xodimlar (xodim zonasida 2 s
    turgan) va kassir joyidagilardan tashqari. Kamida MIN_STAY s turgan sanaladi (o'tib ketayotgan sanalmaydi);
    oldidagi odam to'sib qo'yib ≤ LOST_HOLD s ko'rinmay qolgan ham sanaladi. Ko'rsatiladigan son — oxirgi LEN_WINDOW s
    dagi qiymatlar medianasi (detektor bir kadr adashsa raqam sakramaydi). Zonada STATIC_SEC dan uzoq "turgan odam"
    (manekin, plakat, doim turgan qo'riqchi) — hisobga olinmaydi (cheksiz "uzoq kutish" signali bo'lmasin).
  * Kutish vaqti — odam navbatga kirgan paytdan chiqqan paytgacha (to'lov bilan birga). Zonadan EXIT_GRACE s chiqib
    tursa — chiqdi; MIN_WAIT s dan qisqa turgan — o'tib ketgan, yozilmaydi.
  * ID uzilishi: navbatda odamlar bir-birini to'sadi, kuzatuvchi ID ni yo'qotadi. Yo'qolgan a'zo LOST_TTL s eslab
    turiladi: o'sha ID qaytsa — davom etadi; zona ICHIDA birdan paydo bo'lgan yangi ID (tashqaridan kirib kelmagan) —
    eng yaqin yo'qolgan a'zoning davomi (kutish vaqti meros, STITCH_DIST × bo'y masofagacha: to'silgan paytda navbat
    1–2 o'rin oldinga siljigan bo'lishi mumkin). Tashqarida ko'ringan va zonaga kirgan odam — har doim yangi xaridor.
  * Kassir: kassir joyida oyog'i (peshtaxta to'sgan bo'lsa — ramka pasti) bor odam. CASHIER_GRACE s ichida ko'ringan —
    "bor" (egilgan, bir lahza to'silgan kassir "ketdi" deb olinmaydi).

Qoidalar (sozlamalar — Navbat sahifasida):
  1. Qo'shimcha kassa oching: navbatda ≥ queue_max_len kishi queue_long_sec s davomida yoki kimdir ≥ queue_max_wait_min
     daqiqa kutmoqda. Bir kassa uchun queue_cooldown_min daqiqada bir marta.
  2. Kassani tekshiring: navbatda ≥ 2 kishi (15 s dan beri), kassir joyi queue_cashier_sec s bo'sh.
  3. Qo'shimcha kassir kerak emas: oxirgi 30 daqiqada ≥ 2 kassa ochiq (kassir bor), navbat deyarli yo'q
     (band vaqt yig'indisi ≤ (ochiq − 1) × 50%, eng uzun navbat ≤ 2) — bitta kassir boshqa ishga o'tishi mumkin.
     2 soatda ko'pi bilan bir marta; Telegram'ga sukut bo'yicha yuborilmaydi (faqat panelda).
  4. Kassir jadvali: oxirgi 4 haftada bir xil hafta kuni va soatda takrorlanadigan navbat (kamida 2 hafta tarix) —
     har dushanba ertalab tavsiya (app/queue_eval.py: schedule).
  5. Tarmoq signali — bulutda: bir vaqtda (15 daqiqa ichida) ≥ 2 filialda 1/2-qoida — egasiga (cloud/notify.py).

Saqlash: queue_waits (har odam), queue_minute (daqiqalik: kuzatilgan vaqt, uzunlik, band vaqt, kassir, xizmat,
kutish), queue_alerts (qoida signallari) — 90 kun. Bulutga: daqiqalik va signallar (bulut "queues" imkoniyatini
e'lon qilgan bo'lsa — eski bulut navbatni to'smasin). Video tahlili (persist=False) bazaga yozmaydi va signal bermaydi —
natija ro'yxatlarda qaytariladi (aniqlik sinovi).
"""
from __future__ import annotations

import html
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .. import db
from ..logutil import get_logger

log = get_logger("queues")

QUEUE_KIND = "navbat"
CASHIER_KIND = "kassir"
MIN_STAY = 1.0            # uzunlikka kirishi uchun zonada kamida shuncha turgan bo'lsin
MIN_WAIT = 5.0            # bundan qisqa — o'tib ketgan, kutish yozilmaydi
EXIT_GRACE = 2.0          # zonadan tashqarida shuncha tursa — chiqdi
LOST_HOLD = 1.5           # ko'rinmay qolgan a'zo shuncha vaqt uzunlikda sanaladi
LOST_TTL = 8.0            # ko'rinmay qolgan a'zo shuncha vaqt eslab turiladi (ID ulash uchun)
STITCH_DIST = 0.5         # zona ichida paydo bo'lgan yangi ID ↔ yo'qolgan a'zo: masofa (bo'yga nisbatan)
FRESH_SEC = 1.0           # ID shu vaqtdan beri ko'rinayotgan bo'lsa — "yangi" (tashqaridan kirib kelmagan)
LEN_WINDOW = 2.5          # ko'rsatiladigan uzunlik — shu oynadagi mediana
STATIC_SEC = 20 * 60      # zonada shundan uzoq "turgan odam" — haqiqiy xaridor emas (manekin, plakat, doim turgan
                          # qo'riqchi yoki qadoqlovchi): uzunlikka, kutishga va qoidalarga kirmaydi, kutishi yozilmaydi
TRACE_MAX = 200_000       # video tahlilida qadamlar ro'yxati chegarasi (soatlab video — xotira o'smasin)
CASHIER_GRACE = 8.0
MAX_DT = 2.0              # AI qadamlari orasidagi bundan katta tanaffus kuzatilgan vaqtga qo'shilmaydi
SEEN_TTL = 10.0
LIVE_SEC = 15.0           # holat shundan eski — kamera ishlamayapti
TWO_SEC = 15.0            # 2-qoida: navbatda ≥ 2 kishi kamida shuncha vaqtdan beri
R3_WINDOW = 30 * 60
R3_COOLDOWN = 2 * 3600
KEEP_DAYS = 90
ALERT_KEEP_DAYS = 180

RULES = {
    "R1": "Qo'shimcha kassa oching",
    "R2": "Kassani tekshiring",
    "R3": "Qo'shimcha kassir kerak emas",
    "R4": "Kassir jadvali",
    "R5": "Tarmoq signali",
}
LEVEL = {"R1": "warning", "R2": "danger", "R3": "info", "R4": "info", "R5": "danger"}

DEFAULTS = {"queue_max_len": "4", "queue_max_wait_min": "3", "queue_long_sec": "30", "queue_cashier_sec": "45",
            "queue_cooldown_min": "10", "queue_tg": "1", "queue_tg_low": "0", "queue_tg_schedule": "1"}
LIMITS = {"queue_max_len": (2, 30), "queue_max_wait_min": (1, 60), "queue_long_sec": (5, 600),
          "queue_cashier_sec": (10, 900), "queue_cooldown_min": (1, 240)}


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def fmt_dur(sec: float) -> str:
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} daq {s} s" if s else f"{m} daq"
    h, m = divmod(m, 60)
    return f"{h} soat {m} daq"


# ----------------------------------------------------------------------------- sozlamalar (5 s kesh)
_cfg_lock = threading.Lock()
_cfg_cache: list = [0.0, None]


def _num(v, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return min(max(x, lo), hi)


def cfg() -> dict:
    now = time.monotonic()
    with _cfg_lock:
        if _cfg_cache[1] is not None and now - _cfg_cache[0] < 5.0:
            return _cfg_cache[1]
    try:
        s = db.get_settings()
    except Exception:  # noqa: BLE001
        s = {}

    def g(k):
        lo, hi = LIMITS[k]
        return _num(s.get(k, DEFAULTS[k]), float(DEFAULTS[k]), lo, hi)
    c = {"max_len": int(g("queue_max_len")), "max_wait": g("queue_max_wait_min") * 60.0,
         "long_sec": g("queue_long_sec"), "cashier_sec": g("queue_cashier_sec"),
         "cooldown": g("queue_cooldown_min") * 60.0,
         "tg": str(s.get("queue_tg", DEFAULTS["queue_tg"])) == "1",
         "tg_low": str(s.get("queue_tg_low", DEFAULTS["queue_tg_low"])) == "1",
         "tg_schedule": str(s.get("queue_tg_schedule", DEFAULTS["queue_tg_schedule"])) == "1",
         "work_hours": str(s.get("work_hours", "") or "")}
    with _cfg_lock:
        _cfg_cache[0], _cfg_cache[1] = now, c
    return c


def reload_cfg() -> None:
    with _cfg_lock:
        _cfg_cache[1] = None


# ----------------------------------------------------------------------------- holat
@dataclass
class _Member:
    tid: int
    t_in: float               # navbatga kirgan vaqt (ID almashsa — meros)
    last_in: float            # oxirgi marta zona ichida ko'ringan
    last_t: float             # oxirgi marta ko'ringan (ichida yoki tashqarida)
    pt: tuple                 # oyoq nuqtasi (px)
    h: float                  # odam bo'yi (px)
    out_since: float | None = None
    ids: int = 1              # necha ID ulangan (ma'lumot uchun)

    def static(self, t: float) -> bool:
        return t - self.t_in > STATIC_SEC


@dataclass
class _Q:
    zone_id: int
    name: str
    geom: tuple
    cashier_zone: int | None
    started: float
    members: dict = field(default_factory=dict)       # tid -> _Member
    lost: dict = field(default_factory=dict)          # tid -> _Member (ko'rinmay qolgan)
    samples: deque = field(default_factory=deque)     # (t, xom uzunlik)
    length: int = 0
    raw: int = 0
    last_t: float = 0.0
    long_since: float | None = None
    two_since: float | None = None
    cashier_seen: float = 0.0
    fired: dict = field(default_factory=dict)         # qoida -> vaqt
    max_wait: float = 0.0


class _Acc:
    """Bitta zona, bitta daqiqa."""
    __slots__ = ("minute", "zone_id", "zone_name", "source_id", "source_name", "t_obs", "len_sum", "len_max",
                 "long_sec", "busy_sec", "cashier_sec", "cashier_known", "served", "wait_sum", "wait_n", "wait_max")

    def __init__(self, minute: int, zone_id: int, zone_name: str, source_id, source_name: str):
        self.minute, self.zone_id, self.zone_name = minute, zone_id, zone_name
        self.source_id, self.source_name = source_id, source_name
        self.t_obs = self.len_sum = self.long_sec = self.busy_sec = self.cashier_sec = self.wait_sum = 0.0
        self.len_max = self.served = self.wait_n = 0
        self.cashier_known = 0
        self.wait_max = 0.0

    def row(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


_acc_lock = threading.Lock()
_acc: dict[tuple[int, int], _Acc] = {}
_live_lock = threading.Lock()
_live: dict[int, "QueueMonitor"] = {}                 # source_id -> jonli kamera monitori
_notifier = None
ASYNC_ALERTS = True                                   # sinovlarda False — signal darhol (bir oqimda)


def set_notifier(bot) -> None:
    global _notifier
    _notifier = bot


def _acc_for(minute: int, q: _Q, source_id, source_name: str) -> _Acc:
    key = (minute, q.zone_id)
    a = _acc.get(key)
    if a is None:
        a = _Acc(minute, q.zone_id, q.name, source_id, source_name)
        _acc[key] = a
    a.zone_name = q.name
    return a


def _last_fired(zone_id: int) -> dict:
    try:
        return {r["rule"]: float(r["t"]) for r in db.rows(
            "SELECT rule, MAX(t) AS t FROM queue_alerts WHERE zone_id=? AND t >= ? GROUP BY rule",
            (zone_id, time.time() - 6 * 3600))}
    except Exception:  # noqa: BLE001
        return {}


def _centroid(z) -> np.ndarray:
    return np.asarray(z.pts, dtype=np.float64).mean(axis=0)


def pairs(zoneset) -> list[tuple]:
    """(navbat zonasi, unga eng yaqin kassir zonasi yoki None)."""
    qs = [z for z in getattr(zoneset, "zones", []) or [] if z.kind == QUEUE_KIND]
    ks = [z for z in getattr(zoneset, "zones", []) or [] if z.kind == CASHIER_KIND]
    out = []
    for q in qs:
        best = None
        if ks:
            c = _centroid(q)
            best = min(ks, key=lambda k: float(np.hypot(*(_centroid(k) - c))))
        out.append((q, best))
    return out


class QueueMonitor:
    """Bitta kamera uchun. update() har AI qadamida chaqiriladi (LineCounter.update ichidan)."""

    def __init__(self, source_id: int | None, source_name: str = "", persist: bool = True):
        self.source_id = source_id
        self.source_name = source_name
        self.persist = persist and bool(source_id)
        # Video tahlili / aniqlik sinovi — natija ro'yxatlarda. Jonli kamera persist keyin o'chirilsa (video fayl qaytadan
        # aylanmoqda) — yig'ilmaydi (xotira o'smasin)
        self.collect = not self.persist
        self.queues: dict[int, _Q] = {}
        self.first_seen: dict[int, float] = {}
        self.last_seen: dict[int, float] = {}
        self.waits: list[dict] = []          # persist=False: barcha kutishlar (video tahlili, sinov)
        self.trace: deque = deque(maxlen=TRACE_MAX)   # persist=False: (t, zona, uzunlik, xom) — har qadam
        self.updated_at = 0.0
        self.snapshot: list[dict] = []       # jonli holat (web oqimi o'qiydi — atomar almashtiriladi)

    # ------------------------------------------------------------------
    def update(self, tracked: list, t: float, w: int, h: int, zoneset=None, status: dict | None = None,
               persist: bool | None = None) -> None:
        if persist is not None:
            self.persist = bool(persist) and bool(self.source_id)
        prs = pairs(zoneset)
        if not prs:
            if self.queues:
                self.queues.clear()
                self.snapshot = []
            return
        status = status or {}
        pts = []
        for p in tracked:
            tid = int(getattr(p, "track_id", -1))
            if tid < 0:
                continue
            b = p.box
            pts.append((tid, (float(b[0] + b[2]) / 2.0, float(b[3])), max(float(b[3] - b[1]), 1.0),
                        bool((status.get(tid) or {}).get("staff"))))
        for tid, *_ in pts:
            self.first_seen.setdefault(tid, t)
            self.last_seen[tid] = t
        for tid in [k for k, v in self.last_seen.items() if t - v > SEEN_TTL]:
            self.last_seen.pop(tid, None)
            self.first_seen.pop(tid, None)
        live = set()
        for qz, kz in prs:
            q = self.queues.get(qz.id)
            geom = tuple(map(tuple, np.round(np.asarray(qz.pts, dtype=np.float64), 5).tolist()))
            if q is None or q.geom != geom:
                q = _Q(qz.id, qz.name, geom, kz.id if kz else None, started=t, cashier_seen=t)
                if self.persist:
                    q.fired = _last_fired(qz.id)         # dastur qayta ishga tushsa ham signal darhol takrorlanmasin
                self.queues[qz.id] = q
            q.name = qz.name
            if (kz.id if kz else None) != q.cashier_zone:
                q.cashier_zone = kz.id if kz else None
                q.cashier_seen = t
            self._step(q, qz, kz, pts, t, w, h)
            live.add(qz.id)
        for zid in [z for z in self.queues if z not in live]:
            self.queues.pop(zid, None)
        self.updated_at = time.time()
        self.snapshot = [self._state(q, t) for q in self.queues.values()]
        if self.persist:
            with _live_lock:
                _live[int(self.source_id)] = self

    # ------------------------------------------------------------------
    def _step(self, q: _Q, qz, kz, pts: list, t: float, w: int, h: int) -> None:
        inside: dict[int, tuple] = {}
        visible: dict[int, tuple] = {}
        cashier_now = False
        for tid, foot, bh, staff in pts:
            in_k = kz is not None and kz.contains(foot, w, h)
            if in_k:
                cashier_now = True
            # Kassir joyidagi odam navbatda emas; lekin bir lahza (oyoq nuqtasi silkinsa, to'layotgan xaridor peshtaxtaga
            # engashsa) — a'zolikni darhol o'chirmaydi: zonadan tashqari kabi, EXIT_GRACE dan keyin chiqqan hisoblanadi.
            # Butunlay chiqariladigan — faqat xodim (analyzer: xodim zonasida 2 s)
            visible[tid] = (foot, bh, staff)
            if not staff and not in_k and qz.contains(foot, w, h):
                inside[tid] = (foot, bh)
        if cashier_now:
            q.cashier_seen = t
        # Ko'rinmay qolgan a'zo o'sha ID bilan zonadan TASHQARIDA ko'rindi — chiqib ketyapti (boshqa yangi ID unga
        # ulanmasin); chiqish odatdagidek EXIT_GRACE bilan
        for tid in [k for k in q.lost if k in visible and k not in inside]:
            m = q.lost.pop(tid)
            if visible[tid][2]:
                continue
            q.members[tid] = m
        # 1) mavjud a'zolar
        for tid in list(q.members):
            m = q.members[tid]
            if tid in inside:
                foot, bh = inside[tid]
                m.last_in = m.last_t = t
                m.pt, m.h, m.out_since = foot, bh, None
            elif tid in visible:
                foot, bh, staff = visible[tid]
                if staff:
                    q.members.pop(tid)                   # xodim — xaridor emas, kutish yozilmaydi
                    continue
                m.last_t, m.pt, m.h = t, foot, bh
                if m.out_since is None:
                    m.out_since = t
                if t - m.out_since >= EXIT_GRACE:
                    self._finish(q, q.members.pop(tid), m.last_in)
            else:
                q.lost[tid] = q.members.pop(tid)
        # 2) yangi kirganlar: o'sha ID qaytdi / zona ichida paydo bo'lgan yangi ID — yo'qolgan a'zoning davomi /
        #    tashqaridan kirib kelgan — yangi xaridor
        fresh = []
        for tid, (foot, bh) in inside.items():
            if tid in q.members:
                continue
            m = q.lost.pop(tid, None)
            if m is not None:
                m.last_in = m.last_t = t
                m.pt, m.h, m.out_since = foot, bh, None
                q.members[tid] = m
            else:
                fresh.append(tid)
        cand = []
        for tid in fresh:
            foot, bh = inside[tid]
            if t - self.first_seen.get(tid, t) > FRESH_SEC:
                continue                                 # tashqarida ko'ringan, endi kirdi — yangi xaridor
            for ltid, m in q.lost.items():
                if t - m.last_t > LOST_TTL or m.out_since is not None:
                    continue                             # chiqib ketayotganda yo'qolgan — davomi bo'lishi mumkin emas
                d = math.hypot(foot[0] - m.pt[0], foot[1] - m.pt[1])
                if d <= STITCH_DIST * max(bh, m.h):
                    cand.append((d / max(bh, m.h), tid, ltid))
        used_t, used_l = set(), set()
        for _d, tid, ltid in sorted(cand):
            if tid in used_t or ltid in used_l:
                continue
            used_t.add(tid)
            used_l.add(ltid)
            m = q.lost.pop(ltid)
            foot, bh = inside[tid]
            q.members[tid] = _Member(tid, m.t_in, t, t, foot, bh, None, m.ids + 1)
        for tid in fresh:
            if tid not in used_t:
                foot, bh = inside[tid]
                q.members[tid] = _Member(tid, t, t, t, foot, bh)
        # 3) uzoq ko'rinmagan a'zolar — ketgan (oxirgi marta zona ichida ko'ringan vaqtda)
        for ltid in [k for k, m in q.lost.items() if t - m.last_t > LOST_TTL]:
            m = q.lost.pop(ltid)
            self._finish(q, m, m.last_in)
        # 4) uzunlik
        raw = sum(1 for m in q.members.values() if m.out_since is None and MIN_STAY <= t - m.t_in <= STATIC_SEC)
        raw += sum(1 for m in q.lost.values() if m.out_since is None and t - m.last_t <= LOST_HOLD
                   and MIN_STAY <= t - m.t_in <= STATIC_SEC)
        q.samples.append((t, raw))
        while q.samples and t - q.samples[0][0] > LEN_WINDOW:
            q.samples.popleft()
        vals = sorted(v for _, v in q.samples)
        q.raw = raw
        q.length = vals[len(vals) // 2] if vals else 0
        waits = [t - m.t_in for m in q.members.values() if m.out_since is None and not m.static(t)]
        waits += [t - m.t_in for m in q.lost.values() if m.out_since is None and t - m.last_t <= LOST_HOLD
                  and not m.static(t)]
        q.max_wait = max(waits) if q.length and waits else 0.0
        dt = t - q.last_t if q.last_t else 0.0
        if dt < 0 or dt > MAX_DT:
            dt = 0.0
        q.last_t = t
        if not self.persist:
            if self.collect:
                self.trace.append((t, q.zone_id, q.length, raw))
            return
        c = cfg()
        cashier_ok = q.cashier_zone is not None and t - q.cashier_seen <= CASHIER_GRACE
        if dt > 0:
            with _acc_lock:
                a = _acc_for(int(t // 60) * 60, q, self.source_id, self.source_name)
                a.t_obs += dt
                a.len_sum += q.length * dt
                a.len_max = max(a.len_max, q.length)
                if q.length >= c["max_len"]:
                    a.long_sec += dt
                if q.length >= 1:
                    a.busy_sec += dt
                if q.cashier_zone is not None:
                    a.cashier_known = 1
                    if cashier_ok:
                        a.cashier_sec += dt
        self._rules(q, t, c, cashier_ok)

    def close(self) -> None:
        """Video tugadi: navbatda qolganlar oxirgi marta zona ichida ko'ringan paytda chiqqan deb yoziladi."""
        for q in self.queues.values():
            for m in list(q.members.values()) + list(q.lost.values()):
                self._finish(q, m, m.last_in)
            q.members.clear()
            q.lost.clear()

    # ------------------------------------------------------------------
    def _finish(self, q: _Q, m: _Member, t_out: float) -> None:
        wait = t_out - m.t_in
        if wait < MIN_WAIT or wait > STATIC_SEC:
            return                                       # o'tib ketgan yoki doimiy "odam" — kutish emas
        rec = {"source_id": self.source_id, "zone_id": q.zone_id, "zone_name": q.name, "t_in": m.t_in,
               "t_out": t_out, "wait": round(wait, 2), "track_id": m.tid, "ids": m.ids}
        if not self.persist:
            if self.collect:
                self.waits.append(rec)
            return
        try:
            db.execute("INSERT INTO queue_waits(source_id, zone_id, zone_name, t_in, t_out, wait, track_id, ids) "
                       "VALUES(?,?,?,?,?,?,?,?)", (self.source_id, q.zone_id, q.name, round(m.t_in, 2),
                                                   round(t_out, 2), rec["wait"], m.tid, m.ids))
        except Exception:  # noqa: BLE001
            log.exception("Kutish vaqtini yozib bo'lmadi")
        with _acc_lock:
            a = _acc_for(int(t_out // 60) * 60, q, self.source_id, self.source_name)
            a.served += 1
            a.wait_sum += wait
            a.wait_n += 1
            a.wait_max = max(a.wait_max, wait)

    def _rules(self, q: _Q, t: float, c: dict, cashier_ok: bool) -> None:
        q.long_since = (q.long_since or t) if q.length >= c["max_len"] else None
        q.two_since = (q.two_since or t) if q.length >= 2 else None
        reason = None
        if q.long_since is not None and t - q.long_since >= c["long_sec"]:
            reason = "len"
        elif q.length >= 1 and q.max_wait >= c["max_wait"]:
            reason = "wait"
        if reason and t - q.fired.get("R1", -1e12) >= c["cooldown"]:
            again = t - q.fired.get("R1", -1e12) < 3 * c["cooldown"]
            q.fired["R1"] = t
            others = [x for x in live_status() if not (x["source_id"] == self.source_id and x["zone_id"] == q.zone_id)]
            if reason == "len":
                title = "Navbat hali ham uzun" if again else "Qo'shimcha kassa oching"
                text = (f"{esc(q.name)}: navbatda <b>{q.length} kishi</b> ({fmt_dur(t - q.long_since)} dan beri), eng uzoq "
                        f"kutayotgan — {fmt_dur(q.max_wait)}.")
            else:
                title = "Navbatda uzoq kutishyapti"
                text = (f"{esc(q.name)}: eng uzoq kutayotgan — <b>{fmt_dur(q.max_wait)}</b> (me'yor {fmt_dur(c['max_wait'])}),"
                        f" navbatda {q.length} kishi. Qo'shimcha kassa oching.")
            if others:
                text += "\nBoshqa kassalar: " + "; ".join(f"{esc(x['name'])} — {STATE_UZ.get(x['state'], x['state'])}"
                                                         f"{'' if x['state'] in ('yopiq', 'nomalum') else ', ' + str(x['length']) + ' kishi'}"
                                                         for x in others[:6])
            self._fire("R1", q, title, text, {"reason": reason, "length": q.length, "max_wait": round(q.max_wait),
                                              "cashier": cashier_ok if q.cashier_zone is not None else None})
        if (q.cashier_zone is not None and q.two_since is not None and t - q.two_since >= TWO_SEC
                and t - q.cashier_seen >= c["cashier_sec"] and t - q.fired.get("R2", -1e12) >= c["cooldown"]):
            q.fired["R2"] = t
            self._fire("R2", q, "Kassani tekshiring",
                       f"{esc(q.name)}: navbatda <b>{q.length} kishi</b>, kassir joyi {fmt_dur(t - q.cashier_seen)} dan beri bo'sh.",
                       {"length": q.length, "absent_sec": round(t - q.cashier_seen), "max_wait": round(q.max_wait)})

    def _fire(self, rule: str, q: _Q, title: str, text: str, data: dict) -> None:
        alert = {"t": time.time(), "rule": rule, "level": LEVEL[rule], "source_id": self.source_id,
                 "source_name": self.source_name, "zone_id": q.zone_id, "zone_name": q.name, "title": title,
                 "text": text, "data": data}
        if ASYNC_ALERTS:     # AI oqimi bazaga yozish / Telegram navbatini kutmasin
            threading.Thread(target=raise_alert, args=(alert,), daemon=True, name="queue-alert").start()
        else:
            raise_alert(alert)

    def _state(self, q: _Q, t: float) -> dict:
        cashier = None if q.cashier_zone is None else (t - q.cashier_seen <= CASHIER_GRACE)
        return {"source_id": self.source_id, "source_name": self.source_name, "zone_id": q.zone_id, "name": q.name,
                "length": q.length, "raw": q.raw, "max_wait": round(q.max_wait), "cashier": cashier,
                "cashier_absent": (round(t - q.cashier_seen) if cashier is False else 0),
                "state": state_of(q.length, cashier, cfg()["max_len"]), "t": t}


STATE_UZ = {"navbat": "uzun navbat", "band": "band", "bosh": "bo'sh", "yopiq": "yopiq (kassir yo'q)",
            "kassir_yoq": "navbat bor, kassir yo'q", "nomalum": "ma'lumot yo'q"}


def state_of(length: int, cashier: bool | None, max_len: int) -> str:
    if cashier is False:
        return "kassir_yoq" if length >= 1 else "yopiq"
    if length >= max_len:
        return "navbat"
    return "band" if length >= 1 else "bosh"


def live_status() -> list[dict]:
    """Jonli kameralardagi barcha navbatlar (oxirgi LIVE_SEC ichida yangilangan)."""
    now = time.time()
    out = []
    with _live_lock:
        mons = list(_live.values())
    if not mons:
        return out
    try:      # o'chirilgan zona / kamera ko'rsatilmaydi
        active = {int(r["id"]) for r in db.rows("SELECT id FROM zones WHERE kind=? AND enabled=1", (QUEUE_KIND,))}
    except Exception:  # noqa: BLE001
        active = None
    for m in mons:
        fresh = now - m.updated_at <= LIVE_SEC
        for s in m.snapshot:
            if active is not None and int(s["zone_id"]) not in active:
                continue
            x = dict(s)
            x["live"] = fresh
            if not fresh:
                x.update(length=0, raw=0, max_wait=0, state="nomalum", cashier=None)
            out.append(x)
    out.sort(key=lambda x: (str(x["source_name"]), str(x["name"])))
    return out


def board() -> list[dict]:
    """Barcha yoqilgan navbat zonalari: jonli holat yoki (kamera ishlamasa) "ma'lumot yo'q" — panel, /navbat, bulut."""
    live = live_status()
    have = {int(x["zone_id"]) for x in live}
    rows = db.rows("SELECT z.id, z.name, z.source_id, s.name AS source_name FROM zones z LEFT JOIN sources s "
                   "ON s.id=z.source_id WHERE z.kind=? AND z.enabled=1 ORDER BY z.id", (QUEUE_KIND,))
    for r in rows:
        if int(r["id"]) not in have:
            live.append({"source_id": r["source_id"], "source_name": r["source_name"] or "", "zone_id": r["id"],
                         "name": r["name"], "length": 0, "raw": 0, "max_wait": 0, "cashier": None,
                         "cashier_absent": 0, "state": "nomalum", "t": 0, "live": False})
    live.sort(key=lambda x: (str(x["source_name"]), str(x["name"])))
    return live


# ----------------------------------------------------------------------------- daqiqalik yig'indi
MINUTE_COLS = ("t_obs", "len_sum", "len_max", "long_sec", "busy_sec", "cashier_sec", "cashier_known", "served",
               "wait_sum", "wait_n", "wait_max")


def flush_minutes(upto: int) -> list[dict]:
    """upto (daqiqa boshi) va undan oldingi yopilgan daqiqalar: bazaga (kechikkan qism mavjud qatorga qo'shiladi),
    bulutga (bulut qo'llasa). MetricsSampler.flush har daqiqada chaqiradi."""
    with _acc_lock:
        keys = [k for k in _acc if k[0] <= upto]
        accs = [_acc.pop(k) for k in keys]
    if not accs:
        return []
    out = []
    with db.tx() as c:
        for a in accs:
            r = a.row()
            c.execute(
                "INSERT INTO queue_minute(minute, zone_id, zone_name, source_id, source_name, t_obs, len_sum, len_max, "
                "long_sec, busy_sec, cashier_sec, cashier_known, served, wait_sum, wait_n, wait_max) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(minute, zone_id) DO UPDATE SET "
                "zone_name=excluded.zone_name, source_name=excluded.source_name, t_obs=t_obs+excluded.t_obs, "
                "len_sum=len_sum+excluded.len_sum, len_max=MAX(len_max, excluded.len_max), "
                "long_sec=long_sec+excluded.long_sec, busy_sec=busy_sec+excluded.busy_sec, "
                "cashier_sec=cashier_sec+excluded.cashier_sec, cashier_known=MAX(cashier_known, excluded.cashier_known), "
                "served=served+excluded.served, wait_sum=wait_sum+excluded.wait_sum, wait_n=wait_n+excluded.wait_n, "
                "wait_max=MAX(wait_max, excluded.wait_max)",
                (r["minute"], r["zone_id"], r["zone_name"], r["source_id"], r["source_name"],
                 round(r["t_obs"], 2), round(r["len_sum"], 2), r["len_max"], round(r["long_sec"], 2),
                 round(r["busy_sec"], 2), round(r["cashier_sec"], 2), r["cashier_known"], r["served"],
                 round(r["wait_sum"], 2), r["wait_n"], round(r["wait_max"], 2)))
            row = c.execute("SELECT * FROM queue_minute WHERE minute=? AND zone_id=?", (r["minute"], r["zone_id"])).fetchone()
            out.append(dict(row))
    try:
        from .. import sync

        sync.enqueue_queue_minutes(out)
    except Exception:  # noqa: BLE001
        log.exception("Navbat ko'rsatkichlarini bulut navbatiga qo'yib bo'lmadi")
    return out


def on_minute(minute: int) -> None:
    """MetricsSampler har daqiqada (minute — yopilgan daqiqa boshi): yig'indi, 3- va 4-qoida, tozalash."""
    try:
        flush_minutes(minute)
    except Exception:  # noqa: BLE001
        log.exception("Navbat daqiqasini yozib bo'lmadi")
    if not db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (QUEUE_KIND,)):
        return
    for fn in (check_r3, check_r4, prune):
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("Navbat qoidasi (%s) xatosi", fn.__name__)


# ----------------------------------------------------------------------------- 3- va 4-qoida (do'kon bo'yicha)
def r3_window(now: float | None = None) -> dict:
    """Oxirgi 30 daqiqa: har kassa (zona) — kuzatuv, band ulushi, eng uzun navbat, kassir ulushi."""
    now = time.time() if now is None else now
    m1 = int(now // 60) * 60
    rows = db.rows("SELECT zone_id, MAX(zone_name) AS name, SUM(t_obs) AS obs, SUM(busy_sec) AS busy, "
                   "MAX(len_max) AS mx, SUM(len_sum) AS ls, SUM(cashier_sec) AS cs, MAX(cashier_known) AS ck "
                   "FROM queue_minute WHERE minute >= ? AND minute < ? GROUP BY zone_id", (m1 - R3_WINDOW, m1))
    out = []
    for r in rows:
        obs = float(r["obs"] or 0)
        out.append({"zone_id": r["zone_id"], "name": r["name"], "obs": obs,
                    "busy_pct": float(r["busy"] or 0) / obs if obs else 0.0, "max_len": int(r["mx"] or 0),
                    "avg_len": float(r["ls"] or 0) / obs if obs else 0.0,
                    "cashier_pct": (float(r["cs"] or 0) / obs if obs else 0.0) if r["ck"] else None})
    return {"zones": out, "since": m1 - R3_WINDOW, "until": m1}


def r3_decision(win: dict) -> dict | None:
    """3-qoida sharti (toza): ≥ 2 kassa ochiq (kassir ≥ 80% vaqt, kuzatuv ≥ 80%), band vaqt yig'indisi ≤ (ochiq − 1)
    × 50%, hech qayerda navbat 2 kishidan oshmagan."""
    open_ = [z for z in win["zones"] if z["cashier_pct"] is not None and z["cashier_pct"] >= 0.8
             and z["obs"] >= 0.8 * R3_WINDOW]
    if len(open_) < 2:
        return None
    if any(z["max_len"] > 2 for z in win["zones"]):
        return None
    busy = sum(z["busy_pct"] for z in open_)
    if busy > (len(open_) - 1) * 0.5:
        return None
    return {"open": len(open_), "busy_sum": round(busy, 3),
            "avg_len": round(sum(z["avg_len"] for z in open_) / len(open_), 2),
            "max_len": max(z["max_len"] for z in open_), "names": [z["name"] for z in open_]}


def check_r3(now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    last = float(db.get_setting("queue_r3_at", "0") or 0)
    if now - last < R3_COOLDOWN:
        return None
    d = r3_decision(r3_window(now))
    if not d:
        return None
    db.set_settings({"queue_r3_at": f"{now:.0f}"})
    text = (f"Oxirgi 30 daqiqada {d['open']} ta kassa ochiq ({esc(', '.join(d['names']))}), navbat o'rtacha "
            f"{d['avg_len']:.1f} kishi (eng ko'pi {d['max_len']}). Bitta kassir boshqa ishga (polka, ombor) o'tishi mumkin.")
    a = {"t": now, "rule": "R3", "level": LEVEL["R3"], "source_id": None, "source_name": "", "zone_id": None,
         "zone_name": "", "title": RULES["R3"], "text": text, "data": d}
    raise_alert(a)
    return a


_sched_cache: list = [0.0, None, None]


def schedule_now(now: float | None = None, cached: bool = False) -> dict:
    from ..queue_eval import SCHED_DAYS, schedule

    if cached and _sched_cache[1] is not None and time.time() - _sched_cache[0] < 600 \
            and _sched_cache[2] == cfg()["max_len"]:
        return _sched_cache[1]
    now = time.time() if now is None else now
    c = cfg()
    rows = db.rows("SELECT minute, t_obs, len_max, long_sec, wait_max FROM queue_minute WHERE minute >= ?",
                   (now - SCHED_DAYS * 86400,))
    res = schedule(rows, c["max_len"], c["max_wait"], now)
    _sched_cache[0], _sched_cache[1], _sched_cache[2] = time.time(), res, c["max_len"]
    return res


def check_r4(now: float | None = None, force: bool = False) -> dict | None:
    """Har hafta bir marta: dushanba 09:00 dan keyin (kompyuter dushanba o'chiq bo'lsa — haftaning keyingi kunida)."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    wk = time.strftime("%G-%V", lt)
    if not force:
        if (lt.tm_wday == 0 and lt.tm_hour < 9) or db.get_setting("queue_r4_week", "") == wk:
            return None
    db.set_settings({"queue_r4_week": wk})
    sch = schedule_now(now)
    if not sch["enough"] or not sch["items"]:
        return None
    lines = []
    for it in sch["items"][:8]:
        lines.append(f"• <b>{esc(it['label'])} {it['hours']}</b> — {it['days_obs']} kundan {it['days_busy']} tasida "
                     f"navbat (eng uzuni {it['max_len']} kishi)")
    text = (f"Oxirgi {sch['days']} kunda navbat shu soatlarda takrorlanmoqda:\n" + "\n".join(lines) +
            "\nShu soatlarga qo'shimcha kassir qo'ying (yoki tushlik/tanaffusni boshqa vaqtga suring).")
    a = {"t": now, "rule": "R4", "level": LEVEL["R4"], "source_id": None, "source_name": "", "zone_id": None,
         "zone_name": "", "title": RULES["R4"], "text": text, "data": {"items": sch["items"][:8], "days": sch["days"]}}
    raise_alert(a)
    return a


# ----------------------------------------------------------------------------- signal: saqlash, Telegram, bulut
def raise_alert(a: dict) -> int | None:
    try:
        aid = db.execute("INSERT INTO queue_alerts(t, rule, level, source_id, source_name, zone_id, zone_name, title, "
                         "text, data) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (a["t"], a["rule"], a["level"], a.get("source_id"), a.get("source_name") or "",
                          a.get("zone_id"), a.get("zone_name") or "", a["title"], a["text"],
                          json.dumps(a.get("data") or {}, ensure_ascii=False)))
    except Exception:  # noqa: BLE001
        log.exception("Navbat signalini yozib bo'lmadi")
        return None
    a["id"] = aid
    log.info("Navbat qoidasi %s: %s — %s", a["rule"], a.get("zone_name") or "do'kon",
             html.unescape(a["title"]))
    c = cfg()
    send = c["tg"] if a["rule"] in ("R1", "R2") else (c["tg_low"] if a["rule"] == "R3" else c["tg_schedule"])
    if send and _notifier is not None:
        try:
            n = _notifier.send_queue_alert(telegram_text(a))
            db.execute("UPDATE queue_alerts SET tg_sent=? WHERE id=?", (int(n or 0), aid))
        except Exception:  # noqa: BLE001
            log.exception("Navbat signalini Telegram'ga yuborib bo'lmadi")
    try:
        from .. import sync

        sync.enqueue_queue_alert(a)
    except Exception:  # noqa: BLE001
        log.exception("Navbat signalini bulut navbatiga qo'yib bo'lmadi")
    return aid


ICON = {"R1": "🧾", "R2": "⚠️", "R3": "ℹ️", "R4": "📅", "R5": "🌐"}


def telegram_text(a: dict) -> str:
    store = db.get_setting("store_name", "") if a["rule"] in ("R3", "R4") else ""
    head = f"{ICON.get(a['rule'], '🧾')} <b>{esc(a['title'])}</b>" + (f" — {esc(store)}" if store else "")
    return f"{head}\n{a['text']}\n<i>{time.strftime('%H:%M', time.localtime(a['t']))} · /navbat — hozirgi holat</i>"


def prune() -> None:
    now = time.time()
    last = float(db.get_setting("queue_prune_at", "0") or 0)
    if now - last < 3600:
        return
    db.set_settings({"queue_prune_at": f"{now:.0f}"})
    db.execute("DELETE FROM queue_waits WHERE t_out < ?", (now - KEEP_DAYS * 86400,))
    db.execute("DELETE FROM queue_minute WHERE minute < ?", (now - KEEP_DAYS * 86400,))
    db.execute("DELETE FROM queue_alerts WHERE t < ?", (now - ALERT_KEEP_DAYS * 86400,))


# ----------------------------------------------------------------------------- hisobot
def has_queues() -> bool:
    return bool(db.scalar("SELECT 1 FROM zones WHERE kind=? AND enabled=1 LIMIT 1", (QUEUE_KIND,)))


def summary(t0: float, t1: float) -> dict:
    """Oraliq bo'yicha har kassa va jami: o'rtacha/eng uzun kutish, xizmat ko'rsatilgan, o'rtacha va eng uzun navbat,
    uzun navbat vaqti, kassa ochiq ulushi, Little tekshiruvi."""
    from ..queue_eval import little

    zs = db.rows("SELECT zone_id, MAX(zone_name) AS name, MAX(source_name) AS source_name, SUM(t_obs) AS obs, "
                 "SUM(len_sum) AS ls, MAX(len_max) AS mx, SUM(long_sec) AS lng, SUM(busy_sec) AS busy, "
                 "SUM(cashier_sec) AS cs, MAX(cashier_known) AS ck, SUM(served) AS served, SUM(wait_sum) AS ws, "
                 "SUM(wait_n) AS wn, MAX(wait_max) AS wm FROM queue_minute WHERE minute >= ? AND minute < ? "
                 "GROUP BY zone_id ORDER BY zone_id", (t0, t1))
    per = []
    tot = {"obs": 0.0, "ls": 0.0, "served": 0, "ws": 0.0, "wn": 0, "lng": 0.0, "wm": 0.0, "mx": 0}
    for r in zs:
        obs = float(r["obs"] or 0)
        wn = int(r["wn"] or 0)
        x = {"zone_id": r["zone_id"], "name": r["name"], "source_name": r["source_name"], "observed_sec": round(obs),
             "avg_len": round(float(r["ls"] or 0) / obs, 2) if obs else None, "max_len": int(r["mx"] or 0),
             "long_sec": round(float(r["lng"] or 0)), "busy_pct": round(float(r["busy"] or 0) / obs, 3) if obs else None,
             "open_pct": (round(float(r["cs"] or 0) / obs, 3) if obs else None) if r["ck"] else None,
             "served": int(r["served"] or 0), "avg_wait": round(float(r["ws"] or 0) / wn, 1) if wn else None,
             "max_wait": round(float(r["wm"] or 0), 1),
             "little": little(obs, float(r["ls"] or 0), int(r["served"] or 0), float(r["ws"] or 0), wn)}
        per.append(x)
        tot["obs"] += obs
        tot["ls"] += float(r["ls"] or 0)
        tot["served"] += x["served"]
        tot["ws"] += float(r["ws"] or 0)
        tot["wn"] += wn
        tot["lng"] += float(r["lng"] or 0)
        tot["wm"] = max(tot["wm"], x["max_wait"])
        tot["mx"] = max(tot["mx"], x["max_len"])
    p90 = None
    ws = [float(r["wait"]) for r in db.rows("SELECT wait FROM queue_waits WHERE t_out >= ? AND t_out < ? ORDER BY wait",
                                            (t0, t1))]
    if ws:
        p90 = ws[min(len(ws) - 1, int(math.ceil(0.9 * len(ws))) - 1)]
    return {"zones": per, "served": tot["served"], "avg_wait": round(tot["ws"] / tot["wn"], 1) if tot["wn"] else None,
            "p90_wait": round(p90, 1) if p90 is not None else None, "max_wait": round(tot["wm"], 1),
            "max_len": tot["mx"], "long_sec": round(tot["lng"]),
            "little": little(tot["obs"], tot["ls"], tot["served"], tot["ws"], tot["wn"])}


def hourly(t0: float, t1: float) -> list[dict]:
    rows = db.rows("SELECT CAST((minute - ?) / 3600 AS INTEGER) AS h, SUM(t_obs) AS obs, SUM(len_sum) AS ls, "
                   "MAX(len_max) AS mx, SUM(served) AS served, SUM(wait_sum) AS ws, SUM(wait_n) AS wn "
                   "FROM queue_minute WHERE minute >= ? AND minute < ? GROUP BY h ORDER BY h", (t0, t0, t1))
    out = []
    for r in rows:
        obs = float(r["obs"] or 0)
        wn = int(r["wn"] or 0)
        out.append({"hour": t0 + int(r["h"]) * 3600, "avg_len": round(float(r["ls"] or 0) / obs, 2) if obs else 0,
                    "max_len": int(r["mx"] or 0), "served": int(r["served"] or 0),
                    "avg_wait": round(float(r["ws"] or 0) / wn, 1) if wn else None})
    return out


def status_text() -> str:
    """Telegram /navbat."""
    st = board()
    if not st:
        if not has_queues():
            return ("🧾 <b>Navbat</b>\n\nKassa navbati zonasi hali chizilmagan. Web panel → Kameralar → Zonalar → "
                    "<b>Kassa navbati</b> (va <b>Kassir joyi</b>).")
        return "🧾 <b>Navbat</b>\n\nNavbat kamerasi hozir ishlamayapti."
    lines = ["🧾 <b>Navbat — hozir</b>", ""]
    for x in st:
        if not x["live"]:
            lines.append(f"⚪️ {esc(x['name'])}: kamera ishlamayapti")
            continue
        icon = {"navbat": "🔴", "kassir_yoq": "🟠", "band": "🟡", "bosh": "🟢", "yopiq": "⚪️"}.get(x["state"], "⚪️")
        extra = ""
        if x["length"]:
            extra = f" · eng uzoq kutayotgan {fmt_dur(x['max_wait'])}"
        cash = "" if x["cashier"] is None else (" · kassir bor" if x["cashier"] else " · kassir yo'q")
        lines.append(f"{icon} {esc(x['name'])}: {x['length']} kishi{extra}{cash}")
    d0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    s = summary(d0, time.time() + 60)
    if s["served"]:
        lines += ["", f"📊 Bugun: {s['served']} xaridor navbatdan o'tdi, o'rtacha kutish "
                      f"{fmt_dur(s['avg_wait'] or 0)}, eng uzoq {fmt_dur(s['max_wait'])}"]
    last = db.row("SELECT * FROM queue_alerts WHERE t >= ? ORDER BY id DESC LIMIT 1", (d0,))
    if last:
        lines.append(f"🔔 Oxirgi signal: {time.strftime('%H:%M', time.localtime(last['t']))} — {esc(last['title'])}"
                     + (f" ({esc(last['zone_name'])})" if last["zone_name"] else ""))
    return "\n".join(lines)
