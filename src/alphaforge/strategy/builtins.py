"""内置策略工厂：MA 交叉 / RSI 反转 / 布林带触轨。"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from alphaforge.models.order import Order
from alphaforge.strategy.base import Context, StrategyBase


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return float(np.mean(values[-period:]))


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    arr = np.array(values[-(period + 1):], dtype="float64")
    diffs = np.diff(arr)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _bollinger(values: List[float], period: int = 20, num_std: float = 2.0):
    if len(values) < period:
        return None, None, None
    arr = np.array(values[-period:], dtype="float64")
    mid = float(arr.mean())
    sd = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    return mid + num_std * sd, mid, mid - num_std * sd


class MACrossoverStrategy(StrategyBase):
    """均线金叉死叉策略。"""

    name = "ma_crossover"

    def __init__(self, fast: int = 5, slow: int = 20, code: str = "600519.SH",
                 trade_qty: int = 100):
        self.fast = fast
        self.slow = slow
        self.code = code
        self.trade_qty = trade_qty
        self._price_history: List[float] = []
        self._prev_fast: Optional[float] = None
        self._prev_slow: Optional[float] = None

    def on_bar(self, ctx: Context) -> List[Order]:
        price = ctx.quotes.get(self.code)
        if price is None or price <= 0:
            return []
        self._price_history.append(price)
        fast = _sma(self._price_history, self.fast)
        slow = _sma(self._price_history, self.slow)
        if fast is None or slow is None:
            return []
        orders: List[Order] = []
        if self._prev_fast is not None and self._prev_slow is not None:
            # 金叉：fast 上穿 slow
            if self._prev_fast <= self._prev_slow and fast > slow:
                pos = ctx.portfolio.get_position(self.code) if ctx.portfolio else None
                if pos is None or pos.qty == 0:
                    orders.append(Order(
                        time=ctx.date, code=self.code, side="BUY",
                        qty=self.trade_qty, price=price, reason="MA金叉",
                    ))
            # 死叉：fast 下穿 slow
            elif self._prev_fast >= self._prev_slow and fast < slow:
                pos = ctx.portfolio.get_position(self.code) if ctx.portfolio else None
                if pos and pos.qty > 0:
                    orders.append(Order(
                        time=ctx.date, code=self.code, side="SELL",
                        qty=pos.qty, price=price, reason="MA死叉",
                    ))
        self._prev_fast = fast
        self._prev_slow = slow
        return orders


class RSIStrategy(StrategyBase):
    """RSI 超买超卖反转策略。"""

    name = "rsi_reversal"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70,
                 code: str = "600519.SH", trade_qty: int = 100):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.code = code
        self.trade_qty = trade_qty
        self._price_history: List[float] = []

    def on_bar(self, ctx: Context) -> List[Order]:
        price = ctx.quotes.get(self.code)
        if price is None or price <= 0:
            return []
        self._price_history.append(price)
        rsi = _rsi(self._price_history, self.period)
        if rsi is None:
            return []
        orders: List[Order] = []
        pos = ctx.portfolio.get_position(self.code) if ctx.portfolio else None
        if rsi < self.oversold and (pos is None or pos.qty == 0):
            orders.append(Order(
                time=ctx.date, code=self.code, side="BUY",
                qty=self.trade_qty, price=price, reason=f"RSI超卖({rsi:.0f})",
            ))
        elif rsi > self.overbought and pos and pos.qty > 0:
            orders.append(Order(
                time=ctx.date, code=self.code, side="SELL",
                qty=pos.qty, price=price, reason=f"RSI超买({rsi:.0f})",
            ))
        return orders


class BollingerStrategy(StrategyBase):
    """布林带触轨策略。"""

    name = "bollinger"

    def __init__(self, period: int = 20, num_std: float = 2.0,
                 code: str = "600519.SH", trade_qty: int = 100):
        self.period = period
        self.num_std = num_std
        self.code = code
        self.trade_qty = trade_qty
        self._price_history: List[float] = []

    def on_bar(self, ctx: Context) -> List[Order]:
        price = ctx.quotes.get(self.code)
        if price is None or price <= 0:
            return []
        self._price_history.append(price)
        upper, mid, lower = _bollinger(self._price_history, self.period, self.num_std)
        if upper is None:
            return []
        orders: List[Order] = []
        pos = ctx.portfolio.get_position(self.code) if ctx.portfolio else None
        if price <= lower and (pos is None or pos.qty == 0):
            orders.append(Order(
                time=ctx.date, code=self.code, side="BUY",
                qty=self.trade_qty, price=price, reason="布林下轨",
            ))
        elif price >= upper and pos and pos.qty > 0:
            orders.append(Order(
                time=ctx.date, code=self.code, side="SELL",
                qty=pos.qty, price=price, reason="布林上轨",
            ))
        return orders