"""Estado runtime do bot (não persistido além do histórico)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_PATH = DATA_DIR / "history.json"
_BRT = timezone(timedelta(hours=-3))


@dataclass
class TradeResult:
    timestamp: str
    asset: str
    direction: str        # CALL / PUT
    amount: float
    expiration: int
    strategy: str
    score: float
    ai_confidence: float
    result: str           # WIN / LOSS / DRAW / PENDING / SIM
    profit: float = 0.0
    timeframe: str = "M1"
    martingale_level: int = 0
    # contexto de mercado no momento da entrada (para análise posterior)
    market_adx: float = 0.0
    market_rsi: float = 0.0
    market_bb_width: float = 0.0


@dataclass
class BotState:
    running: bool = False
    connected: bool = False
    start_balance: float = 0.0
    current_balance: float = 0.0
    daily_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    current_loss_streak: int = 0
    martingale_level: int = 0
    trades_today: int = 0
    last_trade_time: Optional[float] = None
    open_trades: int = 0          # trades atualmente aguardando resultado
    history: List[TradeResult] = field(default_factory=list)

    def register_trade(self, trade: TradeResult) -> None:
        self.history.append(trade)
        if trade.result == "WIN":
            self.wins += 1
            self.current_loss_streak = 0
            self.martingale_level = 0
        elif trade.result == "LOSS":
            self.losses += 1
            self.current_loss_streak += 1
        elif trade.result == "DRAW":
            self.draws += 1
        else:
            return  # CANCELADO / SIM — não afeta estatísticas diárias
        self.daily_pnl += trade.profit
        self.trades_today += 1

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.trades_today = 0
        self.current_loss_streak = 0
        self.martingale_level = 0
        self.open_trades = 0

    @property
    def winrate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total else 0.0

    def save_history(self) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in self.history], f, indent=2, ensure_ascii=False)

    def load_history(self) -> None:
        if not HISTORY_PATH.exists():
            return
        try:
            with HISTORY_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.history = [TradeResult(**t) for t in data]
            self._rebuild_daily_stats()  # reconstrói contadores do dia após reiniciar
        except Exception:
            self.history = []

    def _rebuild_daily_stats(self) -> None:
        """Reconstrói wins/losses/pnl/streak do dia atual a partir do histórico.
        Chamado em load_history() para que um reinicio do bot não zere o placar.
        """
        today = datetime.now(_BRT).strftime("%Y-%m-%d")
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.current_loss_streak = 0
        for trade in self.history:
            if not trade.timestamp.startswith(today):
                continue
            if trade.result == "WIN":
                self.wins += 1
                self.current_loss_streak = 0
            elif trade.result == "LOSS":
                self.losses += 1
                self.current_loss_streak += 1
            elif trade.result == "DRAW":
                self.draws += 1
            else:
                continue  # CANCELADO / SIM
            self.daily_pnl += trade.profit
            self.trades_today += 1
