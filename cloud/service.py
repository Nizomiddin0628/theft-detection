"""Bulutdagi umumiy amallar (panel API va bulut Telegram boti bir xil yo'ldan foydalanadi)."""
from __future__ import annotations

import time

from . import db as cdb

STATUSES = ("yangi", "tasdiqlangan", "rad_etilgan")


class NotFound(Exception):
    pass


def event_for(user: dict, uid: str) -> dict:
    """Foydalanuvchi ko'ra oladigan hodisa; boshqa filialniki yoki yo'q bo'lsa — NotFound (mavjudlik siri saqlanadi)."""
    ev = cdb.row("SELECT * FROM events WHERE uid=:u", {"u": uid})
    if not ev or ev["branch_id"] not in cdb.branch_ids_for(user):
        raise NotFound(uid)
    return ev


def review(user: dict, uid: str, status: str, note: str | None = None, via: str = "Bulut", ip: str = "") -> dict:
    """Hodisani tekshirish (✅/❌/qaytarish): bazada yangilanadi, edge'ga `review` buyrug'i, bulut Telegram
    xabarlari tahrirlanadi, panelga jonli xabar. Qaytaradi: {"uid", "status", "reviewed_by"}."""
    if status not in STATUSES:
        raise ValueError("holat: yangi, tasdiqlangan yoki rad_etilgan")
    ev = event_for(user, uid)
    now = time.time()
    # updated_at edge soati doirasida qoladi: edge soati bulutdan orqada bo'lsa, undan keyin kelgan haqiqiy
    # yangilanishlar (Telegram'da ✅) "eskiroq" deb tashlanmasin
    br = cdb.row("SELECT clock_skew FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {}
    upd = max(float(ev.get("updated_at") or 0) + 0.001, now - float(br.get("clock_skew") or 0))
    data = cdb.json_load(ev.get("data")) or {}
    by = f"{via}: {user.get('name') or user['email']}"[:100]
    data.update({"status": status, "reviewed_by": by if status != "yangi" else None,
                 "reviewed_at": now if status != "yangi" else None, "updated_at": upd})
    if note is not None:
        data["note"] = note
    with cdb.engine().begin() as c:
        c.execute(cdb.events.update().where(cdb.events.c.uid == uid).values(status=status, updated_at=upd, data=data))
        c.execute(cdb.commands.insert().values(branch_id=ev["branch_id"], type="review", created_at=now, payload={
            "local_id": ev["local_id"], "uid": uid, "status": status, "note": note, "by": by, "t": now}))
    cdb.audit("review", user, uid, {"status": status, "via": via, "note": bool(note)}, ip=ip or via.lower())
    from . import notify
    from .live import hub

    notify.on_status_change(uid, status, by)
    hub.publish("event_update", {"uid": uid, "status": status, "reviewed_by": by}, ev["branch_id"])
    return {"uid": uid, "status": status, "reviewed_by": by}


# ----------------------------------------------------------------------------- 3.1.0: zarar summasi (so'm, qo'lda)
class NotAllowed(Exception):
    """Amal shu hodisaga to'g'ri kelmaydi (xavfsizlik signali, video tahlili, hali tasdiqlanmagan)."""


MAX_SUM = 100_000_000_000
_MULT = (("mlrd", 1_000_000_000), ("milliard", 1_000_000_000), ("mln", 1_000_000), ("million", 1_000_000),
         ("ming", 1_000), ("k", 1_000))
SAFETY_KINDS = ("fall", "lying", "night", "crowd", "blocked", "fire", "fight")


def parse_sum(raw) -> int | None:
    """Edge'dagi app/losses.py: parse_sum bilan bir xil (bulutda app paketi yo'q). "150000", "150 000", "150 ming",
    "1,5 mln" → butun so'm; bo'sh yoki 0 → None; tushunarsiz → ValueError."""
    import re

    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("summa son bo'lishi kerak")
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip().lower().replace("so'm", "").replace("som", "").replace("sum", "").replace("uzs", "")
        s = s.replace(" ", " ").strip()
        if not s:
            return None
        mult = 1
        for word, m in _MULT:
            if s.endswith(word):
                mult, s = m, s[: -len(word)].strip()
                break
        if mult > 1:
            s = s.replace(" ", "").replace(",", ".")
            if not re.fullmatch(r"\d+(\.\d+)?", s):
                raise ValueError("summani raqam bilan yozing, masalan 150000")
            v = float(s) * mult
        else:
            if not re.fullmatch(r"\d{1,3}([ ,.]\d{3})+|\d+", s):
                raise ValueError("summani raqam bilan yozing, masalan 150000")
            v = float(re.sub(r"[ ,.]", "", s))
    if v != v or v < 0:
        raise ValueError("summa manfiy bo'lishi mumkin emas")
    if v > MAX_SUM:
        raise ValueError("summa juda katta")
    n = int(round(v))
    return n or None


def fmt_sum(n) -> str:
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        v = 0
    return f"{v:,}".replace(",", " ") + " so'm"


def loss_allowed(ev: dict) -> bool:
    d = cdb.json_load(ev.get("data")) or {}
    return d.get("job_id") is None and (ev.get("kind") or "") not in SAFETY_KINDS


def set_loss(user: dict, uid: str, amount: int | None, via: str = "Bulut", ip: str = "") -> dict:
    """Tasdiqlangan o'g'irlik signaliga taxminiy zarar: bazada, edge'ga `loss` buyrug'i, Telegram xabarlari yangilanadi."""
    ev = event_for(user, uid)
    if not loss_allowed(ev):
        raise NotAllowed("summa faqat do'kondagi o'g'irlik signaliga yoziladi")
    if ev.get("status") != "tasdiqlangan":
        raise NotAllowed("avval signalni tasdiqlang")
    br = cdb.row("SELECT clock_skew, version FROM branches WHERE id=:b", {"b": ev["branch_id"]}) or {}
    from .video_tests import version_tuple

    if version_tuple(br.get("version")) < (3, 1, 0):
        # eski edge summani bilmaydi: keyingi yangilanishida bulutdagi summa yo'qolib qolardi
        raise NotAllowed("do'kon kompyuterini 3.1.0 ga yangilang — eski versiya zarar summasini saqlay olmaydi")
    now = time.time()
    upd = max(float(ev.get("updated_at") or 0) + 0.001, now - float(br.get("clock_skew") or 0))
    by = f"{via}: {user.get('name') or user['email']}"[:100]
    data = cdb.json_load(ev.get("data")) or {}
    data.update({"loss_sum": amount, "loss_by": by if amount else None, "loss_at": now if amount else None,
                 "updated_at": upd})
    with cdb.engine().begin() as c:
        c.execute(cdb.events.update().where(cdb.events.c.uid == uid).values(loss_sum=amount, updated_at=upd, data=data))
        c.execute(cdb.commands.insert().values(branch_id=ev["branch_id"], type="loss", created_at=now, payload={
            "local_id": ev["local_id"], "uid": uid, "loss_sum": amount, "by": by, "t": now}))
    cdb.audit("loss", user, uid, {"sum": amount, "via": via}, ip=ip or via.lower())
    from . import notify
    from .live import hub

    notify.on_status_change(uid, "tasdiqlangan", data.get("reviewed_by") or by)   # xabarlarda summa ko'rinsin
    hub.publish("event_update", {"uid": uid, "loss_sum": amount}, ev["branch_id"])
    return {"uid": uid, "loss_sum": amount, "loss_by": by if amount else None}


def loss_summary(branch_ids: list[int], day0: float, month0: float) -> dict:
    """Panel bosh sahifasi: bugun, shu oy, jami (summasi yozilgan tasdiqlangan o'g'irliklar), filiallar bo'yicha shu oy,
    oxirgi 30 kunda summasi yozilmagan tasdiqlar."""
    empty = {"sum": 0, "n": 0}
    if not branch_ids:
        return {"today": empty, "month": empty, "total": empty, "missing": 0, "branches": []}
    marks = ",".join(f":b{i}" for i in range(len(branch_ids)))
    params: dict = {f"b{i}": v for i, v in enumerate(branch_ids)}
    base = f"FROM events WHERE branch_id IN ({marks}) AND status='tasdiqlangan' AND loss_sum > 0"

    def agg(t0: float | None) -> dict:
        q = f"SELECT COALESCE(SUM(loss_sum), 0) AS s, COUNT(*) AS n {base}" + (" AND created_at >= :t0" if t0 else "")
        r = cdb.row(q, {**params, **({"t0": t0} if t0 else {})}) or {}
        return {"sum": int(r.get("s") or 0), "n": int(r.get("n") or 0)}

    per = {int(r["branch_id"]): r for r in cdb.rows(
        f"SELECT branch_id, COALESCE(SUM(loss_sum), 0) AS s, COUNT(*) AS n {base} AND created_at >= :t0 "
        "GROUP BY branch_id", {**params, "t0": month0})}
    names = {int(r["id"]): r["name"] for r in cdb.rows(f"SELECT id, name FROM branches WHERE id IN ({marks})", params)}
    missing = 0
    for r in cdb.rows(f"SELECT kind, data FROM events WHERE branch_id IN ({marks}) AND status='tasdiqlangan' AND "
                      "(loss_sum IS NULL OR loss_sum = 0) AND created_at >= :t0 LIMIT 5000",
                      {**params, "t0": time.time() - 30 * 86400}):
        if loss_allowed(r):
            missing += 1
    return {"today": agg(day0), "month": agg(month0), "total": agg(None), "missing": missing,
            "branches": [{"id": b, "name": names.get(b, str(b)), "month_sum": int(per[b]["s"] or 0) if b in per else 0,
                          "month_n": int(per[b]["n"] or 0) if b in per else 0} for b in branch_ids]}
