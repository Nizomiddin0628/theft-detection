"""AI modellar to'plamlari, ularning litsenziyasi, fayllari va yuklab olish.

Mahsulot sotilishi uchun modellar litsenziyasi muhim:
  * YOLO11 (Ultralytics) — AGPL-3.0: yopiq mahsulotda ishlatish uchun pullik Enterprise litsenziya kerak.
  * YOLOX (Megvii) va RTMPose (OpenMMLab) — Apache-2.0: tijoratda erkin (litsenziya matni saqlanadi).

To'plamlar:
  yolo11  — YOLO11n-pose (odam + skelet) va YOLO11n (buyum). Hozirgi standart.
  open    — YOLOX-nano (odam) + RTMPose-t (skelet, har odam alohida) + YOLOX-tiny (buyum). Apache-2.0.
  open-s  — YOLOX-tiny (odam) + RTMPose-s + YOLOX-tiny. Aniqroq, lekin og'irroq.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from ..config import MODELS_DIR
from ..logutil import get_logger

log = get_logger("models")

YOLOX_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/{}.onnx"
RTM_URL = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/{}.zip"

# kalit -> fayl, litsenziya, manba
FILES = {
    "yolo11n-pose": {"file": "yolo11n-pose.onnx", "license": "AGPL-3.0", "source": "Ultralytics"},
    "yolo11n": {"file": "yolo11n.onnx", "license": "AGPL-3.0", "source": "Ultralytics"},
    "yolox-nano": {"file": "yolox_nano.onnx", "license": "Apache-2.0", "source": "Megvii YOLOX 0.1.1rc0",
                   "url": YOLOX_URL.format("yolox_nano"), "dynamic": True},
    "yolox-tiny": {"file": "yolox_tiny.onnx", "license": "Apache-2.0", "source": "Megvii YOLOX 0.1.1rc0",
                   "url": YOLOX_URL.format("yolox_tiny"), "dynamic": True},
    "rtmpose-t": {"file": "rtmpose_t.onnx", "license": "Apache-2.0", "source": "OpenMMLab RTMPose (body7)",
                  "url": RTM_URL.format("rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504")},
    "rtmpose-s": {"file": "rtmpose_s.onnx", "license": "Apache-2.0", "source": "OpenMMLab RTMPose (body7)",
                  "url": RTM_URL.format("rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504")},
}

MODEL_SETS = {
    "yolo11": {"label": "YOLO11 (hozirgi)", "license": "AGPL-3.0",
               "hint": "Sinalgan. Tijoratda sotish uchun Ultralytics litsenziyasi kerak.",
               "pose": "yolo11n-pose", "det": "yolo11n"},
    "open": {"label": "YOLOX + RTMPose-t (ochiq)", "license": "Apache-2.0",
             "hint": "Litsenziyasi toza. Skelet nuqtalari aniqroq (har odam alohida kattalashtiriladi).",
             "person": "yolox-nano", "pose": "rtmpose-t", "det": "yolox-tiny"},
    "open-s": {"label": "YOLOX + RTMPose-s (ochiq, aniqroq)", "license": "Apache-2.0",
               "hint": "Eng aniq, lekin og'irroq — kuchli kompyuter yoki kam kamera uchun.",
               "person": "yolox-tiny", "pose": "rtmpose-s", "det": "yolox-tiny"},
}

# RTMPose nuqta ishonchini YOLO11 shkalasiga o'tkazish (bir xil odamlarda kvantillar bo'yicha moslangan,
# do'kon videolarida). Tahlilchi chegaralari (0.3 / 0.45 / 0.6) shu shkala uchun sozlangan.
KPT_CALIB = {
    "rtmpose-t": ([0.047, 0.227, 0.278, 0.327, 0.385, 0.454, 0.507, 0.548, 0.583, 0.609, 0.636, 0.66, 0.681,
                   0.7, 0.717, 0.735, 0.757, 0.778, 0.802, 0.837, 1.0],
                  [0.004, 0.05, 0.127, 0.237, 0.345, 0.437, 0.524, 0.635, 0.732, 0.796, 0.843, 0.889, 0.925,
                   0.945, 0.96, 0.972, 0.982, 0.988, 0.993, 0.996, 0.999]),
    "rtmpose-s": ([0.058, 0.262, 0.329, 0.39, 0.458, 0.525, 0.574, 0.621, 0.658, 0.687, 0.708, 0.726, 0.744,
                   0.764, 0.783, 0.804, 0.823, 0.844, 0.871, 0.906, 1.138],
                  [0.004, 0.05, 0.127, 0.237, 0.345, 0.437, 0.524, 0.635, 0.732, 0.796, 0.843, 0.889, 0.925,
                   0.945, 0.96, 0.972, 0.982, 0.988, 0.993, 0.996, 0.999]),
}


def calibrate(model: str, scores: np.ndarray) -> np.ndarray:
    tab = KPT_CALIB.get(model)
    if not tab:
        return scores
    return np.interp(scores, tab[0], tab[1]).astype(np.float32)


def path(key: str) -> Path:
    return MODELS_DIR / FILES[key]["file"]


def missing(set_name: str) -> list[str]:
    ms = MODEL_SETS.get(set_name, {})
    keys = [ms[k] for k in ("person", "pose", "det") if ms.get(k)]
    return [k for k in keys if not path(k).exists()]


def status() -> dict:
    return {name: {**{k: v for k, v in ms.items()}, "missing": missing(name)} for name, ms in MODEL_SETS.items()}


# ----------------------------------------------------------------------------- yuklab olish
MAX_MODEL_BYTES = 300 * 1024 * 1024      # yuklab olish va qo'lda yuklash chegarasi


def _download(url: str, dst: Path) -> None:
    """Oqimli yuklash: fayl xotiraga to'liq o'qilmaydi (8 GB RAM'li kompyuterda 100+ MB arxiv ham muammo emas)."""
    req = urllib.request.Request(url, headers={"User-Agent": "HushyorAI"})
    total = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MODEL_BYTES:
                raise ValueError("fayl juda katta")
            f.write(chunk)


def make_yolox_dynamic(src: Path, dst: Path) -> None:
    """YOLOX ONNX'ini istalgan o'lchamga moslash (Reshape [1,85,N] -> [1,85,-1]). onnx paketi kerak."""
    import onnx
    from onnx import numpy_helper

    m = onnx.load(str(src))
    g = m.graph
    inits = {i.name: i for i in g.initializer}
    consts = {n.output[0]: n for n in g.node if n.op_type == "Constant"}
    for n in g.node:
        if n.op_type != "Reshape":
            continue
        name = n.input[1]
        if name in inits:
            arr = numpy_helper.to_array(inits[name]).copy()
            if arr.ndim == 1 and len(arr) == 3 and arr[0] == 1:
                arr[2] = -1
                inits[name].CopyFrom(numpy_helper.from_array(arr.astype(np.int64), name))
        elif name in consts:
            arr = numpy_helper.to_array(consts[name].attribute[0].t).copy()
            if arr.ndim == 1 and len(arr) == 3 and arr[0] == 1:
                arr[2] = -1
                consts[name].attribute[0].t.CopyFrom(numpy_helper.from_array(arr.astype(np.int64)))
    dims = g.input[0].type.tensor_type.shape.dim
    dims[2].dim_param, dims[3].dim_param = "height", "width"
    g.output[0].type.tensor_type.shape.dim[1].dim_param = "anchors"
    del g.value_info[:]
    onnx.checker.check_model(m)
    onnx.save(m, str(dst))


def validate(key: str, p: Path) -> dict:
    """Fayl haqiqatan shu model ekanini tekshirish: ONNX Runtime ochadi, kirish shakli kutilganga mos.
    Mos bo'lmasa ValueError. Qaytaradi: {"input": shakl, "outputs": soni}."""
    import onnxruntime as ort

    try:
        sess = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"ONNX fayl ochilmadi ({e.__class__.__name__})") from None
    inp = sess.get_inputs()[0].shape
    outs = sess.get_outputs()
    if len(inp) != 4 or inp[1] not in (3, "3"):
        raise ValueError(f"kirish shakli rasm emas: {inp}")
    if key.startswith("rtmpose"):
        if (inp[2], inp[3]) != (256, 192) or len(outs) < 2:
            raise ValueError(f"RTMPose 256x192 (simcc_x, simcc_y) kutilgan, fayl: {inp}, {len(outs)} chiqish")
    elif key.startswith("yolox"):
        out = outs[0].shape
        if len(out) != 3 or out[2] not in (85, "85"):
            raise ValueError(f"YOLOX chiqishi (1, N, 85) kutilgan, fayl: {out}")
        if FILES[key].get("dynamic") and isinstance(inp[2], int) and isinstance(inp[3], int):
            raise ValueError("YOLOX statik o'lchamli — dastur o'zgaruvchan o'lchamga o'tkazishi kerak")
    elif key.startswith("yolo11"):
        out = outs[0].shape
        want = 56 if key == "yolo11n-pose" else 84
        if len(out) != 3 or out[1] not in (want, str(want)):
            raise ValueError(f"YOLO11 chiqishi kutilgan (1, {want}, N), fayl: {out}")
    return {"input": [str(x) for x in inp], "outputs": len(outs)}


