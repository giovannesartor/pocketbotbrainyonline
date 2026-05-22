"""🧬 CORE STATS — WR por núcleo individual × hora.

Rastreia performance de cada um dos 8 núcleos do scalper em cada hora BRT,
permitindo identificar quais cores são "ouro" e quais são "lixo" em cada
janela do dia.

Persiste em data/core_stats.json.

API:
  • register(cores_used: list[str], hour: int, result: str)
  • core_wr(core: str, hour: int) → (wr, n) | None
  • core_score_bonus(cores_used, hour) → float (delta a aplicar no base_score)
  • summary() → dict completo p/ comando /cores
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PATH = Path(__file__).resolve().parents[1] / "data" / "core_stats.json"
_LOCK = threading.Lock()
_ROLLING = 80

CORE_NAMES = (
    "Tick Momentum",
    "EMA Cross",
    "RSI Extremo",
    "VWAP Touch",
    "BB Squeeze Break",
    "Stoch Reversal",
    "Fractal Pivot",
    "HA Strong",
)


class _CoreStats:
    """Estrutura: {"core|hour": ["WIN","LOSS",...]}"""

    def __init__(self) -> None:
        self._data: Dict[str, List[str]] = {}
        self._load()

    def _load(self) -> None:
        if not _PATH.exists():
            return
        try:
            with _PATH.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data = {k: list(v) for k, v in raw.items() if isinstance(v, list)}
        except Exception:
            pass

    def _save(self) -> None:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _PATH.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def register(self, cores_used: List[str], hour: int, result: str) -> None:
        if result not in ("WIN", "LOSS") or not cores_used:
            return
        with _LOCK:
            for core in cores_used:
                key = f"{core}|{hour}"
                arr = self._data.setdefault(key, [])
                arr.append(result)
                if len(arr) > _ROLLING:
                    del arr[: len(arr) - _ROLLING]
            self._save()

    def core_wr(self, core: str, hour: int, min_n: int = 8) -> Optional[Tuple[float, int]]:
        with _LOCK:
            arr = self._data.get(f"{core}|{hour}", [])
            n = len(arr)
            if n < min_n:
                return None
            wins = sum(1 for r in arr if r == "WIN")
            return (wins / n, n)

    def core_score_bonus(self, cores_used: List[str], hour: int) -> float:
        """Soma bônus/penalty por core ativo nesta hora.
          WR ≥ 65% → +0.20  (core ouro nessa hora)
          WR ≥ 55% → +0.10
          WR 45-55% → 0     (neutro)
          WR 35-45% → -0.15
          WR < 35%  → -0.30 (core lixo nessa hora)
        Soma todos os cores; clamp final em [-1.5, +1.5].
        """
        total = 0.0
        for core in cores_used:
            res = self.core_wr(core, hour, min_n=8)
            if res is None:
                continue
            wr, _ = res
            if wr >= 0.65:
                total += 0.20
            elif wr >= 0.55:
                total += 0.10
            elif wr >= 0.45:
                total += 0.0
            elif wr >= 0.35:
                total -= 0.15
            else:
                total -= 0.30
        return max(-1.5, min(1.5, total))

    def summary(self) -> Dict[str, Dict[int, Dict[str, float]]]:
        """Retorna {core: {hour: {wr, n}}}."""
        out: Dict[str, Dict[int, Dict[str, float]]] = {c: {} for c in CORE_NAMES}
        with _LOCK:
            for key, arr in self._data.items():
                if "|" not in key:
                    continue
                core, hr = key.rsplit("|", 1)
                try:
                    h = int(hr)
                except ValueError:
                    continue
                n = len(arr)
                if n == 0:
                    continue
                wins = sum(1 for r in arr if r == "WIN")
                out.setdefault(core, {})[h] = {"wr": round(wins / n * 100, 1), "n": n}
        return out


CORE_STATS = _CoreStats()
