"""Ranking dinâmico + pesos adaptativos rolling (janela dos últimos N trades)."""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Deque, Dict, List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RANKING_PATH = DATA_DIR / "ranking.json"

ROLLING_WINDOW = 50       # janela para pesos adaptativos
MIN_ROLLING_SAMPLES = 8   # mínimo para bônus rolling entrar em vigor


@dataclass
class StrategyStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    profit: float = 0.0
    # Histórico dos últimos resultados (serializado como lista)
    recent: List[str] = field(default_factory=list)

    @property
    def winrate(self) -> float:
        effective = self.wins + self.losses
        return (self.wins / effective * 100) if effective else 0.0

    def rolling_winrate(self) -> float:
        """Winrate considerando somente a janela recente (ROLLING_WINDOW)."""
        if not self.recent:
            return 0.0
        window = self.recent[-ROLLING_WINDOW:]
        wins = window.count("WIN")
        losses = window.count("LOSS")
        effective = wins + losses
        return (wins / effective * 100) if effective else 0.0

    def rolling_size(self) -> int:
        return min(len(self.recent), ROLLING_WINDOW)


class StrategyRanking:
    """
    Sistema de performance por estratégia.

    Bônus de score = 70% rolling + 30% histórico total (quando janela já tem
    amostras suficientes). Antes disso, usa só o histórico total.
    """

    BONUS_CAP = 2.0

    def __init__(self, path: Path = RANKING_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.stats: Dict[str, StrategyStats] = {}
        self._load()

    # ---- IO ----
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            stats: Dict[str, StrategyStats] = {}
            for name, st in data.items():
                # backward-compat: ranking.json antigo sem 'recent'
                if "recent" not in st:
                    st["recent"] = []
                stats[name] = StrategyStats(**st)
            self.stats = stats
        except Exception:
            self.stats = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {}
        for n, s in self.stats.items():
            # trunca histórico para 2x a janela (para diluir disco)
            s.recent = s.recent[-ROLLING_WINDOW * 2:]
            payload[n] = asdict(s)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    # ---- Atualização ----
    def register_result(self, strategy: str, result: str, profit: float) -> None:
        with self._lock:
            st = self.stats.setdefault(strategy, StrategyStats())
            st.trades += 1
            st.profit += profit
            st.recent.append(result)
            if result == "WIN":
                st.wins += 1
            elif result == "LOSS":
                st.losses += 1
            else:
                st.draws += 1
            self._save()

    # ---- Leitura ----
    def _bonus_from_wr(self, wr: float) -> float:
        if wr >= 65:
            return min(self.BONUS_CAP, (wr - 65) / 10)
        if wr < 50:
            return max(-self.BONUS_CAP, (wr - 50) / 10)
        return 0.0

    def bonus(self, strategy: str) -> float:
        """
        Bônus adaptativo:
          - Se existem < 5 trades reais → sem bônus.
          - Se janela rolling já tem >= MIN_ROLLING_SAMPLES → bônus = 0.7*rolling + 0.3*total.
          - Caso contrário → bônus = total (histórico completo).
        """
        st = self.stats.get(strategy)
        if not st or (st.wins + st.losses) < 5:
            return 0.0
        total_bonus = self._bonus_from_wr(st.winrate)
        if st.rolling_size() >= MIN_ROLLING_SAMPLES:
            rolling_bonus = self._bonus_from_wr(st.rolling_winrate())
            return round(0.7 * rolling_bonus + 0.3 * total_bonus, 3)
        return round(total_bonus, 3)

    def ranking(self) -> List[Dict]:
        rows = []
        for name, st in self.stats.items():
            rows.append({
                "strategy": name,
                "trades": st.trades,
                "wins": st.wins,
                "losses": st.losses,
                "winrate": st.winrate,
                "rolling_winrate": st.rolling_winrate(),
                "rolling_size": st.rolling_size(),
                "profit": st.profit,
                "bonus": self.bonus(name),
            })
        rows.sort(key=lambda r: (r["rolling_winrate"] or r["winrate"], r["profit"]), reverse=True)
        return rows

    def pretty(self) -> str:
        rows = self.ranking()
        if not rows:
            return "📊 Ranking vazio — execute algumas operações."
        lines = ["<b>📈 Ranking de Estratégias (adaptativo)</b>",
                 "<i>Rolling = janela de 50 trades recentes</i>\n"]
        for i, r in enumerate(rows, 1):
            rw = r["rolling_winrate"]
            rn = r["rolling_size"]
            rw_str = f"{rw:.1f}% ({rn}/50)" if rn else "—"
            lines.append(
                f"{i}. <b>{r['strategy']}</b>\n"
                f"   └ Total: {r['winrate']:.1f}% | Rolling: {rw_str} | "
                f"Bônus: {r['bonus']:+.2f} | $ {r['profit']:+.2f}"
            )
        return "\n".join(lines)
