"""Ko'rish modellari sinovi: to'plamlar (YOLO11 / YOLOX + MoveNet / YOLOX + RTMPose), muhitlar (ONNX Runtime /
OpenVINO), API, 2.7.0: litsenziyasi toza to'plam (open-m — tavsiya, standart emas), zaxira zanjiri, SHA-256,
oldindan yuklab olish, tayyorlik bandi.

Ishga tushirish:  python -m tests.test_models

Fayli yo'q va dastur bilan kelmaydigan to'plamlar (RTMPose — OpenMMLab saytidan, MoveNet Thunder — GitHub'dan)
"o'tkazib yuborildi" deb chiqadi (xato emas). YOLO11 va open-ml (YOLOX-tiny + MoveNet Lightning) dastur bilan
birga keladi — ular bo'lmasa xato.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_models_")   # hech qachon jonli data/ emas
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.pop("HUSHYOR_MODEL_SET", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app import db  # noqa: E402
from app.ai import model_zoo, runtime  # noqa: E402
from app.ai.engine import Engine  # noqa: E402

results: list[bool] = []
BUNDLED = ("yolo11", "open-ml")          # fayllari repoda — har doim bo'lishi kerak


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def skip(name: str, why: str) -> None:
    print(f"  SKIP {name}  — {why}", flush=True)


def frame(sec: float = 1.0) -> np.ndarray:
    cap = cv2.VideoCapture(str(ROOT / "samples/dokon_namuna_20s.mp4"))
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok_, f = cap.read()
    cap.release()
    return f


def wait_idle(c, n: int = 240) -> dict:
    st = {}
    for _ in range(n):
        st = c.get("/api/models").json()
        if not st["state"]["busy"]:
            break
        time.sleep(0.5)
    return st


class TempModels:
    """model_zoo.MODELS_DIR ni vaqtinchalik papkaga almashtirish (haqiqiy models/ ga tegmaslik uchun).
    `copy` — nusxa olinadigan fayllar kalitlari; qolganlari "yo'q" bo'ladi."""

    def __init__(self, copy=()):
        self.copy = copy

    def __enter__(self) -> Path:
        self.old = model_zoo.MODELS_DIR
        self.dir = Path(tempfile.mkdtemp(prefix="hushyor_mz_"))
        for k in self.copy:
            src = self.old / model_zoo.FILES[k]["file"]
            if src.exists():
                shutil.copy(src, self.dir / src.name)
        model_zoo.MODELS_DIR = self.dir
        return self.dir

    def __exit__(self, *a) -> None:
        model_zoo.MODELS_DIR = self.old
        shutil.rmtree(self.dir, ignore_errors=True)


def test_calibration_and_choose() -> None:
    s = np.linspace(0, 1, 50)
    for key in ("rtmpose-t", "movenet-thunder", "movenet-lightning"):
        c = model_zoo.calibrate(key, s)
        ok(f"{key} ishonchi YOLO shkalasiga o'tkaziladi (monoton, 0..1)", (np.diff(c) >= 0).all() and c.min() >= 0
           and c.max() <= 1, f"0.5 -> {float(model_zoo.calibrate(key, np.array([0.5]))[0]):.2f}")
    ok("Model turi: yolo / rtmpose / movenet", model_zoo.kind("yolo11n-pose") == "yolo" and
       model_zoo.kind("rtmpose-t") == "rtmpose" and model_zoo.kind("movenet-thunder") == "movenet")
    rt, info = runtime.choose("auto", model_zoo.path("yolo11n-pose"), 2, cache=None)
    ok("Avto rejim: ikkalasini o'lchab tanlaydi", rt in runtime.RUNTIMES and info.get("fp") and
       ("openvino" in info or not runtime.openvino_available()), info.get("reason", ""))
    rt2, info2 = runtime.choose("auto", model_zoo.path("yolo11n-pose"), 2, cache=info)
    ok("Avto natijasi saqlanadi (qayta o'lchanmaydi)", rt2 == rt and info2 is info)


