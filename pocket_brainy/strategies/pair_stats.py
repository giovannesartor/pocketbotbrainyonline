"""Tracking de performance por par (ativo, estratégia).

Permite score adaptativo: estratégia que historicamente erra num ativo
específico precisa de score maior pra entrar.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAIR_STATS_PATH = DATA_DIR / "pair_stats.json"

ROLLING_WINDOW = 30          # janela de trades por par
MIN_SAMPLES_FOR_ADJUST = 6   # mínimo de trades antes de ajustar threshold


@dataclass
class PairStat:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    recent: List[str] = field(default_factory=list)

    @property
    def winrate(self) -> float:
        eff = self.wins + self.losses
        return (self.wins / eff * 100) if eff else 0.0

    def rolling_winrate(self) -> float:
        if not self.recent:
            return 0.0
        window = self.recent[-ROLLING_WINDOW:]
        wins = window.count("WIN")
        losses = window.count("LOSS")
        eff = wins + losses
        return (wins / eff * 100) if eff else 0.0

    def rolling_size(self) -> int:
        return min(len(self.recent), ROLLING_WINDOW)


class PairStatsTracker:
    """Tracking thread-safe por (ativo, estratégia) com score adjustment."""

    def __init__(self, path: Path = PAIR_STATS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.stats: Dict[str, PairStat] = {}
        self._load()

    @staticmethod
    def _key(asset: str, strategy: str) -> str:
        return f"{asset}::{strategy}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if "recent" not in v:
                    v["recent"] = []
                self.stats[k] = PairStat(**v)
        except Exception:
            self.stats = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {}
        for k, s in self.stats.items():
            s.recent = s.recent[-ROLLING_WINDOW * 2:]
            payload[k] = asdict(s)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def register_result(self, asset: str, strategy: str, result: str) -> None:
        with self._lock:
            key = self._key(asset, strategy)
            st = self.stats.setdefault(key, PairStat())
            st.trades += 1
            st.recent.append(result)
            if result == "WIN":
                st.wins += 1
            elif result == "LOSS":
                st.losses += 1
            else:
                st.draws += 1
            self._save()

    def get(self, asset: str, strategy: str) -> Optional[PairStat]:
        return self.stats.get(self._key(asset, strategy))

    def score_adjustment(self, asset: str, strategy: str) -> float:
        """Retorna ajuste do min_score para esse par.

        Lógica:
          - Sem amostras suficientes → 0 (usa min_score global)
          - Winrate >= 65% → -0.5 (libera score menor, par confiável)
          - Winrate >= 55% → 0   (neutro)
          - Winrate >= 45% → +0.5 (exige score um pouco maior)
          - Winrate >= 35% → +1.0 (exige score bem maior)
          - Winrate <  35% → +2.0 (par tóxico, praticamente bloqueia)

        Usa rolling se tiver amostras suficientes, senão winrate total.
        """
        st = self.get(asset, strategy)
        if not st or (st.wins + st.losses) < MIN_SAMPLES_FOR_ADJUST:
            return 0.0
        wr = st.rolling_winrate() if st.rolling_size() >= MIN_SAMPLES_FOR_ADJUST else st.winrate
        if wr >= 65:
            return -0.5
        if wr >= 55:
            return 0.0
        if wr >= 45:
            return 0.5
        if wr >= 35:
            return 1.0
        return 2.0

    def top_pairs(self, n: int = 5) -> List[Tuple[str, str, float, int]]:
        """Retorna [(asset, strategy, rolling_wr, sample_size)] ordenado por winrate."""
        rows = []
        for k, st in self.stats.items():
            if (st.wins + st.losses) < MIN_SAMPLES_FOR_ADJUST:
                continue
            asset, strategy = k.split("::", 1)
            wr = st.rolling_winrate() if st.rolling_size() >= MIN_SAMPLES_FOR_ADJUST else st.winrate
            rows.append((asset, strategy, wr, st.rolling_size()))
        rows.sort(key=lambda x: x[2], reverse=True)
        return rows[:n]

    def top_assets_overall(self, n: int = 10, min_trades: int = 5) -> List[Tuple[str, float, int, int, int]]:
        """Agrega por ATIVO (somando todas estratégias).

        Retorna [(asset, winrate%, wins, losses, total_trades)] ordenado por winrate.
        """
        agg: Dict[str, Dict[str, int]] = {}
        for k, st in self.stats.items():
            asset = k.split("::", 1)[0]
            a = agg.setdefault(asset, {"wins": 0, "losses": 0, "draws": 0})
            a["wins"] += st.wins
            a["losses"] += st.losses
            a["draws"] += st.draws
        rows: List[Tuple[str, float, int, int, int]] = []
        for asset, a in agg.items():
            eff = a["wins"] + a["losses"]
            total = eff + a["draws"]
            if eff < min_trades:
                continue
            wr = (a["wins"] / eff) * 100.0
            rows.append((asset, wr, a["wins"], a["losses"], total))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows[:n]

    def top_scalper_assets(self, n: int = 5, min_trades: int = 3) -> List[Tuple[str, float, int]]:
        """Top ativos do Scalper Sniper (somente essa estratégia).

        Retorna [(asset, rolling_wr%, total_recent)] ordenado por winrate.
        """
        rows: List[Tuple[str, float, int]] = []
        for k, st in self.stats.items():
            asset, strategy = k.split("::", 1)
            if strategy != "Scalper Sniper":
                continue
            recent = st.recent[-ROLLING_WINDOW:]
            wins = recent.count("WIN")
            losses = recent.count("LOSS")
            eff = wins + losses
            if eff < min_trades:
                continue
            wr = (wins / eff) * 100.0
            rows.append((asset, wr, eff))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows[:n]
