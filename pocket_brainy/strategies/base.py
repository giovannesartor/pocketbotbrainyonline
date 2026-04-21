"""Contrato base para todas as estratégias."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..utils.indicators import Candle


# Pesos por timeframe — cada estratégia define seus próprios pesos.
DEFAULT_WEIGHTS: Dict[str, float] = {"M1": 1.0, "M5": 1.0, "M15": 1.0}


@dataclass
class Signal:
    strategy: str
    direction: str               # CALL | PUT
    base_score: float            # 0..10
    confidence: float            # 0..100
    timeframe: str = "M1"
    weight: float = 1.0
    ranking_bonus: float = 0.0   # aplicado pelo ranking dinâmico
    confluence: Dict[str, bool] = field(default_factory=dict)
    notes: str = ""

    @property
    def final_score(self) -> float:
        """score_final = score_base × peso_timeframe + ranking_bonus."""
        return self.base_score * self.weight + self.ranking_bonus


class BaseStrategy(ABC):
    name: str = "Base"
    weights: Dict[str, float] = DEFAULT_WEIGHTS

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def weight_for(self, timeframe: str) -> float:
        return self.weights.get(timeframe, 1.0)

    @abstractmethod
    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        """Retorna um sinal ou None se não houver setup."""

    # ---- utilidades de confluência ----
    @staticmethod
    def confluence_score(flags: Dict[str, bool]) -> float:
        """Aplica pontuação conforme o contrato: RSI +1, EMA +1, MACD +1, Tendência +2, ADX +1."""
        weights = {"rsi": 1, "ema": 1, "macd": 1, "trend": 2, "adx": 1}
        return float(sum(w for k, w in weights.items() if flags.get(k)))