def test_defaults_and_licenses() -> None:
    """2.7.0: standart — YOLO11 (ochiq to'plam yiqilishda yomonroq, docs/MODELS.md); ochiq tavsiya — open-m."""
    ok("Standart to'plam YOLO11, ochiq tavsiya open-m (YOLOX + MoveNet, Apache-2.0)", model_zoo.DEFAULT_SET == "yolo11"
       and model_zoo.OPEN_SET == "open-m" and model_zoo.MODEL_SETS["open-m"]["license"] == "Apache-2.0")
    ok("Yangi o'rnatish (bo'sh baza): model_set = yolo11", db.get_setting("model_set") == "yolo11",
       str(db.get_setting("model_set")))
    ok("Ochiq to'plam izohida yiqilish kamchiligi yozilgan (rostini)", "yiqilish" in model_zoo.MODEL_SETS["open-m"]["hint"])
    db.set_settings({"model_set": "open-m"})
    db.init_db()                                          # yangilangan dastur eski bazani ochganda
    ok("Tanlangan to'plam qayta ishga tushganda saqlanadi (o'zi almashmaydi)", db.get_setting("model_set") == "open-m")
    ok("Zaxira to'plamlar: open-ml, keyin yolo11", model_zoo.FALLBACK == ("open-ml", "yolo11"))
    for ms in model_zoo.MODEL_SETS.values():
        keys = [ms[k] for k in ("person", "pose", "det") if ms.get(k)]
        lic = {model_zoo.FILES[k]["license"] for k in keys}
        if ms["license"] != ("Apache-2.0" if lic == {"Apache-2.0"} else "AGPL-3.0"):
            ok(f"{ms['label']}: litsenziya belgisi fayllarga mos", False, str(lic))
            break
    else:
        ok("Har to'plam litsenziya belgisi uning fayllariga mos", True)
    for k in BUNDLED:
        ok(f"{k} fayllari dastur bilan birga keladi", not model_zoo.missing(k), ", ".join(model_zoo.missing(k)))
    for key in ("movenet-thunder", "movenet-lightning"):
        f = model_zoo.FILES[key]
        ok(f"{key}: o'zgarmas commit URL va SHA-256", "/515743a113e49b4f31677b1f6252c0041f237e30/" in f["url"] and
           len(f.get("sha256", "")) == 64)
    p = model_zoo.path("movenet-lightning")
    if p.exists():
        ok("Dastur bilan kelgan MoveNet Lightning SHA-256 mos", model_zoo.sha256(p) == model_zoo.FILES[
            "movenet-lightning"]["sha256"])
    p = model_zoo.path("movenet-thunder")
    if p.exists():
        ok("MoveNet Thunder SHA-256 mos", model_zoo.sha256(p) == model_zoo.FILES["movenet-thunder"]["sha256"])
    lic = (ROOT / "models/LICENSES.md").read_text(encoding="utf-8")
    ok("models/LICENSES.md da MoveNet bor", "MoveNet" in lic and "Apache-2.0" in lic)


