"""O'qitilgan xulq klassifikatorini yuklash va ehtimollik hisoblash."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

from ..config import MODELS_DIR
from ..logutil import get_logger
from .features import window_features

log = get_logger("ml")

ACTIVE_POINTER = MODELS_DIR / "behavior_active.json"


class BehaviorModel:
    _lock = threading.Lock()
    _cached: "BehaviorModel | None" = None
    _cached_path: str | None = None
    load_error: str | None = None

    def __init__(self, path: Path):
        import joblib

        bundle = joblib.load(path)
        self.model = bundle["model"]
        self.names: list[str] = bundle["features"]
        self.window: float = float(bundle.get("window", 2.5))
        self.version: str = str(bundle.get("version", path.stem))
        self.path = path

    @classmethod
    def active_path(cls) -> Path | None:
        if ACTIVE_POINTER.exists():
            try:
                p = Path(json.loads(ACTIVE_POINTER.read_text(encoding="utf-8"))["path"])
                if not p.is_absolute():
                    p = MODELS_DIR / p
                if p.exists():
                    return p
            except Exception as e:  # pragma: no cover
                log.warning("Faol model ko'rsatkichi o'qilmadi: %s", e)
        cands = sorted(MODELS_DIR.glob("behavior_*.joblib"))
        return cands[-1] if cands else None

    @classmethod
    def get(cls) -> "BehaviorModel | None":
        path = cls.active_path()
        if path is None:
            return None
        with cls._lock:
            if cls._cached is None or cls._cached_path != str(path):
                try:
                    import warnings

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        cls._cached = BehaviorModel(path)
                    cls._cached_path = str(path)
                    cls.load_error = None
                    log.info("Xulq modeli yuklandi: %s", path.name)
                except Exception as e:
                    log.error("Xulq modelini yuklab bo'lmadi (%s): %s", path.name, e)
                    cls._cached = None
                    cls._cached_path = str(path)
                    cls.load_error = str(e)
            return cls._cached

    @classmethod
    def set_active(cls, path: Path) -> None:
        ACTIVE_POINTER.write_text(json.dumps({"path": path.name}), encoding="utf-8")
        with cls._lock:
            cls._cached = None
            cls._cached_path = None

    def predict(self, seq: list[tuple[float, np.ndarray, np.ndarray]]) -> float | None:
        if not seq:
            return None
        t_end = seq[-1][0]
        win = [s for s in seq if t_end - s[0] <= self.window]
        x = window_features(win)
        if x is None:
            return None
        try:
            p = self.model.predict_proba(x[None])[0, 1]
            return float(p)
        except Exception as e:  # pragma: no cover
            log.warning("ML bashorat xatosi: %s", e)
            return None
