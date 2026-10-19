"""Filiallarni taqqoslash va haftalik xulosa (2.4.0, 8-bosqich) — rahbariyat uchun.

Davr (mahalliy vaqt, CLOUD_TZ_OFFSET): kun yoki hafta (dushanba–yakshanba); solishtirish — bir hafta oldingi xuddi
shunday davr. Har filial: tashriflar (+/- %), navbat (o'rtacha kutish, xizmat), o'g'irlik signallari (✅/❌/javobsiz,
xato ulushi), xavfsizlik signallari, edge ishlagan vaqt ulushi (daqiqalik ko'rsatkich kelgan daqiqalar). Tavsiyalar —
filiallararo oddiy qoidalar (boshqalardan ancha yomon ko'rsatkich, aloqasiz vaqt, javobsiz signallar).
Hisob SQL'da (SQLite va PostgreSQL bir xil), numpy kerak emas.
"""
from __future__ import annotations

import time

from . import db as cdb

SAFETY_KINDS = ("fall", "lying", "night", "crowd", "blocked", "fire", "fight")
WEEKDAYS = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")
WAIT_RATIO, WAIT_MIN, WAIT_SERVED = 1.5, 60.0, 30
FALSE_SHARE, FALSE_MIN = 0.6, 5
UNREV_MIN, UNREV_SHARE = 5, 0.3
VISIT_DROP, VISIT_BASE = -20.0, 100
UPTIME_MIN = 0.9


