"""Persiste WR por (estratégia, regime). Usado pelo manager pra filtrar
estratégias que historicamente perdem no regime atual.

Estrutura:  data/regime_stats.json
{
  "stats": {
    "Scalper Sniper|TREND_UP": ["WIN", "LOSS", ...],
    "Bollinger+RSI|RANGE":     ["WIN", "WIN", "LOSS"],
    ...
  }
}
Cada lista guarda no máximo 200 últimos resultados (rolling).
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PATH = DATA_DIR / "regime_stats.json"
MAX_PER_KEY = 200

_LOCK = threading.Lock()


class RegimeStats:
    def __init__(self):
        self._data: Dict[str, Deque[str]] = {}
        self._load()

    def _load(self) -> None:
        if not PATH.exists():
            return
        try:
            with PATH.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            stats = raw.get("stats", {})
            for k, arr in stats.items():
                self._data[k] = deque(arr[-MAX_PER_KEY:], maxlen=MAX_PER_KEY)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with PATH.open("w", encoding="utf-8") as f:
                json.dump(
                    {"stats": {k: list(v) for k, v in self._data.items()}},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass

    def register(self, strategy: str, regime: str, result: str) -> None:
        if not strategy or not regime or result not in ("WIN", "LOSS", "DRAW"):
            return
        if result == "DRAW":
            return  # draws não contam pra WR
        key = f"{strategy}|{regime}"
        with _LOCK:
            arr = self._data.setdefault(key, deque(maxlen=MAX_PER_KEY))
            arr.append(result)
            self._save()

    def wr(self, strategy: str, regime: str) -> Tuple[float, int]:
        """Retorna (wr_percent, n). Se sem dados, retorna (0.0, 0)."""
        key = f"{strategy}|{regime}"
        with _LOCK:
            arr = self._data.get(key)
            if not arr:
                return 0.0, 0
            n = len(arr)
            wins = sum(1 for r in arr if r == "WIN")
            return (wins / n) * 100.0, n

    def matrix(self) -> Dict[str, Dict[str, Tuple[float, int]]]:
        """Retorna dict[estrategia][regime] = (wr, n)."""
        out: Dict[str, Dict[str, Tuple[float, int]]] = {}
        with _LOCK:
            for key, arr in self._data.items():
                if "|" not in key:
                    continue
                strat, regime = key.rsplit("|", 1)
                n = len(arr)
                if n == 0:
                    continue
                wins = sum(1 for r in arr if r == "WIN")
                out.setdefault(strat, {})[regime] = ((wins / n) * 100.0, n)
        return out


REGIME_STATS = RegimeStats()
