"""Controlador Martingale — progressão por multiplicador fixo com nível máximo.

Inclui:
- Martingale clássico (multiplicador após LOSS)
- Smart Gale: só executa gale se novo sinal tem score >= ratio × score do sinal anterior
- Soros (anti-martingale): após WIN, próxima entrada reinveste parte do lucro
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import MartingaleConfig


@dataclass
class MartingaleState:
    level: int = 0
    last_amount: float = 0.0
    last_score: float = 0.0       # score do último sinal (pra smart gale)
    soros_level: int = 0          # nível atual de soros encadeado
    soros_bonus: float = 0.0      # valor extra do lucro a reinvestir


class MartingaleController:
    """Aplica martingale + soros de acordo com a configuração."""

    def __init__(self, cfg: MartingaleConfig, base_amount: float):
        self.cfg = cfg
        self.base_amount = base_amount
        self.state = MartingaleState()
        # Configs externas (setadas pelo bot)
        self.smart_gale: bool = True
        self.smart_gale_ratio: float = 1.5
        self.soros_enabled: bool = False
        self.soros_pct: float = 50.0
        self.soros_max_levels: int = 2

    def next_amount(self) -> float:
        # Martingale: prioriza nível ativo
        if self.cfg.enabled and self.state.level > 0:
            amount = self.base_amount * (self.cfg.multiplier ** self.state.level)
            return round(amount, 2)
        # Soros: após win, encadeia parte do lucro
        if self.soros_enabled and self.state.soros_level > 0 and self.state.soros_bonus > 0:
            return round(self.base_amount + self.state.soros_bonus, 2)
        return self.base_amount

    def can_gale(self, new_signal_score: float) -> bool:
        """Smart Gale: só permite gale se novo sinal é claramente melhor que o anterior."""
        if not self.smart_gale or self.state.level == 0:
            return True
        if self.state.last_score <= 0:
            return True
        return new_signal_score >= self.state.last_score * self.smart_gale_ratio

    def register_signal(self, score: float) -> None:
        """Memoriza score do sinal atual (chamado antes de enviar entry)."""
        self.state.last_score = score

    def on_result(self, result: str, profit: float = 0.0) -> None:
        if result == "WIN":
            # Soros: acumula parte do lucro pra próxima entrada
            if self.soros_enabled and profit > 0 and self.state.soros_level < self.soros_max_levels:
                self.state.soros_bonus = round(profit * (self.soros_pct / 100.0), 2)
                self.state.soros_level += 1
            else:
                self.state.soros_bonus = 0.0
                self.state.soros_level = 0
            if self.cfg.reset_after_win:
                self.state.level = 0
        elif result == "LOSS":
            # Loss zera soros e tenta gale
            self.state.soros_bonus = 0.0
            self.state.soros_level = 0
            if not self.cfg.enabled:
                return
            if self.state.level < self.cfg.max_level:
                self.state.level += 1
            else:
                # atingiu teto — reset
                self.state.level = 0

    def reset(self) -> None:
        self.state = MartingaleState()
