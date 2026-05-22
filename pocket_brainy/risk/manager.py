"""Gerenciamento de risco — stop win/loss, streak, overtrading."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from ..core.config import BotConfig
from ..core.state import BotState


@dataclass
class RiskDecision:
    allow: bool
    reason: str = ""


class RiskManager:
    def __init__(self, cfg: BotConfig, state: BotState):
        self.cfg = cfg
        self.state = state

    # ---- cálculos de stop absolutos ----
    def stop_win_absolute(self) -> float:
        if self.cfg.stop_win_is_percent:
            base = max(self.state.start_balance, 1e-9)
            return base * (self.cfg.stop_win / 100.0)
        return self.cfg.stop_win

    def stop_loss_absolute(self) -> float:
        if self.cfg.stop_loss_is_percent:
            base = max(self.state.start_balance, 1e-9)
            return base * (self.cfg.stop_loss / 100.0)
        return self.cfg.stop_loss

    def can_trade(self) -> RiskDecision:
        # stop win
        if self.state.daily_pnl >= self.stop_win_absolute():
            return RiskDecision(False, f"Stop Win atingido ($ {self.state.daily_pnl:+.2f}).")
        # stop loss (pnl negativo)
        if -self.state.daily_pnl >= self.stop_loss_absolute():
            return RiskDecision(False, f"Stop Loss atingido ($ {self.state.daily_pnl:+.2f}).")
        # máx operações
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            return RiskDecision(False, f"Máx. operações diárias atingido ({self.state.trades_today}).")
        # streak de loss
        if self.state.current_loss_streak >= self.cfg.max_loss_streak:
            return RiskDecision(False, f"Streak de loss atingiu {self.state.current_loss_streak}.")
        # delay entre operações — BYPASS em scalper (cooldown já é por ativo)
        if self.state.last_trade_time is not None and not getattr(self.cfg, "scalper_mode", False):
            since = time.time() - self.state.last_trade_time
            if since < self.cfg.delay_between_trades:
                remaining = self.cfg.delay_between_trades - since
                return RiskDecision(False, f"Delay anti-overtrading ({remaining:.1f}s restantes).")
        return RiskDecision(True, "")

    def mark_trade_time(self) -> None:
        self.state.last_trade_time = time.time()
