"""Estratégia Alligator + RSI + MACD."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class AlligatorRsiMacdStrategy(BaseStrategy):
    name = "Alligator+RSI+MACD"
    weights = {"M1": 0.9, "M5": 1.3, "M15": 1.4}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 50:
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]
        median = [(h + l) / 2 for h, l in zip(high, low)]

        jaw, teeth, lips = Indicators.alligator(median)
        rsi = Indicators.rsi(close, 14)
        macd_line, sig_line, hist = Indicators.macd(close)

        j, t, l_ = jaw[-1], teeth[-1], lips[-1]
        if np.isnan([j, t, l_, rsi[-1], hist[-1]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        # CALL: lábios > dentes > mandíbulas (abrindo) + RSI>50 + MACD hist>0
        if l_ > t > j and rsi[-1] > 50 and hist[-1] > 0:
            direction = "CALL"
            flags["trend"] = True
            flags["rsi"] = True
            flags["macd"] = True
            flags["ema"] = True
        elif l_ < t < j and rsi[-1] < 50 and hist[-1] < 0:
            direction = "PUT"
            flags["trend"] = True
            flags["rsi"] = True
            flags["macd"] = True
            flags["ema"] = True

        if direction is None:
            return None

        base = self.confluence_score(flags)
        confidence = 55 + base * 7
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 95.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"Alligator aberto, RSI={rsi[-1]:.1f}, MACD_hist={hist[-1]:.5f}",
        )