def test_engines() -> None:
    f = frame(1.0)
    rts = ["onnxruntime"] + (["openvino"] if runtime.openvino_available() else [])
    base = {}
    for ms in model_zoo.MODEL_SETS:
        if model_zoo.missing(ms):
            if ms in BUNDLED:
                ok(f"{ms}: fayllar bor", False, ", ".join(model_zoo.missing(ms)))
            else:
                skip(f"{ms}: odam va skelet", "fayl yo'q: " + ", ".join(model_zoo.missing(ms)))
            continue
        for rt in rts:
            db.set_settings({"ai_runtime": rt})
            eng = Engine(model_set=ms, strict=True)
            eng.detect_persons(f, size=640, conf=0.3)            # birinchi chaqiruv (isinish) — vaqtga kirmaydi
            t0 = time.perf_counter()
            P = eng.detect_persons(f, size=640, conf=0.3)
            dt = (time.perf_counter() - t0) * 1000
            good = (eng.model_set == ms and eng.runtime == rt and len(P) >= 2
                    and all(p.kpts.shape == (17, 3) for p in P)
                    and all(0 <= p.kpts[:, 2].min() and p.kpts[:, 2].max() <= 1.0 for p in P))
            ok(f"{ms} / {rt}: odam va skelet", good, f"{len(P)} odam, {dt:.0f} ms, litsenziya {eng.licenses}")
            if model_zoo.kind(eng.pose_name) == "movenet":
                # Skelet nuqtalari odam ramkasi atrofida (kesmadan qaytarish to'g'ri)
                inside = []
                for p in P:
                    x0, y0, x1, y1 = p.box
                    w, h = x1 - x0, y1 - y0
                    v = p.kpts[p.kpts[:, 2] > 0.5]
                    if len(v):
                        inside.append(((v[:, 0] > x0 - 0.3 * w) & (v[:, 0] < x1 + 0.3 * w) &
                                       (v[:, 1] > y0 - 0.3 * h) & (v[:, 1] < y1 + 0.3 * h)).mean())
                ok(f"{ms} / {rt}: ishonchli nuqtalar odam ramkasida", inside and min(inside) > 0.9,
                   f"{[round(float(x), 2) for x in inside]}")
                base.setdefault(ms, {})[rt] = P
            objs = eng.detect_objects_in(f, np.array([300, 150, 620, 470]), size=320, conf=0.2)
            ok(f"{ms} / {rt}: qo'l atrofida buyum qidirish ishlaydi", isinstance(objs, list) and
               all(o.src == "hand" and o.box[0] >= 300 - 1 for o in objs), f"{len(objs)} ta")
            if ms == "yolo11":
                base.setdefault(ms, {})[rt] = P
    for ms, d in base.items():
        if len(d) == 2:
            b0 = np.array(sorted(p.box[0] for p in d["onnxruntime"]))
            b1 = np.array(sorted(p.box[0] for p in d["openvino"]))
            ok(f"{ms}: OpenVINO va ONNX Runtime bir xil natija (FP32)", len(b0) == len(b1) and
               np.abs(b0 - b1).max() < 1.5, f"{len(b0)} / {len(b1)}")
    db.set_settings({"ai_runtime": "onnxruntime"})


def test_open_m_details() -> None:
    """open-m: odam detektori ≤ 512 px; MoveNet va YOLO11 bir xil odamlarni topadi."""
    if model_zoo.missing("open-ml"):
        return
    f = frame(4.0)
    eng = Engine(model_set="open-ml", strict=True)
    shapes = []
    orig = eng.person

    class Spy:
        def __getattr__(self, a):
            return getattr(orig, a)

        def __call__(self, x):
            shapes.append(x.shape)
            return orig(x)
    eng.person = Spy()
    P = eng.detect_persons(f, size=640, conf=0.3)
    eng.person = orig
    ok("open-ml: odam detektori ≤ 512 px da ishlaydi (640 so'ralganda ham)", shapes and max(shapes[0][2:]) <= 512,
       str(shapes[:1]))
    y = Engine(model_set="yolo11", strict=True).detect_persons(f, size=640, conf=0.3)

    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
        return ix * iy / u if u > 0 else 0
    matched = sum(1 for q in y if any(iou(q.box, p.box) >= 0.5 for p in P))
    ok("open-ml va YOLO11 bir xil odamlarni topadi (IoU ≥ 0.5)", y and matched >= len(y) - 1,
       f"YOLO11 {len(y)}, mos {matched}, open-ml {len(P)}")


