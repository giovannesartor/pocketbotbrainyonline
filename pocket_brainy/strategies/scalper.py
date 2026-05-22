"""🎯 SCALPER STRATEGY — Modo cirúrgico para 10s/30s/M1.

Arquitetura em 3 camadas:

  ┌────────────────────────────────────────────────────────────┐
  │  A. FILTROS DE MERCADO (block-or-pass — todos obrigatórios)│
  │     • Wick ratio: 3 últimas velas com pavios > 60% → BLOCK │
  │     • ATR mínimo: vol < 30% da média → BLOCK (mercado morto)│
  │     • ATR máximo: vol > 250% da média → BLOCK (manip)      │
  │     • Vela "limpa" anterior: corpo >= 40% range → BLOCK se não│
  ├────────────────────────────────────────────────────────────┤
  │  B. NÚCLEOS DE SINAL (precisa ≥1 disparar)                  │
  │     1. Tick Momentum: 3 velas seguidas + corpo crescente   │
  │     2. EMA Cross Cirúrgico: EMA3×EMA8 + última no terço fav │
  │     3. RSI Extremo: <25/>75 + reversão na última vela      │
  │     4. VWAP Touch: preço encostou VWAP + reversão           │
  ├────────────────────────────────────────────────────────────┤
  │  C. CONFIRMAÇÕES (todas obrigatórias após núcleo disparar) │
  │     1. Confluência ≥3 indicadores (RSI/MACD/EMA/Stoch)     │
  │     2. Última vela com fechamento no terço favorável (≥70%)│
  │     3. Vela anterior na direção OU Pin Bar/Engulfing       │
  │     4. Sem divergência oculta RSI vs preço                 │
  └────────────────────────────────────────────────────────────┘

Score escala com:
  • Quantos núcleos dispararam (1→6.5, 2→7.5, 3+→8.5)
  • Quantas confirmações fortes (cada extra +0.3 no score / +3 conf)
  • Volume relativo (>=1.5x → +0.5)
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from ..utils.indicators import Candle, Indicators
from .base import BaseStrategy, Signal

# Caminho do ranking interno do scalper (winrate por TF e por núcleo)
_SCALPER_STATS_PATH = Path(__file__).resolve().parents[1] / "data" / "scalper_stats.json"
_STATS_LOCK = threading.Lock()
_ROLLING_WINDOW = 20         # últimas N entradas pra calcular WR
_DISABLE_THRESHOLD = 0.30    # WR < 30% → desliga aquele núcleo/TF
_MIN_TRADES_TO_DISABLE = 30  # número mínimo de trades antes de desligar
# 🔴 Kill switch global: se True, ignora TODOS os filtros do ranking interno
# (núcleos e TFs sempre habilitados). Útil para coletar amostra fresca após
# resetar o histórico ou quando o ranking ficou "venenoso" demais.
SCALPER_RANKING_DISABLED = True

# 📊 Contadores globais de scan (visíveis para logs do bot)
SCAN_STATS = {
    "total": 0,        # combos asset×TF analisados
    "wick": 0,         # rejeitado por wick spike
    "atr_low": 0,      # rejeitado por ATR < mínimo
    "atr_high": 0,     # rejeitado por ATR > máximo
    "doji_prev": 0,    # vela anterior é doji
    "doji_last": 0,    # vela atual é doji
    "prev_clean": 0,   # vela anterior corpo < 25%
    "no_core": 0,      # nenhum núcleo disparou
    "core_tie": 0,     # empate CALL/PUT
    "confirms": 0,     # passou núcleo mas falhou confirmações
    "low_score": 0,    # gerou signal mas score < min
    "payout_low": 0,   # rejeitado por payout abaixo do mínimo
    "spread_block": 0, # rejeitado por filtro de spread/slippage
    "approved": 0,     # signal aprovado
    "best_score": 0.0, # melhor score visto no scan
    "near_misses": [], # lista de [asset, tf, dir, score] com score próximo do min
}


def reset_scan_stats() -> None:
    """Zera contadores de scan (chamado pelo bot a cada janela de log)."""
    SCAN_STATS["total"] = 0
    SCAN_STATS["wick"] = 0
    SCAN_STATS["atr_low"] = 0
    SCAN_STATS["atr_high"] = 0
    SCAN_STATS["doji_prev"] = 0
    SCAN_STATS["doji_last"] = 0
    SCAN_STATS["prev_clean"] = 0
    SCAN_STATS["no_core"] = 0
    SCAN_STATS["core_tie"] = 0
    SCAN_STATS["confirms"] = 0
    SCAN_STATS["low_score"] = 0
    SCAN_STATS["payout_low"] = 0
    SCAN_STATS["spread_block"] = 0
    SCAN_STATS["approved"] = 0
    SCAN_STATS["best_score"] = 0.0
    SCAN_STATS["near_misses"] = []


class _ScalperRanking:
    """Ranking interno do Scalper — desabilita TFs/núcleos com WR baixo.

    Persistido em data/scalper_stats.json. Cada chave é 'TF:nucleo' (ex: 'S10:EMA Cross')
    e armazena os resultados rolling (W/L) das últimas N entradas.
    """

    def __init__(self) -> None:
        self._results: Dict[str, Deque[str]] = {}
        self._load()

    def _load(self) -> None:
        if not _SCALPER_STATS_PATH.exists():
            return
        try:
            with _SCALPER_STATS_PATH.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, results in raw.items():
                self._results[key] = deque(results[-_ROLLING_WINDOW:], maxlen=_ROLLING_WINDOW)
        except Exception:
            pass

    def _save(self) -> None:
        _SCALPER_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {k: list(v) for k, v in self._results.items()}
            with _SCALPER_STATS_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def register(self, timeframe: str, cores: List[str], result: str) -> None:
        """Registra resultado WIN/LOSS/DRAW para o conjunto (TF, núcleos)."""
        if result not in ("WIN", "LOSS"):
            return
        with _STATS_LOCK:
            for nucleo in cores:
                key = f"{timeframe}:{nucleo}"
                self._results.setdefault(key, deque(maxlen=_ROLLING_WINDOW)).append(result)
            # também armazena por TF puro
            tf_key = f"{timeframe}:_TF_"
            self._results.setdefault(tf_key, deque(maxlen=_ROLLING_WINDOW)).append(result)
            self._save()

    def is_enabled(self, timeframe: str, nucleo: str) -> bool:
        """Retorna False se aquele núcleo num TF teve WR < threshold nas últimas N entradas."""
        if SCALPER_RANKING_DISABLED:
            return True
        key = f"{timeframe}:{nucleo}"
        with _STATS_LOCK:
            results = self._results.get(key)
            if not results or len(results) < _MIN_TRADES_TO_DISABLE:
                return True
            wins = sum(1 for r in results if r == "WIN")
            wr = wins / len(results)
            return wr >= _DISABLE_THRESHOLD

    def is_tf_enabled(self, timeframe: str) -> bool:
        if SCALPER_RANKING_DISABLED:
            return True
        key = f"{timeframe}:_TF_"
        with _STATS_LOCK:
            results = self._results.get(key)
            if not results or len(results) < _MIN_TRADES_TO_DISABLE:
                return True
            wins = sum(1 for r in results if r == "WIN")
            wr = wins / len(results)
            return wr >= _DISABLE_THRESHOLD

    def stats_summary(self) -> Dict[str, Dict[str, float]]:
        """Resumo legible: chave → {wr, n, enabled}."""
        out: Dict[str, Dict[str, float]] = {}
        with _STATS_LOCK:
            for key, results in self._results.items():
                n = len(results)
                if n == 0:
                    continue
                wins = sum(1 for r in results if r == "WIN")
                wr = (wins / n) * 100.0
                enabled = (n < _MIN_TRADES_TO_DISABLE) or (wr >= _DISABLE_THRESHOLD * 100)
                out[key] = {"wr": round(wr, 1), "n": n, "enabled": enabled}
        return out


# Singleton compartilhado
SCALPER_RANKING = _ScalperRanking()


class ScalperStrategy(BaseStrategy):
    """Estratégia única do modo Scalper — substitui todas as outras quando ativo."""

    name = "Scalper Sniper"
    # Pesos altos só em TFs curtos. Em M5/M15 não opera (não é o caso de uso).
    weights = {"S5": 0.9, "S10": 1.0, "S30": 1.1, "M1": 1.2, "M5": 0.0, "M15": 0.0}

    # ───────────────────────── ATR helper ─────────────────────────
    @staticmethod
    def _atr(candles: List[Candle], period: int = 14) -> np.ndarray:
        h = np.array([c.high for c in candles], dtype=float)
        l = np.array([c.low for c in candles], dtype=float)
        c = np.array([c.close for c in candles], dtype=float)
        tr = np.zeros(len(c))
        for i in range(1, len(c)):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        # SMMA / Wilder
        return Indicators.smma(tr, period)

    # ───────────────────────── VWAP helper ────────────────────────
    @staticmethod
    def _vwap(candles: List[Candle]) -> float:
        """VWAP simples sobre as velas fornecidas."""
        total_pv = 0.0
        total_v = 0.0
        for c in candles:
            typical = (c.high + c.low + c.close) / 3.0
            v = max(c.volume, 1e-9)
            total_pv += typical * v
            total_v += v
        if total_v < 1e-9:
            # Fallback: média dos closes
            return float(np.mean([c.close for c in candles]))
        return total_pv / total_v

    @staticmethod
    def _session_vwap(candles: List[Candle]) -> Optional[float]:
        """VWAP calculada desde o início do dia BRT (00h BRT = 03h UTC).

        Retorna None se não houver candles suficientes da sessão atual.
        Institucional opera VWAP de sessão — preço rejeitando essa linha
        é sinal forte de continuação/reversão.
        """
        if not candles:
            return None
        from datetime import datetime, timezone, timedelta
        _BRT = timezone(timedelta(hours=-3))
        # Encontra timestamp da meia-noite BRT atual
        now_brt = datetime.now(_BRT)
        midnight_brt = now_brt.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = int(midnight_brt.timestamp())
        session = [c for c in candles if int(getattr(c, "timestamp", 0)) >= cutoff]
        if len(session) < 5:
            return None
        return ScalperStrategy._vwap(session)

    # ───────────────────────── analyze ────────────────────────────
    def analyze(self, candles: List[Candle], timeframe: str) -> Optional[Signal]:
        if self.weight_for(timeframe) <= 0:
            return None
        if len(candles) < 50:
            return None

        # 📊 Conta cada combo asset×TF efetivamente analisado
        SCAN_STATS["total"] += 1

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        # ╔════════════════════════════════════════════════════════╗
        # ║ A. FILTROS DE MERCADO                                  ║
        # ╚════════════════════════════════════════════════════════╝

        # A.1 — Wick ratio nas 3 velas anteriores
        wick_spike = 0
        for _c in candles[-4:-1]:
            _rng = _c.high - _c.low
            if _rng < 1e-9:
                continue
            _body = abs(_c.close - _c.open)
            if (1.0 - _body / _rng) > 0.60:
                wick_spike += 1
        if wick_spike >= 2:
            return None  # Manipulação OTC detectada

        # A.2/A.3 — ATR window
        atr = self._atr(candles, period=14)
        if np.isnan(atr[-1]):
            return None
        recent_atr = atr[-1]
        # Média de ATR dos últimos 50 candles válidos (excluindo o atual)
        valid_atr = atr[-50:-1]
        valid_atr = valid_atr[~np.isnan(valid_atr)]
        if len(valid_atr) < 10:
            return None
        avg_atr = float(np.mean(valid_atr))
        if avg_atr < 1e-9:
            return None
        atr_ratio = recent_atr / avg_atr
        _atr_floor = 0.30
        if atr_ratio < _atr_floor:
            SCAN_STATS["atr_low"] += 1
            return None  # Mercado morto / manipulado
        if atr_ratio > 2.50:
            SCAN_STATS["atr_high"] += 1
            return None  # Volatilidade explosiva (notícia/manipulação)

        # A.4 — Vela anterior precisa ser "limpa" (corpo decente)
        prev_range = prev.high - prev.low
        prev_body = abs(prev.close - prev.open)
        if prev_range > 1e-9 and (prev_body / prev_range) < 0.40:
            return None  # Vela anterior é doji/indecisão

        # A.5 (F1) — Anti-doji explicito na vela anterior: corpo < 20% range = indecisão forte
        if prev_range > 1e-9 and (prev_body / prev_range) < 0.20:
            return None
        # A.6 (F1) — Anti-doji também na vela atual
        last_range_check = last.high - last.low
        last_body_check = abs(last.close - last.open)
        if last_range_check > 1e-9 and (last_body_check / last_range_check) < 0.20:
            return None

        # ╔════════════════════════════════════════════════════════╗
        # ║ B. NÚCLEOS DE SINAL                                    ║
        # ╚════════════════════════════════════════════════════════╝

        closes = np.array([c.close for c in candles], dtype=float)
        opens = np.array([c.open for c in candles], dtype=float)
        highs = np.array([c.high for c in candles], dtype=float)
        lows = np.array([c.low for c in candles], dtype=float)

        ema3 = Indicators.ema(closes, 3)
        ema8 = Indicators.ema(closes, 8)
        ema21 = Indicators.ema(closes, 21)
        ema200 = Indicators.ema(closes, 200)
        rsi = Indicators.rsi(closes, 14)
        macd_line, macd_sig, macd_hist = Indicators.macd(closes, 12, 26, 9)
        stoch_k, stoch_d = Indicators.stochastic(highs, lows, closes, 14, 3)
        vwap = self._vwap(candles[-30:])

        cores_call: List[str] = []
        cores_put: List[str] = []

        # B.1 — Tick Momentum (com anti-exaustão)
        c1, c2, c3 = candles[-4], candles[-3], candles[-2]  # 3 velas antes da última
        b1 = abs(c1.close - c1.open)
        b2 = abs(c2.close - c2.open)
        b3 = abs(c3.close - c3.open)
        # Anti-exaustão: conta velas consecutivas mesma direção terminando em c3 (candles[-2])
        # Se >=5 velas seguidas na mesma direção = momentum exausto, descarta sinal de continuação
        def _consec_same_dir(end_idx: int, bullish: bool) -> int:
            cnt = 0
            i = end_idx
            while i >= -len(candles):
                _cc = candles[i]
                if bullish and _cc.close > _cc.open:
                    cnt += 1
                elif (not bullish) and _cc.close < _cc.open:
                    cnt += 1
                else:
                    break
                i -= 1
            return cnt
        if (c1.close > c1.open and c2.close > c2.open and c3.close > c3.open
                and b3 >= b2 >= b1 and last.close > last.open):
            if _consec_same_dir(-2, True) < 5:
                cores_call.append("Tick Momentum")
        elif (c1.close < c1.open and c2.close < c2.open and c3.close < c3.open
              and b3 >= b2 >= b1 and last.close < last.open):
            if _consec_same_dir(-2, False) < 5:
                cores_put.append("Tick Momentum")

        # B.2 — EMA Cross Cirúrgico (EMA3 cruzou EMA8 nesta ou na anterior)
        if not (np.isnan(ema3[-2]) or np.isnan(ema8[-2])):
            cross_up_now = ema3[-1] > ema8[-1] and ema3[-2] <= ema8[-2]
            cross_dn_now = ema3[-1] < ema8[-1] and ema3[-2] >= ema8[-2]
            cross_up_prev = ema3[-2] > ema8[-2] and ema3[-3] <= ema8[-3]
            cross_dn_prev = ema3[-2] < ema8[-2] and ema3[-3] >= ema8[-3]
            last_rng = last.high - last.low
            last_pos = (last.close - last.low) / last_rng if last_rng > 1e-9 else 0.5
            if (cross_up_now or cross_up_prev) and last_pos >= 0.55 and last.close > last.open:
                cores_call.append("EMA Cross")
            elif (cross_dn_now or cross_dn_prev) and last_pos <= 0.45 and last.close < last.open:
                cores_put.append("EMA Cross")

        # B.3 — RSI Extremo + Reversão
        if not np.isnan(rsi[-2]):
            if rsi[-2] < 25 and last.close > last.open and last.close > prev.close:
                cores_call.append("RSI Extremo")
            elif rsi[-2] > 75 and last.close < last.open and last.close < prev.close:
                cores_put.append("RSI Extremo")

        # B.4 — VWAP Touch + Reversão
        # Preço tocou/cruzou VWAP nas últimas 3 candles e agora reverte
        touched_below = any(c.low <= vwap <= c.high and c.close < vwap for c in candles[-4:-1])
        touched_above = any(c.low <= vwap <= c.high and c.close > vwap for c in candles[-4:-1])
        if touched_below and last.close > vwap and last.close > last.open:
            cores_call.append("VWAP Touch")
        elif touched_above and last.close < vwap and last.close < last.open:
            cores_put.append("VWAP Touch")

        # B.5 (N1) — Bollinger Squeeze + Break
        # Squeeze: width < 0.5% do preço; Break: vela atual rompe banda
        bb_lo, bb_mid, bb_up = Indicators.bollinger(closes, 20, 2.0)
        if not (np.isnan(bb_lo[-2]) or np.isnan(bb_up[-2]) or np.isnan(bb_mid[-2])):
            bb_width_prev = (bb_up[-2] - bb_lo[-2]) / bb_mid[-2] if bb_mid[-2] > 1e-9 else 0
            if bb_width_prev < 0.005:  # Squeeze (bandas comprimidas)
                if last.close > bb_up[-2] and last.close > last.open:
                    cores_call.append("BB Squeeze Break")
                elif last.close < bb_lo[-2] and last.close < last.open:
                    cores_put.append("BB Squeeze Break")

        # B.6 (N2) — Stochastic Reversal: %K cruza %D em zona extrema + reversão
        if not (np.isnan(stoch_k[-2]) or np.isnan(stoch_d[-2])):
            cross_up = stoch_k[-1] > stoch_d[-1] and stoch_k[-2] <= stoch_d[-2]
            cross_dn = stoch_k[-1] < stoch_d[-1] and stoch_k[-2] >= stoch_d[-2]
            in_oversold = stoch_k[-2] < 20 or stoch_d[-2] < 20
            in_overbought = stoch_k[-2] > 80 or stoch_d[-2] > 80
            if cross_up and in_oversold and last.close > last.open and last.close > prev.close:
                cores_call.append("Stoch Reversal")
            elif cross_dn and in_overbought and last.close < last.open and last.close < prev.close:
                cores_put.append("Stoch Reversal")

        # B.7 (N3) — Fractal Pivot: prev é mínima/máxima local de 5 (centro de janela 5)
        # Janela: candles[-6:-1] (5 velas), centro é candles[-4] = c2 já definido
        if len(candles) >= 6:
            window5 = candles[-6:-1]
            pivot = candles[-4]  # centro da janela
            is_pivot_low = pivot.low == min(c.low for c in window5)
            is_pivot_high = pivot.high == max(c.high for c in window5)
            # Confirmação: última vela na direção da reversão do pivot
            if is_pivot_low and last.close > last.open and last.close > prev.close:
                cores_call.append("Fractal Pivot")
            elif is_pivot_high and last.close < last.open and last.close < prev.close:
                cores_put.append("Fractal Pivot")

        # B.8 (N4) — Heikin-Ashi Strong: vela HA sem wick contrário (corpo cheio)
        # HA close = (O+H+L+C)/4; HA open = (HA_open_prev + HA_close_prev)/2
        if len(candles) >= 3:
            ha_open_prev = (prev.open + prev.close) / 2.0
            ha_close = (last.open + last.high + last.low + last.close) / 4.0
            ha_open = (ha_open_prev + (prev.open + prev.high + prev.low + prev.close) / 4.0) / 2.0
            ha_high = max(last.high, ha_open, ha_close)
            ha_low = min(last.low, ha_open, ha_close)
            ha_body = abs(ha_close - ha_open)
            ha_range = ha_high - ha_low
            if ha_range > 1e-9 and (ha_body / ha_range) >= 0.85:
                # HA bullish forte: HA_close > HA_open e wick inferior < 5% range
                if ha_close > ha_open and (ha_open - ha_low) / ha_range < 0.05:
                    cores_call.append("HA Strong")
                elif ha_close < ha_open and (ha_high - ha_open) / ha_range < 0.05:
                    cores_put.append("HA Strong")

        # B.9 — PSar Flip: Parabolic SAR virou direção na última ou penúltima vela.
        # Ideal para mercados rápidos/voláteis — detecta reversão sem lag de médias.
        psar = Indicators.parabolic_sar(highs, lows)
        if len(psar) >= 3 and not (np.isnan(psar[-1]) or np.isnan(psar[-2])):
            _psar_flip_up = psar[-1] < last.close and psar[-2] >= prev.close
            _psar_flip_dn = psar[-1] > last.close and psar[-2] <= prev.close
            if _psar_flip_up and last.close > last.open:
                cores_call.append("PSar Flip")
            elif _psar_flip_dn and last.close < last.open:
                cores_put.append("PSar Flip")

        # 🎯 Filtro do ranking interno: remove núcleos com WR < 40% nas últimas 20 entradas
        if not SCALPER_RANKING.is_tf_enabled(timeframe):
            return None  # TF inteiro reprovado
        cores_call = [n for n in cores_call if SCALPER_RANKING.is_enabled(timeframe, n)]
        cores_put = [n for n in cores_put if SCALPER_RANKING.is_enabled(timeframe, n)]

        # Decide direção
        n_call = len(cores_call)
        n_put = len(cores_put)
        if n_call == 0 and n_put == 0:
            SCAN_STATS["no_core"] += 1
            return None
        if n_call > n_put:
            direction = "CALL"
            cores = cores_call
        elif n_put > n_call:
            direction = "PUT"
            cores = cores_put
        else:
            SCAN_STATS["core_tie"] += 1
            return None  # Empate — não opera

        # ╔════════════════════════════════════════════════════════╗
        # ║ C. CONFIRMAÇÕES (precisa passar em 3 das 4)            ║
        # ╚════════════════════════════════════════════════════════╝
        confirmations_passed = 0
        confirmations_failed: List[str] = []

        # C.1 — Confluência ≥3 indicadores
        votes = 0
        if direction == "CALL":
            if not np.isnan(rsi[-1]) and rsi[-1] > 50:
                votes += 1
            if not np.isnan(macd_hist[-1]) and macd_hist[-1] > 0:
                votes += 1
            if not np.isnan(ema21[-1]) and last.close > ema21[-1]:
                votes += 1
            if not np.isnan(stoch_k[-1]) and stoch_k[-1] > 50:
                votes += 1
        else:
            if not np.isnan(rsi[-1]) and rsi[-1] < 50:
                votes += 1
            if not np.isnan(macd_hist[-1]) and macd_hist[-1] < 0:
                votes += 1
            if not np.isnan(ema21[-1]) and last.close < ema21[-1]:
                votes += 1
            if not np.isnan(stoch_k[-1]) and stoch_k[-1] < 50:
                votes += 1
        if votes >= 3:
            confirmations_passed += 1
        else:
            confirmations_failed.append("confluência")

        # C.2 — Última vela: fechamento forte no terço favorável (>=70%)
        last_rng = last.high - last.low
        if last_rng < 1e-9:
            return None  # Doji puro: descarta sempre (sem range = sem sinal)
        last_pos = (last.close - last.low) / last_rng
        c2_ok = (
            (direction == "CALL" and last_pos >= 0.70) or
            (direction == "PUT" and last_pos <= 0.30)
        )
        if c2_ok:
            confirmations_passed += 1
        else:
            confirmations_failed.append("fechamento_terço")

        # C.3 — Vela anterior na direção OU Pin Bar/Engulfing
        prev_in_dir = (
            (direction == "CALL" and prev.close > prev.open) or
            (direction == "PUT" and prev.close < prev.open)
        )
        is_pin = self.is_pin_bar(last, direction)
        is_eng = self.is_engulfing(prev, last, direction)
        if prev_in_dir or is_pin or is_eng:
            confirmations_passed += 1
        else:
            confirmations_failed.append("vela_anterior/padrão")

        # C.4 — Sem divergência oculta RSI vs preço
        c4_ok = True
        if not np.isnan(rsi[-3]) and not np.isnan(rsi[-1]):
            if direction == "CALL":
                # Preço crescendo + RSI caindo = divergência baixista (rejeita CALL)
                if last.close > prev2.close and rsi[-1] < rsi[-3] - 5:
                    c4_ok = False
            else:
                # Preço caindo + RSI subindo = divergência altista (rejeita PUT)
                if last.close < prev2.close and rsi[-1] > rsi[-3] + 5:
                    c4_ok = False
        if c4_ok:
            confirmations_passed += 1
        else:
            confirmations_failed.append("divergência_RSI")

        # Precisa de no mínimo 3 das 4 confirmações (bypass de 1 núcleo desativado:
        # gerava muitos sinais de baixa convicção que revertiam nos últimos segundos).
        if confirmations_passed < 3:
            SCAN_STATS["confirms"] += 1
            return None

        # 🚫 Pavio contrário grande na vela atual = mercado já testou e foi rejeitado.
        # Para CALL: wick superior > 40% do range = topo já vendido.
        # Para PUT:  wick inferior > 40% do range = fundo já comprado.
        _last_rng_w = last.high - last.low
        if _last_rng_w > 1e-9:
            _upper_wick = last.high - max(last.open, last.close)
            _lower_wick = min(last.open, last.close) - last.low
            if direction == "CALL" and (_upper_wick / _last_rng_w) > 0.40:
                SCAN_STATS["confirms"] += 1
                return None
            if direction == "PUT" and (_lower_wick / _last_rng_w) > 0.40:
                SCAN_STATS["confirms"] += 1
                return None

        # ╔════════════════════════════════════════════════════════╗
        # ║ SCORE & SIGNAL                                         ║
        # ╚════════════════════════════════════════════════════════╝

        n_cores = len(cores)
        if n_cores >= 3:
            base_score = 8.5
            confidence = 88.0
        elif n_cores == 2:
            base_score = 7.5
            confidence = 80.0
        else:
            base_score = 6.5
            confidence = 73.0

        # Bônus por confirmações fortes (votes acima de 3)
        if votes >= 4:
            base_score += 0.3
            confidence += 3.0

        # Bônus por padrão de vela explícito
        if is_pin or is_eng:
            base_score += 0.4
            confidence += 4.0

        # 🕯️ 3-Bar Reversal: vela forte oposta → indecisão → vela forte nova direção.
        # Padrão de alta confiança em mercados voláteis.
        _3bar_bonus = False
        _v1, _v2, _v3 = candles[-4], candles[-3], candles[-2]
        _v1b = abs(_v1.close - _v1.open); _v1r = _v1.high - _v1.low
        _v2b = abs(_v2.close - _v2.open); _v2r = _v2.high - _v2.low
        _v3b = abs(_v3.close - _v3.open)
        if _v1r > 1e-9 and _v2r > 1e-9 and _v1b > 1e-9:
            _3bar_call = (
                _v1.close < _v1.open and _v1b / _v1r > 0.6
                and _v2b / _v2r < 0.35
                and _v3.close > _v3.open and _v3b >= _v1b * 0.7
            )
            _3bar_put = (
                _v1.close > _v1.open and _v1b / _v1r > 0.6
                and _v2b / _v2r < 0.35
                and _v3.close < _v3.open and _v3b >= _v1b * 0.7
            )
            if (direction == "CALL" and _3bar_call) or (direction == "PUT" and _3bar_put):
                base_score += 0.5
                confidence += 4.0
                _3bar_bonus = True

        # ⚡ ATR em aceleração: mercado ganhando energia → momentum real, não ruído.
        # Janela: ATR atual vs ATR 5 velas atrás (15%–100% de aceleração = ideal).
        if len(atr) >= 5 and not np.isnan(atr[-5]) and atr[-5] > 1e-9:
            _atr_accel = atr[-1] / atr[-5]
            if 1.15 <= _atr_accel <= 2.0:
                base_score += 0.3
                confidence += 2.0

        # 💥 Corpo burst: último corpo é o maior dos 8 anteriores → decisão do mercado.
        _recent_bodies = [abs(c.close - c.open) for c in candles[-9:-1]]
        _last_body = abs(last.close - last.open)
        if _recent_bodies and _last_body >= max(_recent_bodies) and _last_body > 1e-9:
            base_score += 0.4
            confidence += 3.0

        # 🪝 Pavio mínimo alinhado: quase sem wick contrário = convicção forte.
        # CALL: pavio inferior < 10% do range (mercado não testou o baixo).
        # PUT:  pavio superior < 10% do range (mercado não testou o alto).
        if _last_rng_w > 1e-9:
            if direction == "CALL":
                _lower_wick_pct = (min(last.open, last.close) - last.low) / _last_rng_w
                if _lower_wick_pct < 0.10:
                    base_score += 0.2
                    confidence += 2.0
            else:
                _upper_wick_pct = (last.high - max(last.open, last.close)) / _last_rng_w
                if _upper_wick_pct < 0.10:
                    base_score += 0.2
                    confidence += 2.0

        # Bônus por volume alto
        if len(candles) >= 22:
            vols = [c.volume for c in candles[-21:-1]]
            avg_v = sum(vols) / len(vols) if vols else 0.0
            if avg_v > 1e-9 and last.volume / avg_v >= 1.5:
                base_score += 0.5
                confidence += 3.0

        # 📈 Score adaptativo por volatilidade (modula base_score conforme regime)
        #   ATR ratio > 1.5  → -0.5 (agitado, exige convicção maior)
        #   ATR ratio 1.0-1.5 → 0    (neutro)
        #   ATR ratio 0.7-1.0 → +0.1 (lateral leve, ok)
        #   ATR ratio < 0.7  → +0.3 (lateral forte, mercado previsível)
        if atr_ratio > 1.5:
            base_score -= 0.5
            confidence -= 4.0
        # ⛔ Bônus de ATR baixo removido: lateral/morto na OTC é zona de manipulação,
        # não de previsibilidade. Premiar isso enchia a estatística de losses.
        # 🧹 Limpeza do gráfico: ratio sombra/corpo das últimas 10 velas
        #   <0.5 → +0.2 (velas limpas, tendência clara)
        #   0.5-1.5 → 0 (normal)
        #   >1.5 → -0.4 (mercado sujo, indeciso)
        if len(candles) >= 11:
            _wick_total = 0.0
            _body_total = 0.0
            for _c in candles[-11:-1]:  # 10 últimas FECHADAS
                _r = _c.high - _c.low
                _b = abs(_c.close - _c.open)
                if _r < 1e-9:
                    continue
                _wick_total += (_r - _b)
                _body_total += _b
            if _body_total > 1e-9:
                _ratio = _wick_total / _body_total
                if _ratio < 0.5:
                    base_score += 0.2
                    confidence += 2.0
                elif _ratio > 1.5:
                    base_score -= 0.4
                    confidence -= 4.0
        # 🏛️ Session VWAP: bônus se preço está rejeitando VWAP de sessão BRT
        #   Rejeição = preço tocou VWAP nas últimas 3 velas e agora se afasta
        _svwap = self._session_vwap(candles)
        if _svwap is not None and _svwap > 0:
            _touched_below_s = any(
                c.low <= _svwap <= c.high and c.close < _svwap for c in candles[-4:-1]
            )
            _touched_above_s = any(
                c.low <= _svwap <= c.high and c.close > _svwap for c in candles[-4:-1]
            )
            if direction == "CALL" and _touched_below_s and last.close > _svwap:
                base_score += 0.4
                confidence += 3.0
            elif direction == "PUT" and _touched_above_s and last.close < _svwap:
                base_score += 0.4
                confidence += 3.0
        # Penalidade extra se nos extremos absolutos
        if atr_ratio < 0.50 or atr_ratio > 2.0:
            base_score -= 0.3
            confidence -= 3.0

        # 📉 Filtro de tendência EMA200: penaliza sinais contra-tendência maior.
        #   Precisa de >=150 velas para EMA200 convergir. Com 150+ candles:
        #   • Contra-tendência (1-2 núcleos): -0.8 → bloqueia (base 6.5/7.5 − 0.8 < 7.0)
        #   • Contra-tendência (3+ núcleos): -0.8 → ainda passa (8.5 − 0.8 = 7.7 ≥ 7.0)
        #   Favor de tendência: +0.3 (mercado alinhado = convicção extra)
        _ema200_trend_note = ""
        if len(candles) >= 150 and not np.isnan(ema200[-1]):
            _above_ema200 = last.close > ema200[-1]
            if direction == "CALL" and not _above_ema200:
                base_score -= 0.8
                confidence -= 6.0
                _ema200_trend_note = " | ⚠️ Contra-EMA200"
            elif direction == "PUT" and _above_ema200:
                base_score -= 0.8
                confidence -= 6.0
                _ema200_trend_note = " | ⚠️ Contra-EMA200"
            elif direction == "CALL" and _above_ema200:
                base_score += 0.3
                confidence += 3.0
            elif direction == "PUT" and not _above_ema200:
                base_score += 0.3
                confidence += 3.0

        confidence = max(0.0, min(99.0, confidence))

        # 🧠 Pattern Learner: bônus se padrão de 3 velas tem histórico vencedor
        try:
            from .pattern_learner import PATTERN_LEARNER
            _pat_key = PATTERN_LEARNER.classify(candles[-3:])
            _pat_bonus = PATTERN_LEARNER.bonus(_pat_key, direction)
            if _pat_bonus > 0:
                base_score += _pat_bonus
                confidence = min(99.0, confidence + _pat_bonus * 4.0)
        except Exception:
            _pat_key = ""
            _pat_bonus = 0.0

        # 🧬 Core Stats: bônus/penalty pelos cores ativos nesta hora BRT
        try:
            from .core_stats import CORE_STATS
            from datetime import datetime, timezone, timedelta
            _h_brt = datetime.now(timezone(timedelta(hours=-3))).hour
            _core_bonus = CORE_STATS.core_score_bonus(cores, _h_brt)
            base_score += _core_bonus
            confidence = max(0.0, min(99.0, confidence + _core_bonus * 3.0))
        except Exception:
            _core_bonus = 0.0

        # 📊 Atualiza contadores de scan: aprovado + best_score
        SCAN_STATS["approved"] += 1
        if base_score > SCAN_STATS["best_score"]:
            SCAN_STATS["best_score"] = float(base_score)

        notes = f"🎯 [{n_cores}núcleos+{votes}votos] " + " + ".join(cores)
        if is_pin:
            notes += " | 📌 Pin"
        if is_eng:
            notes += " | 📌 Eng"
        notes += _ema200_trend_note

        return Signal(
            strategy=self.name,
            direction=direction,
            base_score=base_score,
            confidence=confidence,
            timeframe=timeframe,
            weight=self.weight_for(timeframe),
            confluence={
                "tick_momentum":    "Tick Momentum"    in cores,
                "ema_cross":        "EMA Cross"        in cores,
                "rsi_extremo":      "RSI Extremo"      in cores,
                "vwap_touch":       "VWAP Touch"       in cores,
                "bb_squeeze_break": "BB Squeeze Break" in cores,
                "stoch_reversal":   "Stoch Reversal"   in cores,
                "fractal_pivot":    "Fractal Pivot"    in cores,
                "ha_strong":        "HA Strong"        in cores,
                "psar_flip":        "PSar Flip"        in cores,
                "3bar_reversal":    _3bar_bonus,
                "indicator_votes": votes,
                "pattern_key": _pat_key,
                "pattern_bonus": round(_pat_bonus, 2),
            },
            notes=notes,
        )
