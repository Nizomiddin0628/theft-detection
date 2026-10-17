"""Odamlarni sanash (3-bosqich, 1.9.0): kirish/chiqish chizig'i, joriy son, xodimlarni hisobdan chiqarish.

Kamera zonalari sahifasida **sanash chizig'i** chiziladi: ikki nuqta, eshik ostonasida (odam oyoqlari o'tadigan joyda).
Chiziq yo'nalishli: strelka "ichkari" tomonni ko'rsatadi; "Yo'nalishni almashtirish" — nuqtalar o'rnini almashtiradi.
Odam oyog'i (ramka pastining o'rtasi) chiziqning bir tomonidan ikkinchisiga ishonchli o'tsa — bitta o'tish:
tashqaridan ichkariga — **kirdi**, teskari — **chiqdi**.

Ishonchlilik:
  * Gisterezis: chiziq atrofida (odam bo'yining ~10% i) "chegara" yo'lagi bor — shu yo'lakda turgan, oyog'i qimirlayotgan
    odam ikki marta sanalmaydi; tomon faqat yo'lakdan chiqqanda almashadi.
  * Faqat chiziq kesmasi: o'tish nuqtasi chiziq uchlaridan tashqarida bo'lsa (yonidan aylanib o'tgan) — sanalmaydi.
  * Kuzatuv uzilishi: ID yo'qolib, yaqin joyda yangi ID paydo bo'lsa (1.2 s ichida) — oldingi holat (qaysi tomonda
    edi) meros olinadi: odam to'sib qolganda yoki kuzatuvchi ID almashtirganda ikki marta / umuman sanalmay qolmasin.
    Ulash PROBATION_SEC "sinovda": shu orada eski ID qaytsa (detektor bir kadr ko'rmagan, yonida boshqa odam paydo
    bo'lgan ekan) — holat egasiga qaytadi, yangi ID noldan boshlaydi, sinovdagi o'tishlar sanalmaydi. Avval har ID
    o'z holatini oladi, keyin yangi ID'lar eng yaqin juftlar bo'yicha ulanadi (ro'yxat tartibi ahamiyatsiz).
    Faqat ishonchli ulash (keyingi kadr, juda yaqin) "bitta odam" deb olinadi — xodim belgisi va avvalgi o'tishlar
    shundagina meros bo'ladi (eshikdan ketma-ket o'tgan ikki kishi bir-biriga "xodim" belgisini yuqtirmasin).
  * Chiziq o'zgarsa (tahrirlandi, yo'nalishi almashtirildi) — shu chiziqdagi holatlar tashlanadi (soxta o'tish yo'q).
  * Sanashdagi har qanday xato kamera tahliliga (o'g'irlikni aniqlashga) ta'sir qilmaydi — update() xatoni yutadi.
  * Xodim: "xodim" zonasida 2 s turgan odam (analyzer belgisi) — o'tishlari xodim deb sanaladi, xaridorlarga
    kirmaydi; keyin xodim ekani ma'lum bo'lsa ham (ertalab eshikdan kirib, peshtaxta ortiga o'tsa) — shu odamning
    avvalgi o'tishlari qayta belgilanadi. Ish vaqtidan tashqaridagi o'tishlar (sozlama `work_hours`, masalan
    09:00-22:00) alohida — xaridorlar soniga kirmaydi.

Hisob: "kirdi/chiqdi" (xaridorlar) — xodimsiz va ish vaqtida. "Hozir ichkarida" va kun oxiri farqi — xodimsiz,
lekin ish vaqtidan qat'i nazar (22:00 dan keyin chiqqan xaridor ham ichkaridan chiqdi). "Ichkarida" do'kon yopiq
paytining o'rtasida (09:00-22:00 bo'lsa 03:30 da; ish vaqti berilmasa yarim tunda) yoki qo'lda "0 qilish" dan beri.

Saqlash: har o'tish `counts` jadvaliga (90 kun), daqiqalik yig'indi `metrics_minute` ga (kirdi/chiqdi — bulutga
ham ketadi). Video tahlili va aniqlik sinovida (persist=False) bazaga yozilmaydi — o'tishlar ro'yxati qaytariladi.
"""
from __future__ import annotations

import dataclasses
import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .. import db
from ..logutil import get_logger

log = get_logger("counting")

