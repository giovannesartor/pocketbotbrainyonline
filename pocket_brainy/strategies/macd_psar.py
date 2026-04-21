"""MACD + Parabolic SAR."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class MacdPsarStrategy(BaseStrategy):
    name = "MACD+SAR"
    weights = {"M1": 1.0, "M5": 1.2, "M15": 1.3}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 30:
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]

        _, _, hist = Indicators.macd(close)
        sar = Indicators.parabolic_sar(high, low)
        adx = Indicators.adx(high, low, close)

        if np.isnan([hist[-1], hist[-2]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        cross_up = hist[-2] <= 0 < hist[-1]
        cross_dn = hist[-2] >= 0 > hist[-1]

        if cross_up and sar[-1] < close[-1]:
            direction = "CALL"
            flags["macd"] = True
            flags["trend"] = True
        elif cross_dn and sar[-1] > close[-1]:
            direction = "PUT"
            flags["macd"] = True
            flags["trend"] = True

        if direction is None:
            return None

        flags["adx"] = (not np.isnan(adx[-1])) and adx[-1] > 22
        base = self.confluence_score(flags)
        confidence = 55 + base * 6
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 92.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"MACD hist cross, SAR={sar[-1]:.5f}, ADX={adx[-1]:.1f}",
        )
