"""2-bosqich darvozasi: "yangi (ochiq, Apache-2.0) modellar aniqligi eskisidan (YOLO11) kam emas".

Avval panelda: **Aniqlik sinovi** → belgilangan videolar ("Belgilash tugallandi") → **Hammasini solishtirish**
(YOLO11 va ochiq to'plam(lar) bir xil videolarda). Keyin shu skript oxirgi solishtirishni o'qib, xulosa beradi:

    python tests/model_gate.py                     # data/hushyor.db (faqat o'qiydi — jonli dasturga xalaqit bermaydi)
    python tests/model_gate.py --db boshqa.db --base yolo11 --new open

Mezon: yangi to'plamning F1 va recall (topilgan o'g'irliklar ulushi) eskisidan kam emas. Precision va soatiga xato
signal ham ko'rsatiladi (ularning yomonlashuvi — ogohlantirish). Kam ma'lumotda (< 20 video yoki < 15 belgilangan
hodisa) xulosa "taxminiy" deb belgilanadi: bitta hodisa farqi natijani o'zgartirishi mumkin.
Chiqish kodi: 0 — o'tdi, 1 — o'tmadi, 2 — solishtirish topilmadi.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_runs(db: Path) -> list[dict]:
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM eval_runs WHERE status='tayyor' ORDER BY id DESC")]
    finally:
        c.close()
    for r in rows:
        try:
            r["model_set"] = (json.loads(r.get("settings") or "{}") or {}).get("model_set") or "yolo11"
        except ValueError:
            r["model_set"] = "yolo11"
        try:
            det = json.loads(r.get("details") or "[]") or []
        except ValueError:
            det = []
        r["video_ids"] = tuple(sorted(int(v.get("video_id", 0)) for v in det))
        r["labels"] = sum(int(v.get("labels", 0) or 0) for v in det)
    return rows


def pick(rows: list[dict], base: str, new: str) -> tuple[dict, dict] | None:
    """Eng oxirgi juftlik: ikkala to'plam AYNAN bir xil videolarda baholangan."""
    for r_new in rows:
        if r_new["model_set"] != new or not r_new["video_ids"]:
            continue
        for r_base in rows:
            if r_base["model_set"] == base and r_base["video_ids"] == r_new["video_ids"]:
                return r_base, r_new
    return None


def fmt(v, pct: bool = False, nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.0f}%" if pct else f"{v:.{nd}f}"


def main() -> int:
    for st in (sys.stdout, sys.stderr):     # Windows konsoli/fayl (cp1251) emoji'da yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "hushyor.db"))
    ap.add_argument("--base", default="yolo11", help="eski to'plam")
    ap.add_argument("--new", default="open", help="yangi to'plam (open yoki open-s)")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print(f"Baza topilmadi: {db}")
        return 2
    rows = load_runs(db)
    pair = pick(rows, a.base, a.new)
    if not pair:
        sets = sorted({r["model_set"] for r in rows})
        print(f"'{a.base}' va '{a.new}' bir xil videolarda baholanmagan (tayyor natijalar: {sets or 'yo`q'}).\n"
              "Panel → Aniqlik sinovi → videolarni belgilang → \"Hammasini solishtirish\".")
        return 2
    b, n = pair
    vids = len(b["video_ids"])
    print(f"Solishtirish: #{b['id']} ({a.base}) va #{n['id']} ({a.new}) — {vids} video, "
          f"{(b.get('hours') or 0):.2f} soat, belgilangan hodisa: {b['labels']}\n")
    print("| To'plam | TP | FP | FN | Precision | Recall | F1 | Xato/soat | Kechikish (med) | Tezlik (s/video s) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in (b, n):
        print(f"| {r['model_set']} | {r['tp']} | {r['fp']} | {r['fn']} | {fmt(r['precision'], True)} | "
              f"{fmt(r['recall'], True)} | {fmt(r['f1'])} | {fmt(r['fp_per_hour'], nd=1)} | "
              f"{fmt(r['latency_med'], nd=1)} s | {fmt(r.get('speed'))} |")
    f1_ok = (n["f1"] or 0) >= (b["f1"] or 0) - 1e-9
    rec_ok = (n["recall"] or 0) >= (b["recall"] or 0) - 1e-9
    warn = []
    if (n["precision"] or 0) < (b["precision"] or 0) - 0.05:
        warn.append(f"precision {fmt(b['precision'], True)} → {fmt(n['precision'], True)} (xato signal ko'paydi)")
    if (n["fp_per_hour"] or 0) > (b["fp_per_hour"] or 0) * 1.25 + 0.2:
        warn.append(f"soatiga xato signal {fmt(b['fp_per_hour'], nd=1)} → {fmt(n['fp_per_hour'], nd=1)}")
    if n.get("speed") and b.get("speed") and n["speed"] > b["speed"] * 1.5:
        warn.append(f"tahlil sekinroq: {b['speed']:.2f} → {n['speed']:.2f} s har video soniyasiga")
    weak = vids < 20 or b["labels"] < 15
    passed = f1_ok and rec_ok
    print()
    print(f"F1:     {fmt(b['f1'])} → {fmt(n['f1'])}  {'✅' if f1_ok else '❌'}")
    print(f"Recall: {fmt(b['recall'], True)} → {fmt(n['recall'], True)}  {'✅' if rec_ok else '❌'}")
    for w in warn:
        print(f"⚠️  {w}")
    verdict = "O'TDI ✅" if passed else "O'TMADI ❌"
    if weak:
        verdict += f" (taxminiy: {vids} video, {b['labels']} hodisa — 20+ video, 15+ hodisa tavsiya etiladi)"
    print(f"\nDarvoza \"yangi modellar aniqligi eskisidan kam emas\": {verdict}")
    if not passed:
        print("Tavsiya: ochiq to'plamni hozircha yoqmang (Model sahifasida YOLO11 qolsin); qaysi videolarda farq "
              "borligini panelda shu ikki natijani ochib ko'ring.")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