def mark_broken(key: str) -> Path | None:
    """Ochilmaydigan (buzuq) model fayli chetga olinadi: <nom>.broken. Shunda to'plam "yo'q" bo'lib ko'rinadi va
    panelda qayta yuklab olish/yuklash mumkin (avval buzuq fayl "bor" deb turib, har safar xato berardi)."""
    p = path(key)
    if not p.exists():
        return None
    dst = p.with_name(p.name + ".broken")
    try:
        dst.unlink(missing_ok=True)
        p.replace(dst)
        log.error("Buzuq model fayli chetga olindi: %s → %s", p.name, dst.name)
        return dst
    except OSError:
        log.exception("Buzuq model faylini chetga olib bo'lmadi: %s", p.name)
        return None


def ensure(key: str, local_zip: Path | None = None, force: bool = False) -> Path:
    """Model fayli bo'lmasa — yuklab oladi (yoki qo'lda yuklangan .zip / .onnx fayldan oladi).
    Natija `models/` ga faqat tekshiruvdan o'tgach (validate) tushadi."""
    info = FILES[key]
    dst = path(key)
    if dst.exists() and not force and local_zip is None:
        return dst
    if "url" not in info and local_zip is None:
        raise FileNotFoundError(f"{dst.name} topilmadi")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "download.bin"
        if local_zip is not None:
            if local_zip.stat().st_size > MAX_MODEL_BYTES:
                raise ValueError("fayl juda katta (300 MB dan oshmasin)")
            raw = local_zip
        else:
            _download(info["url"], raw)
        tmp = Path(td) / "model.onnx"
        if zipfile.is_zipfile(raw):
            with zipfile.ZipFile(raw) as z:
                names = [n for n in z.namelist() if n.endswith(".onnx")]
                names.sort(key=lambda n: (not n.endswith("end2end.onnx"), len(n)))
                if not names:
                    raise ValueError("Arxivda .onnx fayl yo'q")
                with z.open(names[0]) as src, open(tmp, "wb") as f:
                    shutil.copyfileobj(src, f, 1 << 20)
        else:
            shutil.copyfile(raw, tmp)
        if info.get("dynamic"):
            try:
                make_yolox_dynamic(tmp, Path(td) / "dyn.onnx")
                tmp = Path(td) / "dyn.onnx"
            except Exception as e:  # noqa: BLE001 — allaqachon dinamik bo'lishi mumkin; validate hal qiladi
                log.info("%s: o'lcham o'zgartirilmadi (%s)", key, e.__class__.__name__)
        validate(key, tmp)
        part = dst.with_name(dst.name + ".part")
        shutil.copyfile(tmp, part)
        part.replace(dst)                  # atomar: ishlayotgan dastur yarim yozilgan faylni ko'rmaydi
    log.info("Model tayyor: %s (%s, %s)", dst.name, info["source"], info["license"])
    return dst


def ensure_set(set_name: str) -> list[str]:
    done = []
    for key in missing(set_name):
        ensure(key)
        done.append(key)
    return done


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":   # python -m app.ai.model_zoo [open|open-s]
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "open"
    print("Yuklanmoqda:", missing(name) or "hammasi bor")
    print("Tayyor:", ensure_set(name))
