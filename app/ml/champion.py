"""Champion / challenger (2.3.0, 7-bosqich): yangi xulq modeli darhol almashtirilmaydi.

  champion    faol model — ballga qo'shiladi (analyzer: ml_weight × max(0, p − 0,5) × 2).
  challenger  yangi o'qitilgan model — "soyada": har signal paytida ehtimoli hisoblanib hodisaga yoziladi (`ml_ch`),
              lekin ball va signalga ta'sir qilmaydi.

Solishtiruv faqat qo'riqchi javob bergan o'g'irlik hodisalarida (✅ = haqiqiy, ❌ = xato signal), ikki manbadan:

  1) Ko'rmagan baholash (oof): challenger o'qitilganda har javob berilgan hodisa uni ko'rmagan model bilan baholanadi
     (app/ml/train.py: oof_feedback). Champion ehtimoli — hodisa paytida haqiqatan ishlagan modelniki (`ml_prob`).
  2) Soya (jonli): challenger yaratilgandan keyingi hodisalar (`ml_ch` yozilgan) — eng halol, lekin sekin to'planadi.

Har hodisa uchun "challenger bo'lganda ball qancha bo'lardi" qayta hisoblanadi: asl ball − champion hissasi +
challenger hissasi; hozirgi chegara bilan solishtiriladi (champion uchun ham o'sha chegara — teng sharoit). Natija:
qancha ❌ signal chiqmay qolardi (yaxshi) va qancha ✅ signal yo'qolardi (yomon).

Cheklov (rostini aytsam): faqat champion bergan signallar o'lchanadi. Champion o'tkazib yuborgan, challenger
topadigan o'g'irliklar bu yerda ko'rinmaydi — ularni Aniqlik sinovi (belgilangan videolar) ko'rsatadi.

Tavsiya faqat soya natijasidan: yetarli ma'lumot (≥ MIN_EVENTS ta, har turdan ≥ MIN_EACH) va challenger yomonroq emas —
yo'qolgan ✅ yo'q (ko'pi bilan MAX_LOST_TP) va ❌ kamroq yoki AUC ≥ +0,02. Ko'rmagan baholash faqat ma'lumot uchun:
challenger o'sha hodisadan keyingi javoblarda ham o'qitilgan, champion ehtimoli esa hodisa paytidagi eski modelniki —
bu baho yangi modelga yon bosadi. Har signalda o'sha paytdagi haqiqiy chegara (zona, polka) va ML og'irligi yoziladi
(`thr`, `ml_w`) — solishtiruv shu bilan; "chiqish" signallari kirmaydi.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import db
from ..ai.safety import SAFETY_KINDS as SAFETY
from ..logutil import get_logger

log = get_logger("champion")

MIN_EVENTS = 20
MIN_EACH = 5
MAX_LOST_TP = 0
AUC_GAIN = 0.02
ML_LABEL = "AI model ehtimoli"


def ml_contrib(breakdown) -> float:
    """Champion modelning ballga qo'shgan hissasi (breakdown: [["AI model ehtimoli 83%", 7], ...])."""
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown or "[]")
        except ValueError:
            breakdown = []
    for item in breakdown or []:
        try:
            if str(item[0]).startswith(ML_LABEL):
                return float(item[1])
        except (TypeError, IndexError, ValueError):
            continue
    return 0.0


def contrib(prob: float | None, weight: float) -> float:
    return 0.0 if prob is None else float(round(weight * max(0.0, float(prob) - 0.5) * 2))


