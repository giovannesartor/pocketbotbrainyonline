"""🧠 PATTERN LEARNER — Aprende padrões de 3 velas que precedem WINs.

Cada padrão é codificado por:
  • Direção de cada vela: B (bull), R (bear), D (doji)
  • Tamanho relativo do corpo: L (large >0.66), M (med 0.33-0.66), S (small <0.33)

Exemplo: "BBB_LMS" = 3 bulls com corpos large→med→small.

Persiste em data/candle_patterns.json: {pattern_key: {wins, losses}}.

API:
  • PATTERN_LEARNER.classify(candles[-3:]) → str (pattern_key)
  • PATTERN_LEARNER.bonus(pattern_key) → float (0.0 a +0.5 pra adicionar ao score)
  • PATTERN_LEARNER.register(pattern_key, "WIN"|"LOSS") → None
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List

from ..utils.indicators import Candle

_PATH = Path(__file__).resolve().parents[1] / "data" / "candle_patterns.json"
_LOCK = threading.Lock()
_MIN_TRADES_FOR_BONUS = 10
_BONUS_WR_THRESHOLD = 0.60   # WR >= 60% → ganha bônus
_MAX_BONUS = 0.5


def _body_size_class(c: Candle) -> str:
    rng = c.high - c.low
    if rng < 1e-9:
        return "S"
    body = abs(c.close - c.open) / rng
    if body < 0.20:
        return "D"  # doji
    if body < 0.50:
        return "S"
    if body < 0.75:
        return "M"
    return "L"


def _direction_class(c: Candle) -> str:
    if abs(c.close - c.open) < (c.high - c.low) * 0.20:
        return "D"
    return "B" if c.close > c.open else "R"


class _PatternLearner:
    def __init__(self) -> None:
        self._stats: Dict[str, Dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if not _PATH.exists():
            return
        try:
            with _PATH.open("r", encoding="utf-8") as f:
                self._stats = json.load(f)
        except Exception:
            self._stats = {}

    def _save(self) -> None:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _PATH.open("w", encoding="utf-8") as f:
                json.dump(self._stats, f, indent=2)
        except Exception:
            pass

    def classify(self, candles: List[Candle]) -> str:
        """Recebe as 3 últimas velas (ordem cronológica) e retorna chave."""
        if len(candles) < 3:
            return ""
        last3 = candles[-3:]
        dirs = "".join(_direction_class(c) for c in last3)
        sizes = "".join(_body_size_class(c) for c in last3)
        return f"{dirs}_{sizes}"

    def bonus(self, pattern_key: str, direction: str = "") -> float:
        """Retorna bônus de score (0 a +0.5) se padrão tem WR >= 60% e N>=10.

        `direction` opcional: chave fica diferente para CALL vs PUT.
        """
        if not pattern_key:
            return 0.0
        key = f"{pattern_key}|{direction}" if direction else pattern_key
        with _LOCK:
            data = self._stats.get(key)
            if not data:
                return 0.0
            n = data.get("wins", 0) + data.get("losses", 0)
            if n < _MIN_TRADES_FOR_BONUS:
                return 0.0
            wr = data["wins"] / n
            if wr < _BONUS_WR_THRESHOLD:
                return 0.0
            # Escala: 60%→+0.2, 70%→+0.35, 80%+→+0.5
            return min(_MAX_BONUS, 0.2 + (wr - 0.60) * 1.5)

    def register(self, pattern_key: str, result: str, direction: str = "") -> None:
        if not pattern_key or result not in ("WIN", "LOSS"):
            return
        key = f"{pattern_key}|{direction}" if direction else pattern_key
        with _LOCK:
            d = self._stats.setdefault(key, {"wins": 0, "losses": 0})
            if result == "WIN":
                d["wins"] += 1
            else:
                d["losses"] += 1
            self._save()

    def stats_summary(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        with _LOCK:
            for key, d in self._stats.items():
                n = d["wins"] + d["losses"]
                if n == 0:
                    continue
                wr = (d["wins"] / n) * 100.0
                out[key] = {"wr": round(wr, 1), "n": n}
        return out


PATTERN_LEARNER = _PatternLearner()