def test_fallback() -> None:
    """Standart to'plam fayli yo'q / buzuq — kameralar to'xtamaydi: open-ml, u ham bo'lmasa YOLO11."""
    db.set_settings({"model_set": "open-m"})
    with TempModels(copy=("yolox-tiny", "movenet-lightning", "yolo11n-pose", "yolo11n")):
        eng = Engine()
        ok("Thunder yo'q → zaxira open-ml (litsenziyasi toza) ishlaydi", eng.model_set == "open-ml", eng.model_set)
    with TempModels(copy=("yolox-tiny", "yolo11n-pose", "yolo11n")):
        eng = Engine()
        ok("MoveNet umuman yo'q → YOLO11 ishlaydi", eng.model_set == "yolo11", eng.model_set)
    with TempModels(copy=("yolox-tiny", "movenet-lightning", "yolo11n-pose", "yolo11n")) as d:
        (d / "movenet_thunder.onnx").write_bytes(b"bu onnx emas")
        eng = Engine()
        ok("Buzuq Thunder fayli → open-ml, buzuq fayl chetga olinadi (qayta yuklab olish mumkin)",
           eng.model_set == "open-ml" and not (d / "movenet_thunder.onnx").exists() and
           (d / "movenet_thunder.onnx.broken").exists(), eng.model_set)
    with TempModels(copy=("yolox-tiny", "yolo11n-pose")):
        eng = Engine()
        ok("MoveNet yo'q va YOLO11 buyum modeli yo'q → baribir YOLO11 (buyum qidirish o'chiq, kameralar to'xtamaydi)",
           eng.model_set == "yolo11" and eng.det is None, eng.model_set)
    with TempModels(copy=("yolox-tiny", "yolo11n-pose", "yolo11n")):
        try:
            Engine(model_set="open-m", strict=True)
            ok("Qat'iy rejim (panelda tanlash): yo'q to'plam xato beradi, zaxiraga jim o'tmaydi", False)
        except FileNotFoundError:
            ok("Qat'iy rejim (panelda tanlash): yo'q to'plam xato beradi, zaxiraga jim o'tmaydi", True)
    os.environ["HUSHYOR_MODEL_SET"] = "yolo11"
    try:
        ok("HUSHYOR_MODEL_SET sozlamadan ustun (sinov/baholash uchun)", Engine().model_set == "yolo11")
        os.environ["HUSHYOR_MODEL_SET"] = "open-m"
        with TempModels(copy=("yolox-tiny", "movenet-lightning", "yolo11n-pose", "yolo11n")):
            try:
                Engine()
                ok("HUSHYOR_MODEL_SET qat'iy: fayli yo'q bo'lsa xato (zaxira natijasi o'tib ketmaydi)", False)
            except FileNotFoundError:
                ok("HUSHYOR_MODEL_SET qat'iy: fayli yo'q bo'lsa xato (zaxira natijasi o'tib ketmaydi)", True)
        os.environ["HUSHYOR_MODEL_SET"] = "open_m"
        try:
            Engine()
            ok("HUSHYOR_MODEL_SET da xato nom — xato (YOLO11 natijasi boshqa nom bilan o'tib ketmaydi)", False)
        except ValueError:
            ok("HUSHYOR_MODEL_SET da xato nom — xato (YOLO11 natijasi boshqa nom bilan o'tib ketmaydi)", True)
    finally:
        os.environ.pop("HUSHYOR_MODEL_SET", None)
    db.set_settings({"model_set": "nimadir"})
    ok("Noma'lum to'plam nomi → standart (YOLO11)", Engine().model_set == "yolo11")
    db.set_settings({"model_set": "open-m"})


def test_download_checks() -> None:
    """Yuklab olingan fayl SHA-256 bilan tekshiriladi; MoveNet shakli tekshiriladi."""
    light = model_zoo.path("movenet-lightning")
    if not light.exists():
        return
    real = model_zoo._download
    try:
        model_zoo._download = lambda url, dst: shutil.copyfile(light, dst)     # "internet" lightning faylini beradi
        with TempModels() as d:
            try:
                model_zoo.ensure("movenet-thunder")
                ok("SHA-256 mos kelmagan yuklama rad etiladi", False)
            except ValueError as e:
                ok("SHA-256 mos kelmagan yuklama rad etiladi, fayl yozilmaydi", "SHA-256" in str(e) and
                   not (d / "movenet_thunder.onnx").exists(), str(e))
            p = model_zoo.ensure("movenet-lightning")
            ok("To'g'ri fayl yuklab olinadi va tekshiriladi", p.exists() and model_zoo.sha256(p) ==
               model_zoo.FILES["movenet-lightning"]["sha256"])
            ok("Yo'q fayllar ro'yxati takrorsiz (odam = buyum detektori)", model_zoo.missing("open-ml") == ["yolox-tiny"],
               str(model_zoo.missing("open-ml")))
        thunder = model_zoo.path("movenet-thunder")
        if thunder.exists():
            model_zoo._download = lambda url, dst: shutil.copyfile(thunder, dst)
            with TempModels() as d:
                got = model_zoo.prefetch()
                ok("prefetch(): ochiq to'plamning SHA-256 li fayli yuklanadi (YOLOX — URL bor, lekin xeshsiz: yo'q)",
                   got == ["movenet-thunder"] and (d / "movenet_thunder.onnx").exists(), str(got))
                ok("prefetch() qayta chaqirilsa — hech narsa yuklamaydi", model_zoo.prefetch() == [])
    finally:
        model_zoo._download = real
    try:
        model_zoo.validate("movenet-thunder", light)
        ok("Thunder o'rniga Lightning fayli (192 px) rad etiladi", False)
    except ValueError as e:
        ok("Thunder o'rniga Lightning fayli (192 px) rad etiladi", "MoveNet" in str(e), str(e)[:80])
    try:
        model_zoo.validate("movenet-lightning", model_zoo.path("yolox-tiny"))
        ok("MoveNet o'rniga YOLOX fayli rad etiladi", False)
    except ValueError:
        ok("MoveNet o'rniga YOLOX fayli rad etiladi", True)
    info = model_zoo.validate("movenet-lightning", light)
    ok("MoveNet Lightning tekshiruvdan o'tadi", info["input"] == ["1", "192", "192", "3"], str(info))


