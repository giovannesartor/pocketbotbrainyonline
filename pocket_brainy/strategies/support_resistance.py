"""Estratégia de Suporte e Resistência (pivôs locais)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class SupportResistanceStrategy(BaseStrategy):
    name = "Suporte/Resistência"
    weights = {"M1": 0.8, "M5": 1.5, "M15": 1.5}

    def __init__(self, lookback: int = 40, tolerance: float = 0.0010, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.lookback = lookback
        self.tolerance = tolerance

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < self.lookback + 5:
            return None
        if not self.has_min_volume(candles):
            return None
        recent = candles[-self.lookback:]
        highs  = np.array([c.high  for c in recent])
        lows   = np.array([c.low   for c in recent])
        last_close = candles[-1].close

        resistance = float(np.percentile(highs, 95))
        support    = float(np.percentile(lows,   5))

        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]
        rsi   = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx   = Indicators.adx(high, low, close)

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        near_support    = abs(last_close - support)    / last_close < self.tolerance
        near_resistance = abs(resistance - last_close) / last_close < self.tolerance

        # CALL: toca suporte + vela de alta
        if near_support and candles[-1].close > candles[-1].open:
            direction = "CALL"
            flags["trend"] = True
        # PUT: toca resistência + vela de baixa
        elif near_resistance and candles[-1].close < candles[-1].open:
            direction = "PUT"
            flags["trend"] = True

        if direction is None:
            return None

        # ── Filtro 1: RSI confirma a zona ───────────────────────────────────
        if not np.isnan(rsi[-1]):
            if direction == "CALL" and rsi[-1] > 58:
                return None
            if direction == "PUT"  and rsi[-1] < 42:
                return None
            flags["rsi"] = (direction == "CALL" and rsi[-1] < 48) or \
                            (direction == "PUT"  and rsi[-1] > 52)

        # ── Filtro 2: MACD pelo menos não piorando ────────────────────────────
        if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
            flags["macd"] = (direction == "CALL" and hist[-1] >= hist[-2]) or \
                             (direction == "PUT"  and hist[-1] <= hist[-2])

        # ── Filtro 3: Múltiplos toques no nível (S/R testado ≥ 2×) ───────────
        level   = support if direction == "CALL" else resistance
        touches = sum(
            1 for c in recent[:-1]
            if abs(c.low  - level) / level < self.tolerance * 2 or
               abs(c.high - level) / level < self.tolerance * 2
        )
        if touches < 1:
            return None  # nível nunca testado antes — S/R fraco

        # ── Filtro 4: Corpo da vela de entrada — sem dojis ────────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.18 * avg_b:
            return None

        # ── Filtro 5: ADX não muito alto — evitar rompimento do nível ────────
        if not np.isnan(adx[-1]):
            if adx[-1] > 35:
                return None
            flags["adx"] = adx[-1] < 25

        base = self.confluence_score(flags)
        confidence = 62 + base * 4
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 93.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=(
                f"{'Suporte' if direction == 'CALL' else 'Resistência'}={level:.5f} "
                f"RSI={rsi[-1]:.1f} Toques={touches} ADX={adx[-1]:.1f}"
            ),
        )
