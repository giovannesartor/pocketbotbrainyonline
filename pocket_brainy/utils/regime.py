"""Detecção de regime de mercado: TREND_UP | TREND_DOWN | RANGE | CHOP.

Heurística simples (rápida, sem dependências novas):

  • slope: regressão linear dos closes das últimas N velas, normalizado pelo
    desvio-padrão dos closes (z-slope). |z-slope| >= 0.6 → tendência.
  • adx_proxy: usa range médio (high-low) vs std dos closes — proxy barata.
  • range_ratio: amplitude (max-min) dos closes vs std. baixo = RANGE.

Resultado:
  - TREND_UP   : slope positivo forte
  - TREND_DOWN : slope negativo forte
  - RANGE      : slope fraco + amplitude baixa (mercado lateral controlado)
  - CHOP       : slope fraco + amplitude alta (lateral caótico, evitar)
"""
from __future__ import annotations

from typing import List

import numpy as np

from .indicators import Candle

REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "CHOP")


def detect_regime(candles: List[Candle], window: int = 30) -> str:
    """Classifica o regime das últimas `window` candles.
    Retorna uma das strings em REGIMES, ou "" se candles insuficientes.
    """
    if len(candles) < window:
        return ""
    recent = candles[-window:]
    closes = np.array([c.close for c in recent], dtype=float)
    if closes.std() <= 1e-9:
        return "RANGE"

    # slope normalizado (z-score do slope vs std dos closes)
    x = np.arange(len(closes), dtype=float)
    slope, _ = np.polyfit(x, closes, 1)
    # Variação total esperada se a tendência for "real": slope * window
    expected_move = slope * (len(closes) - 1)
    z_slope = expected_move / (closes.std() + 1e-9)

    # Amplitude relativa (range / std)
    amp_ratio = (closes.max() - closes.min()) / (closes.std() + 1e-9)

    # Decisão
    if z_slope >= 0.6:
        return "TREND_UP"
    if z_slope <= -0.6:
        return "TREND_DOWN"
    # Sem tendência clara — distinguir RANGE controlado de CHOP caótico.
    # CHOP: amplitude alta com slope ~0 = vai e volta forte (whipsaw).
    if amp_ratio >= 4.0:
        return "CHOP"
    return "RANGE"


def regime_emoji(regime: str) -> str:
    return {
        "TREND_UP": "📈",
        "TREND_DOWN": "📉",
        "RANGE": "↔️",
        "CHOP": "🌊",
    }.get(regime, "❓")