def test_readiness_and_bg() -> None:
    from app import pilot, web

    def item():
        return next((x for x in pilot.readiness(force=True)["items"] if x["key"] == "license"), None)
    old = Engine._instance
    try:
        Engine._instance = None
        db.set_settings({"model_set": "open-m"})
        it = item()
        ok("Tayyorlik: ochiq to'plam — litsenziya «ok»", it and it["state"] == "ok" and it["optional"] and
           it["link"] == "/model", str(it))
        db.set_settings({"model_set": "yolo11"})
        it = item()
        ok("Tayyorlik: YOLO11 — ogohlantirish (sotishdan oldin o'ting)", it and it["state"] == "warn" and
           "AGPL" in it["detail"], str(it))
        db.set_settings({"model_set": "open-m"})
        Engine._instance = Engine(model_set="yolo11", strict=True)
        it = item()
        ok("Tayyorlik: haqiqatda ishlayotgan to'plam hisobga olinadi (zaxira YOLO11 bo'lsa — ogohlantirish)",
           it and it["state"] == "warn", str(it))
    finally:
        Engine._instance = old
    # Fonda yuklab olish: tanlangan to'plam ishlamayapti va fayli yo'q, lekin URL bor → vazifa boshlanadi
    calls = []
    real = web._models_bg
    web._models_bg = lambda task, fn: calls.append(task)
    try:
        with TempModels(copy=("yolox-tiny", "movenet-lightning", "yolo11n-pose", "yolo11n")):
            db.set_settings({"model_set": "open-m"})
            eng = Engine()
            web._ensure_model_set(eng)
            ok("Yangi o'rnatish: Thunder fonda yuklab olinadi (shu orada open-ml ishlaydi)",
               eng.model_set == "open-ml" and len(calls) == 1, f"{eng.model_set} {calls}")
            calls.clear()
            db.set_settings({"model_set": "yolo11"})
            web._ensure_model_set(Engine(model_set="yolo11", strict=True))
            ok("Ishlayotgan to'plam tanlanganiga teng — hech narsa yuklanmaydi", not calls, str(calls))
    finally:
        web._models_bg = real
        db.set_settings({"model_set": "open-m"})