LINE_KIND = "chiziq"
BAND_FRAC = 0.10          # chegara yo'lagi: odam bo'yining shuncha qismi (oyoq nuqtasi silkinishi)
BAND_MIN_PX = 4.0
SEG_TOL = 0.08            # chiziq uchlaridan shuncha (chiziq uzunligiga nisbatan) tashqarida ham sanaladi
STITCH_SEC = 1.2          # yo'qolgan izni yangi ID bilan ulash muddati
STITCH_DIST = 0.8         # ... masofa: odam bo'yiga nisbatan
SURE_SEC = 0.35           # ishonchli ulash ("bitta odam"): shu vaqt ichida va
SURE_DIST = 0.5           # ... shuncha masofada (odam bo'yiga nisbatan)
PROBATION_SEC = 0.8       # ulashdan keyin shu vaqt ichida eski ID qaytsa (detektor bir-ikki kadr ko'rmagan ekan) —
                          # ulash bekor, shu oraliqdagi o'tishlar sanalmaydi; qaytmasa — tasdiqlanadi
STATE_TTL = 10.0          # ko'rinmay qolgan iz holati shuncha vaqtdan keyin tashlanadi
KEEP_DAYS = 90


@dataclass
class Line:
    id: int
    name: str
    p1: tuple[float, float]   # 0..1
    p2: tuple[float, float]

    def px(self, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
        return (np.array([self.p1[0] * w, self.p1[1] * h], dtype=np.float64),
                np.array([self.p2[0] * w, self.p2[1] * h], dtype=np.float64))


@dataclass
class Crossing:
    line_id: int
    line_name: str
    dir: str                  # "in" | "out"
    t: float
    track_id: int
    staff: bool = False
    offhours: bool = False
    x: float = 0.0            # o'tish nuqtasi (0..1)
    y: float = 0.0
    chain: int = 0            # bitta odam (ID uzilsa ham) — xodim qayta belgilash uchun
    db_id: int | None = None


@dataclass
class _St:
    side: int = 0                     # -1 / +1 — ishonchli tomon, 0 — hali noma'lum
    conf_pt: tuple = (0.0, 0.0)       # oxirgi ishonchli nuqta (px)
    last_pt: tuple = (0.0, 0.0)
    prev_pt: tuple | None = None
    last_t: float = 0.0
    prev_t: float = 0.0
    h: float = 0.0
    chain: int = 0
    staff: bool = False
    crossings: list = field(default_factory=list)
    # Ulash sinov muddatida (PROBATION_SEC): eski ID holatining nusxasi, uning ID si va shu paytdagi o'tishlar
    origin: "_St | None" = None
    from_tid: int = -1
    stitch_t: float = 0.0
    sure: bool = False
    pending: list = field(default_factory=list)


def lines_from(zoneset) -> list[Line]:
    out = []
    for z in getattr(zoneset, "zones", []) or []:
        if z.kind == LINE_KIND and len(z.pts) >= 2:
            out.append(Line(int(z.id), z.name, (float(z.pts[0][0]), float(z.pts[0][1])),
                            (float(z.pts[1][0]), float(z.pts[1][1]))))
    return out


def side_of(a: np.ndarray, b: np.ndarray, p) -> float:
    """Musbat — "ichkari" (a→b vektorining o'ng tomoni rasm koordinatalarida: y pastga)."""
    return float((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / max(float(np.hypot(*(b - a))), 1e-6)


def _seg_param(a: np.ndarray, b: np.ndarray, p0, p1) -> tuple[float, float] | None:
    """Harakat kesmasi p0→p1 va chiziq kesishgan joy: (chiziq bo'ylab parametr: 0 — a, 1 — b;
    harakat bo'ylab parametr: 0 — p0, 1 — p1). Parallel bo'lsa None."""
    d = b - a
    m = np.array(p1, dtype=np.float64) - np.array(p0, dtype=np.float64)
    den = d[0] * m[1] - d[1] * m[0]
    if abs(den) < 1e-9:
        return None
    q = np.array(p0, dtype=np.float64) - a
    return float((q[0] * m[1] - q[1] * m[0]) / den), float((q[0] * d[1] - q[1] * d[0]) / den)


def in_work_hours(t: float, spec: str) -> bool:
    """spec: "09:00-22:00" (kechasi o'tadigan "20:00-02:00" ham), bo'sh — har doim ish vaqti."""
    spec = (spec or "").strip()
    if not spec or "-" not in spec:
        return True
    try:
        a, b = [x.strip() for x in spec.split("-", 1)]
        ah, am = (int(v) for v in a.split(":"))
        bh, bm = (int(v) for v in b.split(":"))
    except ValueError:
        return True
    lt = time.localtime(t)
    cur = lt.tm_hour * 60 + lt.tm_min
    s_, e_ = ah * 60 + am, bh * 60 + bm
    if s_ == e_:
        return True                      # "09:00-09:00" — kecha-kunduz
    return s_ <= cur < e_ if s_ < e_ else (cur >= s_ or cur < e_)


# "Hozir ichkarida" boshi va biznes kuni — app/count_eval.py da (darvoza skripti ham o'sha hisobni ishlatadi)
from ..count_eval import business_day, occupancy_start  # noqa: E402,F401


class LineCounter:
    """Bitta kamera uchun. update() har AI qadamida (kuzatilgan odamlar bilan) chaqiriladi."""

    def __init__(self, source_id: int | None, source_name: str = "", persist: bool = True):
        self.source_id = source_id
        self.source_name = source_name
        self.persist = persist and bool(source_id)
        # Video tahlili / aniqlik sinovi — o'tishlar ro'yxatda qaytariladi. Jonli kamerada persist keyin o'chirilsa
        # (video fayl qaytadan aylanmoqda) — o'tishlar yig'ilmaydi (xotira o'smasin)
        self.collect = not self.persist
        self.states: dict[tuple[int, int], _St] = {}       # (chiziq, iz) -> holat
        self.lost: dict[tuple[int, int], _St] = {}         # yo'qolgan izlar (ulash uchun)
        self.events: list[Crossing] = []                   # persist=False bo'lsa — hammasi shu yerda
        self._chain = 0
        self.geom: dict[int, tuple] = {}                   # chiziq -> nuqtalari (o'zgarsa holatlar tashlanadi)
        self._err_t = 0.0
        self.queue = None                                  # 2.0.0: kassa navbati (app/ai/queues.py) — zonasi bo'lsa
        self._qerr_t = 0.0
        self.flow = None                                   # 2.1.0: oqim va heatmap (app/ai/flow.py) — kalibrlangan bo'lsa
        self._ferr_t = 0.0

    def _new_chain(self) -> int:
        self._chain += 1
        return self._chain

    def update(self, tracked: list, t: float, w: int, h: int, zoneset=None, status: dict | None = None,
               work_hours: str | None = None) -> list[Crossing]:
        """Har AI qadamida. Xato bo'lsa ham kamera tahlili to'xtamaydi (xato jurnalga, daqiqada bir marta).
        2.0.0: shu yerda kassa navbati ham o'lchanadi (worker.py ga yangi ulanish kerak emas); navbatdagi xato sanashga,
        sanashdagi xato navbatga ta'sir qilmaydi. 2.1.0: oqim va heatmap ham shu yerda (app/ai/flow.py)."""
        try:
            out = self._update(tracked, t, w, h, zoneset, status, work_hours)
        except Exception:  # noqa: BLE001
            out = []
            if time.time() - self._err_t > 60:
                self._err_t = time.time()
                log.exception("Sanash xatosi (%s) — kamera tahlili davom etadi", self.source_name)
        if self.queue is not None or any(getattr(z, "kind", "") == "navbat" for z in getattr(zoneset, "zones", []) or []):
            try:
                if self.queue is None:
                    from . import queues

                    self.queue = queues.QueueMonitor(self.source_id, self.source_name, persist=self.persist)
                self.queue.source_name = self.source_name
                self.queue.update(tracked, t, w, h, zoneset, status, persist=self.persist)
            except Exception:  # noqa: BLE001
                if time.time() - self._qerr_t > 60:
                    self._qerr_t = time.time()
                    log.exception("Navbat o'lchash xatosi (%s) — kamera tahlili davom etadi", self.source_name)
        # 2.1.0: oqim va heatmap — kamera do'kon rejasiga kalibrlangan bo'lsa (xatosi sanash va navbatga ta'sir qilmaydi)
        try:
            if self.flow is None and self.source_id:
                from . import flow

                if flow.has_calib(self.source_id):
                    self.flow = flow.FlowMonitor(self.source_id, self.source_name, persist=self.persist)
            if self.flow is not None:
                self.flow.source_name = self.source_name
                self.flow.update(tracked, t, w, h, zoneset, status, persist=self.persist)
        except Exception:  # noqa: BLE001
            if time.time() - self._ferr_t > 60:
                self._ferr_t = time.time()
                log.exception("Oqim o'lchash xatosi (%s) — kamera tahlili davom etadi", self.source_name)
        return out

    def _update(self, tracked: list, t: float, w: int, h: int, zoneset, status: dict | None,
                work_hours: str | None) -> list[Crossing]:
        lines = lines_from(zoneset)
        if not lines:
            if self.states or self.lost:
                self.states.clear()
                self.lost.clear()
            return []
        status = status or {}
        # Chiziq tahrirlangan yoki yo'nalishi almashtirilgan — eski "qaysi tomonda" ma'lumoti endi noto'g'ri
        for ln in lines:
            g = (ln.p1, ln.p2)
            if self.geom.get(ln.id) != g:
                if ln.id in self.geom:
                    for d in (self.states, self.lost):
                        for k in [k for k in d if k[0] == ln.id]:
                            d.pop(k, None)
                self.geom[ln.id] = g
        # Shu qadamda ko'rinmagan izlar darhol "yo'qolganlar"ga o'tadi: kuzatuvchi ID ni almashtirsa (eski ID yo'qolib,
        # o'sha kadrda yangisi paydo bo'lsa) yangi ID ular bilan ulanadi. Shu ID qaytsa — o'z holati qaytadi.
        feet: dict[int, tuple] = {}
        for p in tracked:
            tid = int(getattr(p, "track_id", -1))
            if tid < 0:
                continue
            box = p.box
            feet[tid] = ((float(box[0] + box[2]) / 2.0, float(box[3])), max(float(box[3] - box[1]), 1.0))
        for key in [k for k in self.states if k[1] not in feet]:
            self.lost[key] = self.states.pop(key)
        if work_hours is None:
            work_hours = db.get_setting("work_hours", "") if self.persist else ""
        out: list[Crossing] = []
        for ln in lines:
            a, b = ln.px(w, h)
            # 1) Avval har ID o'z holatini oladi (bir-ikki kadr ko'rinmay qaytgan ham; uning holatini sinov muddatida
            #    boshqa yangi ID olgan bo'lsa — qaytarib olinadi). Tartib muhim emas: yangi ID'lar keyin ulanadi
            fresh = []
            for tid in feet:
                key = (ln.id, tid)
                if key in self.states:
                    continue
                st = self.lost.pop(key, None) or self._reclaim(ln.id, tid)
                if st is not None:
                    self.states[key] = st
                else:
                    fresh.append(tid)
            # 2) Yangi ID'lar — yaqinda yo'qolgan izlar bilan (eng yaqin juftlar avval), bo'lmasa — yangi odam
            for tid, st in self._stitch_all(ln.id, [(tid, *feet[tid]) for tid in fresh], t).items():
                self.states[(ln.id, tid)] = st
            for tid in fresh:
                self.states.setdefault((ln.id, tid), _St(chain=self._new_chain()))
            # 3) Harakat
            for tid, (foot, bh) in feet.items():
                st = self.states[(ln.id, tid)]
                st.prev_pt, st.prev_t = st.last_pt if st.last_t else None, st.last_t
                st.last_pt, st.last_t, st.h = foot, t, bh
                if (status.get(tid) or {}).get("staff") and not st.staff:
                    st.staff = True
                    self._restaff(st)
                d = side_of(a, b, foot)
                band = max(BAND_FRAC * bh, BAND_MIN_PX)
                if abs(d) < band:
                    continue                              # chegara yo'lagida — tomon o'zgarmaydi
                s = 1 if d > 0 else -1
                if st.side == 0:
                    st.side, st.conf_pt = s, foot
                    continue
                if s == st.side:
                    st.conf_pt = foot
                    continue
                uv = _seg_param(a, b, st.conf_pt, foot)
                st_side_prev, st.side = st.side, s
                st.conf_pt = foot
                if uv is None or not (-SEG_TOL <= uv[0] <= 1 + SEG_TOL) or not (-0.01 <= uv[1] <= 1.01):
                    continue                              # chiziq yonidan aylanib o'tdi (yoki chiziq kesilmagan)
                xr = (a + (b - a) * min(max(uv[0], 0.0), 1.0))
                c = Crossing(ln.id, ln.name, "in" if st_side_prev < 0 else "out", t, tid, st.staff,
                             not in_work_hours(t, work_hours), float(xr[0] / max(w, 1)), float(xr[1] / max(h, 1)),
                             st.chain)
                if st.origin is not None:
                    st.pending.append(c)                  # ulash hali tasdiqlanmagan — kutadi
                else:
                    st.crossings.append(c)
                    out.append(c)
        # 4) Sinov muddati o'tgan ulashlar tasdiqlanadi (eski ID qaytmadi — haqiqatan ID almashgan)
        for dct in (self.states, self.lost):
            for st in dct.values():
                if st.origin is not None and t - st.stitch_t >= PROBATION_SEC:
                    out.extend(self._confirm(st))
        # Juda eski yo'qolgan izlar tashlanadi
        for key in [k for k, st in self.lost.items() if t - st.last_t > STATE_TTL]:
            st = self.lost.pop(key)
            if st.origin is not None:
                out.extend(self._confirm(st))
        if out:
            out.sort(key=lambda c: c.t)
            self._emit(out)
        return out

    def _stitch_all(self, line_id: int, fresh: list[tuple], t: float) -> dict[int, _St]:
        """Yangi ID'larni yaqinda yo'qolgan izlar bilan ulash: barcha juftlar masofa bo'yicha, eng yaqini avval
        (ro'yxat tartibi ahamiyatsiz). Ulangan holat sinov muddatida: eski ID qaytsa — bekor qilinadi."""
        if not fresh:
            return {}
        pairs = []
        for key, st in self.lost.items():
            if key[0] != line_id or st.origin is not None or not (0 <= t - st.last_t <= STITCH_SEC):
                continue
            px_, py_ = st.last_pt
            if st.prev_pt is not None and st.last_t > st.prev_t:
                dt = min(t - st.last_t, 0.6)
                vx = (st.last_pt[0] - st.prev_pt[0]) / (st.last_t - st.prev_t)
                vy = (st.last_pt[1] - st.prev_pt[1]) / (st.last_t - st.prev_t)
                px_, py_ = px_ + vx * dt, py_ + vy * dt
            for tid, foot, bh in fresh:
                dist = math.hypot(foot[0] - px_, foot[1] - py_)
                if dist <= STITCH_DIST * max(bh, st.h):
                    pairs.append((dist, tid, key, bh))
        res: dict[int, _St] = {}
        used: set = set()
        for dist, tid, key, bh in sorted(pairs):
            if tid in res or key in used:
                continue
            used.add(key)
            old = self.lost.pop(key)
            # Faqat "qaysi tomonda edi" darhol meros; xodim belgisi va avvalgi o'tishlar — tasdiqlangandan keyin va
            # ishonchli ulashda (keyingi kadr, juda yaqin) — aks holda boshqa odam bo'lishi mumkin
            res[tid] = _St(side=old.side, conf_pt=old.conf_pt, last_pt=old.last_pt, last_t=old.last_t,
                           prev_t=old.prev_t, h=old.h, chain=self._new_chain(),
                           origin=dataclasses.replace(old, crossings=list(old.crossings)), from_tid=key[1],
                           stitch_t=t, sure=(t - old.last_t) <= SURE_SEC and dist <= SURE_DIST * max(bh, old.h))
        return res

    def _reclaim(self, line_id: int, tid: int) -> _St | None:
        """Eski ID qaytdi (detektor bir-ikki kadr ko'rmagan ekan), uning holatini sinov muddatida yangi ID olgan edi:
        holat egasiga qaytadi, yangi ID esa boshqa odam — noldan boshlaydi (sinovdagi o'tishlari sanalmaydi)."""
        for dct in (self.states, self.lost):
            for key, st in dct.items():
                if key[0] == line_id and st.origin is not None and st.from_tid == tid:
                    dct[key] = _St(last_pt=st.last_pt, last_t=st.last_t, h=st.h, chain=self._new_chain())
                    return st.origin
        return None

    def _confirm(self, st: _St) -> list[Crossing]:
        """Ulash tasdiqlandi: ishonchli bo'lsa — bitta odam (xodim belgisi va avvalgi o'tishlar birlashadi)."""
        o, st.origin = st.origin, None
        if o is not None and st.sure:
            st.chain = o.chain
            st.crossings = o.crossings + st.crossings
            st.staff = st.staff or o.staff
        pend, st.pending = st.pending, []
        for c in pend:
            c.chain = st.chain
            c.staff = c.staff or st.staff
        st.crossings.extend(pend)
        if st.staff:
            self._restaff(st)
        return pend

    def _restaff(self, st: _St) -> None:
        """Odam xodim ekani keyin ma'lum bo'ldi — uning shu chiziqdagi avvalgi o'tishlari xodimga o'tadi."""
        changed = [c for c in st.crossings if not c.staff]
        for c in changed:
            c.staff = True
        if changed and self.persist:
            ids = [c.db_id for c in changed if c.db_id]
            if ids:
                try:
                    store().mark_staff(ids)
                except Exception:  # noqa: BLE001
                    log.exception("Xodim o'tishlarini qayta belgilab bo'lmadi")

    def _emit(self, crossings: list[Crossing]) -> None:
        if not self.persist:
            if self.collect:
                self.events.extend(crossings)
            return
        try:
            store().add(self.source_id, self.source_name, crossings)
        except Exception:  # noqa: BLE001
            log.exception("O'tishlarni yozib bo'lmadi")


# ============================================================================ saqlash va hisobot
class CountStore:
    """counts jadvali: har o'tish. Daqiqalik yig'indi metrics.py da (minute_counts) olinadi."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last_prune = 0.0

    def add(self, source_id: int | None, source_name: str, crossings: list[Crossing]) -> None:
        with self.lock, db.tx() as c:
            for x in crossings:
                cur = c.execute("INSERT INTO counts(t, source_id, source_name, line_id, line_name, dir, track_id, staff, "
                                "offhours, x, y) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (x.t, source_id, source_name, x.line_id, x.line_name, x.dir, x.track_id,
                                 int(x.staff), int(x.offhours), round(x.x, 4), round(x.y, 4)))
                x.db_id = cur.lastrowid
        # AI kechiksa o'tish daqiqasi allaqachon yopilgan (yozilgan) bo'lishi mumkin — o'sha daqiqa to'g'rilanadi
        now_m = int(time.time() // 60) * 60
        late = {int(x.t // 60) * 60 for x in crossings if x.t < now_m}
        if late and source_id is not None:
            try:
                from .. import metrics

                for m in sorted(late):
                    metrics.recount(int(source_id), m)
            except Exception:  # noqa: BLE001
                log.exception("Kechikkan o'tish daqiqasini to'g'rilab bo'lmadi")
        for x in crossings:
            log.info("Sanash: %s %s (%s%s)", source_name, "kirdi" if x.dir == "in" else "chiqdi", x.line_name,
                     ", xodim" if x.staff else (", ish vaqtidan tashqari" if x.offhours else ""))
        if time.time() - self.last_prune > 3600:
            self.last_prune = time.time()
            db.execute("DELETE FROM counts WHERE t < ?", (time.time() - KEEP_DAYS * 86400,))

    def mark_staff(self, ids: list[int]) -> None:
        if not ids:
            return
        q = ",".join("?" * len(ids))
        rows = db.rows(f"SELECT DISTINCT source_id, CAST(t / 60 AS INTEGER) * 60 AS minute FROM counts WHERE id IN ({q})",
                       ids)
        db.execute(f"UPDATE counts SET staff=1 WHERE id IN ({q})", ids)
        # O'tgan daqiqalar yig'indisi (va bulutdagi nusxasi) to'g'rilanadi
        try:
            from .. import metrics

            for r in rows:
                metrics.recount(int(r["source_id"] or 0), int(r["minute"]))
        except Exception:  # noqa: BLE001
            log.exception("Daqiqalik sanashni to'g'rilab bo'lmadi")


_store: CountStore | None = None
_store_lock = threading.Lock()


def store() -> CountStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CountStore()
        return _store


def minute_counts(source_id: int, minute: int) -> dict:
    """Bir daqiqa (minute..minute+60) bo'yicha: xaridorlar kirdi/chiqdi (ish vaqtida), xodimlar, ish vaqtidan
    tashqari (jami va yo'nalish bo'yicha — "ichkarida" hisobi uchun)."""
    r = db.row("SELECT "
               "SUM(dir='in' AND staff=0 AND offhours=0) AS cin, SUM(dir='out' AND staff=0 AND offhours=0) AS cout, "
               "SUM(dir='in' AND staff=1) AS sin, SUM(dir='out' AND staff=1) AS sout, "
               "SUM(dir='in' AND offhours=1 AND staff=0) AS oin, SUM(dir='out' AND offhours=1 AND staff=0) AS oout, "
               "COUNT(*) AS n FROM counts WHERE source_id=? AND t>=? AND t<?", (source_id, minute, minute + 60)) or {}
    if not r.get("n"):
        return {}
    oin, oout = int(r["oin"] or 0), int(r["oout"] or 0)
    return {"in": int(r["cin"] or 0), "out": int(r["cout"] or 0), "staff_in": int(r["sin"] or 0),
            "staff_out": int(r["sout"] or 0), "offhours": oin + oout, "off_in": oin, "off_out": oout}


def hook_ok() -> bool:
    """Kamera jarayoni (app/ai/worker.py) sanashga ulanganmi — scripts/apply_counting_hook.py."""
    try:
        from . import worker
        return "counter" in getattr(worker.Processor, "__dataclass_fields__", {})
    except Exception:  # noqa: BLE001
        return False


def has_lines(source_id: int) -> bool:
    return bool(db.scalar("SELECT 1 FROM zones WHERE source_id=? AND kind=? AND enabled=1 LIMIT 1",
                          (source_id, LINE_KIND)))


def day_start(t: float | None = None) -> float:
    lt = time.localtime(t or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def summary(t0: float, t1: float, source_id: int | None = None) -> dict:
    """Oraliq bo'yicha: xaridorlar kirdi/chiqdi, xodimlar, ish vaqtidan tashqari, soatlik taqsimot, eng gavjum soat."""
    where, params = "t>=? AND t<?", [t0, t1]
    if source_id is not None:
        where += " AND source_id=?"
        params.append(source_id)
    tot = db.row("SELECT SUM(dir='in' AND staff=0 AND offhours=0) AS cin, SUM(dir='out' AND staff=0 AND offhours=0) AS cout, "
                 "SUM(dir='in' AND staff=0) AS ain, SUM(dir='out' AND staff=0) AS aout, "
                 "SUM(staff=1) AS staff, SUM(offhours=1 AND staff=0) AS oh FROM counts WHERE " + where, params) or {}
    hours = db.rows("SELECT CAST((t - ?) / 3600 AS INTEGER) AS h, SUM(dir='in' AND staff=0 AND offhours=0) AS cin, "
                    "SUM(dir='out' AND staff=0 AND offhours=0) AS cout FROM counts WHERE " + where +
                    " GROUP BY h ORDER BY h", [t0] + params)
    cin, cout = int(tot.get("cin") or 0), int(tot.get("cout") or 0)
    ain, aout = int(tot.get("ain") or 0), int(tot.get("aout") or 0)     # xodimsiz, ish vaqtidan qat'i nazar
    busiest = max(hours, key=lambda r: r["cin"] or 0) if hours else None
    return {"in": cin, "out": cout, "all_in": ain, "all_out": aout,
            "staff": int(tot.get("staff") or 0), "offhours": int(tot.get("oh") or 0),
            "inside": max(0, ain - aout), "drift_pct": round(abs(ain - aout) / ain * 100, 1) if ain else None,
            "hourly": [{"hour": t0 + int(r["h"]) * 3600, "in": int(r["cin"] or 0), "out": int(r["cout"] or 0)}
                       for r in hours],
            "busiest_hour": (t0 + int(busiest["h"]) * 3600) if busiest and busiest["cin"] else None}


def daily(days: int = 30, source_id: int | None = None) -> list[dict]:
    """Kunlik jadval (oxirgi N kun, mahalliy vaqt bo'yicha). Kirdi/chiqdi — kalendar kuni; kun oxiri farqi — biznes
    kunida (count_eval.business_day: yopilgandan keyin, yarim tundan keyin chiqqanlar ham)."""
    out = []
    end = day_start() + 86400
    spec = db.get_setting("work_hours", "")
    for i in range(days):
        t1 = end - i * 86400
        t0 = day_start(t1 - 43200)
        s = summary(t0, t1, source_id)
        b0, b1 = business_day(t0, spec)
        bs = summary(b0, b1, source_id) if (b0, b1) != (t0, t1) else s
        out.append({"day": t0, "in": s["in"], "out": s["out"], "all_in": bs["all_in"], "all_out": bs["all_out"],
                    "staff": s["staff"], "offhours": s["offhours"], "drift_pct": bs["drift_pct"],
                    "busiest_hour": s["busiest_hour"]})
    return out
