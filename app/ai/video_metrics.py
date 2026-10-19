"""3.1.0: video tahlili natijasidagi o'lchovlar — navbat, sanash chizig'i, zonalar (bo'limlar) bo'yicha tashriflar.

Bulut panelidagi "Video sinovi"da egasi video kadri ustiga zonalar chizadi (app/ai/zones.py: from_list) yoki kamera
zonalarini tanlaydi. Tahlil tugagach shu yerdagi `summarize` qisqa natija tayyorlaydi (jobs.metrics, bulutga — hello
dagi video_tests hisoboti bilan). Bazaga (jonli statistika) hech narsa yozilmaydi: video tahlili persist=False.

  queue  — har "Kassa navbati" zonasi: eng uzun navbat, o'rtacha uzunlik (vaqt bo'yicha), navbat bo'lgan vaqt ulushi,
           xizmat ko'rsatilganlar (kutish yozilganlar), o'rtacha va eng uzoq kutish, uzunlik grafigi (≤ 60 nuqta).
  lines  — har sanash chizig'i: kirdi / chiqdi (xaridorlar) va xodimlar o'tishi.
  areas  — boshqa zonalar (polka, kassa, eshik...): nechta odam kamida AREA_MIN_SEC turdi (oyog'i zonada yoki polkada —
           qo'li), o'rtacha turish vaqti, polkaga qo'l uzatishlar soni. Bitta kamerada "qaysi bo'limga ko'p qarashdi".
"""
from __future__ import annotations

import numpy as np

AREA_KINDS = ("polka", "kassa", "eshik", "kassir", "xodim")
AREA_MIN_SEC = 2.0          # zonada shundan kam turgan — o'tib ketgan
KP_CONF = 0.3
MAX_DT = 2.0                # AI qadamlari orasidagi bundan katta tanaffus vaqtga qo'shilmaydi
SERIES_MAX = 60


class AreaVisits:
    """Har AI qadamida: kuzatilgan odamlar → zonalar bo'yicha turish vaqti va polkaga qo'l uzatishlar."""

    def __init__(self):
        self.dwell: dict[tuple[int, int], float] = {}      # (zona, iz) -> soniya
        self.reach: dict[int, int] = {}                    # zona -> qo'l uzatishlar
        self.hand_in: dict[tuple[int, int, int], bool] = {}  # (zona, iz, qo'l) -> oldingi qadamda zonada edi
        self.last_t: float | None = None
        self.persons_max = 0
        self.names: dict[int, tuple[str, str]] = {}

    def update(self, tracked: list, t: float, w: int, h: int, zoneset, status: dict | None = None) -> None:
        zones = [z for z in (getattr(zoneset, "zones", None) or []) if z.kind in AREA_KINDS]
        self.persons_max = max(self.persons_max, len(tracked))
        dt = 0.0 if self.last_t is None else t - self.last_t
        if dt < 0 or dt > MAX_DT:
            dt = 0.0
        self.last_t = t
        if not zones:
            return
        for z in zones:
            self.names[z.id] = (z.name, z.kind)
        for p in tracked:
            tid = int(getattr(p, "track_id", -1))
            if tid < 0:
                continue
            box = p.box
            foot = ((float(box[0]) + float(box[2])) / 2.0, float(box[3]))
            kp = getattr(p, "kpts", None)
            wrists = []
            if kp is not None and len(kp) > 10:
                for i in (9, 10):
                    if float(kp[i][2]) >= KP_CONF:
                        wrists.append((i, (float(kp[i][0]), float(kp[i][1]))))
            for z in zones:
                hands_in = {i: z.contains(pt, w, h) for i, pt in wrists}
                inside = z.contains(foot, w, h) or (z.kind == "polka" and any(hands_in.values()))
                if inside and dt:
                    self.dwell[(z.id, tid)] = self.dwell.get((z.id, tid), 0.0) + dt
                if z.kind == "polka":
                    for i, now_in in hands_in.items():
                        key = (z.id, tid, i)
                        if now_in and not self.hand_in.get(key, False):
                            self.reach[z.id] = self.reach.get(z.id, 0) + 1
                        self.hand_in[key] = now_in

    def result(self) -> list[dict]:
        out = []
        for zid, (name, kind) in self.names.items():
            stays = [v for (z, _), v in self.dwell.items() if z == zid and v >= AREA_MIN_SEC]
            out.append({"name": name, "kind": kind, "visitors": len(stays),
                        "avg_dwell": round(sum(stays) / len(stays), 1) if stays else None,
                        "reaches": int(self.reach.get(zid, 0)) if kind == "polka" else None})
        return sorted(out, key=lambda x: (-x["visitors"], x["name"]))