def auc(labels: list[int], probs: list[float]) -> float | None:
    """ROC-AUC (Mann–Whitney), sklearn'siz."""
    pos = [p for l, p in zip(labels, probs) if l == 1]
    neg = [p for l, p in zip(labels, probs) if l == 0]
    if not pos or not neg:
        return None
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def simulate(events: list[dict], prob_key: str, weight: float, threshold: float) -> dict:
    """events: [{"status", "score", "breakdown", "ml_prob", prob_key, "thr"?, "ml_w"?, "score_raw"?}] — champion va
    challenger qaysi hodisalarda signal berardi. Chegara — o'sha signaldagi haqiqiy chegara (`thr`: zona/polka bilan,
    2.3.0 dan yoziladi); eski hodisalarda — hozirgi umumiy chegara (taxminiy, `approx` da sanaladi). ML o'chiq paytdagi
    hodisalar (2.3.0: `ml_w` NULL) solishtirilmaydi."""
    out = {"n": 0, "tp": 0, "fp": 0, "champ_tp": 0, "champ_fp": 0, "lost_tp": [], "saved_fp": [], "new_fp": [],
           "auc_champ": None, "auc_ch": None, "approx": 0, "pos": 0, "neg": 0}
    labels, pc, pn = [], [], []
    for e in events:
        p_new = e.get(prob_key)
        if p_new is None or e.get("status") not in ("tasdiqlangan", "rad_etilgan"):
            continue
        new_style = e.get("thr") is not None
        if new_style and e.get("ml_w") is None:
            continue                                   # signal paytida ML o'chiq edi — teng sharoit yo'q
        thr = float(e["thr"]) if new_style else threshold
        w = float(e["ml_w"]) if new_style else weight
        y = 1 if e["status"] == "tasdiqlangan" else 0
        score = float(e["score_raw"] if e.get("score_raw") is not None else (e.get("score") or 0))
        base = score - ml_contrib(e.get("breakdown"))
        champ = score >= thr
        ch = base + contrib(p_new, w) >= thr
        out["n"] += 1
        out["approx"] += int(not new_style)
        out["pos" if y else "neg"] += 1
        out["champ_tp" if y else "champ_fp"] += int(champ)
        out["tp" if y else "fp"] += int(ch)
        if y and champ and not ch:
            out["lost_tp"].append(e["id"])
        if not y and champ and not ch:
            out["saved_fp"].append(e["id"])
        if not y and ch and not champ:
            out["new_fp"].append(e["id"])
        if e.get("ml_prob") is not None:
            labels.append(y)
            pc.append(float(e["ml_prob"]))
            pn.append(float(p_new))
    out["auc_champ"] = auc(labels, pc)
    out["auc_ch"] = auc(labels, pn)
    return out


def verdict(sim: dict) -> dict:
    enough = sim["n"] >= MIN_EVENTS and sim["pos"] >= MIN_EACH and sim["neg"] >= MIN_EACH
    if not enough:
        return {"enough": False, "better": False,
                "text": f"Ma'lumot kam: {sim['n']} ta javob (kerak ≥ {MIN_EVENTS}, har turdan ≥ {MIN_EACH})"}
    lost = len(sim["lost_tp"])
    fp_gain = sim["champ_fp"] - sim["fp"]
    auc_gain = (sim["auc_ch"] - sim["auc_champ"]) if sim["auc_ch"] is not None and sim["auc_champ"] is not None else 0
    better = lost <= MAX_LOST_TP and (fp_gain > 0 or auc_gain >= AUC_GAIN) and sim["fp"] <= sim["champ_fp"]
    if better:
        text = (f"Challenger yaxshiroq: xato signal {sim['champ_fp']} → {sim['fp']}, haqiqiy signal yo'qolmaydi"
                + (f", AUC +{auc_gain:.2f}" if auc_gain > 0 else ""))
    elif lost > MAX_LOST_TP:
        text = f"Challenger {lost} ta haqiqiy (✅) signalni o'tkazib yuborardi — faollashtirish tavsiya etilmaydi"
    else:
        text = "Challenger champion'dan yaxshi emas (xato signal kamaymaydi) — hozirgisi qolgani ma'qul"
    return {"enough": True, "better": better, "text": text, "fp_gain": fp_gain, "auc_gain": round(auc_gain, 3)}


def _theft_events(since: float = 0.0) -> list[dict]:
    marks = ",".join("?" for _ in SAFETY)
    # "Chiqish" signali ballida ML bevosita yo'q (avvalgi signal bali) va o'sha hodisani ikkinchi marta sanaydi — kirmaydi
    return db.rows(f"SELECT id, created_at, status, score, score_raw, thr, ml_w, breakdown, ml_prob, ml_ch, ml_ch_ver "
                   f"FROM events WHERE status IN ('tasdiqlangan','rad_etilgan') AND (kind IS NULL OR kind NOT IN "
                   f"({marks}, 'exit')) AND created_at>=?", (*SAFETY, since))


