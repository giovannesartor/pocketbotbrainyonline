"""Breakout — rompimento de máxima/mínima recente."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class BreakoutStrategy(BaseStrategy):
    name = "Breakout"
    weights = {"M1": 0.9, "M5": 1.3, "M15": 1.4}

    def __init__(self, lookback: int = 20, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.lookback = lookback

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < self.lookback + 5:
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]

        hh = max(h for h in high[-self.lookback - 1:-1])
        ll = min(l_ for l_ in low[-self.lookback - 1:-1])
        adx = Indicators.adx(high, low, close)

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        if close[-1] > hh:
            direction = "CALL"
            flags["trend"] = True
        elif close[-1] < ll:
            direction = "PUT"
            flags["trend"] = True

        if direction is None:
            return None

        flags["adx"] = (not np.isnan(adx[-1])) and adx[-1] > 25
        base = 3 + self.confluence_score(flags)
        confidence = 58 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 95.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"Breakout HH={hh:.5f} LL={ll:.5f}, ADX={adx[-1]:.1f}",
        )
