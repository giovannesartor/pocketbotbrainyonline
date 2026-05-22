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
from ..utils.regime import detect_regime
from .base import BaseStrategy, Signal
from .ranking import StrategyRanking
from .pair_stats import PairStatsTracker
from .regime_stats import REGIME_STATS

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
        self.pair_stats = PairStatsTracker()
        self.market_filter = MarketFilter()
        self._lock = threading.Lock()
        # Cache de análise por (asset, tf, last_candle_ts, min_score, min_confidence).
        # Evita recomputar todas as estratégias se o mesmo conjunto de velas
        # fechadas é analisado de novo dentro do mesmo candle (possível quando
        # vol_ratio/payout cache hit dispara nova avaliação no mesmo tick).
        self._analysis_cache: Dict[str, AssetAnalysis] = {}
        # 🎯 Modo Scalper: quando True, só ScalperStrategy roda
        self._scalper_only: bool = False
        self._load_states()

    def set_scalper_only(self, enabled: bool) -> None:
        """Liga/desliga o modo scalper-exclusivo. Limpa o cache de análise."""
        if self._scalper_only != enabled:
            self._scalper_only = enabled
            self._analysis_cache.clear()

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
        if self._scalper_only:
            return [s for s in self.strategies if s.name == "Scalper Sniper"]
        # Fora do modo scalper: oculta a Scalper das estratégias normais
        return [s for s in self.strategies if s.enabled and s.name != "Scalper Sniper"]

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
        regime_filter_enabled: bool = False,
        regime_min_trades: int = 20,
        regime_min_wr: float = 45.0,
    ) -> AssetAnalysis:
        """Executa todas as estratégias habilitadas e escolhe a melhor."""
        # Cache hit: mesmas velas fechadas → mesma análise.
        if candles:
            _last_ts = getattr(candles[-1], "timestamp", 0)
            _cache_k = f"{asset}|{timeframe}|{_last_ts}|{min_score:.2f}|{min_confidence:.1f}"
            _hit = self._analysis_cache.get(_cache_k)
            if _hit is not None:
                return _hit
        else:
            _cache_k = None

        market = self.market_filter.evaluate(candles)

        # 🌡️ Regime de mercado (TREND_UP/DOWN, RANGE, CHOP)
        regime = detect_regime(candles, window=30) if len(candles) >= 30 else ""

        # 📡 ATR absoluto simples (média do True Range das últimas 14 velas)
        _atr_abs = 0.0
        if len(candles) >= 15:
            _trs = []
            for i in range(-14, 0):
                _h, _l = candles[i].high, candles[i].low
                _pc = candles[i - 1].close
                _trs.append(max(_h - _l, abs(_h - _pc), abs(_l - _pc)))
            _atr_abs = sum(_trs) / len(_trs)

        # Detecção de manipulação OTC: se 2+ das últimas 3 velas têm
        # wick total > 70% do range (corpo pequeno + sombras grandes), é
        # sinal de stop hunt / liquidez sendo varrida → bloqueia entradas.
        _wick_block = False
        if len(candles) >= 4:
            _spike = 0
            for _c in candles[-4:-1]:  # 3 últimas FECHADAS
                _rng = _c.high - _c.low
                if _rng <= 1e-9:
                    continue
                _body = abs(_c.close - _c.open)
                _wick_ratio = 1.0 - (_body / _rng)
                if _wick_ratio > 0.70:
                    _spike += 1
            if _spike >= 2:
                _wick_block = True

        trend_blocked = set(MarketFilter.trend_strategies()) if market.is_lateral else set()
        # 🎯 Scalper mode: ignora filtro lateral (ADX/BB foi calibrado para M5/M15;
        # em S10/S30/M1 o ADX vive baixo e travaria todas as entradas).
        if self._scalper_only:
            trend_blocked = set()

        signals: List[Signal] = []
        # Volume relativo da última vela vs média 20 anteriores (só calcula uma vez por análise)
        _vol_ratio = 0.0
        if len(candles) >= 22:
            _vols = [c.volume for c in candles[-21:-1]]
            _avg_vol = sum(_vols) / len(_vols) if _vols else 0.0
            if _avg_vol > 1e-9:
                _vol_ratio = candles[-1].volume / _avg_vol

        for strat in self.enabled_strategies():
            if strat.name in trend_blocked:
                continue
            # 🌡️ Regime filter: bloqueia estratégias com WR ruim no regime atual
            if regime_filter_enabled and regime:
                _wr, _n = REGIME_STATS.wr(strat.name, regime)
                if _n >= regime_min_trades and _wr < regime_min_wr:
                    continue
            try:
                sig = strat.analyze(candles, timeframe)
            except Exception as e:
                logger.exception(f"Erro na estratégia {strat.name}: {e}")
                continue
            if not sig:
                continue
            # 📡 Popula snapshot do mercado no sinal (filtro spread, regime stats)
            if candles:
                sig.signal_close = float(candles[-1].close)
            sig.signal_atr = float(_atr_abs)
            sig.market_regime = regime
            sig.ranking_bonus = self.ranking.bonus(strat.name)
            if market.lateral_penalty > 0 and strat.name in MarketFilter.trend_strategies():
                sig.ranking_bonus -= market.lateral_penalty
            # Score adaptativo por par (ativo+estratégia): subtrai do bonus para
            # "empurrar" a barra de entrada para cima em pares tóxicos.
            sig.ranking_bonus -= self.pair_stats.score_adjustment(asset, strat.name)
            # Volume relativo: reforça sinal quando volume da vela sinalizadora é alto.
            # >=1.5x média: +0.5 | >=2.0x: +1.0 | <0.5x média: -0.5 (volume fraco)
            if _vol_ratio >= 2.0:
                sig.ranking_bonus += 1.0
                sig.notes = (sig.notes + " | 📊 Vol 2x+").strip(" |")
            elif _vol_ratio >= 1.5:
                sig.ranking_bonus += 0.5
                sig.notes = (sig.notes + " | 📊 Vol alto").strip(" |")
            elif 0 < _vol_ratio < 0.5:
                sig.ranking_bonus -= 0.5
                sig.notes = (sig.notes + " | 📉 Vol fraco").strip(" |")
            signals.append(sig)

        best = None
        if signals and not _wick_block:
            # Filtrar mínimo de score/confiança
            filtered = [s for s in signals if s.confidence >= min_confidence and s.final_score >= min_score]
            if filtered:
                best = max(filtered, key=lambda s: (s.final_score, s.confidence))
            else:
                # 📊 Near-miss: signal existe mas score < min. Registra para visibilidade.
                try:
                    from .scalper import SCAN_STATS as _SS
                    _best_unfiltered = max(signals, key=lambda s: s.final_score)
                    _SS["low_score"] += 1
                    if _best_unfiltered.final_score >= (min_score - 1.5):
                        _SS["near_misses"].append([
                            asset, timeframe, _best_unfiltered.direction,
                            round(_best_unfiltered.final_score, 2),
                        ])
                        # Limita lista a 30 itens
                        if len(_SS["near_misses"]) > 30:
                            _SS["near_misses"] = _SS["near_misses"][-30:]
                    if _best_unfiltered.final_score > _SS["best_score"]:
                        _SS["best_score"] = float(_best_unfiltered.final_score)
                except Exception:
                    pass

        if _wick_block and signals:
            # Anota motivo no melhor sinal (mesmo bloqueado, fica visível no log).
            for _s in signals:
                _s.notes = (_s.notes + " | 🚫 Wick spike (manip OTC)").strip(" |")

        analysis = AssetAnalysis(
            asset=asset,
            timeframe=timeframe,
            best_signal=best,
            all_signals=signals,
            market_state=market,
        )
        if _cache_k is not None:
            # Limpa cache se crescer demais (~200 entradas).
            if len(self._analysis_cache) > 200:
                self._analysis_cache.clear()
            self._analysis_cache[_cache_k] = analysis
        return analysis