def _version(path: Path | None) -> dict | None:
    if path is None:
        return None
    v = db.row("SELECT * FROM model_versions WHERE path=? ORDER BY id DESC LIMIT 1", (path.name,))
    return v or {"id": None, "path": path.name, "created_at": path.stat().st_mtime if path.exists() else None,
                 "oof": None, "metrics": "{}"}


def compare() -> dict:
    """Panel (AI model sahifasi) uchun: champion, challenger, ko'rmagan baholash va soya natijalari, tavsiya."""
    from .model import BehaviorModel

    s = db.get_settings()
    weight = float(s.get("ml_weight", 10) or 10)
    thr = float(s.get("threshold", 60) or 60)
    champ_p = BehaviorModel.active_path()
    ch_p = BehaviorModel.challenger_path()
    res = {"champion": champ_p.name if champ_p else None, "challenger": ch_p.name if ch_p else None,
           "weight": weight, "threshold": thr, "cv": None, "shadow": None, "verdict": None,
           "use_ml": s.get("use_ml", "1") == "1"}
    if ch_p is None:
        return res
    v = _version(ch_p)
    res["challenger_created"] = v.get("created_at")
    res["challenger_id"] = v.get("id")
    try:
        oof = {int(k): float(x) for k, x in json.loads(v.get("oof") or "{}").items()}
    except (TypeError, ValueError):
        oof = {}
    evs = _theft_events()
    for e in evs:
        e["p_oof"] = oof.get(int(e["id"]))
    res["cv"] = simulate(evs, "p_oof", weight, thr)
    since = float(v.get("created_at") or 0)
    shadow = [e for e in evs if e["created_at"] >= since and e.get("ml_ch") is not None
              and e.get("ml_ch_ver") == ch_p.name]
    res["shadow"] = simulate(shadow, "ml_ch", weight, thr)
    vs = verdict(res["shadow"])
    vs["source"] = "soya (jonli)"
    if not vs["enough"]:
        # Ko'rmagan baholash faqat ma'lumot uchun: challenger hamma (keyingi) javoblarda o'qitilgan, champion ehtimoli
        # esa hodisa paytidagi eski modelniki — bu baho yangi modelga yon bosadi. Tavsiya faqat soyadan.
        vc = verdict(res["cv"])
        hint = ""
        if vc["enough"]:
            hint = (" Ko'rmagan baholashda: " + vc["text"][0].lower() + vc["text"][1:]
                    + " (faqat ma'lumot — yangi modelga yon bosadi).")
        vs["text"] += " — tavsiya challenger soyada ishlagan davrdagi javoblardan keyin." + hint
        vs["better"] = False
    res["verdict"] = vs
    return res


def promote() -> dict:
    """Challenger → champion. Eski champion versiyalar ro'yxatida qoladi (bir tugma bilan qaytarish mumkin)."""
    from .model import BehaviorModel

    ch_p = BehaviorModel.challenger_path()
    if ch_p is None:
        raise ValueError("Challenger model yo'q")
    old = BehaviorModel.active_path()
    BehaviorModel.set_active(ch_p)
    BehaviorModel.set_challenger(None)
    db.execute("UPDATE model_versions SET active=0")
    row = db.row("SELECT id FROM model_versions WHERE path=? ORDER BY id DESC LIMIT 1", (ch_p.name,))
    if row:
        db.execute("UPDATE model_versions SET active=1, role='champion', promoted_at=? WHERE id=?", (time.time(), row["id"]))
    log.info("Challenger faollashtirildi: %s (oldingi: %s)", ch_p.name, old.name if old else "—")
    return {"champion": ch_p.name, "previous": old.name if old else None}


def drop(role: str = "rad etilgan") -> None:
    """Challenger'ni olib tashlash. role: 'rad etilgan' (qo'lda) yoki 'eskirgan' (yangi challenger / champion almashdi)."""
    from .model import CHALLENGER_POINTER, BehaviorModel, pointer_name

    name = pointer_name(CHALLENGER_POINTER)         # fayl o'chirilgan bo'lsa ham jadvaldagi rol yangilansin
    BehaviorModel.set_challenger(None)
    if name:
        db.execute("UPDATE model_versions SET role=? WHERE path=? AND role='challenger'", (role, Path(name).name))
