"""Filtro anti-loss: detecta mercado lateral via ADX + largura de Bollinger."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np

from .indicators import Indicators, Candle


@dataclass
class MarketState:
    is_lateral: bool
    adx_value: float
    bb_width: float
    description: str


class MarketFilter:
    """Determina se o mercado está lateral (range) ou em tendência."""

    def __init__(self, adx_threshold: float = 20.0, bb_width_threshold: float = 0.015):
        self.adx_threshold = adx_threshold
        self.bb_width_threshold = bb_width_threshold

    def evaluate(self, candles: List[Candle]) -> MarketState:
        if len(candles) < 30:
            return MarketState(False, 0.0, 0.0, "candles insuficientes")
        high = [c.high for c in candles]
        low = [c.low for c in candles]
        close = [c.close for c in candles]

        adx = Indicators.adx(high, low, close)
        adx_val = float(adx[-1]) if not np.isnan(adx[-1]) else 0.0

        lower, mid, upper = Indicators.bollinger(close, 20, 2.0)
        last_mid = mid[-1] if not np.isnan(mid[-1]) else close[-1]
        width = (upper[-1] - lower[-1]) / max(last_mid, 1e-9)

        is_lateral = adx_val < self.adx_threshold and width < self.bb_width_threshold
        desc = (
            f"ADX={adx_val:.1f} (<{self.adx_threshold}={'sim' if adx_val < self.adx_threshold else 'não'}), "
            f"BB_width={width:.4f} (<{self.bb_width_threshold}={'sim' if width < self.bb_width_threshold else 'não'})"
        )
        return MarketState(is_lateral, adx_val, float(width), desc)

    @staticmethod
    def trend_strategies() -> List[str]:
        """Estratégias bloqueadas em mercado lateral."""
        return ["RSI+EMA", "Alligator+RSI+MACD", "MACD+SAR", "MultiFiltro", "Breakout"]

    @staticmethod
    def range_strategies() -> List[str]:
        """Estratégias permitidas em mercado lateral."""
        return ["MHI", "Bollinger+RSI", "Suporte/Resistência"]
