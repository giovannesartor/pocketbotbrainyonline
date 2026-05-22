"""Price Action Puro — detecta padrões de vela sem indicadores lagging.

Padrões primários (exigem ao menos 1 para emitir sinal):
  • Pin Bar bullish / bearish
  • Engulfing bullish / bearish
  • Outside Bar bullish / bearish

Confirmações adicionais (aumentam score/confiança):
  • Tweezer Top / Bottom
  • Three-Bar Reversal
  • Corpo crescente (momentum)

Score e confiança escalam com o número de padrões confirmados:
  1 padrão → score 5.5 | conf 65%
  2 padrões → score 6.5 | conf 73%
  3+ padrões → score 7.5 | conf 80%
"""
from __future__ import annotations

from typing import List, Optional

from ..utils.indicators import Candle
from .base import BaseStrategy, Signal


class PriceActionStrategy(BaseStrategy):
    """Detecta padrões de Price Action puros — sem indicadores lagging."""

    name = "Price Action"
    weights = {"M1": 0.9, "M5": 1.1, "M15": 1.3}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 10:
            return None
        if self.weight_for(timeframe) <= 0:
            return None
        if not self.has_min_volume(candles):
            return None

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        avg_body = self.avg_body(candles, period=14)
        if avg_body < 1e-9:
            return None

        direction: Optional[str] = None
        patterns_found: List[str] = []

        # ─── 1. Pin Bar ────────────────────────────────────────────────
        if self.is_pin_bar(last, "CALL") and self.candle_body(last) >= 0.3 * avg_body:
            direction = "CALL"
            patterns_found.append("Pin Bar Bullish")
        elif self.is_pin_bar(last, "PUT") and self.candle_body(last) >= 0.3 * avg_body:
            direction = "PUT"
            patterns_found.append("Pin Bar Bearish")

        # ─── 2. Engulfing ──────────────────────────────────────────────
        if self.is_engulfing(prev, last, "CALL"):
            _d = "CALL"
            if direction is None:
                direction = _d
            if direction == _d:
                patterns_found.append("Engulfing Bullish")
        if self.is_engulfing(prev, last, "PUT"):
            _d = "PUT"
            if direction is None:
                direction = _d
            if direction == _d:
                patterns_found.append("Engulfing Bearish")

        # ─── 3. Outside Bar ────────────────────────────────────────────
        if last.high > prev.high and last.low < prev.low:
            mid_prev = (prev.high + prev.low) / 2.0
            if last.close > mid_prev and last.close > last.open:
                _d = "CALL"
                if direction is None:
                    direction = _d
                if direction == _d:
                    patterns_found.append("Outside Bar Bullish")
            elif last.close < mid_prev and last.close < last.open:
                _d = "PUT"
                if direction is None:
                    direction = _d
                if direction == _d:
                    patterns_found.append("Outside Bar Bearish")

        # Sem padrão primário → sem sinal
        if not direction or not patterns_found:
            return None

        # ─── 4. Tweezer (confirmação) ──────────────────────────────────
        tol = avg_body * 0.15  # tolerância de 15% do corpo médio
        if direction == "CALL":
            if (abs(last.low - prev.low) <= tol
                    and prev.close < prev.open
                    and last.close > last.open):
                patterns_found.append("Tweezer Bottom")
        else:
            if (abs(last.high - prev.high) <= tol
                    and prev.close > prev.open
                    and last.close < last.open):
                patterns_found.append("Tweezer Top")

        # ─── 5. Three-Bar Reversal (confirmação) ──────────────────────
        if direction == "CALL":
            three_bear = all(c.close < c.open for c in [prev2, prev])
            reversal_body = self.candle_body(last) >= 0.7 * avg_body
            if three_bear and last.close > last.open and reversal_body:
                patterns_found.append("3-Bar Reversal Bullish")
        else:
            three_bull = all(c.close > c.open for c in [prev2, prev])
            reversal_body = self.candle_body(last) >= 0.7 * avg_body
            if three_bull and last.close < last.open and reversal_body:
                patterns_found.append("3-Bar Reversal Bearish")

        # ─── 6. Corpo crescente (momentum, confirmação) ────────────────
        body_now = self.candle_body(last)
        body_prev = self.candle_body(prev)
        if body_now > body_prev * 1.3 and body_now >= 0.6 * avg_body:
            if direction == "CALL" and last.close > last.open:
                patterns_found.append("Momentum Bullish")
            elif direction == "PUT" and last.close < last.open:
                patterns_found.append("Momentum Bearish")

        # ─── Scoring ──────────────────────────────────────────────────
        n = len(patterns_found)
        if n == 1:
            base_score = 5.5
            confidence = 65.0
        elif n == 2:
            base_score = 6.5
            confidence = 73.0
        else:
            base_score = 7.5
            confidence = 80.0

        # Penalidade por corpo fraco na última vela
        if self.candle_body(last) < 0.4 * avg_body:
            base_score -= 0.5
            confidence -= 5.0

        notes = " | ".join(patterns_found)

        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base_score,
            confidence=confidence,
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence={
                "pin_bar":    any("Pin Bar"    in p for p in patterns_found),
                "engulfing":  any("Engulfing"  in p for p in patterns_found),
                "outside_bar":any("Outside Bar" in p for p in patterns_found),
                "tweezer":    any("Tweezer"    in p for p in patterns_found),
            },
            notes=notes,
        )