def test_api() -> None:
    from fastapi.testclient import TestClient

    from app.web import app

    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "next": "/"})
    wait_idle(c)
    r = c.get("/api/models").json()
    ok("Modellar ro'yxati", set(r["sets"]) == set(model_zoo.MODEL_SETS) and r["active"].get("model_set"))
    ok("Noto'g'ri to'plam rad etiladi", c.post("/api/models/activate", json={"model_set": "xyz"}).status_code == 400)
    r = c.post("/api/models/activate", json={"model_set": "open-ml"})
    ok("Ochiq (tez) to'plamni tanlash", r.status_code == 200, r.text[:100])
    st = wait_idle(c)
    ok("Modellar fonda qayta yuklandi", st["active"]["model_set"] == "open-ml" and not st["state"]["error"],
       f"{st['active'].get('model_set')} {st['state']}")
    ok("Engine.get() yangisini beradi", Engine.get().model_set == "open-ml")
    ok("Faol to'plam litsenziyasi Apache-2.0", Engine.get().licenses == ["Apache-2.0"], str(Engine.get().licenses))
    # Band paytida rad etilgan tanlov sozlamani o'zgartirmaydi (band holat aniq — boshqa vazifa "ketmoqda")
    from app import web
    web._models_claim("sinov")
    try:
        r2 = c.post("/api/models/activate", json={"model_set": "yolo11"})
    finally:
        web.models_state["busy"] = False
    ok("Band paytida tanlov 409 va sozlamaga tegmaydi", r2.status_code == 409 and
       db.get_setting("model_set") == "open-ml", f"{r2.status_code} {db.get_setting('model_set')}")
    c.post("/api/models/activate", json={"model_set": "yolo11"})
    st = wait_idle(c)
    ok("YOLO11 ga qaytish mumkin (bir tugma)", st["active"]["model_set"] == "yolo11" and
       db.get_setting("model_set") == "yolo11", st["active"].get("model_set"))
    ok("Ro'yxat bilan noto'g'ri so'rov 400 (500 emas)", c.post("/api/models/activate", json={"model_set": ["open"]}).status_code == 400
       and c.post("/api/eval/run", json={"model_sets": {"a": 1}}).status_code == 400)
    if model_zoo.missing("open-m"):
        r = c.post("/api/models/activate", json={"model_set": "open-m"})
        ok("Fayli yo'q to'plamni tanlash 400 (avval yuklab olish)", r.status_code == 400 and "yuklab" in r.text)
    # Statik (rasmiy 416x416) YOLOX fayli — dastur YOLO11 ga qaytadi, to'xtamaydi
    stat = Path("/root/bench/models/yolox_nano.onnx")
    if stat.exists() and not model_zoo.missing("open"):
        with TempModels(copy=("rtmpose-t", "yolox-tiny", "yolo11n-pose", "yolo11n")) as d:
            shutil.copy(stat, d / "yolox_nano.onnx")
            eng = Engine(model_set="open")
            ok("Statik YOLOX fayli bilan zaxiraga qaytadi", eng.model_set in model_zoo.FALLBACK, eng.model_set)
    # Qo'lda yuklash: fayl nomidan model aniqlanadi
    from app.web import _model_key_from_name as kn
    ok("Fayl nomidan model: movenet_singlepose_thunder_4.onnx, movenet_lightning.onnx, yolox_tiny.onnx",
       kn("movenet_singlepose_thunder_4.onnx") == "movenet-thunder" and kn("MoveNet_Lightning.onnx") ==
       "movenet-lightning" and kn("yolox_tiny.onnx") == "yolox-tiny" and kn("thunder.onnx") is None)
    r = c.post("/api/models/upload", files={"file": ("nimadir.zip", b"xx", "application/zip")})
    ok("Noma'lum fayl nomi rad etiladi", r.status_code == 400 and "movenet" in r.text, r.text[:120])
    with TempModels(copy=("movenet-lightning",)) as d:
        before = model_zoo.sha256(d / "movenet_lightning.onnx")
        r = c.post("/api/models/upload", files={"file": ("movenet_lightning.onnx", b"bu onnx emas",
                                                         "application/octet-stream")})
        ok("Buzilgan MoveNet fayli rad etiladi, eski model joyida", r.status_code == 400 and
           model_zoo.sha256(d / "movenet_lightning.onnx") == before, f"{r.status_code} {r.text[:80]}")
        light = ROOT / "models/movenet_lightning.onnx"
        (d / "movenet_lightning.onnx").unlink()
        with open(light, "rb") as fh:
            r = c.post("/api/models/upload", files={"file": ("movenet_singlepose_lightning_4.onnx", fh,
                                                             "application/octet-stream")})
        ok("To'g'ri MoveNet faylini qo'lda yuklash", r.status_code == 200 and r.json().get("model") ==
           "movenet-lightning" and (d / "movenet_lightning.onnx").exists(), r.text[:80])
    page = c.get("/model")
    ok("Sahifa ochiladi (MoveNet qo'lda yuklash ko'rsatmasi bilan)", page.status_code == 200 and
       "movenet_thunder.onnx" in page.text and 'id="vlic"' in page.text)


def main() -> int:
    db.init_db()
    test_calibration_and_choose()
    test_defaults_and_licenses()
    test_engines()
    test_open_m_details()
    test_fallback()
    test_download_checks()
    test_readiness_and_bg()
    test_api()
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
