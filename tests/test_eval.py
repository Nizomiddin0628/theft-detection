"""Aniqlik sinovi (baholash) testlari: solishtirish mantig'i va to'liq API (haqiqiy model bilan, ~20 s video).

Ishga tushirish:  python -m tests.test_eval
"""
from __future__ import annotations

import sys
import time

from app import db, evaluation

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def test_match() -> None:
    labels = [{"start": 10.0, "end": 12.0, "kind": "pocket"}, {"start": 40.0, "end": 41.0, "kind": "bag"}]
    alerts = [{"t": 9.0, "kind": "pocket"},       # boshlanishdan 1 s oldin — to'g'ri (2 s tolerantlik)
              {"t": 13.0, "kind": "pocket"},      # o'sha hodisaga takroriy — xato emas
              {"t": 25.0, "kind": "torso"},       # belgisiz joy — xato
              {"t": 48.5, "kind": "bag"}]         # tugashdan 7.5 s keyin — kech (6 s dan ko'p) → xato
    m = evaluation.match(labels, alerts)
    ok("To'g'ri signal topildi (oldinroq kelgan ham)", len(m["tp"]) == 1 and m["tp"][0]["delay"] == -1.0)
    ok("Takroriy signal xato hisoblanmaydi", m["dup"] == 1)
    ok("Belgisiz va juda kech signal — xato", len(m["fp"]) == 2, str([f["alert"]["t"] for f in m["fp"]]))
    ok("Signal kelmagan hodisa — o'tkazilgan", len(m["fn"]) == 1 and m["fn"][0]["label"]["start"] == 40.0)
    m2 = evaluation.match([{"start": 10.0, "end": 12.0, "kind": "pocket"}, {"start": 15.0, "end": 17.0, "kind": "bag"}],
                          [{"t": 11.0, "kind": "pocket"}, {"t": 16.0, "kind": "bag"}])
    ok("Yaqin ikki hodisa — har biri o'z signalini oladi", len(m2["tp"]) == 2 and not m2["fn"] and m2["dup"] == 0,
       f"TP {len(m2['tp'])} FN {len(m2['fn'])} dup {m2['dup']}")
    s = evaluation.summarize([{**m, "duration": 1800.0}])
    ok("Aniqlik 1/3, topish 1/2, xato/soat 4", abs(s["precision"] - 1 / 3) < 1e-9 and s["recall"] == 0.5 and
       s["fp_per_hour"] == 4.0, str(s))
    e = evaluation.summarize([{**evaluation.match([], []), "duration": 600.0}])
    ok("Hodisasiz va signalsiz video: aniqlik/topish aniqlanmagan, xato 0", e["precision"] is None and
       e["recall"] is None and e["fp_per_hour"] == 0.0)
    cl = evaluation.clean_labels([{"start": 5, "end": 3, "kind": "x"}, {"start": "a"}, {"start": 1, "end": 1}])
    ok("Belgilar tozalanadi va tartiblanadi", cl == [
        {"start": 1.0, "end": 1.2, "kind": "other", "note": ""}, {"start": 3.0, "end": 5.0, "kind": "other", "note": ""}],
       str(cl))


def test_api() -> None:
    from fastapi.testclient import TestClient

    from app.web import app

    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"})
    db.set_settings({"threshold": 35, "dwell_sec": 0.3, "use_ml": "1"})
    with open("samples/dokon_namuna_20s.mp4", "rb") as f:
        r = c.post("/api/eval/videos", files={"file": ("dokon.mp4", f, "video/mp4")}, data={"zone_source": ""})
    ok("Video yuklandi", r.status_code == 200 and r.json()["video"]["play_status"] == "tayyor", r.text[:150])
    vid = r.json()["video"]["id"]
    ok("Brauzer uchun video beriladi", c.get(f"/media/eval/{vid}").status_code == 200)
    r = c.post("/api/eval/run", json={})
    ok("Belgilash tugallanmagan video baholanmaydi", r.status_code == 400)
    c.post(f"/api/eval/videos/{vid}", json={"labels": [{"start": 17.0, "end": 19.5, "kind": "torso"}], "reviewed": 1})
    v = c.get("/api/eval/videos").json()[0]
    ok("Belgilar saqlandi", v["reviewed"] == 1 and v["labels"][0]["start"] == 17.0)
    r = c.post("/api/eval/run", json={})
    ok("Baholash boshlandi", r.status_code == 200, r.text[:100])
    run_id = r.json()["run_id"]
    ok("Ikkinchi marta bir vaqtda boshlab bo'lmaydi", c.post("/api/eval/run", json={}).status_code == 409)
    t0 = time.time()
    while time.time() - t0 < 300 and c.get("/api/eval/status").json()["running"]:
        time.sleep(1)
    d = c.get(f"/api/eval/runs/{run_id}").json()
    ok("Baholash tugadi", d["status"] == "tayyor", f"{d['status']} {d.get('error')}")
    det = d["details"][0]
    ok("Signal belgilangan hodisa bilan mos tushdi (TP)", d["tp"] == 1 and d["fn"] == 0,
       f"TP {d['tp']} FP {d['fp']} FN {d['fn']} signallar {[a['t'] for a in det['alerts']]}")
    ok("Natijada aniqlik va topish bor", d["recall"] == 1.0 and d["precision"] is not None)
    ok("Hodisalar va Telegram yaratilmadi", (db.scalar("SELECT COUNT(*) FROM events") or 0) == 0)
    ok("Tarix ro'yxatida ko'rinadi", any(x["id"] == run_id for x in c.get("/api/eval/runs").json()))
    ok("Sahifa ochiladi", c.get("/eval").status_code == 200)
    c.post(f"/api/eval/videos/{vid}/delete")
    ok("Video o'chirildi", not c.get("/api/eval/videos").json())


def main() -> int:
    db.init_db()
    test_match()
    test_api()
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
