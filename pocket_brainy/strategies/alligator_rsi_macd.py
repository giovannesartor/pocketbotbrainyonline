"""Estratégia Alligator + RSI + MACD."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class AlligatorRsiMacdStrategy(BaseStrategy):
    name = "Alligator+RSI+MACD"
    weights = {"M1": 0.9, "M5": 1.3, "M15": 1.4}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 55:
            return None
        if not self.has_min_volume(candles):
            return None
        close  = [c.close for c in candles]
        high   = [c.high  for c in candles]
        low    = [c.low   for c in candles]
        median = [(h + l) / 2 for h, l in zip(high, low)]

        jaw, teeth, lips = Indicators.alligator(median)
        rsi = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx = Indicators.adx(high, low, close)

        j, t, l_ = jaw[-1], teeth[-1], lips[-1]
        if np.isnan([j, t, l_, rsi[-1], hist[-1], adx[-1]]).any():
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        # CALL: lábios > dentes > mandíbulas (alligator abrindo) + RSI>50 + MACD hist>0
        if l_ > t > j and rsi[-1] > 50 and hist[-1] > 0:
            direction = "CALL"
            flags.update(trend=True, rsi=True, macd=True, ema=True)
        elif l_ < t < j and rsi[-1] < 50 and hist[-1] < 0:
            direction = "PUT"
            flags.update(trend=True, rsi=True, macd=True, ema=True)

        if direction is None:
            return None

        # ── Filtro 1: RSI em zona de momentum, não exausta ──────────────────
        if direction == "CALL" and not (52 <= rsi[-1] <= 72):
            return None
        if direction == "PUT"  and not (28 <= rsi[-1] <= 48):
            return None

        # ── Filtro 2: Alligator widening (boca se abrindo, não se fechando) ──
        if len(lips) >= 4 and not np.isnan(lips[-4]) and not np.isnan(teeth[-4]):
            gap_now  = abs(lips[-1] - teeth[-1])
            gap_prev = abs(lips[-4] - teeth[-4])
            if gap_now < gap_prev * 0.85:
                return None  # alligator fechando mais de 15% — tendência esgotando

        # ── Filtro 3: MACD pelo menos não desacelerando ──────────────────────
        if not np.isnan(hist[-2]):
            if direction == "CALL" and hist[-1] < hist[-2]:
                return None
            if direction == "PUT"  and hist[-1] > hist[-2]:
                return None

        # ── Filtro 4: Qualidade da última vela ───────────────────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.18 * avg_b:
            return None

        # ── Filtro 5: Divergência RSI ────────────────────────────────────────
        _lb = 6
        if len(close) > _lb and not np.isnan(rsi[-_lb]):
            if direction == "CALL" and close[-1] > close[-_lb] and rsi[-1] < rsi[-_lb]:
                return None
            if direction == "PUT"  and close[-1] < close[-_lb] and rsi[-1] > rsi[-_lb]:
                return None

        flags["adx"] = adx[-1] > 20
        base = self.confluence_score(flags)
        confidence = 57 + base * 7
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 96.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"Alligator widening, RSI={rsi[-1]:.1f}, MACD={hist[-1]:.5f}, ADX={adx[-1]:.1f}",
        )
