"""Estratégia de Suporte e Resistência (pivôs locais)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle
from .base import BaseStrategy, Signal


class SupportResistanceStrategy(BaseStrategy):
    name = "Suporte/Resistência"
    weights = {"M1": 0.8, "M5": 1.5, "M15": 1.5}

    def __init__(self, lookback: int = 40, tolerance: float = 0.0008, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.lookback = lookback
        self.tolerance = tolerance

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < self.lookback + 5:
            return None
        recent = candles[-self.lookback:]
        highs = np.array([c.high for c in recent])
        lows = np.array([c.low for c in recent])
        last_close = candles[-1].close

        resistance = float(np.percentile(highs, 95))
        support = float(np.percentile(lows, 5))

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        # CALL: toca o suporte (bounce)
        if abs(last_close - support) / last_close < self.tolerance and candles[-1].close > candles[-1].open:
            direction = "CALL"
            flags["trend"] = True
        # PUT: toca a resistência (rejeição)
        elif abs(resistance - last_close) / last_close < self.tolerance and candles[-1].close < candles[-1].open:
            direction = "PUT"
            flags["trend"] = True

        if direction is None:
            return None

        base = 3 + self.confluence_score(flags)
        confidence = 60 + base * 4
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 92.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"Suporte={support:.5f} Resistência={resistance:.5f} Close={last_close:.5f}",
        )
