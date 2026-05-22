"""MHI — Método das 3 velas (reversão após 3 velas consecutivas)."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class MhiStrategy(BaseStrategy):
    name = "MHI"
    weights = {"M1": 1.5, "M5": 1.0, "M15": 0.8}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 30:
            return None
        if not self.has_min_volume(candles):
            return None
        last3 = candles[-4:-1]  # 3 velas fechadas antes da última
        greens = sum(1 for c in last3 if c.close > c.open)
        reds   = sum(1 for c in last3 if c.close < c.open)

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        if reds == 3:
            direction = "CALL"
            flags["trend"] = True
        elif greens == 3:
            direction = "PUT"
            flags["trend"] = True

        if direction is None:
            return None

        # ── Filtro 1: Corpo forte em cada vela do padrão ─────────────────────
        avg = self.avg_body(candles, 20)
        if avg > 1e-9 and any(self.candle_body(c) < 0.4 * avg for c in last3):
            return None

        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]

        rsi   = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx   = Indicators.adx(high, low, close)
        bb_lo, _, bb_hi = Indicators.bollinger(close, 20, 2.0)

        if np.isnan([rsi[-1]]).any():
            return None

        # ── Filtro 2: RSI em zona extrema (confirma sobrevenda/sobrecompra) ──
        if direction == "CALL" and rsi[-1] > 52:
            return None
        if direction == "PUT"  and rsi[-1] < 48:
            return None
        flags["rsi"] = (direction == "CALL" and rsi[-1] < 40) or \
                       (direction == "PUT"  and rsi[-1] > 60)

        # ── Filtro 3: Preço próximo à banda de Bollinger ─────────────────────
        if not np.isnan(bb_lo[-1]) and not np.isnan(bb_hi[-1]):
            bb_width = (bb_hi[-1] - bb_lo[-1]) if (bb_hi[-1] - bb_lo[-1]) > 1e-9 else 1e-9
            bb_pos = (close[-1] - bb_lo[-1]) / bb_width
            if direction == "CALL" and bb_pos > 0.55:
                return None
            if direction == "PUT"  and bb_pos < 0.45:
                return None

        # ── Filtro 4: ADX baixo — MHI é de reversão, evitar tendências fortes ─
        if not np.isnan(adx[-1]):
            if adx[-1] > 35:
                return None
            flags["adx"] = adx[-1] < 22

        # ── Filtro 5: MACD virando na direção da reversão ───────────────────
        if not np.isnan(hist[-1]) and not np.isnan(hist[-2]):
            flags["macd"] = (direction == "CALL" and hist[-1] >= hist[-2]) or \
                             (direction == "PUT"  and hist[-1] <= hist[-2])

        base = self.confluence_score(flags)
        confidence = 57 + base * 5
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 87.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=(
                f"3 velas {'vermelhas' if reds == 3 else 'verdes'} "
                f"RSI={rsi[-1]:.1f} ADX={adx[-1]:.1f}"
            ),
        )
