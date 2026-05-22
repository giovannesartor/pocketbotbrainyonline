"""Contrato base para todas as estratégias."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..utils.indicators import Candle


# Pesos por timeframe — cada estratégia define seus próprios pesos.
# S10 / S30 = scalper TFs (default 0.0 = estratégias normais não operam neles).
DEFAULT_WEIGHTS: Dict[str, float] = {"S10": 0.0, "S30": 0.0, "M1": 1.0, "M5": 1.0, "M15": 1.0}


@dataclass
class Signal:
    strategy: str
    direction: str               # CALL | PUT
    base_score: float            # 0..10
    confidence: float            # 0..100
    timeframe: str = "M1"
    weight: float = 1.0
    ranking_bonus: float = 0.0        # aplicado pelo ranking dinâmico
    tf_confluence_bonus: float = 0.0   # aplicado quando 2+ TFs concordam na mesma direção
    confluence: Dict[str, bool] = field(default_factory=dict)
    notes: str = ""

    # 📡 Snapshot do mercado no momento do sinal (usado por filtros pré-ordem)
    signal_close: float = 0.0    # close da última vela quando o sinal foi gerado
    signal_atr: float = 0.0      # ATR absoluto (preço) — referência de volatilidade
    market_regime: str = ""      # TREND_UP | TREND_DOWN | RANGE | CHOP

    @property
    def final_score(self) -> float:
        """score_final = score_base × peso_timeframe + ranking_bonus + tf_confluence_bonus."""
        return self.base_score * self.weight + self.ranking_bonus + self.tf_confluence_bonus


class BaseStrategy(ABC):
    name: str = "Base"
    weights: Dict[str, float] = DEFAULT_WEIGHTS

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def weight_for(self, timeframe: str) -> float:
        return self.weights.get(timeframe, 1.0)

    @abstractmethod
    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        """Retorna um sinal ou None se não houver setup."""

    # ---- utilidades de confluência ----
    @staticmethod
    def confluence_score(flags: Dict[str, bool]) -> float:
        """Aplica pontuação conforme o contrato: RSI +1, EMA +1, MACD +1, Tendência +2, ADX +1."""
        weights = {"rsi": 1, "ema": 1, "macd": 1, "trend": 2, "adx": 1}
        return float(sum(w for k, w in weights.items() if flags.get(k)))

    @staticmethod
    def has_min_volume(candles: List[Candle], period: int = 20, threshold: float = 0.2) -> bool:
        """True se o volume da última vela não está suspeitosamente baixo.
        Retorna True (não filtra) quando dados de volume são indisponíveis (todos zero)."""
        if len(candles) < period + 1:
            return True
        vols = [c.volume for c in candles[-(period + 1):-1]]
        avg = sum(vols) / len(vols) if vols else 0.0
        if avg < 1e-9:
            return True  # sem dados de volume — não filtra
        return candles[-1].volume >= threshold * avg

    @staticmethod
    def candle_body(c: Candle) -> float:
        """Tamanho do corpo da vela (abs(close - open))."""
        return abs(c.close - c.open)

    @staticmethod
    def avg_body(candles: List[Candle], period: int = 20) -> float:
        """Tamanho médio dos corpos das últimas `period` velas."""
        if not candles:
            return 0.0
        recent = candles[-period:]
        bodies = [abs(c.close - c.open) for c in recent]
        return sum(bodies) / len(bodies)

    @staticmethod
    def candle_entry_ok(candle: Candle, direction: str) -> bool:
        """Verifica se a vela fechou numa posição favorável à entrada.

        CALL: fechamento no terço superior do range (posição ≥ 55%).
        PUT : fechamento no terço inferior do range (posição ≤ 45%).
        Dojis (range ≈ 0) são aceitos sem filtro.
        """
        rng = candle.high - candle.low
        if rng < 1e-9:
            return True  # doji puro — não filtra
        position = (candle.close - candle.low) / rng  # 0.0=mínima, 1.0=máxima
        if direction == "CALL":
            return position >= 0.55
        return position <= 0.45

    @staticmethod
    def is_pin_bar(candle: Candle, direction: str) -> bool:
        """Detecta Pin Bar na direção do sinal.

        Pin Bar bullish (CALL): sombra inferior longa (>= 2x o corpo) + corpo no terço superior.
        Pin Bar bearish (PUT) : sombra superior longa (>= 2x o corpo) + corpo no terço inferior.
        """
        rng = candle.high - candle.low
        if rng < 1e-9:
            return False
        body = abs(candle.close - candle.open)
        if body < 1e-9:
            return False  # doji não é pin bar
        upper_shadow = candle.high - max(candle.open, candle.close)
        lower_shadow = min(candle.open, candle.close) - candle.low
        if direction == "CALL":
            # sombra inferior longa, corpo no topo
            return lower_shadow >= 2.0 * body and upper_shadow <= body
        else:
            # sombra superior longa, corpo na base
            return upper_shadow >= 2.0 * body and lower_shadow <= body

    @staticmethod
    def is_engulfing(prev: Candle, curr: Candle, direction: str) -> bool:
        """Detecta Engulfing (vela atual engole o corpo da anterior) na direção do sinal.

        Engulfing bullish (CALL): curr fecha acima e abre abaixo do corpo anterior.
        Engulfing bearish (PUT) : curr fecha abaixo e abre acima do corpo anterior.
        """
        prev_high_body = max(prev.open, prev.close)
        prev_low_body = min(prev.open, prev.close)
        curr_high_body = max(curr.open, curr.close)
        curr_low_body = min(curr.open, curr.close)
        if direction == "CALL":
            return (curr.close > curr.open and           # vela atual de alta
                    curr_low_body <= prev_low_body and    # abre abaixo do corpo anterior
                    curr_high_body >= prev_high_body)     # fecha acima do corpo anterior
        else:
            return (curr.close < curr.open and           # vela atual de baixa
                    curr_high_body >= prev_high_body and  # abre acima do corpo anterior
                    curr_low_body <= prev_low_body)       # fecha abaixo do corpo anterior
