"""Heikin-Ashi Reversal — exaustão de tendência via velas suavizadas.

Usado principalmente em M15 onde o ruído de M5 é filtrado.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


def _heikin_ashi(candles: List[Candle]) -> List[Tuple[float, float, float, float]]:
    """Converte velas OHLC em Heikin-Ashi (ha_open, ha_high, ha_low, ha_close)."""
    out: List[Tuple[float, float, float, float]] = []
    for i, c in enumerate(candles):
        ha_close = (c.open + c.high + c.low + c.close) / 4.0
        if i == 0:
            ha_open = (c.open + c.close) / 2.0
        else:
            prev_open, _, _, prev_close = out[-1]
            ha_open = (prev_open + prev_close) / 2.0
        ha_high = max(c.high, ha_open, ha_close)
        ha_low  = min(c.low, ha_open, ha_close)
        out.append((ha_open, ha_high, ha_low, ha_close))
    return out


class HeikinAshiReversalStrategy(BaseStrategy):
    """Detecta exaustão de tendência: 3+ velas HA na mesma cor + sinal de reversão."""

    name = "Heikin-Ashi Reversal"
    weights = {"M1": 0.0, "M5": 1.0, "M15": 1.5}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 30:
            return None
        if self.weight_for(timeframe) <= 0:
            return None
        if not self.has_min_volume(candles):
            return None

        ha = _heikin_ashi(candles)
        if len(ha) < 5:
            return None

        # Última vela HA e 3 anteriores
        last = ha[-1]
        prev1 = ha[-2]
        prev2 = ha[-3]
        prev3 = ha[-4]

        def is_bull(c): return c[3] > c[0]   # ha_close > ha_open
        def is_bear(c): return c[3] < c[0]
        def upper_wick(c): return c[1] - max(c[0], c[3])
        def lower_wick(c): return min(c[0], c[3]) - c[2]
        def body(c): return abs(c[3] - c[0])

        direction: Optional[str] = None
        notes_extra = ""

        # ── Reversão BEARISH: 3+ velas verdes seguidas + última vela com pavio superior ──
        if is_bull(prev3) and is_bull(prev2) and is_bull(prev1):
            up_wick = upper_wick(prev1)
            b = body(prev1)
            # Exaustão: pavio superior >= corpo (rejeição visível)
            if b > 1e-9 and up_wick >= b * 0.8:
                # Confirmação: vela atual HA virou bearish OU pavio inferior pequeno
                if is_bear(last) or upper_wick(last) > body(last):
                    direction = "PUT"
                    notes_extra = f"HA-EXH-BULL pavio_sup={up_wick:.5f} corpo={b:.5f}"

        # ── Reversão BULLISH: 3+ velas vermelhas seguidas + pavio inferior ──
        if direction is None and is_bear(prev3) and is_bear(prev2) and is_bear(prev1):
            lo_wick = lower_wick(prev1)
            b = body(prev1)
            if b > 1e-9 and lo_wick >= b * 0.8:
                if is_bull(last) or lower_wick(last) > body(last):
                    direction = "CALL"
                    notes_extra = f"HA-EXH-BEAR pavio_inf={lo_wick:.5f} corpo={b:.5f}"

        if direction is None:
            return None

        # ── Confirmação RSI saindo de zona extrema ──────────────────────────
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]
        rsi = Indicators.rsi(close, 14)
        adx = Indicators.adx(high, low, close)

        if np.isnan(rsi[-1]) or np.isnan(adx[-1]):
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": True, "adx": False}

        if direction == "PUT":
            if rsi[-1] < 50:
                return None  # já baixou demais — sinal atrasado
            if rsi[-1] > 60:
                flags["rsi"] = True
        else:  # CALL
            if rsi[-1] > 50:
                return None
            if rsi[-1] < 40:
                flags["rsi"] = True

        # ── Filtro: ADX não muito alto (tendência forte continua) ───────────
        if adx[-1] > 40:
            return None
        flags["adx"] = adx[-1] < 28

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
            notes=f"{notes_extra} RSI={rsi[-1]:.1f} ADX={adx[-1]:.1f}",
        )
