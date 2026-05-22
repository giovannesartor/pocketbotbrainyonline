"""Bollinger + RSI (reversão nas bandas)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class BollingerRsiStrategy(BaseStrategy):
    name = "Bollinger+RSI"
    weights = {"M1": 1.3, "M5": 1.3, "M15": 1.1}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 35:
            return None
        if not self.has_min_volume(candles):
            return None
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]
        lower, _, upper = Indicators.bollinger(close, 20, 2.0)
        rsi   = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx   = Indicators.adx(high, low, close)

        if np.isnan([lower[-1], upper[-1], rsi[-1]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        # CALL: toca banda inferior + RSI sobrevendido
        if close[-1] <= lower[-1] and rsi[-1] < 32:
            direction = "CALL"
            flags["rsi"]   = True
            flags["trend"] = True
        # PUT: toca banda superior + RSI sobrecomprado
        elif close[-1] >= upper[-1] and rsi[-1] > 68:
            direction = "PUT"
            flags["rsi"]   = True
            flags["trend"] = True

        if direction is None:
            return None

        # ── Filtro 1: RSI tinha extremo mais forte nas últimas 3 velas ───────
        # Garante que o extremo de RSI é genuíno e não só marginal
        if len(rsi) >= 4:
            rsi_window = [rsi[i] for i in (-4, -3, -2) if not np.isnan(rsi[i])]
            if direction == "CALL" and not any(r < 35 for r in rsi_window):
                return None
            if direction == "PUT"  and not any(r > 65 for r in rsi_window):
                return None

        # ── Filtro 2: MACD pelo menos não piorando na direção contrária ──────
        if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
            if direction == "CALL" and hist[-1] < hist[-2] - abs(hist[-2]) * 0.5:
                return None  # queda acelerada — sem sinal de reversão
            if direction == "PUT"  and hist[-1] > hist[-2] + abs(hist[-2]) * 0.5:
                return None
            flags["macd"] = (direction == "CALL" and hist[-1] > hist[-2]) or \
                              (direction == "PUT"  and hist[-1] < hist[-2])

        # ── Filtro 3: ADX < 35 — reversão falha em tendências muito fortes ───
        if not np.isnan(adx[-1]):
            if adx[-1] > 35:
                return None
            flags["adx"] = adx[-1] < 22

        # ── Filtro 4: Corpo da vela de entrada — sem dojis ───────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.15 * avg_b:
            return None

        base = self.confluence_score(flags)
        confidence = 63 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 92.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"BB=[{lower[-1]:.5f},{upper[-1]:.5f}] RSI={rsi[-1]:.1f} ADX={adx[-1]:.1f}",
        )
