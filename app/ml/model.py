"""O'qitilgan xulq klassifikatorini yuklash va ehtimollik hisoblash."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np

from ..config import DATA_DIR, MODELS_DIR
from ..logutil import get_logger
from .features import window_features

log = get_logger("ml")

# Dastur bilan kelgan model ko'rsatkichi (git'da). 2.3.0 dan: shu kompyuterda tanlangan model ko'rsatkichi data/ da —
# models/behavior_active.json o'zgarmaydi, git ishchi daraxti toza qoladi (yangilash skripti "toza" deb tekshiradi)
BUNDLED_POINTER = MODELS_DIR / "behavior_active.json"
ACTIVE_POINTER = DATA_DIR / "behavior_active.json"
# 2.3.0: champion/challenger — yangi o'qitilgan model avval "soyada" ishlaydi (ball va signalga ta'sir qilmaydi, faqat
# ehtimoli hodisaga yoziladi), solishtirilgandan keyin faollashtiriladi (app/ml/champion.py)
CHALLENGER_POINTER = DATA_DIR / "behavior_challenger.json"
BUNDLED_DEFAULT = "behavior_20261012_054319.joblib"   # git'dagi models/behavior_active.json ko'rsatadigan fayl


_ptr_cache: dict[str, tuple] = {}     # ko'rsatkich yo'li -> (mtime_ns, hajm, nom): har kadrda faylni ochmaslik uchun


def _write_pointer(ptr: Path, name: str | None) -> None:
    """Atomar yozish: kamera oqimi yarim yozilgan faylni o'qib qolmasin. Vaqtinchalik fayl nomi noyob (ikki yozuvchi
    to'qnashmasin); Windows'da o'qish paytida os.replace PermissionError berishi mumkin — qisqa kutib qayta uriniladi."""
    import tempfile

    ptr.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ptr.parent), prefix=ptr.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"path": name}))
        for i in range(40):
            try:
                os.replace(tmp, ptr)
                break
            except PermissionError:
                if i == 39:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    _ptr_cache.pop(str(ptr), None)


def pointer_name(ptr: Path) -> str | None:
    """Ko'rsatkichdagi fayl nomi (fayl bor-yo'qligidan qat'i nazar). O'qib bo'lmasa — oxirgi o'qilgan qiymat."""
    key = str(ptr)
    try:
        st = ptr.stat()
    except FileNotFoundError:
        _ptr_cache.pop(key, None)
        return None
    except OSError:
        c = _ptr_cache.get(key)
        return c[2] if c else None
    c = _ptr_cache.get(key)
    if c and c[0] == st.st_mtime_ns and c[1] == st.st_size:
        return c[2]
    try:
        name = json.loads(ptr.read_text(encoding="utf-8")).get("path") or None
    except (OSError, ValueError, AttributeError) as e:
        if c:                                  # Windows: yozish (os.replace) paytida o'qilmadi — oxirgisi qoladi
            return c[2]
        log.warning("Model ko'rsatkichi o'qilmadi (%s): %s", ptr.name, e)
        return None
    _ptr_cache[key] = (st.st_mtime_ns, st.st_size, name)
    return name


def _read_pointer(ptr: Path) -> Path | None:
    name = pointer_name(ptr)
    if not name:
        return None
    p = Path(name)
    p = p if p.is_absolute() else MODELS_DIR / p
    return p if p.exists() else None


def migrate_pointer() -> bool:
    """2.2.0 gacha tanlangan model git'dagi models/behavior_active.json ga yozilardi. Shu kompyuterda boshqa model
    tanlangan bo'lsa — data/ ga ko'chiriladi (yangilash skripti git faylini asl holiga qaytarganda tanlov yo'qolmasin)."""
    if ACTIVE_POINTER.exists() or not BUNDLED_POINTER.exists():
        return False
    p = _read_pointer(BUNDLED_POINTER)
    if p is None or p.name == BUNDLED_DEFAULT:
        return False
    _write_pointer(ACTIVE_POINTER, p.name)
    log.info("Faol model ko'rsatkichi data/ ga ko'chirildi: %s", p.name)
    return True


class BehaviorModel:
    _lock = threading.Lock()
    _cached: "BehaviorModel | None" = None
    _cached_path: str | None = None
    _failed: dict = {}                # yo'l -> mtime: yuklab bo'lmagan fayl (o'zgarmaguncha qayta urinilmaydi)
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
        for ptr in (ACTIVE_POINTER, BUNDLED_POINTER):
            p = _read_pointer(ptr)
            if p is not None:
                return p
        cands = sorted(MODELS_DIR.glob("behavior_*.joblib"))
        return cands[-1] if cands else None

    @classmethod
    def get(cls) -> "BehaviorModel | None":
        path = cls.active_path()
        if path is None:
            return None
        with cls._lock:
            if cls._cached_path != str(path) or (cls._cached is None and not cls._failed_same(path)):
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
                    cls._mark_failed(path)
            return cls._cached

    @classmethod
    def _mtime(cls, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @classmethod
    def _failed_same(cls, path: Path) -> bool:
        return cls._failed.get(str(path)) == cls._mtime(path)

    @classmethod
    def _mark_failed(cls, path: Path) -> None:
        cls._failed[str(path)] = cls._mtime(path)

    @classmethod
    def set_active(cls, path: Path) -> None:
        _write_pointer(ACTIVE_POINTER, path.name)
        with cls._lock:
            cls._cached = None
            cls._cached_path = None

    # ---------------------------------------------------------------- challenger (soya model)
    _ch_cached: "BehaviorModel | None" = None
    _ch_path: str | None = None

    @classmethod
    def challenger_path(cls) -> Path | None:
        return _read_pointer(CHALLENGER_POINTER)

    @classmethod
    def get_challenger(cls) -> "BehaviorModel | None":
        path = cls.challenger_path()
        if path is None:
            return None
        with cls._lock:
            if cls._ch_path != str(path) or (cls._ch_cached is None and not cls._failed_same(path)):
                try:
                    import warnings

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        cls._ch_cached = BehaviorModel(path)
                except Exception as e:  # noqa: BLE001
                    log.error("Challenger modelni yuklab bo'lmadi (%s): %s", path.name, e)
                    cls._ch_cached = None
                    cls._mark_failed(path)
                cls._ch_path = str(path)
            return cls._ch_cached

    @classmethod
    def set_challenger(cls, path: Path | None) -> None:
        _write_pointer(CHALLENGER_POINTER, None if path is None else path.name)
        with cls._lock:
            cls._ch_cached = None
            cls._ch_path = None

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
