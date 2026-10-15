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
