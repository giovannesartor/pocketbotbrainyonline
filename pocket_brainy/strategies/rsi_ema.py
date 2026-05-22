"""Estratégia RSI + EMA."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class RsiEmaStrategy(BaseStrategy):
    name = "RSI+EMA"
    weights = {"M1": 1.0, "M5": 1.2, "M15": 1.3}

    def __init__(self, rsi_period: int = 14, ema_fast: int = 9, ema_slow: int = 21, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < max(self.ema_slow, self.rsi_period) + 5:
            return None
        if not self.has_min_volume(candles):
            return None
        close = [c.close for c in candles]
        high  = [c.high  for c in candles]
        low   = [c.low   for c in candles]

        rsi   = Indicators.rsi(close, self.rsi_period)
        ema_f = Indicators.ema(close, self.ema_fast)
        ema_s = Indicators.ema(close, self.ema_slow)
        adx   = Indicators.adx(high, low, close)
        _, _, hist = Indicators.macd(close)

        r       = rsi[-1]
        ef, es  = ema_f[-1], ema_s[-1]
        c_now   = close[-1]
        prev_ef = ema_f[-2]
        prev_es = ema_s[-2]

        if np.isnan([r, ef, es, prev_ef, prev_es]).any():
            return None

        direction = None
        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}

        # CALL: RSI saindo da sobrevenda + EMA9 cruzou acima EMA21 + preço acima
        if r < 45 and ef > es and c_now > ef and prev_ef <= prev_es * 1.0001:
            direction = "CALL"
            flags["rsi"]   = r < 45
            flags["ema"]   = ef > es
            flags["trend"] = c_now > ef
        # PUT: RSI saindo da sobrecompra + EMA9 cruzou abaixo EMA21 + preço abaixo
        elif r > 55 and ef < es and c_now < ef and prev_ef >= prev_es * 0.9999:
            direction = "PUT"
            flags["rsi"]   = r > 55
            flags["ema"]   = ef < es
            flags["trend"] = c_now < ef

        if direction is None:
            return None

        # ── Filtro 1: RSI não muito exausta após o cruzamento ───────────────
        if direction == "CALL" and r > 65:
            return None
        if direction == "PUT"  and r < 35:
            return None

        # ── Filtro 2: MACD confirma a direção ──────────────────────────────────
        if not np.isnan(hist[-1]):
            if direction == "CALL" and hist[-1] <= 0:
                return None
            if direction == "PUT"  and hist[-1] >= 0:
                return None
            flags["macd"] = True

        # ── Filtro 3: ADX ────────────────────────────────────────────────────────
        if not np.isnan(adx[-1]):
            flags["adx"] = adx[-1] > 18

        # ── Filtro 4: Corpo da vela de entrada — sem dojis ────────────────────
        avg_b = self.avg_body(candles, 20)
        if avg_b > 1e-9 and self.candle_body(candles[-1]) < 0.15 * avg_b:
            return None

        # ── Filtro 5: Divergência RSI ─────────────────────────────────────────
        _lb = 5
        if len(close) > _lb and not np.isnan(rsi[-_lb]):
            if direction == "CALL" and close[-1] > close[-_lb] and rsi[-1] < rsi[-_lb]:
                return None
            if direction == "PUT"  and close[-1] < close[-_lb] and rsi[-1] > rsi[-_lb]:
                return None

        base = self.confluence_score(flags)
        confidence = 52 + base * 8
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 95.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"RSI={r:.1f} EMA9={ef:.5f} EMA21={es:.5f} MACD={hist[-1]:.5f}",
        )
