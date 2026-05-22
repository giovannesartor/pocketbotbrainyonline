from .base import BaseStrategy, Signal
from .rsi_ema import RsiEmaStrategy
from .alligator_rsi_macd import AlligatorRsiMacdStrategy
from .support_resistance import SupportResistanceStrategy
from .mhi import MhiStrategy
from .macd_psar import MacdPsarStrategy
from .bollinger_rsi import BollingerRsiStrategy
from .multi_filter import MultiFilterStrategy
from .breakout import BreakoutStrategy
from .divergence import DivergenceStrategy
from .heikin_ashi import HeikinAshiReversalStrategy
from .stochastic_bollinger import StochasticBollingerStrategy
from .three_inside import ThreeInsideStrategy
from .price_action import PriceActionStrategy
from .scalper import ScalperStrategy
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
    DivergenceStrategy,
    HeikinAshiReversalStrategy,
    StochasticBollingerStrategy,
    ThreeInsideStrategy,
    PriceActionStrategy,
    ScalperStrategy,
]

__all__ = [
    "BaseStrategy", "Signal", "StrategyManager", "StrategyRanking", "ALL_STRATEGIES",
    "PriceActionStrategy", "ScalperStrategy",
    *[s.__name__ for s in ALL_STRATEGIES],
]