class QueueStats:
    """Har AI qadamida navbat uzunligi (QueueMonitor.queues) — yig'indilar zona bo'yicha. QueueMonitor.trace cheklangan
    (uzun videoda boshi tushib qoladi), bu esa butun video bo'yicha."""

    def __init__(self, duration_est: float = 0.0):
        self.step = max(0.5, float(duration_est) / SERIES_MAX) if duration_est and duration_est > 0 else 2.0
        self.z: dict[int, dict] = {}

    def update(self, qm, t: float) -> None:
        for zid, q in list(getattr(qm, "queues", {}).items()):
            st = self.z.setdefault(int(zid), {"name": q.name, "max": 0, "acc": 0.0, "busy": 0.0, "obs": 0.0,
                                              "last_t": None, "last_len": 0, "buckets": {}})
            st["name"] = q.name
            length = int(getattr(q, "length", 0) or 0)
            if st["last_t"] is not None:
                dt = t - st["last_t"]
                if 0 < dt <= MAX_DT:
                    st["obs"] += dt
                    st["acc"] += st["last_len"] * dt
                    if st["last_len"] >= 1:
                        st["busy"] += dt
            st["last_t"], st["last_len"] = t, length
            st["max"] = max(st["max"], length)
            i = int(max(0.0, t) // self.step)
            st["buckets"][i] = max(st["buckets"].get(i, 0), length)

    def series(self, zid: int) -> list[list[float]]:
        b = (self.z.get(int(zid)) or {}).get("buckets") or {}
        if not b:
            return []
        n = max(b) + 1
        vals = [b.get(i, 0) for i in range(n)]
        if n > SERIES_MAX:                            # taxminiy davomiylik noto'g'ri bo'lsa — bo'laklar birlashtiriladi
            k = -(-n // SERIES_MAX)
            vals = [max(vals[j:j + k]) for j in range(0, n, k)]
            return [[round((j + 0.5) * self.step * k, 1), v] for j, v in enumerate(vals)]
        return [[round((i + 0.5) * self.step, 1), v] for i, v in enumerate(vals)]


def compact(m: dict, limit: int = 16_000) -> dict:
    """O'lchovlar JSON'i chegaradan katta bo'lsa — avval grafiklar, keyin zonalar ro'yxati qisqartiriladi (hammasi
    tashlab yuborilmaydi)."""
    import json

    def size() -> int:
        return len(json.dumps(m, ensure_ascii=False))
    if size() <= limit:
        return m
    for q in m.get("queue") or []:
        q["series"] = q.get("series", [])[::2]
    if size() > limit:
        for q in m.get("queue") or []:
            q["series"] = []
    while size() > limit and m.get("areas"):
        m["areas"] = m["areas"][:-1]
    while size() > limit and len(m.get("queue") or []) > 1:
        m["queue"] = m["queue"][:-1]
    return m


def _series(points: list[tuple[float, int]], duration: float) -> list[list[float]]:
    """Navbat uzunligi grafigi: ≤ SERIES_MAX bo'lak, har bo'lakda eng katta qiymat."""
    if not points or duration <= 0:
        return []
    n = min(SERIES_MAX, max(1, int(duration // 2) or 1))
    step = duration / n
    vals = [0] * n
    for t, v in points:
        i = min(n - 1, max(0, int(t // step)))
        vals[i] = max(vals[i], int(v))
    return [[round((i + 0.5) * step, 1), vals[i]] for i in range(n)]


def summarize(proc, areas: AreaVisits | None, duration: float, zoneset=None, qstats: QueueStats | None = None) -> dict:
    """Video tugagach (FileJob): navbat, sanash, zonalar. Hech narsa o'lchanmagan bo'lsa ham — bo'sh ro'yxatlar.
    qstats — butun video bo'yicha navbat yig'indilari (bo'lmasa — QueueMonitor.trace dan)."""
    out: dict = {"duration": round(float(duration or 0), 1), "queue": [], "lines": [], "areas": [],
                 "zones": len(getattr(zoneset, "zones", None) or [])}
    counter = getattr(proc, "counter", None)
    if counter is not None:
        lines: dict[int, dict] = {}
        for z in (getattr(zoneset, "zones", None) or []):
            if z.kind == "chiziq":
                lines[int(z.id)] = {"name": z.name, "in": 0, "out": 0, "staff": 0}
        for c in getattr(counter, "events", []) or []:
            d = lines.setdefault(int(c.line_id), {"name": c.line_name, "in": 0, "out": 0, "staff": 0})
            if c.staff:
                d["staff"] += 1
            elif c.dir in ("in", "out"):
                d[c.dir] += 1
        out["lines"] = list(lines.values())
        qm = getattr(counter, "queue", None)
        if qm is not None:
            qm.close()
            names = {int(zid): q.name for zid, q in getattr(qm, "queues", {}).items()}
            for z in (getattr(zoneset, "zones", None) or []):
                if z.kind == "navbat":
                    names.setdefault(int(z.id), z.name)
            by_zone: dict[int, list] = {}
            for t, zid, length, _raw in list(qm.trace):
                by_zone.setdefault(int(zid), []).append((float(t), int(length)))
            for zid, name in sorted(names.items()):
                st = qstats.z.get(zid) if qstats is not None else None
                if st is not None:
                    obs, busy, acc, mx, ser = st["obs"], st["busy"], st["acc"], st["max"], qstats.series(zid)
                else:
                    pts = by_zone.get(zid, [])
                    obs = busy = acc = 0.0
                    for (t0, l0), (t1, _l1) in zip(pts, pts[1:]):
                        dt = t1 - t0
                        if dt <= 0 or dt > MAX_DT:
                            continue
                        obs += dt
                        acc += l0 * dt
                        if l0 >= 1:
                            busy += dt
                    mx, ser = max((v for _, v in pts), default=0), _series(pts, float(duration or 0))
                waits = [float(w["wait"]) for w in qm.waits if int(w["zone_id"]) == zid]
                out["queue"].append({
                    "name": name, "max_len": int(mx),
                    "avg_len": round(acc / obs, 2) if obs else 0.0,
                    "busy_pct": round(busy / obs, 3) if obs else 0.0,
                    "served": len(waits), "avg_wait": round(float(np.mean(waits)), 1) if waits else None,
                    "max_wait": round(max(waits), 1) if waits else None,
                    "series": ser})
    if areas is not None:
        out["areas"] = areas.result()
        out["persons_max"] = int(areas.persons_max)
    return out


def brief(m: dict | None) -> list[str]:
    """Telegram va panel uchun qisqa qatorlar (oddiy matn)."""
    if not m:
        return []
    lines = []
    for q in m.get("queue") or []:
        s = f"🧾 {q['name']}: eng uzun navbat {q['max_len']} kishi"
        if q.get("served"):
            s += f", {q['served']} xaridor o'tdi, o'rtacha kutish {_dur(q.get('avg_wait'))}"
        lines.append(s)
    for ln in m.get("lines") or []:
        lines.append(f"🚪 {ln['name']}: kirdi {ln['in']}, chiqdi {ln['out']}" + (f" (xodim {ln['staff']})" if ln.get("staff") else ""))
    areas = [a for a in (m.get("areas") or []) if a.get("kind") == "polka"]
    if areas:
        top = ", ".join(f"{a['name']} {a['visitors']}" for a in areas[:4])
        lines.append(f"🗺 Bo'limlar (to'xtaganlar): {top}")
    return lines


def _dur(sec) -> str:
    """Bulutdagi cloud/notify.fmt_dur bilan bir xil (Telegram matni ikkala botda bir xil bo'lsin)."""
    sec = max(0, int(float(sec or 0)))
    if sec >= 3600:
        return f"{sec // 3600} soat {sec % 3600 // 60} daqiqa"
    if sec >= 90:
        return f"{round(sec / 60)} daqiqa"
    return f"{sec} soniya"
