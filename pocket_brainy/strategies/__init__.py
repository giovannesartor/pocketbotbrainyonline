from .base import BaseStrategy, Signal
from .rsi_ema import RsiEmaStrategy
from .alligator_rsi_macd import AlligatorRsiMacdStrategy
from .support_resistance import SupportResistanceStrategy
from .mhi import MhiStrategy
from .macd_psar import MacdPsarStrategy
from .bollinger_rsi import BollingerRsiStrategy
from .multi_filter import MultiFilterStrategy
from .breakout import BreakoutStrategy
from .manager import StrategyManager
from .ranking import StrategyRanking

ALL_STRATEGIES = [
    RsiEmaStrategy,
    AlligatorRsiMacdStrategy,
    SupportResistanceStrategy,
    MhiStrategy,
    MacdPsarStrategy,
    BollingerRsiStrategy,
    MultiFilterStrategy,
    BreakoutStrategy,
]

__all__ = [
    "BaseStrategy", "Signal", "StrategyManager", "StrategyRanking", "ALL_STRATEGIES",
    *[s.__name__ for s in ALL_STRATEGIES],
]
