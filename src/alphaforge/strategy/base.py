"""策略基类与上下文。

StrategyBase 提供 on_bar / on_fill / on_day_end 回调；
Context 注入 portfolio / history / calendar / config / logger，策略只读。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from alphaforge.engine.market_rules import MarketRules
from alphaforge.logger import get_logger
from alphaforge.models.fill import Fill
from alphaforge.models.order import Order
from alphaforge.models.portfolio import Portfolio, PortfolioSnapshot


@dataclass
class Context:
    """策略运行上下文（每日注入）。"""

    date: datetime                      # 当前交易日
    bar_index: int = 0                  # 当前 bar 序号
    portfolio: Optional[Portfolio] = None
    history: List[PortfolioSnapshot] = field(default_factory=list)
    quotes: Dict[str, float] = field(default_factory=dict)  # 当日行情 {code: price}
    prev_close: Dict[str, float] = field(default_factory=dict)  # 前收盘
    rules: Optional[MarketRules] = None
    config: Any = None
    logger: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


class StrategyBase(ABC):
    """策略抽象基类。"""

    name: str = "base"

    def initialize(self, ctx: Context) -> None:
        """初始化（回测开始前调用一次）。"""
        return None

    @abstractmethod
    def on_bar(self, ctx: Context) -> List[Order]:
        """每根 bar 调用，返回订单列表。"""
        raise NotImplementedError

    def on_fill(self, ctx: Context, fill: Fill) -> None:
        """成交回报回调。"""
        return None

    def on_day_end(self, ctx: Context) -> None:
        """每日收盘回调。"""
        return None