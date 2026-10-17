"""Xulq klassifikatorini o'qitish.

Ma'lumot manbalari:
  1) sintetik skelet ketma-ketliklari (doim mavjud, boshlang'ich model uchun)
  2) PoseLift dataseti (agar papka ko'rsatilsa)
  3) Qo'riqchi tasdiqlagan / rad etgan hodisalar (tizim ishlagan sari ko'payadi — eng qimmatli)

Buyruq qatoridan:  python -m app.ml.train --synthetic 3000 [--poselift PAPKA]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .. import db
from ..config import FEEDBACK_DIR, MODELS_DIR, POSES_DIR
from ..logutil import get_logger
from ..ai.safety import SAFETY_KINDS
from . import synthetic
from .features import feature_names, window_features
from .model import BehaviorModel

log = get_logger("train")
WINDOW = 2.5


def _feedback_sample(data: dict, status: str, ev_id) -> dict | None:
    t_ev = float(data.get("t_event", 0))
    # Ishlash paytidagi bilan bir xil oyna: signal lahzasigacha bo'lgan oxirgi WINDOW soniya
    seq = [(float(t), np.asarray(k, dtype=np.float32), np.asarray(b, dtype=np.float32))
           for t, k, b in data.get("seq", []) if t_ev - WINDOW <= float(t) <= t_ev + 0.01]
    if len(seq) < 4:
        return None
    return {"frames": seq, "label": 1 if status == "tasdiqlangan" else 0,
            "group": f"ev{ev_id}", "behavior": "feedback"}


def feedback_windows() -> list[dict]:
    """Qo'riqchi javoblari: bazadagi hodisalar + o'chirilgan hodisalar arxivi (data/feedback)."""
    out, seen = [], set()
    # 2.2.0: xavfsizlik signallari (yiqilish, tungi kirish...) o'g'irlik modeliga aralashmaydi
    marks = ",".join("?" for _ in SAFETY_KINDS)
    for ev in db.rows("SELECT id, status, pose_path FROM events WHERE status IN ('tasdiqlangan','rad_etilgan') "
                      f"AND pose_path IS NOT NULL AND (kind IS NULL OR kind NOT IN ({marks}))", SAFETY_KINDS):
        p = POSES_DIR / ev["pose_path"]
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        s = _feedback_sample(data, ev["status"], ev["id"])
        if s:
            out.append(s)
            seen.add(int(ev["id"]))
    for p in sorted(FEEDBACK_DIR.glob("ev*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ev_id = int(data.get("event_id", 0))
        if ev_id in seen or data.get("status") not in ("tasdiqlangan", "rad_etilgan") or data.get("kind") in SAFETY_KINDS:
            continue
        s = _feedback_sample(data, data["status"], ev_id)
        if s:
            out.append(s)
            seen.add(ev_id)
    return out


def _featurize(samples: list[dict], log_fn: Callable[[str], None], name: str):
    X, y, g, src = [], [], [], []
    t0 = time.time()
    for i, s in enumerate(samples):
        f = window_features(s["frames"])
        if f is None:
            continue
        X.append(f)
        y.append(s["label"])
        g.append(s["group"])
        src.append(s["behavior"])
        if (i + 1) % 1000 == 0:
            log_fn(f"  {name}: {i + 1}/{len(samples)} oyna tayyor ({time.time() - t0:.0f} s)")
    return X, y, g, src


def _metrics(y_true: np.ndarray, prob: np.ndarray, thr: float = 0.5) -> dict:
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, precision_score,
                                 recall_score, roc_auc_score)

    pred = (prob >= thr).astype(int)
    m = {
        "n": int(len(y_true)), "pos": int(y_true.sum()),
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
    }
    try:
        m["roc_auc"] = float(roc_auc_score(y_true, prob)) if len(set(y_true.tolist())) > 1 else None
    except ValueError:
        m["roc_auc"] = None
    cm = confusion_matrix(y_true, pred, labels=[0, 1])
    m["confusion"] = cm.tolist()
    return m


def train(n_synthetic: int = 3000, poselift_dir: str | None = None, use_feedback: bool = True,
          seed: int | None = None, log_fn: Callable[[str], None] | None = None, activate: bool = True) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupShuffleSplit
    import joblib

    logs: list[str] = []

    def say(msg: str):
        logs.append(msg)
        log.info(msg)
        if log_fn:
            log_fn(msg)

    seed = int(time.time()) % 100000 if seed is None else seed
    say("Ma'lumotlar yig'ilmoqda...")
    samples: list[dict] = []
    desc = {}
    if n_synthetic > 0:
        syn = synthetic.sample_windows(n_synthetic, seed=seed, window=WINDOW)
        samples += syn
        desc["sintetik"] = len(syn)
        say(f"Sintetik oynalar: {len(syn)}")
    if poselift_dir:
        from . import poselift

        pl = poselift.windows(poselift_dir, window=WINDOW)
        samples += pl
        desc["PoseLift"] = len(pl)
        say(f"PoseLift oynalari: {len(pl)} (ijobiy: {sum(s['label'] for s in pl)})")
    if use_feedback:
        fb = feedback_windows()
        # Haqiqiy do'kon ma'lumoti eng qimmatli — og'irligini oshirish uchun 5 marta takrorlanadi
        samples += fb * 5
        desc["qo'riqchi javoblari"] = len(fb)
        say(f"Qo'riqchi tasdiqlagan/rad etgan hodisalar: {len(fb)}")
    if not samples:
        raise RuntimeError("O'qitish uchun ma'lumot yo'q")

    say("Belgilar (feature) hisoblanmoqda...")
    X, y, g, src = _featurize(samples, say, "oynalar")
    X, y, g, src = np.array(X), np.array(y), np.array(g), np.array(src)
    if len(set(y.tolist())) < 2:
        raise RuntimeError("Ikkala sinf (oddiy va shubhali) namunasi kerak")
    say(f"Jami: {len(y)} ta namuna, {int(y.sum())} ta shubhali, {len(feature_names())} ta belgi")

    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed)
    tr, te = next(gss.split(X, y, g))
    clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.08, max_leaf_nodes=31,
                                         l2_regularization=1.0, class_weight="balanced", random_state=seed)
    say("Model o'qitilmoqda (Gradient Boosting)...")
    t0 = time.time()
    clf.fit(X[tr], y[tr])
    say(f"O'qitish tugadi: {time.time() - t0:.1f} s")
    prob = clf.predict_proba(X[te])[:, 1]
    metrics = {"overall": _metrics(y[te], prob)}
    for s in sorted(set(src[te].tolist())):
        if s in ("poselift", "feedback"):
            m = src[te] == s
            metrics[s] = _metrics(y[te][m], prob[m])
    if "feedback" not in metrics:
        pass
    syn_mask = ~np.isin(src[te], ["poselift", "feedback"])
    if syn_mask.any():
        metrics["synthetic"] = _metrics(y[te][syn_mask], prob[syn_mask])
        per_beh = {}
        for b in sorted(set(src[te][syn_mask].tolist())):
            mb = src[te] == b
            per_beh[b] = float(((prob[mb] >= 0.5).astype(int) == y[te][mb]).mean())
        metrics["per_behavior_accuracy"] = per_beh

    # Yakuniy modelni barcha ma'lumotda qayta o'qitish
    clf.fit(X, y)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = MODELS_DIR / f"behavior_{stamp}.joblib"
    joblib.dump({"model": clf, "features": feature_names(), "window": WINDOW, "version": stamp,
                 "metrics": metrics, "data": desc}, path)
    om = metrics["overall"]
    say(f"Sinov natijasi: aniqlik {om['accuracy']:.1%}, precision {om['precision']:.1%}, "
        f"recall {om['recall']:.1%}, F1 {om['f1']:.1%}")
    vid = db.execute("INSERT INTO model_versions(created_at, path, metrics, n_samples, n_pos, data_desc, active) "
                     "VALUES(?,?,?,?,?,?,0)",
                     (time.time(), path.name, json.dumps(metrics), int(len(y)), int(y.sum()),
                      json.dumps(desc, ensure_ascii=False)))
    if activate:
        db.execute("UPDATE model_versions SET active=0")
        db.execute("UPDATE model_versions SET active=1 WHERE id=?", (vid,))
        BehaviorModel.set_active(path)
        say(f"Yangi model faollashtirildi: {path.name}")
    return {"path": str(path), "metrics": metrics, "version_id": vid, "logs": logs, "data": desc}


def main():
    ap = argparse.ArgumentParser(description="Hushyor AI xulq modelini o'qitish")
    ap.add_argument("--synthetic", type=int, default=3000, help="sintetik oynalar soni")
    ap.add_argument("--poselift", type=str, default=None, help="PoseLift papkasi (.pkl va .npy fayllar)")
    ap.add_argument("--no-feedback", action="store_true", help="qo'riqchi javoblarini ishlatmaslik")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    from ..logutil import setup_logging

    setup_logging()
    db.init_db()
    res = train(a.synthetic, a.poselift, not a.no_feedback, seed=a.seed)
    print(json.dumps(res["metrics"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
