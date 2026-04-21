"""Gerenciador multi-estratégia: executa todas, filtra e escolhe o melhor sinal."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Type

from ..utils.indicators import Candle
from ..utils.logger import get_logger
from ..utils.market_filter import MarketFilter, MarketState
from .base import BaseStrategy, Signal
from .ranking import StrategyRanking

logger = get_logger("strategies.manager")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATES_PATH = DATA_DIR / "strategies.json"


@dataclass
class AssetAnalysis:
    asset: str
    timeframe: str
    best_signal: Optional[Signal]
    all_signals: List[Signal]
    market_state: Optional[MarketState]
    payout: float = 0.0
    assertiveness: float = 0.0


class StrategyManager:
    """Orquestra estratégias + ranking + filtro de mercado."""

    def __init__(self, strategy_classes: List[Type[BaseStrategy]], ranking: Optional[StrategyRanking] = None):
        self.strategies: List[BaseStrategy] = [s() for s in strategy_classes]
        self.ranking = ranking or StrategyRanking()
        self.market_filter = MarketFilter()
        self._lock = threading.Lock()
        self._load_states()

    # ---- Persistência do toggle ativo/inativo ----
    def _load_states(self) -> None:
        if not STATES_PATH.exists():
            self._save_states()
            return
        try:
            with STATES_PATH.open("r", encoding="utf-8") as f:
                states: Dict[str, bool] = json.load(f)
            for s in self.strategies:
                if s.name in states:
                    s.enabled = states[s.name]
        except Exception as e:
            logger.error(f"Falha ao carregar estados das estratégias: {e}")

    def _save_states(self) -> None:
        STATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATES_PATH.open("w", encoding="utf-8") as f:
            json.dump({s.name: s.enabled for s in self.strategies}, f, indent=2, ensure_ascii=False)

    def toggle(self, name: str) -> Optional[bool]:
        with self._lock:
            for s in self.strategies:
                if s.name == name:
                    s.enabled = not s.enabled
                    self._save_states()
                    return s.enabled
        return None

    def enabled_strategies(self) -> List[BaseStrategy]:
        return [s for s in self.strategies if s.enabled]

    def list_status(self) -> List[Dict]:
        return [{"name": s.name, "enabled": s.enabled} for s in self.strategies]

    # ---- Análise ----
    def analyze_asset(
        self,
        asset: str,
        candles: List[Candle],
        timeframe: str,
        min_score: float,
        min_confidence: float,
    ) -> AssetAnalysis:
        """Executa todas as estratégias habilitadas e escolhe a melhor."""
        market = self.market_filter.evaluate(candles)

        trend_blocked = set(MarketFilter.trend_strategies()) if market.is_lateral else set()

        signals: List[Signal] = []
        for strat in self.enabled_strategies():
            if strat.name in trend_blocked:
                continue
            try:
                sig = strat.analyze(candles, timeframe)
            except Exception as e:
                logger.exception(f"Erro na estratégia {strat.name}: {e}")
                continue
            if not sig:
                continue
            sig.ranking_bonus = self.ranking.bonus(strat.name)
            signals.append(sig)

        best = None
        if signals:
            # Filtrar mínimo de score/confiança
            filtered = [s for s in signals if s.confidence >= min_confidence and s.final_score >= min_score]
            if filtered:
                best = max(filtered, key=lambda s: (s.final_score, s.confidence))

        return AssetAnalysis(
            asset=asset,
            timeframe=timeframe,
            best_signal=best,
            all_signals=signals,
            market_state=market,
        )
