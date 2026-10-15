"""Model ishga tushirish muhiti: ONNX Runtime yoki OpenVINO (ikkalasi ham CPU'da, PyTorch kerak emas).

OpenVINO ko'pincha Intel protsessorlarda ancha tez; AMD'da farq kompyuterga bog'liq. Shuning uchun "auto"
rejimida dastur birinchi ishga tushganda ikkalasini shu kompyuterda o'lchaydi va tezini tanlaydi. Natija
bazada saqlanadi (protsessor, kutubxona versiyasi yoki model o'zgarsa — qayta o'lchanadi).

Aniqlik bir xil bo'lishi uchun OpenVINO doim FP32 da ishlatiladi (ba'zi server protsessorlarida u o'zi
bf16 ga o'tib natijani biroz o'zgartiradi).
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import time
from pathlib import Path

import numpy as np

from ..logutil import get_logger

log = get_logger("runtime")

RUNTIMES = ("onnxruntime", "openvino")
GPU_PROVIDERS = ("CUDAExecutionProvider", "CoreMLExecutionProvider", "DmlExecutionProvider")


def openvino_available() -> bool:
    try:
        import openvino  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def ort_gpu() -> list[str]:
    import onnxruntime as ort

    avail = ort.get_available_providers()
    return [p for p in GPU_PROVIDERS if p in avail]


class Runner:
    """Bitta model: x (1,3,H,W float32) -> birinchi chiqish. Chaqiruvlar engine navbati ichida (bir vaqtda bitta)."""

    def __init__(self, path: Path, runtime: str, threads: int):
        self.path = Path(path)
        self.runtime = runtime
        self.threads = threads
        if runtime == "openvino":
            import openvino as ov

            core = ov.Core()
            cfg = {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_NUM_THREADS": int(threads),
                   "INFERENCE_PRECISION_HINT": "f32", "NUM_STREAMS": 1}
            self._compiled = core.compile_model(str(self.path), "CPU", cfg)
            self._req = self._compiled.create_infer_request()
            self._out = self._compiled.outputs[0]
            shape = self._compiled.inputs[0].get_partial_shape()
            self.fixed_hw = (None if shape[2].is_dynamic or shape[3].is_dynamic
                             else (shape[2].get_length(), shape[3].get_length()))
            self.fixed_batch = None if shape[0].is_dynamic else shape[0].get_length()
            self.providers = ["OpenVINO CPU"]
        else:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = int(threads)
            opts.inter_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            # Standart holatda ORT oqimlari ish bo'lmasa ham "aylanib" CPU ni 100% band qiladi
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
            providers = ort_gpu() + ["CPUExecutionProvider"]
            self._sess = ort.InferenceSession(str(self.path), opts, providers=providers)
            self._input = self._sess.get_inputs()[0].name
            shape = self._sess.get_inputs()[0].shape
            self.fixed_hw = ((shape[2], shape[3]) if isinstance(shape[2], int) and isinstance(shape[3], int)
                             else None)
            self.fixed_batch = shape[0] if isinstance(shape[0], int) else None
            self.providers = self._sess.get_providers()

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.runtime == "openvino":
            return self._req.infer({0: x})[self._out]
        return self._sess.run(None, {self._input: x})[0]

    def run_all(self, x: np.ndarray) -> list[np.ndarray]:
        """Barcha chiqishlar (masalan, RTMPose: simcc_x va simcc_y), modeldagi tartibda.
        Model partiyasi statik bo'lsa (ba'zi RTMPose fayllari: batch=1) — bittadan yuritib birlashtiriladi."""
        if self.fixed_batch and x.shape[0] != self.fixed_batch:
            if self.fixed_batch != 1:
                raise ValueError(f"model partiyasi {self.fixed_batch} ga bog'langan")
            parts = [self._run_all(x[i:i + 1]) for i in range(x.shape[0])]
            return [np.concatenate([p[k] for p in parts], 0) for k in range(len(parts[0]))]
        return self._run_all(x)

    def _run_all(self, x: np.ndarray) -> list[np.ndarray]:
        if self.runtime == "openvino":
            res = self._req.infer({0: x})
            return [res[o] for o in self._compiled.outputs]
        return self._sess.run(None, {self._input: x})


def _bench(runner: Runner, hw: tuple[int, int], n: int = 8) -> float:
    h, w = runner.fixed_hw or hw
    x = (np.random.rand(1, 3, h, w)).astype(np.float32)
    for _ in range(3):
        runner(x)
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        runner(x)
        ts.append(time.perf_counter() - t)
    return statistics.median(ts) * 1000


def fingerprint(model: Path) -> str:
    import onnxruntime as ort

    ov = ""
    try:
        import openvino

        ov = openvino.__version__
    except Exception:  # noqa: BLE001
        pass
    st = model.stat() if model.exists() else None
    return "|".join([platform.processor() or platform.machine(), str(os.cpu_count()), ort.__version__, ov,
                     model.name, str(st.st_size if st else 0)])


def choose(pref: str, probe: Path, threads: int, cache: dict | None = None,
           hw: tuple[int, int] = (384, 640)) -> tuple[str, dict]:
    """Qaysi muhitda ishlash. pref: auto | onnxruntime | openvino. Qaytaradi (tanlov, o'lchov ma'lumoti)."""
    if pref == "openvino" and not openvino_available():
        log.warning("OpenVINO o'rnatilmagan — ONNX Runtime ishlatiladi (pip install openvino)")
        pref = "onnxruntime"
    if pref in RUNTIMES:
        return pref, {"choice": pref, "reason": "sozlamada tanlangan"}
    if ort_gpu():
        return "onnxruntime", {"choice": "onnxruntime", "reason": "GPU bor (" + ", ".join(ort_gpu()) + ")"}
    if not openvino_available():
        return "onnxruntime", {"choice": "onnxruntime", "reason": "OpenVINO o'rnatilmagan"}
    fp = fingerprint(probe)
    if cache and cache.get("fp") == fp and cache.get("choice") in RUNTIMES:
        return cache["choice"], cache
    res = {"fp": fp, "t": time.time()}
    for rt in RUNTIMES:
        try:
            res[rt] = round(_bench(Runner(probe, rt, threads), hw), 1)
        except Exception as e:  # noqa: BLE001
            log.warning("%s o'lchovida xato: %s", rt, e)
            res[rt] = None
    o, v = res.get("onnxruntime"), res.get("openvino")
    # OpenVINO yakka o'lchovda kamida 30% tez bo'lsagina tanlanadi: ko'p kamerali ish paytida (kamera o'qish,
    # klip yozish bilan birga) uning ustunligi kamayadi — sinov serverida 1.3 baravar tez chiqib, umumiy
    # tezlikda yutmagan. Aks holda sinalgan ONNX Runtime qoladi.
    res["choice"] = "openvino" if (v is not None and (o is None or v < o * 0.7)) else "onnxruntime"
    res["reason"] = f"o'lchandi: ONNX Runtime {o} ms, OpenVINO {v} ms"
    log.info("AI muhiti tanlandi: %s (%s)", res["choice"], res["reason"])
    return res["choice"], res


def dumps(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)
