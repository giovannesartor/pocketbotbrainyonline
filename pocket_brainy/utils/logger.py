"""Sistema de logging centralizado (arquivo + console)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_FMT = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configura logging global (console + arquivo rotativo)."""
    root = logging.getLogger()
    if root.handlers:  # evitar duplicação
        return

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(_FMT, _DATEFMT)

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
