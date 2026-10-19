"""3.1.0: o'g'irlik tasdiqlanganda qo'lda kiritiladigan taxminiy zarar (so'm) — "oldini olingan zarar".

Summa faqat qo'lda kiritiladi (egasining tanlovi): signal tasdiqlanganda — web panelda, Telegram'da (✅ dan keyin bot
so'raydi) yoki bulut panelida. Hisobotlarda faqat TASDIQLANGAN o'g'irlik signallari summasi "oldini olingan zarar"
deb sanaladi; summa yozilgan-u, keyin qaror bekor qilingan/xato deb belgilangan hodisa hisobga kirmaydi.
Xavfsizlik signallari (yiqilish va h.k.) uchun summa yo'q.
"""
from __future__ import annotations

import re
import time

from . import db

MAX_SUM = 100_000_000_000          # 100 mlrd so'm — yozuv xatosi (ortiqcha nol) bo'lsa ham bazaga kirmasin


def allowed(ev: dict | None) -> bool:
    """Summa shu hodisaga yoziladimi: do'kondagi o'g'irlik turidagi signal (xavfsizlik ham, yuklangan video ham emas)."""
    if not ev:
        return False
    from .ai.safety import SAFETY_KINDS
    from .config import DEMO

    if (ev.get("kind") or "") in SAFETY_KINDS or ev.get("job_id") is not None:
        return False
    if not DEMO and ev.get("source_id") is not None:
        # 3.2.0: "jonli rejim"da sinalgan video fayl — do'kondagi haqiqiy o'g'irlik emas
        if db.scalar("SELECT kind FROM sources WHERE id=?", (ev["source_id"],)) == "file":
            return False
    return True


_MULT = (("mlrd", 1_000_000_000), ("milliard", 1_000_000_000), ("mln", 1_000_000), ("million", 1_000_000),
         ("ming", 1_000), ("k", 1_000))


def parse_sum(raw) -> int | None:
    """"150000", "150 000", "150,000", "150.000", "150 ming", "1,5 mln", "150k" → so'm (butun son).
    Bo'sh yoki 0 → None (summa olib tashlanadi). Tushunarsiz → ValueError."""
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
            # "1,5 mln" / "1.5 mln" — kasr qismi bor bo'lishi mumkin
            s = s.replace(" ", "").replace(",", ".")
            if not re.fullmatch(r"\d+(\.\d+)?", s):
                raise ValueError("summani raqam bilan yozing, masalan 150000")
            v = float(s) * mult
        else:
            # Ming ajratgichlari: bo'shliq, vergul, nuqta ("150 000", "150,000", "1.250.000")
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
    """150000 → "150 000 so'm"."""
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        v = 0
    return f"{v:,}".replace(",", " ") + " so'm"


def set_loss(event_id: int, amount: int | None, by: str, at: float | None = None) -> dict | None:
    """Summani yozadi (None — olib tashlaydi). Bulut navbatiga (sync hook) va Telegram'ga o'zgarish o'zi ketadi.
    at — kiritilgan vaqt (bulut buyrug'ida bulutdagi vaqt; sukut — hozir)."""
    ev = db.get_event(event_id)
    if not ev or not allowed(ev):
        return None
    db.update_event(event_id, loss_sum=amount, loss_by=(by or "")[:100] if amount else None,
                    loss_at=(at or time.time()) if amount else None)
    return db.get_event(event_id)


def totals(since: float | None = None, until: float | None = None) -> dict:
    """Tasdiqlangan o'g'irlik signallari bo'yicha: summa, summasi yozilganlar soni, tasdiqlanganlar soni."""
    from .ai.safety import SAFETY_KINDS

    where = ["status='tasdiqlangan'", "job_id IS NULL",
             "kind NOT IN (" + ",".join("?" for _ in SAFETY_KINDS) + ")"]
    params: list = list(SAFETY_KINDS)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if until is not None:
        where.append("created_at < ?")
        params.append(until)
    r = db.row(f"SELECT COALESCE(SUM(loss_sum), 0) AS s, SUM(CASE WHEN loss_sum > 0 THEN 1 ELSE 0 END) AS n, "
               f"COUNT(*) AS c FROM events WHERE {' AND '.join(where)}", params) or {}
    return {"sum": int(r.get("s") or 0), "with_sum": int(r.get("n") or 0), "confirmed": int(r.get("c") or 0)}


def line(t: dict) -> str | None:
    """Hisobot qatori: "💰 Oldini olingan zarar: 450 000 so'm (3 ta holat)". Summa yo'q bo'lsa — None."""
    if not t or not t.get("sum"):
        return None
    return f"💰 Oldini olingan zarar: {fmt_sum(t['sum'])} ({t['with_sum']} ta holat)"
