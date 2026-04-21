"""MHI — Método das 3 velas (reversão após 3 velas consecutivas)."""
from __future__ import annotations

from typing import List, Optional

from ..utils.indicators import Candle
from .base import BaseStrategy, Signal


class MhiStrategy(BaseStrategy):
    name = "MHI"
    weights = {"M1": 1.5, "M5": 1.0, "M15": 0.8}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 6:
            return None
        last3 = candles[-4:-1]  # 3 velas antes da atual
        greens = sum(1 for c in last3 if c.close > c.open)
        reds = sum(1 for c in last3 if c.close < c.open)

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None
        if reds == 3:
            direction = "CALL"   # reversão após 3 vermelhas
            flags["trend"] = True
        elif greens == 3:
            direction = "PUT"    # reversão após 3 verdes
            flags["trend"] = True

        if direction is None:
            return None

        base = 2 + self.confluence_score(flags)
        confidence = 55 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 85.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"3 velas {'vermelhas' if reds == 3 else 'verdes'} — MHI clássico",
        )
