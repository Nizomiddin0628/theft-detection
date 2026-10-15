"""Ko'rish modellari sinovi: to'plamlar (YOLO11 / YOLOX+RTMPose), muhitlar (ONNX Runtime / OpenVINO), API.

Ishga tushirish:  python -m tests.test_models
"""
from __future__ import annotations

import sys
import time

import cv2
import numpy as np

from app import db
from app.ai import model_zoo, runtime
from app.ai.engine import Engine

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def frame(sec: float = 1.0) -> np.ndarray:
    cap = cv2.VideoCapture("samples/dokon_namuna_20s.mp4")
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok_, f = cap.read()
    cap.release()
    return f


def test_engines() -> None:
    f = frame(1.0)
    rts = ["onnxruntime"] + (["openvino"] if runtime.openvino_available() else [])
    base = None
    for ms in model_zoo.MODEL_SETS:
        if model_zoo.missing(ms):
            ok(f"{ms}: fayllar bor", False, ", ".join(model_zoo.missing(ms)))
            continue
        for rt in rts:
            db.set_settings({"ai_runtime": rt})
            eng = Engine(model_set=ms)
            t0 = time.perf_counter()
            P = eng.detect_persons(f, size=640, conf=0.3)
            dt = (time.perf_counter() - t0) * 1000
            good = (eng.model_set == ms and eng.runtime == rt and len(P) >= 2
                    and all(p.kpts.shape == (17, 3) for p in P)
                    and all(0 <= p.kpts[:, 2].min() and p.kpts[:, 2].max() <= 1.0 for p in P))
            ok(f"{ms} / {rt}: odam va skelet", good, f"{len(P)} odam, {dt:.0f} ms, litsenziya {eng.licenses}")
            objs = eng.detect_objects_in(f, np.array([300, 150, 620, 470]), size=320, conf=0.2)
            ok(f"{ms} / {rt}: qo'l atrofida buyum qidirish ishlaydi", isinstance(objs, list) and
               all(o.src == "hand" and o.box[0] >= 300 - 1 for o in objs), f"{len(objs)} ta")
            if ms == "yolo11" and rt == "onnxruntime":
                base = P
            if ms == "yolo11" and rt == "openvino" and base:
                b0 = np.array(sorted(p.box[0] for p in base))
                b1 = np.array(sorted(p.box[0] for p in P))
                ok("OpenVINO va ONNX Runtime bir xil natija (FP32)", len(b0) == len(b1) and np.abs(b0 - b1).max() < 1.0)
    db.set_settings({"ai_runtime": "auto"})


def test_calibration_and_choose() -> None:
    s = np.linspace(0, 1, 50)
    c = model_zoo.calibrate("rtmpose-t", s)
    ok("RTMPose ishonchi YOLO shkalasiga o'tkaziladi (monoton, 0..1)", (np.diff(c) >= 0).all() and c.min() >= 0
       and c.max() <= 1, f"0.6 -> {float(model_zoo.calibrate('rtmpose-t', np.array([0.6]))[0]):.2f}")
    rt, info = runtime.choose("auto", model_zoo.path("yolo11n-pose"), 2, cache=None)
    ok("Avto rejim: ikkalasini o'lchab tanlaydi", rt in runtime.RUNTIMES and info.get("fp") and
       ("openvino" in info or not runtime.openvino_available()), info.get("reason", ""))
    rt2, info2 = runtime.choose("auto", model_zoo.path("yolo11n-pose"), 2, cache=info)
    ok("Avto natijasi saqlanadi (qayta o'lchanmaydi)", rt2 == rt and info2 is info)


def test_api() -> None:
    from fastapi.testclient import TestClient

    from app.web import app

    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "admin123", "next": "/"})
    r = c.get("/api/models").json()
    ok("Modellar ro'yxati", set(r["sets"]) == set(model_zoo.MODEL_SETS) and r["active"].get("model_set"))
    ok("Noto'g'ri to'plam rad etiladi", c.post("/api/models/activate", json={"model_set": "xyz"}).status_code == 400)
    r = c.post("/api/models/activate", json={"model_set": "open"})
    ok("Ochiq to'plamni tanlash", r.status_code == 200, r.text[:100])
    for _ in range(120):
        if not c.get("/api/models").json()["state"]["busy"]:
            break
        time.sleep(0.5)
    st = c.get("/api/models").json()
    ok("Modellar fonda qayta yuklandi", st["active"]["model_set"] == "open" and not st["state"]["error"],
       f"{st['active'].get('model_set')} {st['state']}")
    ok("Engine.get() yangisini beradi", Engine.get().model_set == "open")
    # Band paytida rad etilgan tanlov sozlamani o'zgartirmaydi
    c.post("/api/models/activate", json={"model_set": "open-s"})
    r2 = c.post("/api/models/activate", json={"model_set": "yolo11"})
    ok("Band paytida ikkinchi tanlov 409 va sozlamaga tegmaydi", r2.status_code == 409 and
       db.get_setting("model_set") in ("open", "open-s"), f"{r2.status_code} {db.get_setting('model_set')}")
    for _ in range(120):
        if not c.get("/api/models").json()["state"]["busy"]:
            break
        time.sleep(0.5)
    ok("Ro'yxat bilan noto'g'ri so'rov 400 (500 emas)", c.post("/api/models/activate", json={"model_set": ["open"]}).status_code == 400
       and c.post("/api/eval/run", json={"model_sets": {"a": 1}}).status_code == 400)
    # Statik (rasmiy 416x416) YOLOX fayli — dastur YOLO11 ga qaytadi, to'xtamaydi
    import shutil
    from pathlib import Path
    stat = Path("/root/bench/models/yolox_nano.onnx")
    if stat.exists():
        dst = model_zoo.path("yolox-nano")
        shutil.copy(dst, dst.with_suffix(".keep"))
        shutil.copy(stat, dst)
        try:
            eng = Engine(model_set="open")
            ok("Statik YOLOX fayli bilan YOLO11 ga qaytadi", eng.model_set == "yolo11")
        finally:
            shutil.move(str(dst.with_suffix(".keep")), dst)
    r = c.post("/api/models/upload", files={"file": ("nimadir.zip", b"xx", "application/zip")})
    ok("Noma'lum fayl nomi rad etiladi", r.status_code == 400)
    r = c.post("/api/models/upload", files={"file": ("rtmpose-t_buzilgan.zip", b"bu zip emas", "application/zip")})
    ok("Buzilgan zip rad etiladi, eski model joyida", r.status_code == 400 and model_zoo.path("rtmpose-t").exists())
    c.post("/api/models/activate", json={"model_set": "yolo11"})
    for _ in range(120):
        if not c.get("/api/models").json()["state"]["busy"]:
            break
        time.sleep(0.5)
    ok("Sahifa ochiladi", c.get("/model").status_code == 200)


def main() -> int:
    db.init_db()
    test_calibration_and_choose()
    test_engines()
    test_api()
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
