"""Estratégia RSI + EMA."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class RsiEmaStrategy(BaseStrategy):
    name = "RSI+EMA"
    weights = {"M1": 1.0, "M5": 1.2, "M15": 1.3}

    def __init__(self, rsi_period: int = 14, ema_fast: int = 9, ema_slow: int = 21, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < max(self.ema_slow, self.rsi_period) + 2:
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]

        rsi = Indicators.rsi(close, self.rsi_period)
        ema_f = Indicators.ema(close, self.ema_fast)
        ema_s = Indicators.ema(close, self.ema_slow)
        adx = Indicators.adx(high, low, close)

        r = rsi[-1]
        ef = ema_f[-1]
        es = ema_s[-1]
        c = close[-1]
        prev_ef = ema_f[-2]
        prev_es = ema_s[-2]

        if np.isnan([r, ef, es, prev_ef, prev_es]).any():
            return None

        direction = None
        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}

        # CALL: RSI saindo da sobrevenda + EMA9 > EMA21 + preço acima das duas
        if r < 45 and ef > es and c > ef and prev_ef <= prev_es * 1.0001:
            direction = "CALL"
            flags["rsi"] = r < 45
            flags["ema"] = ef > es
            flags["trend"] = c > ef
        # PUT: RSI saindo da sobrecompra + EMA9 < EMA21 + preço abaixo
        elif r > 55 and ef < es and c < ef and prev_ef >= prev_es * 0.9999:
            direction = "PUT"
            flags["rsi"] = r > 55
            flags["ema"] = ef < es
            flags["trend"] = c < ef

        if direction is None:
            return None

        flags["adx"] = (not np.isnan(adx[-1])) and adx[-1] > 20
        base = self.confluence_score(flags)                # 0..6
        confidence = 50 + base * 8                          # 50..98
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 95.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"RSI={r:.1f} EMA9={ef:.5f} EMA21={es:.5f}",
        )
