"""Breakout — rompimento de máxima/mínima recente com volume."""
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
        if len(candles) < max(self.lookback + 5, 55):
            return None
        if not self.has_min_volume(candles):
            return None
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]

        hh    = max(h  for h  in high[-self.lookback - 1:-1])
        ll    = min(l_ for l_ in low[-self.lookback  - 1:-1])
        adx   = Indicators.adx(high, low, close)
        rsi   = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        ema50 = Indicators.ema(close, 50)

        if np.isnan([adx[-1], rsi[-1], hist[-1]]).any():
            return None

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

        # ── Filtro 1: RSI não exausta — breakout já sobrecomprado é armadilha ─
        if direction == "CALL" and rsi[-1] > 78:
            return None
        if direction == "PUT"  and rsi[-1] < 22:
            return None
        flags["rsi"] = (direction == "CALL" and rsi[-1] < 72) or (direction == "PUT" and rsi[-1] > 28)

        # ── Filtro 2: MACD confirma a direção ─────────────────────────────────
        if direction == "CALL" and hist[-1] <= 0:
            return None
        if direction == "PUT"  and hist[-1] >= 0:
            return None
        flags["macd"] = True

        # ── Filtro 3: EMA50 alinhada com o breakout (tendência macro) ─────────
        if not np.isnan(ema50[-1]):
            if direction == "CALL" and close[-1] < ema50[-1]:
                return None
            if direction == "PUT"  and close[-1] > ema50[-1]:
                return None
            flags["ema"] = True

        # ── Filtro 4: Corpo da vela de rompimento razoável ──────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.35 * avg_b:
            return None  # vela muito fraca no rompimento

        flags["adx"] = adx[-1] > 22
        base = self.confluence_score(flags)
        confidence = 60 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 95.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"Breakout HH={hh:.5f} LL={ll:.5f} RSI={rsi[-1]:.1f} ADX={adx[-1]:.1f}",
        )
