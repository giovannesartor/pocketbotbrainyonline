"""Persistência e gestão de configurações em JSON."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("core.config")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"


@dataclass
class MartingaleConfig:
    enabled: bool = False
    max_level: int = 2
    multiplier: float = 2.2
    reset_after_win: bool = True


@dataclass
class BotConfig:
    # Credenciais Pocket Option
    po_email: str = ""
    po_password: str = ""
    po_demo: bool = True  # True = conta demo
    po_ssids: List[str] = field(default_factory=list)  # SSIDs manuais (fallback sequencial)

    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Gestão
    entry_amount: float = 2.0
    stop_win: float = 25.0         # $ ou %
    stop_win_is_percent: bool = False
    stop_loss: float = 15.0
    stop_loss_is_percent: bool = False
    max_trades_per_day: int = 50
    max_loss_streak: int = 3
    delay_between_trades: int = 5   # segundos
    max_open_trades: int = 3        # trades simultâneos abertos no máximo

    # Estratégia / sinal
    min_assertiveness: float = 65.0   # %
    min_payout: float = 80.0          # %
    timeframes: List[str] = field(default_factory=lambda: ["M1", "M5"])

    # Ativos
    asset_mode: str = "auto"         # auto | manual
    manual_assets: List[str] = field(default_factory=lambda: ["EURUSD-OTC"])

    # Martingale
    martingale: MartingaleConfig = field(default_factory=MartingaleConfig)

    # IA
    ai_enabled: bool = True

    # Modo simulação
    simulation_mode: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotConfig":
        mart = data.pop("martingale", {}) if isinstance(data.get("martingale"), dict) else {}
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__annotations__})
        cfg.martingale = MartingaleConfig(**{k: v for k, v in mart.items() if k in MartingaleConfig.__annotations__})
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class ConfigManager:
    """Gerencia leitura/escrita atômica do config.json."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._config: BotConfig = self._load()

    # ------- IO -------
    def _load(self) -> BotConfig:
        if not self.path.exists():
            cfg = BotConfig()
            self._save(cfg)
        else:
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                cfg = BotConfig.from_dict(data)
            except Exception as e:
                logger.error(f"Falha ao carregar config: {e}. Usando defaults.")
                cfg = BotConfig()
        # Variáveis de ambiente têm prioridade sobre config.json (Railway deploy)
        self._apply_env_overrides(cfg)
        return cfg

    @staticmethod
    def _apply_env_overrides(cfg: BotConfig) -> None:
        """Sobrescreve campos com variáveis de ambiente, se definidas."""
        _str = os.environ.get
        if _str("PO_EMAIL"):
            cfg.po_email = _str("PO_EMAIL")
        if _str("PO_PASSWORD"):
            cfg.po_password = _str("PO_PASSWORD")
        if _str("TELEGRAM_TOKEN"):
            cfg.telegram_token = _str("TELEGRAM_TOKEN")
        if _str("TELEGRAM_CHAT_ID"):
            cfg.telegram_chat_id = _str("TELEGRAM_CHAT_ID")
        if _str("PO_DEMO") is not None:
            cfg.po_demo = _str("PO_DEMO", "true").lower() in ("1", "true", "yes")
        if _str("PO_SSIDS"):
            # aceita múltiplos SSIDs separados por vírgula
            cfg.po_ssids = [s.strip() for s in _str("PO_SSIDS").split(",") if s.strip()]
        if _str("SIMULATION_MODE") is not None:
            cfg.simulation_mode = _str("SIMULATION_MODE", "true").lower() in ("1", "true", "yes")

    def _save(self, cfg: BotConfig) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # ------- API pública -------
    @property
    def config(self) -> BotConfig:
        return self._config

    def update(self, **kwargs: Any) -> BotConfig:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config, k):
                    setattr(self._config, k, v)
            self._save(self._config)
            return self._config

    def update_martingale(self, **kwargs: Any) -> BotConfig:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config.martingale, k):
                    setattr(self._config.martingale, k, v)
            self._save(self._config)
            return self._config

    def reload(self) -> BotConfig:
        with self._lock:
            self._config = self._load()
            return self._config
