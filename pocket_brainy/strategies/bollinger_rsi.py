"""Bollinger + RSI (reversão nas bandas)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class BollingerRsiStrategy(BaseStrategy):
    name = "Bollinger+RSI"
    weights = {"M1": 1.3, "M5": 1.3, "M15": 1.1}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 30:
            return None
        close = [c.close for c in candles]
        lower, mid, upper = Indicators.bollinger(close, 20, 2.0)
        rsi = Indicators.rsi(close, 14)

        if np.isnan([lower[-1], upper[-1], rsi[-1]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        # CALL: toca banda inferior + RSI < 30
        if close[-1] <= lower[-1] and rsi[-1] < 32:
            direction = "CALL"
            flags["rsi"] = True
        # PUT: toca banda superior + RSI > 70
        elif close[-1] >= upper[-1] and rsi[-1] > 68:
            direction = "PUT"
            flags["rsi"] = True

        if direction is None:
            return None

        base = 3 + self.confluence_score(flags)
        confidence = 60 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 90.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"BB=[{lower[-1]:.5f}, {upper[-1]:.5f}] RSI={rsi[-1]:.1f}",
        )
