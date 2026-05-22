"""Multi-Filtro: EMA50 (macro) + EMA9/21 (micro) + RSI + MACD + ADX + BB."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


class MultiFilterStrategy(BaseStrategy):
    name = "MultiFiltro"
    weights = {"M1": 0.9, "M5": 1.4, "M15": 1.5}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 60:
            return None
        if not self.has_min_volume(candles):
            return None
        close = [c.close for c in candles]
        high = [c.high for c in candles]
        low = [c.low for c in candles]

        ema50 = Indicators.ema(close, 50)
        ema9  = Indicators.ema(close, 9)
        ema21 = Indicators.ema(close, 21)
        rsi   = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx   = Indicators.adx(high, low, close)
        bb_lo, bb_mid, bb_hi = Indicators.bollinger(close, 20, 2.0)
        stoch_k, stoch_d = Indicators.stochastic(high, low, close, 14, 3)

        if np.isnan([ema50[-1], ema9[-1], ema21[-1], rsi[-1], hist[-1], adx[-1],
                     bb_lo[-1], bb_hi[-1]]).any():
            return None

        c_now = close[-1]
        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction = None

        trend_up = c_now > ema50[-1] and ema9[-1] > ema21[-1]
        trend_dn = c_now < ema50[-1] and ema9[-1] < ema21[-1]

        if trend_up and rsi[-1] > 50 and hist[-1] > 0:
            direction = "CALL"
            flags.update(rsi=True, ema=True, macd=True, trend=True)
        elif trend_dn and rsi[-1] < 50 and hist[-1] < 0:
            direction = "PUT"
            flags.update(rsi=True, ema=True, macd=True, trend=True)

        if direction is None:
            return None

        # ── Filtro 1: RSI em zona de momentum, não exausta ──────────────────
        # CALL: RSI 51–72 | PUT: 28–49
        if direction == "CALL" and not (51 <= rsi[-1] <= 72):
            return None
        if direction == "PUT" and not (28 <= rsi[-1] <= 49):
            return None

        # ── Filtro 2: MACD histograma pelo menos não desacelerando ───────────
        if not np.isnan(hist[-2]):
            if direction == "CALL" and hist[-1] < hist[-2]:
                return None  # momentum já caindo
            if direction == "PUT"  and hist[-1] > hist[-2]:
                return None

        # ── Filtro 3: EMA50 com inclinação confirmada (macro não lateral) ────
        if len(ema50) >= 5 and not np.isnan(ema50[-5]):
            slope_ok = (direction == "CALL" and ema50[-1] > ema50[-5]) or \
                       (direction == "PUT"  and ema50[-1] < ema50[-5])
            if not slope_ok:
                return None

        # ── Filtro 4: Preço não overextended em relação à EMA50 ─────────────
        avg_b = self.avg_body(candles, 10)
        dist_ema50 = abs(c_now - ema50[-1])
        if avg_b > 1e-9 and dist_ema50 > 3.5 * avg_b:
            return None  # preço muito afastado — risco de pullback iminente

        # ── Filtro 5: Última vela sem doji ───────────────────────────────────
        avg_b20 = self.avg_body(candles, 20)
        if avg_b20 > 1e-9 and self.candle_body(candles[-1]) < 0.20 * avg_b20:
            return None  # doji/spinning top na entrada

        # ── Filtro 6: Bollinger — não no extremo oposto ──────────────────────
        bb_width = (bb_hi[-1] - bb_lo[-1]) if (bb_hi[-1] - bb_lo[-1]) > 1e-9 else 1e-9
        bb_pos = (c_now - bb_lo[-1]) / bb_width
        if direction == "CALL" and bb_pos > 0.88:
            return None
        if direction == "PUT" and bb_pos < 0.12:
            return None

        # ── Filtro 7: Divergência RSI ────────────────────────────────────────
        _lb = 6
        if len(close) > _lb and not np.isnan(rsi[-_lb]):
            if direction == "CALL" and close[-1] > close[-_lb] and rsi[-1] < rsi[-_lb]:
                return None
            if direction == "PUT"  and close[-1] < close[-_lb] and rsi[-1] > rsi[-_lb]:
                return None

        # ── Filtro A: ADX em tendência crescente (não caindo) ────────────────
        if len(adx) >= 6 and not np.isnan(adx[-5]):
            if adx[-1] < adx[-5]:
                return None  # ADX caindo — tendência se esgotando

        # ── Filtro B: Cross EMA9/21 fresco (últimas 10 velas) ────────────────
        _cross_found = False
        for _i in range(2, min(11, len(ema9))):
            _prev9, _prev21 = ema9[-_i], ema21[-_i]
            if np.isnan(_prev9) or np.isnan(_prev21):
                break
            if direction == "CALL" and _prev9 <= _prev21:
                _cross_found = True
                break
            if direction == "PUT"  and _prev9 >= _prev21:
                _cross_found = True
                break
        if not _cross_found:
            return None  # cruzamento muito antigo — tendência pode estar no fim

        # ── Filtro D: Estocástico em zona de momentum (sem exigir cross exato) ─
        if not (np.isnan(stoch_k[-1]) or np.isnan(stoch_d[-1])):
            if direction == "CALL" and stoch_k[-1] > 75:
                return None  # estocástico sobrecomprado
            if direction == "PUT"  and stoch_k[-1] < 25:
                return None  # estocástico sobrevendido

        flags["adx"] = adx[-1] > 20
        base = self.confluence_score(flags)   # até 6
        confidence = 62 + base * 6
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 97.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=(
                f"RSI={rsi[-1]:.1f} EMA9/21gap={ema9[-1]-ema21[-1]:.5f} "
                f"MACD={hist[-1]:.5f} ADX={adx[-1]:.1f} BB={bb_pos:.2f}"
            ),
        )

