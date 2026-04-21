"""Controlador Martingale — progressão por multiplicador fixo com nível máximo."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import MartingaleConfig


@dataclass
class MartingaleState:
    level: int = 0
    last_amount: float = 0.0


class MartingaleController:
    """Aplica martingale de acordo com a configuração do bot."""

    def __init__(self, cfg: MartingaleConfig, base_amount: float):
        self.cfg = cfg
        self.base_amount = base_amount
        self.state = MartingaleState()

    def next_amount(self) -> float:
        if not self.cfg.enabled or self.state.level == 0:
            return self.base_amount
        amount = self.base_amount * (self.cfg.multiplier ** self.state.level)
        return round(amount, 2)

    def on_result(self, result: str) -> None:
        if result == "WIN":
            if self.cfg.reset_after_win:
                self.state.level = 0
        elif result == "LOSS":
            if not self.cfg.enabled:
                return
            if self.state.level < self.cfg.max_level:
                self.state.level += 1
            else:
                # atingiu teto — reset
                self.state.level = 0

    def reset(self) -> None:
        self.state = MartingaleState()
