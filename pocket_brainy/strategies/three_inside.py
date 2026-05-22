"""Three Inside Up/Down — padrão de reversão de 3 velas (harami + confirmação)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class ThreeInsideStrategy(BaseStrategy):
    """Padrão clássico de 3 velas — reversão confiável especialmente em M15."""

    name = "Three Inside"
    weights = {"M1": 0.0, "M5": 1.1, "M15": 1.5}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 30:
            return None
        if self.weight_for(timeframe) <= 0:
            return None
        if not self.has_min_volume(candles):
            return None

        c1 = candles[-3]  # vela na direção da tendência prévia
        c2 = candles[-2]  # harami: dentro do corpo de c1, direção oposta
        c3 = candles[-1]  # confirmação: fecha além do corpo de c1

        c1_body = abs(c1.close - c1.open)
        c2_body = abs(c2.close - c2.open)
        avg_b = self.avg_body(candles, 20)

        if avg_b < 1e-9 or c1_body < 0.5 * avg_b:
            return None  # vela 1 precisa ser razoavelmente grande

        c1_high_body = max(c1.open, c1.close)
        c1_low_body  = min(c1.open, c1.close)
        c2_high_body = max(c2.open, c2.close)
        c2_low_body  = min(c2.open, c2.close)

        # Harami: corpo de c2 dentro do corpo de c1
        if not (c2_high_body <= c1_high_body and c2_low_body >= c1_low_body):
            return None
        # Corpo de c2 menor que c1
        if c2_body >= c1_body * 0.8:
            return None

        direction: Optional[str] = None
        notes_extra = ""

        # ── Three Inside UP (CALL): c1 bearish, c2 bullish (harami), c3 fecha acima do open de c1 ──
        if c1.close < c1.open and c2.close > c2.open and c3.close > c3.open:
            if c3.close > c1.open:
                direction = "CALL"
                notes_extra = "ThreeInsideUp"

        # ── Three Inside DOWN (PUT): c1 bullish, c2 bearish (harami), c3 fecha abaixo do open de c1 ──
        if direction is None and c1.close > c1.open and c2.close < c2.open and c3.close < c3.open:
            if c3.close < c1.open:
                direction = "PUT"
                notes_extra = "ThreeInsideDown"

        if direction is None:
            return None

        # ── Confluência com indicadores ─────────────────────────────────────
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]
        rsi = Indicators.rsi(close, 14)
        adx = Indicators.adx(high, low, close)
        _, _, hist = Indicators.macd(close)

        if np.isnan(rsi[-1]) or np.isnan(adx[-1]):
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": True, "adx": False}

        # RSI: bônus se em zona apropriada
        if direction == "CALL" and rsi[-1] < 50:
            flags["rsi"] = True
        elif direction == "PUT" and rsi[-1] > 50:
            flags["rsi"] = True

        # MACD: histograma virando na direção
        if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
            if direction == "CALL" and hist[-1] >= hist[-2]:
                flags["macd"] = True
            elif direction == "PUT" and hist[-1] <= hist[-2]:
                flags["macd"] = True

        # ADX moderado — reversão funciona melhor sem tendência muito forte
        if adx[-1] > 38:
            return None
        flags["adx"] = adx[-1] < 25

        base = self.confluence_score(flags)
        confidence = 68 + base * 4
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 93.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"{notes_extra} RSI={rsi[-1]:.1f} ADX={adx[-1]:.1f}",
        )
