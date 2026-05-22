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
        if len(candles) < 35:
            return None
        if not self.has_min_volume(candles):
            return None
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]

        _, _, hist = Indicators.macd(close)
        sar   = Indicators.parabolic_sar(high, low)
        adx   = Indicators.adx(high, low, close)
        rsi   = Indicators.rsi(close, 14)
        ema9  = Indicators.ema(close, 9)
        ema21 = Indicators.ema(close, 21)

        if np.isnan([hist[-1], hist[-2], rsi[-1]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        cross_up = hist[-2] <= 0 < hist[-1]
        cross_dn = hist[-2] >= 0 > hist[-1]

        if cross_up and sar[-1] < close[-1]:
            direction = "CALL"
            flags["macd"]  = True
            flags["trend"] = True
        elif cross_dn and sar[-1] > close[-1]:
            direction = "PUT"
            flags["macd"]  = True
            flags["trend"] = True

        if direction is None:
            return None

        # ── Filtro 1: RSI em zona razoável ────────────────────────────────
        if direction == "CALL" and not (35 <= rsi[-1] <= 75):
            return None
        if direction == "PUT"  and not (25 <= rsi[-1] <= 65):
            return None
        flags["rsi"] = True

        # ── Filtro 2: EMA micro — confirma tendência (bônus de score) ────────
        if not np.isnan(ema9[-1]) and not np.isnan(ema21[-1]):
            flags["ema"] = (direction == "CALL" and ema9[-1] > ema21[-1]) or \
                           (direction == "PUT"  and ema9[-1] < ema21[-1])

        # ── Filtro 3: ADX presente (tendência existente) ──────────────────────
        if not np.isnan(adx[-1]):
            flags["adx"] = adx[-1] > 18

        # ── Filtro 4: Corpo da vela de entrada — sem dojis ────────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.20 * avg_b:
            return None

        # ── Filtro 5: Divergência RSI ─────────────────────────────────────────
        _lb = 5
        if len(close) > _lb and not np.isnan(rsi[-_lb]):
            if direction == "CALL" and close[-1] > close[-_lb] and rsi[-1] < rsi[-_lb]:
                return None
            if direction == "PUT"  and close[-1] < close[-_lb] and rsi[-1] > rsi[-_lb]:
                return None

        base = self.confluence_score(flags)
        confidence = 57 + base * 6
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 93.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"MACD cross, SAR={sar[-1]:.5f}, RSI={rsi[-1]:.1f}, ADX={adx[-1]:.1f}",
        )
