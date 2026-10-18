"""AI modellar to'plamlari, ularning litsenziyasi, fayllari va yuklab olish.

Mahsulot sotilishi uchun modellar litsenziyasi muhim:
  * YOLO11 (Ultralytics) — AGPL-3.0: yopiq mahsulotda ishlatish uchun pullik Enterprise litsenziya kerak.
  * YOLOX (Megvii), MoveNet (Google) va RTMPose (OpenMMLab) — Apache-2.0: tijoratda erkin (litsenziya matni
    saqlanadi, models/LICENSES.md).

To'plamlar:
  yolo11  — YOLO11n-pose (odam + skelet) va YOLO11n (buyum). Standart (2.7.0 da ham — pastdagi o'lchovga qarang).
  open-m  — YOLOX-tiny (odam, ≤ 512 px) + MoveNet Thunder (skelet, har odam alohida) + YOLOX-tiny (buyum).
            Apache-2.0, sotish uchun tavsiya. MoveNet fayli GitHub'da (SHA-256 bilan tekshiriladi).
  open-ml — YOLOX-tiny + MoveNet Lightning: YOLO11 bilan deyarli bir xil tezlik, skelet biroz kam aniq.
  2.7.0 o'lchovi (docs/MODELS.md): sanash, navbat, oqim va to'liq web sinovida open-m = YOLO11; ochiq yiqilish
  videolarida (past sifatli, qariyalar) YOLOX odamni ko'pincha ko'rmaydi — 0/5 (YOLO11 3/5). Shuning uchun standart
  emas: do'konda sinab, panelda bir tugma bilan o'tiladi.
  (YOLOX-nano odam detektori sifatida sinab ko'rilgan: tezroq, lekin bir kadr ko'rmay qolsa kuzatuvchi izni qisman
  ramkaga ulab, sanash chizig'ida "chiqdi + kirdi" juftlarini beradi — ishlatilmaydi.)
  open    — YOLOX-nano + RTMPose-t + YOLOX-tiny. Apache-2.0 (RTMPose fayli OpenMMLab saytidan).
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
# MoveNet (Google, TF Hub, Apache-2.0) — ONNX'ga o'tkazilgan nusxa: Kazuhito00/MoveNet-Python-Example (Apache-2.0),
# o'zgarmas commit (fayl SHA-256 bilan ham tekshiriladi)
MN_URL = ("https://raw.githubusercontent.com/Kazuhito00/MoveNet-Python-Example/"
          "515743a113e49b4f31677b1f6252c0041f237e30/onnx/{}.onnx")

# kalit -> fayl, litsenziya, manba
FILES = {
    "yolo11n-pose": {"file": "yolo11n-pose.onnx", "license": "AGPL-3.0", "source": "Ultralytics"},
    "yolo11n": {"file": "yolo11n.onnx", "license": "AGPL-3.0", "source": "Ultralytics"},
    "yolox-nano": {"file": "yolox_nano.onnx", "license": "Apache-2.0", "source": "Megvii YOLOX 0.1.1rc0",
                   "url": YOLOX_URL.format("yolox_nano"), "dynamic": True},
    "yolox-tiny": {"file": "yolox_tiny.onnx", "license": "Apache-2.0", "source": "Megvii YOLOX 0.1.1rc0",
                   "url": YOLOX_URL.format("yolox_tiny"), "dynamic": True},
    "rtmpose-t": {"file": "rtmpose_t.onnx", "license": "Apache-2.0", "source": "OpenMMLab RTMPose (body7)",
                  "url": RTM_URL.format("rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504"),
                  "kind": "rtmpose"},
    "rtmpose-s": {"file": "rtmpose_s.onnx", "license": "Apache-2.0", "source": "OpenMMLab RTMPose (body7)",
                  "url": RTM_URL.format("rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504"),
                  "kind": "rtmpose"},
    "movenet-thunder": {"file": "movenet_thunder.onnx", "license": "Apache-2.0",
                        "source": "Google MoveNet SinglePose Thunder v4", "url": MN_URL.format("movenet_singlepose_thunder_4"),
                        "sha256": "7fae4dc3cdd07ebd5535a1e9515c6baf945daeb60708158f03f915fc8e42eac6",
                        "kind": "movenet", "input": 256},
    "movenet-lightning": {"file": "movenet_lightning.onnx", "license": "Apache-2.0",
                          "source": "Google MoveNet SinglePose Lightning v4",
                          "url": MN_URL.format("movenet_singlepose_lightning_4"),
                          "sha256": "402dadf6a184171f293bc2ef4edf2f96c4b3e0ed6d3b5890198d036a3087b623",
                          "kind": "movenet", "input": 192},
}

DEFAULT_SET = "yolo11"
OPEN_SET = "open-m"                  # litsenziyasi toza tavsiya (panel, tayyorlik ro'yxati, oldindan yuklab olish)
FALLBACK = ("open-ml", "yolo11")     # tanlangan ochiq to'plam fayli yo'q/buzuq bo'lsa (open-ml fayllari dastur bilan birga)
MODEL_SETS = {
    "yolo11": {"label": "YOLO11 (standart)", "license": "AGPL-3.0",
               "hint": "Sinalgan, yiqilishni yaxshiroq topadi. Tijoratda sotish uchun Ultralytics litsenziyasi kerak.",
               "pose": "yolo11n-pose", "det": "yolo11n"},
    "open-m": {"label": "YOLOX + MoveNet (ochiq, sotish uchun)", "license": "Apache-2.0",
               "hint": "Litsenziyasi toza — sotish mumkin. Sanash, navbat, oqim sinovlarida YOLO11 bilan bir xil; "
                       "past sifatli videoda yiqilishni yomonroq topadi (ochiq videolarda 0/5, YOLO11 3/5). "
                       "O'tgach xulq modelini qayta o'qiting (YOLO11 skeletlarida o'qitilgan). ~1,5 marta og'irroq.",
               "person": "yolox-tiny", "pose": "movenet-thunder", "det": "yolox-tiny", "person_size": 512},
    "open-ml": {"label": "YOLOX + MoveNet Lightning (ochiq, tez)", "license": "Apache-2.0",
                "hint": "Litsenziyasi toza, YOLO11 bilan deyarli bir xil tezlik — kuchsiz kompyuter yoki ko'p kamera "
                        "uchun. Skelet biroz kam aniq; yiqilish signali ishonchsiz (do'kon namunasida 1 ta xato).",
                "person": "yolox-tiny", "pose": "movenet-lightning", "det": "yolox-tiny", "person_size": 512},
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
    # 2.7.0: MoveNet — samples/ ning 4 videosida YOLO11 bilan bir xil odamlar (YOLOX-tiny ramkasi, IoU ≥ 0.5),
    # 23 392 nuqta, kvantillar bo'yicha
    "movenet-thunder": ([0.0, 0.056, 0.101, 0.148, 0.201, 0.258, 0.312, 0.366, 0.416, 0.464, 0.506, 0.543, 0.578,
                         0.611, 0.646, 0.679, 0.715, 0.752, 0.793, 0.84, 1.0],
                        [0.0, 0.015, 0.04, 0.078, 0.138, 0.23, 0.335, 0.439, 0.547, 0.676, 0.771, 0.838, 0.889,
                         0.923, 0.947, 0.964, 0.977, 0.985, 0.992, 0.996, 1.0]),
    "movenet-lightning": ([0.0, 0.068, 0.107, 0.147, 0.193, 0.24, 0.288, 0.331, 0.372, 0.41, 0.446, 0.478, 0.509,
                           0.54, 0.575, 0.611, 0.648, 0.69, 0.737, 0.784, 1.0],
                          [0.0, 0.015, 0.04, 0.078, 0.138, 0.23, 0.335, 0.439, 0.547, 0.676, 0.771, 0.838, 0.889,
                           0.923, 0.947, 0.964, 0.977, 0.985, 0.992, 0.996, 1.0]),
}


def kind(key: str) -> str:
    """Skelet modeli turi: yolo (odam + skelet bitta modelda), rtmpose yoki movenet (har odam alohida)."""
    return FILES.get(key, {}).get("kind", "yolo")


def calibrate(model: str, scores: np.ndarray) -> np.ndarray:
    tab = KPT_CALIB.get(model)
    if not tab:
        return scores
    return np.interp(scores, tab[0], tab[1]).astype(np.float32)


def path(key: str) -> Path:
    return MODELS_DIR / FILES[key]["file"]


def missing(set_name: str) -> list[str]:
    ms = MODEL_SETS.get(set_name, {})
    keys = dict.fromkeys(ms[k] for k in ("person", "pose", "det") if ms.get(k))     # takrorsiz (person = det)
    return [k for k in keys if not path(k).exists()]


def missing_required(set_name: str) -> list[str]:
    """Ishga tushish uchun shart bo'lgan yo'q fayllar: buyum detektori ixtiyoriy (fayli yo'q bo'lsa skelet va qoidalar
    ishlayveradi), lekin u odam detektori bilan bitta fayl bo'lsa (open-ml, open-s) — shart."""
    ms = MODEL_SETS.get(set_name, {})
    return [k for k in missing(set_name) if k != ms.get("det") or k == ms.get("person")]


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
    if len(inp) != 4:
        raise ValueError(f"kirish shakli rasm emas: {inp}")
    if kind(key) == "movenet":
        size = FILES[key]["input"]
        out = outs[0].shape
        if [str(x) for x in inp] != ["1", str(size), str(size), "3"] or [str(x) for x in out[-2:]] != ["17", "3"]:
            raise ValueError(f"MoveNet 1x{size}x{size}x3 -> (17, 3) kutilgan, fayl: {inp} -> {out}")
        return {"input": [str(x) for x in inp], "outputs": len(outs)}
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
            if info.get("sha256") and sha256(raw) != info["sha256"]:
                # Yuklab olingan fayl kutilganidan farq qiladi (buzilgan yoki almashtirilgan) — ishlatilmaydi
                raise ValueError(f"{key}: yuklab olingan fayl SHA-256 mos emas")
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


def prefetch(set_name: str | None = None) -> list[str]:
    """O'rnatish/yangilashda: to'plamning (sukut — ochiq tavsiya, open-m) yo'q fayllarini oldindan yuklab olish —
    faqat SHA-256 bilan tekshiriladiganlari (MoveNet), panelda solishtirish va o'tish darhol mumkin bo'lsin.
    Xato bo'lsa — istisno: chaqiruvchi ogohlantiradi (panelda "Yuklab olish" tugmasi qoladi)."""
    got = []
    for key in missing(set_name or OPEN_SET):
        if FILES[key].get("sha256"):
            ensure(key)
            got.append(key)
    return got


if __name__ == "__main__":   # python -m app.ai.model_zoo [open-m|open-ml|open|open-s]
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else OPEN_SET
    print("Yuklanmoqda:", missing(name) or "hammasi bor")
    print("Tayyor:", ensure_set(name))
