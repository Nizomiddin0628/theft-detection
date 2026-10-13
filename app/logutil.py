"""Jurnal (log): faylga va "Tizim holati" sahifasi uchun xotiraga yoziladi."""
from __future__ import annotations

import logging
import time
from collections import deque
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR

MEMORY_LOG: deque = deque(maxlen=400)


class _MemoryHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            MEMORY_LOG.append({
                "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name.replace("hushyor.", ""),
                "msg": record.getMessage(),
            })
        except Exception:  # pragma: no cover
            pass


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger("hushyor")
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOGS_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    import sys

    if sys.stderr is not None:   # pythonw.exe (oynasiz) rejimida konsol yo'q
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    root.addHandler(_MemoryHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"hushyor.{name}")
