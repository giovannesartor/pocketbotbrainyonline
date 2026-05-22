"""Sistema de logging centralizado (arquivo + console) — timestamps em BRT."""
from __future__ import annotations

import logging
import sys
import time as _time_mod
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_FMT = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_BRT_OFFSET = -3 * 3600  # UTC-3


class _BRTFormatter(logging.Formatter):
    """Formatter que exibe timestamps em BRT (UTC-3)."""

    def converter(self, timestamp):  # type: ignore[override]
        return _time_mod.gmtime(timestamp + _BRT_OFFSET)

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return _time_mod.strftime(datefmt or _DATEFMT, ct)


def setup_logging(level: str = "INFO") -> None:
    """Configura logging global (console + arquivo rotativo)."""
    root = logging.getLogger()
    if root.handlers:  # evitar duplicação
        return

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = _BRTFormatter(_FMT, _DATEFMT)

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Arquivo rotativo
    file_handler = RotatingFileHandler(
        LOG_DIR / "pocket_brainy.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Silenciar bibliotecas verbosas
    for noisy in ("httpx", "httpcore", "telegram", "websockets", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
