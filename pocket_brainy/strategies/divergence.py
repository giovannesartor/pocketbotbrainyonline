"""Divergência RSI/MACD vs preço — reversão de alta confiança em M15."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal


def _find_swings(values: np.ndarray, lookback: int = 30, min_distance: int = 4) -> Tuple[Optional[int], Optional[int]]:
    """Encontra os 2 últimos picos (highs) e 2 últimos vales (lows) na janela.

    Retorna (idx_high2, idx_high1) ou (idx_low2, idx_low1) sendo idx1 o mais recente.
    Implementação: percorre de trás pra frente, escolhe extremos locais separados por min_distance.
    """
    n = len(values)
    start = max(0, n - lookback)
    indices: List[int] = []
    for i in range(n - 2, start, -1):
        if np.isnan(values[i]):
            continue
        if not np.isnan(values[i - 1]) and not np.isnan(values[i + 1]):
            if values[i] > values[i - 1] and values[i] > values[i + 1]:
                if not indices or (indices[-1] - i) >= min_distance:
                    indices.append(i)
            if len(indices) >= 2:
                break
    if len(indices) < 2:
        return None, None
    return indices[1], indices[0]  # mais antigo, mais recente


def _find_swings_low(values: np.ndarray, lookback: int = 30, min_distance: int = 4) -> Tuple[Optional[int], Optional[int]]:
    n = len(values)
    start = max(0, n - lookback)
    indices: List[int] = []
    for i in range(n - 2, start, -1):
        if np.isnan(values[i]):
            continue
        if not np.isnan(values[i - 1]) and not np.isnan(values[i + 1]):
            if values[i] < values[i - 1] and values[i] < values[i + 1]:
                if not indices or (indices[-1] - i) >= min_distance:
                    indices.append(i)
            if len(indices) >= 2:
                break
    if len(indices) < 2:
        return None, None
    return indices[1], indices[0]


class DivergenceStrategy(BaseStrategy):
    """Detecta divergências bullish/bearish entre preço e RSI (com confirmação MACD)."""

    name = "Divergência RSI/MACD"
    # Reversão funciona melhor em timeframes maiores; M1 desabilitado por ruído
    weights = {"M1": 0.0, "M5": 1.1, "M15": 1.6}

    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if len(candles) < 50:
            return None
        if self.weight_for(timeframe) <= 0:
            return None
        if not self.has_min_volume(candles):
            return None

        close = np.array([c.close for c in candles], dtype=float)
        high  = np.array([c.high  for c in candles], dtype=float)
        low   = np.array([c.low   for c in candles], dtype=float)

        rsi = Indicators.rsi(close, 14)
        _, _, hist = Indicators.macd(close)
        adx = Indicators.adx(high, low, close)

        if np.isnan(rsi[-1]) or np.isnan(hist[-1]) or np.isnan(adx[-1]):
            return None

        flags = {"rsi": False, "ema": False, "macd": False, "trend": False, "adx": False}
        direction: Optional[str] = None
        notes_extra = ""

        # ── Divergência BEARISH: preço faz higher high, RSI faz lower high ──
        ph_old, ph_new = _find_swings(high, lookback=35, min_distance=4)
        if ph_old is not None and ph_new is not None:
            if high[ph_new] > high[ph_old] and rsi[ph_new] < rsi[ph_old] - 2.0:
                # Confirmação: RSI atual < 70 (saindo da sobrecompra)
                if rsi[-1] < 70 and rsi[ph_new] >= 60:
                    direction = "PUT"
                    notes_extra = f"DIV-BEAR H:{high[ph_old]:.5f}→{high[ph_new]:.5f} RSI:{rsi[ph_old]:.1f}→{rsi[ph_new]:.1f}"
                    flags["rsi"] = True
                    flags["trend"] = True

        # ── Divergência BULLISH: preço faz lower low, RSI faz higher low ────
        if direction is None:
            pl_old, pl_new = _find_swings_low(low, lookback=35, min_distance=4)
            if pl_old is not None and pl_new is not None:
                if low[pl_new] < low[pl_old] and rsi[pl_new] > rsi[pl_old] + 2.0:
                    if rsi[-1] > 30 and rsi[pl_new] <= 40:
                        direction = "CALL"
                        notes_extra = f"DIV-BULL L:{low[pl_old]:.5f}→{low[pl_new]:.5f} RSI:{rsi[pl_old]:.1f}→{rsi[pl_new]:.1f}"
                        flags["rsi"] = True
                        flags["trend"] = True

        if direction is None:
            return None

        # ── Confirmação MACD: histograma virando na direção da divergência ──
        if not np.isnan(hist[-2]):
            if direction == "CALL" and hist[-1] >= hist[-2]:
                flags["macd"] = True
            elif direction == "PUT" and hist[-1] <= hist[-2]:
                flags["macd"] = True
            else:
                # MACD ainda contra — exige divergência mais forte
                return None

        # ── Filtro: ADX não muito alto (tendência forte mata reversão) ──────
        if adx[-1] > 38:
            return None
        flags["adx"] = adx[-1] < 25

        # ── Filtro: vela de entrada na direção (rejeição visível) ────────────
        if not self.candle_entry_ok(candles[-1], direction):
            return None

        base = self.confluence_score(flags)
        confidence = 70 + base * 4  # divergência confirmada = setup forte
        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base,
            confidence=min(confidence, 94.0),
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence=flags,
            notes=f"{notes_extra} ADX={adx[-1]:.1f}",
        )