def day0(t: float, off: int) -> float:
    return (int(t + off) // 86400) * 86400 - off


def period(kind: str, off: int, now: float | None = None, date_t: float | None = None) -> tuple[float, float]:
    """kind: day | week. date_t berilsa — o'sha kun / o'sha kun tushgan hafta; aks holda oxirgi tugagan davr (kecha /
    o'tgan dushanba–yakshanba — seshanba kuni ham joriy emas, o'tgan hafta)."""
    now = time.time() if now is None else now
    if kind == "week":
        d = day0(date_t if date_t is not None else now, off)
        wd = int((d + off) // 86400 + 3) % 7                     # 1970-01-01 — payshanba (3)
        w0 = d - wd * 86400
        if date_t is None:
            w0 -= 7 * 86400
        return w0, w0 + 7 * 86400
    d = day0(date_t, off) if date_t is not None else day0(now, off) - 86400
    return d, d + 86400


def label(kind: str, t0: float, t1: float, off: int) -> str:
    g = time.gmtime(t0 + off)
    if kind == "week":
        e = time.gmtime(t1 - 1 + off)
        return f"{g.tm_mday:02d}.{g.tm_mon:02d}–{e.tm_mday:02d}.{e.tm_mon:02d}.{e.tm_year}"
    return f"{g.tm_mday:02d}.{g.tm_mon:02d}.{g.tm_year}, {WEEKDAYS[g.tm_wday].lower()}"


def _pct(cur: float, prev: float, base: float = 20) -> float | None:
    return None if not prev or prev < base else round((cur - prev) / prev * 100, 1)


def compare(ids: list[int], t0: float, t1: float, off: int = 0, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    ids = [int(i) for i in ids]
    if not ids:
        return {"branches": [], "recs": [], "total": {"visits": 0, "prev_visits": 0, "change_pct": None, "served": 0,
                                                       "avg_wait": None, "theft": 0, "safety": 0, "unreviewed": 0}}
    inl = ",".join(str(i) for i in ids)                          # butun sonlar — SQL'ga xavfsiz
    names = {r["id"]: r for r in cdb.rows(f"SELECT id, name, last_seen, version FROM branches WHERE id IN ({inl})")}
    p0, p1 = t0 - 7 * 86400, t1 - 7 * 86400
    vis = {}
    for r in cdb.visits_hourly(ids, t0, t1, off=off):
        vis[r["branch_id"]] = vis.get(r["branch_id"], 0) + r["in"]
    pvis = {}
    for r in cdb.visits_hourly(ids, p0, p1, off=off):
        pvis[r["branch_id"]] = pvis.get(r["branch_id"], 0) + r["in"]
    q: dict[int, dict] = {}
    for r in cdb.queue_agg(ids, t0, off, int(t1 - t0), t1):
        a = q.setdefault(r["branch_id"], {"served": 0, "wait_sum": 0.0, "wait_n": 0, "wait_max": 0.0, "long_sec": 0.0,
                                          "obs": 0.0})
        a["served"] += r["served"]
        a["wait_sum"] += r["wait_sum"]
        a["wait_n"] += r["wait_n"]
        a["wait_max"] = max(a["wait_max"], r["wait_max"])
        a["long_sec"] += r["long_sec"]
        a["obs"] += r["t_obs"]
    ev: dict[int, dict] = {}
    for r in cdb.rows(f"SELECT branch_id, kind, status, loss_sum, data FROM events WHERE branch_id IN ({inl}) "
                      "AND created_at >= :a AND created_at < :b", {"a": t0, "b": t1}):
        d = cdb.json_load(r.get("data")) or {}
        if isinstance(d, dict) and d.get("job_id") is not None:
            continue                                             # video tahlili — jonli signal emas
        a = ev.setdefault(r["branch_id"], {"total": 0, "confirmed": 0, "rejected": 0, "unreviewed": 0, "safety": 0,
                                           "safety_unreviewed": 0, "loss": 0, "loss_n": 0})
        if r["kind"] in SAFETY_KINDS:
            a["safety"] += 1
            a["safety_unreviewed"] += r["status"] not in ("tasdiqlangan", "rad_etilgan")
            continue
        a["total"] += 1
        a[{"tasdiqlangan": "confirmed", "rad_etilgan": "rejected"}.get(r["status"], "unreviewed")] += 1
        if r["status"] == "tasdiqlangan" and r.get("loss_sum"):
            a["loss"] += int(r["loss_sum"])                      # 3.1.0: oldini olingan zarar (qo'lda kiritilgan)
            a["loss_n"] += 1
    end = min(t1, now)
    up = {r["branch_id"]: int(r["n"]) for r in cdb.rows(
        f"SELECT branch_id, COUNT(*) AS n FROM metrics_minute WHERE branch_id IN ({inl}) AND source_id=0 "
        "AND minute >= :a AND minute < :b GROUP BY branch_id", {"a": int(t0), "b": int(end)})}

    def uptime(bid: int) -> float | None:
        """Ishlagan vaqt: tizim qatori (har daqiqa, edge ishlasa) kelgan daqiqalar ulushi — edge birinchi marta
        ko'rsatkich yuborganidan boshlab (yangi ulangan do'kon "ishlamagan" deb ko'rinmasin); hech yubormagan — None."""
        first = cdb.scalar("SELECT minute FROM metrics_minute WHERE branch_id=:b AND source_id=0 ORDER BY minute LIMIT 1",
                           {"b": bid})
        if first is None or float(first) >= end:
            return None
        minutes = int((end - max(t0, float(first))) // 60)
        return round(min(1.0, up.get(bid, 0) / minutes), 3) if minutes >= 60 else None
    out = []
    for bid in ids:
        b = names.get(bid) or {"name": str(bid)}
        qq = q.get(bid)
        e = ev.get(bid) or {"total": 0, "confirmed": 0, "rejected": 0, "unreviewed": 0, "safety": 0, "safety_unreviewed": 0,
                            "loss": 0, "loss_n": 0}
        reviewed = e["confirmed"] + e["rejected"]
        out.append({
            "id": bid, "name": b["name"], "version": b.get("version"),
            "online": bool(b.get("last_seen") and now - float(b["last_seen"]) < 180),
            "visits": {"in": vis.get(bid, 0), "prev_in": pvis.get(bid, 0), "change_pct": _pct(vis.get(bid, 0), pvis.get(bid, 0))},
            "queue": ({"served": qq["served"], "avg_wait": round(qq["wait_sum"] / qq["wait_n"], 1) if qq["wait_n"] else None,
                       "max_wait": round(qq["wait_max"], 1), "long_sec": round(qq["long_sec"])} if qq and qq["obs"] else None),
            "theft": {**{k: e[k] for k in ("total", "confirmed", "rejected", "unreviewed", "loss", "loss_n")},
                      "false_share": round(e["rejected"] / reviewed, 3) if reviewed else None},
            "safety": {"total": e["safety"], "unreviewed": e["safety_unreviewed"]},
            "uptime": uptime(bid),
        })
    tot_in = sum(x["visits"]["in"] for x in out)
    tot_prev = sum(x["visits"]["prev_in"] for x in out)
    served = sum((x["queue"] or {}).get("served", 0) for x in out)
    wsum = sum(((x["queue"] or {}).get("avg_wait") or 0) * (x["queue"] or {}).get("served", 0) for x in out)
    total = {"visits": tot_in, "prev_visits": tot_prev, "change_pct": _pct(tot_in, tot_prev),
             "served": served, "avg_wait": round(wsum / served, 1) if served else None,
             "theft": sum(x["theft"]["total"] for x in out), "safety": sum(x["safety"]["total"] for x in out),
             "unreviewed": sum(x["theft"]["unreviewed"] for x in out),
             "loss": sum(x["theft"]["loss"] for x in out), "loss_n": sum(x["theft"]["loss_n"] for x in out)}
    return {"branches": sorted(out, key=lambda x: -x["visits"]["in"]), "total": total, "recs": recommend(out)}


def recommend(rows: list[dict]) -> list[dict]:
    from statistics import median

    recs = []
    waits = {x["id"]: x["queue"]["avg_wait"] for x in rows if x["queue"] and x["queue"]["avg_wait"] is not None
             and x["queue"]["served"] >= WAIT_SERVED}
    for x in rows:
        qx = x["queue"]
        others = [w for i, w in waits.items() if i != x["id"]]
        med = median(others) if others else None                 # boshqa filiallar (o'zi kirmaydi)
        if med and x["id"] in waits and qx["avg_wait"] >= max(WAIT_MIN, med * WAIT_RATIO):
            recs.append({"key": f"wait:{x['id']}", "level": "warn",
                         "text": f"🧾 {x['name']}: o'rtacha kutish {round(qx['avg_wait'])} s — boshqa filiallardan "
                                 f"{qx['avg_wait'] / med:.1f} barobar uzoq. Gavjum soatlarda kassa sonini ko'paytiring."})
        t = x["theft"]
        if (t["confirmed"] + t["rejected"]) >= FALSE_MIN and (t["false_share"] or 0) >= FALSE_SHARE:
            recs.append({"key": f"false:{x['id']}", "level": "warn",
                         "text": f"⚙️ {x['name']}: o'g'irlik signallarining {round(t['false_share'] * 100)}% xato chiqdi — "
                                 f"shu filialda zonalar va sezgirlikni sozlash kerak."})
        if t["unreviewed"] >= UNREV_MIN and t["total"] and t["unreviewed"] / t["total"] >= UNREV_SHARE:
            recs.append({"key": f"unrev:{x['id']}", "level": "info",
                         "text": f"🟡 {x['name']}: {t['unreviewed']} ta signal javobsiz qoldi — qo'riqchi ✅/❌ "
                                 f"bosmayapti (tizim javoblardan o'rganadi)."})
        if x["safety"]["unreviewed"]:
            recs.append({"key": f"safety:{x['id']}", "level": "warn",
                         "text": f"🆘 {x['name']}: {x['safety']['unreviewed']} ta xavfsizlik signali javobsiz."})
        if x["uptime"] is not None and x["uptime"] < UPTIME_MIN:
            recs.append({"key": f"uptime:{x['id']}", "level": "warn",
                         "text": f"📴 {x['name']}: do'kon kompyuteri davrning faqat {round(x['uptime'] * 100)}% ida "
                                 f"ishladi (aloqa yoki quvvat) — kompyuter va internetni tekshiring."})
        v = x["visits"]
        if (v["change_pct"] is not None and v["prev_in"] >= VISIT_BASE and v["change_pct"] <= VISIT_DROP
                and (x["uptime"] is None or x["uptime"] >= UPTIME_MIN)):      # kompyuter o'chiq bo'lsa — sabab o'sha
            recs.append({"key": f"visits:{x['id']}", "level": "info",
                         "text": f"📉 {x['name']}: tashriflar {abs(v['change_pct']):.0f}% kamaydi ({v['in']} / {v['prev_in']})."})
    recs.sort(key=lambda r: 0 if r["level"] == "warn" else 1)
    return recs[:10]


def digest_text(c: dict, lbl: str, company: str) -> str:
    """Egasiga haftalik xulosa (Telegram HTML; email'da oddiy matnga aylanadi)."""
    from html import escape as esc

    t = c["total"]
    ch = t.get("change_pct")
    lines = [f"📊 <b>Haftalik xulosa — {esc(company)}</b>", f"🗓 {esc(lbl)}", "",
             f"👥 Tashriflar: <b>{t.get('visits', 0)}</b>" + (f" ({'+' if ch > 0 else ''}{ch:.0f}%)" if ch is not None else ""),
             f"⚠️ O'g'irlik signallari: {t.get('theft', 0)} (javobsiz {t.get('unreviewed', 0)}) · 🛟 xavfsizlik: {t.get('safety', 0)}"]
    if t.get("loss"):
        lines.append(f"💰 Oldini olingan zarar: <b>{t['loss']:,} so'm</b> ({t.get('loss_n', 0)} ta holat)".replace(",", " "))
    if t.get("avg_wait") is not None:
        lines.append(f"🧾 Navbat: {t['served']} xaridor, o'rtacha kutish {round(t['avg_wait'])} s")
    if c["branches"]:
        lines += ["", "<b>Filiallar (tashrif bo'yicha):</b>"]
        for i, x in enumerate(c["branches"][:10], 1):
            v = x["visits"]
            chg = f" ({'+' if v['change_pct'] > 0 else ''}{v['change_pct']:.0f}%)" if v["change_pct"] is not None else ""
            lines.append(f"{i}. {esc(x['name'])}: {v['in']}{chg} · signal {x['theft']['total']}"
                         + (f" · kutish {round(x['queue']['avg_wait'])} s" if x["queue"] and x["queue"]["avg_wait"] else ""))
    if c["recs"]:
        lines += ["", "<b>Tavsiyalar:</b>"] + [f"• {esc(r['text'])}" for r in c["recs"][:6]]
    out, n = [], 0
    for ln in lines:                       # butun qatorlar bilan qisqartiriladi — HTML teg o'rtasidan kesilmaydi
        if n + len(ln) + 1 > 3900:
            out.append("…")
            break
        out.append(ln)
        n += len(ln) + 1
    return "\n".join(out)
