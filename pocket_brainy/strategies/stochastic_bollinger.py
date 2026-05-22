"""Stochastic + Bollinger Bounce — reversão nas bandas com confirmação estocástica."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class StochasticBollingerStrategy(BaseStrategy):
    """Toque na banda + Stochastic em zona extrema cruzando = sinal de reversão."""

    name = "Stochastic+Bollinger"
    weights = {"M1": 1.0, "M5": 1.2, "M15": 1.4}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 35:
            return None
        if not self.has_min_volume(candles):
            return None

        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]

        lower, mid, upper = Indicators.bollinger(close, 20, 2.0)
        k, d = Indicators.stochastic(high, low, close, k_period=14, d_period=3)
        rsi = Indicators.rsi(close, 14)
        adx = Indicators.adx(high, low, close)

        if np.isnan([lower[-1], upper[-1], k[-1], d[-1], rsi[-1], adx[-1]]).any():
            return None
        if np.isnan(k[-2]) or np.isnan(d[-2]):
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction: Optional[str] = None

        # ── CALL: preço tocou banda inferior + Stoch saindo de sobrevenda ──
        # Toque: low da última vela <= banda inferior (com tolerância)
        touch_lower = low[-1] <= lower[-1] * 1.001
        stoch_oversold = k[-2] < 20 and d[-2] < 20
        stoch_cross_up = k[-1] > d[-1] and k[-2] <= d[-2]

        if touch_lower and (stoch_oversold or k[-1] < 25) and stoch_cross_up:
            direction = "CALL"
            flags["trend"] = True
            flags["rsi"] = rsi[-1] < 40

        # ── PUT: preço tocou banda superior + Stoch saindo de sobrecompra ──
        if direction is None:
            touch_upper = high[-1] >= upper[-1] * 0.999
            stoch_overbought = k[-2] > 80 and d[-2] > 80
            stoch_cross_down = k[-1] < d[-1] and k[-2] >= d[-2]

            if touch_upper and (stoch_overbought or k[-1] > 75) and stoch_cross_down:
                direction = "PUT"
                flags["trend"] = True
                flags["rsi"] = rsi[-1] > 60

        if direction is None:
            return None

        # ── Filtro: ADX moderado — reversão falha em tendência forte ────────
        if adx[-1] > 35:
            return None
        flags["adx"] = adx[-1] < 22

        # ── Filtro: vela de entrada na direção (rejeição visível) ────────────
        if not self.candle_entry_ok(candles[-1], direction):
            return None

        # ── Bônus: padrão de reversão (Pin Bar ou Engulfing) ────────────────
        if self.is_pin_bar(candles[-1], direction) or \
           self.is_engulfing(candles[-2], candles[-1], direction):
            flags["macd"] = True  # usa o flag macd como "padrão de vela confirmado"

        base = self.confluence_score(flags)
        confidence = 65 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 92.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"BB=[{lower[-1]:.5f},{upper[-1]:.5f}] Stoch K={k[-1]:.1f} D={d[-1]:.1f} RSI={rsi[-1]:.1f}",
        )
