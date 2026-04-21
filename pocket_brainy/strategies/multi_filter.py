"""Multi-Filtro: EMA50 (macro) + EMA9/21 (micro) + RSI + MACD + ADX."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class MultiFilterStrategy(BaseStrategy):
    name = "MultiFiltro"
    weights = {"M1": 0.9, "M5": 1.4, "M15": 1.5}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 60:
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]

        ema50 = Indicators.ema(close, 50)
        ema9 = Indicators.ema(close, 9)
        ema21 = Indicators.ema(close, 21)
        rsi = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx = Indicators.adx(high, low, close)

        if np.isnan([ema50[-1], ema9[-1], ema21[-1], rsi[-1], hist[-1], adx[-1]]).any():
            return None

        c = close[-1]
        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        trend_up = c > ema50[-1] and ema9[-1] > ema21[-1]
        trend_dn = c < ema50[-1] and ema9[-1] < ema21[-1]

        if trend_up and rsi[-1] > 50 and hist[-1] > 0:
            direction = "CALL"
            flags.update(rsi=True, ema=True, macd=True, trend=True)
        elif trend_dn and rsi[-1] < 50 and hist[-1] < 0:
            direction = "PUT"
            flags.update(rsi=True, ema=True, macd=True, trend=True)

        if direction is None:
            return None

        flags["adx"] = adx[-1] > 22
        base = self.confluence_score(flags)   # até 6
        confidence = 60 + base * 6
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 97.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"EMA50={ema50[-1]:.5f} RSI={rsi[-1]:.1f} MACD={hist[-1]:.5f} ADX={adx[-1]:.1f}",
        )
