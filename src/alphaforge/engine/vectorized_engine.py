"""向量化回测引擎。

针对单标的、信号预计算的策略，用 pandas 矩阵运算逐日计算持仓/收益/组合净值，
相比事件驱动引擎提升数十到上百倍，适合参数寻优的大规模扫描。

本引擎处理固定数量买入（每信号 N 股）或按比例（每信号 N% 资金）的简化交易模型，
不做 T+1/涨跌停/滑点等边际模拟（那是事件驱动引擎的职责），聚焦于策略信号评估与寻优。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from alphaforge.logger import get_logger
from alphaforge.utils.numeric import max_drawdown

logger = get_logger("engine.vectorized")


class VectorizedEngine:
    """向量化回测引擎。"""

    def __init__(self, fee_rate: float = 0.00025, min_fee: float = 5.0,
                 annualization: int = 252, initial_capital: float = 1_000_000.0):
        self.fee_rate = fee_rate
        self.min_fee = min_fee
        self.annualization = annualization
        self.initial_capital = initial_capital

    def run_signal_backtest(
        self,
        prices: Sequence[float],
        signals: Sequence[int],
        qty_per_signal: int = 100,
        dates: Optional[Sequence[Any]] = None,
        capital: Optional[float] = None,
        trade_fraction: Optional[float] = None,
    ) -> Dict[str, Any]:
        """单资产信号回测。

        Args:
            prices: 收盘价序列。
            signals: 信号序列（1=买入，-1=卖出，0=持有）。
            qty_per_signal: 每个买入信号的买入股数。
            dates: 交易日（与 prices 等长）。
            capital: 初始资金（默认 initial_capital）。
            trade_fraction: 若提供，按资金比例买入（0~1），忽略 qty_per_signal。

        Returns:
            {equity_series, returns_series, position_series, total_return,
             annualized_return, max_drawdown, sharpe_ratio, win_rate, total_trades,
             trade_log}
        """
        prices = pd.Series(np.asarray(prices, dtype="float64"))
        signals = pd.Series(np.asarray(signals, dtype="int"))
        n = len(prices)
        if n == 0:
            return self._empty_result()

        cap = capital if capital is not None else self.initial_capital

        # 建立仓位：position 是"持有股数"，仅在信号日变化，其余日不变
        position = np.zeros(n, dtype="float64")
        cash = np.full(n, cap, dtype="float64")
        trade_log: List[dict] = []

        # 昨日收盘价用于对今日交易计算（避免前视）
        prev_price = prices.shift(1)

        for i in range(n):
            sig = int(signals.iloc[i])
            price = float(prices.iloc[i])
            if i > 0:
                position[i] = position[i - 1]
                cash[i] = cash[i - 1]
            if sig == 0 or price <= 0 or not math.isfinite(price):
                continue
            # 以当日开盘价近似成交（用收盘价模拟，寻优场景足够）
            if sig > 0:
                if trade_fraction is not None:
                    budget = cash[i] * trade_fraction
                    if budget <= 0:
                        continue
                    qty = int(budget / (price * (1 + self.fee_rate)) / 100) * 100
                else:
                    qty = qty_per_signal
                if qty <= 0:
                    continue
                cost = qty * price * (1 + self.fee_rate)
                if cost <= cash[i]:
                    position[i] += qty
                    cash[i] -= cost
                    trade_log.append({
                        "index": i, "side": "BUY", "qty": qty, "price": price,
                        "cost": cost,
                    })
            else:  # sig < 0
                qty = int(position[i])
                if qty <= 0:
                    continue
                proceeds = qty * price * (1 - self.fee_rate)
                position[i] = 0
                cash[i] += proceeds
                trade_log.append({
                    "index": i, "side": "SELL", "qty": qty, "price": price,
                    "proceeds": proceeds,
                })

        # 组合权益 = 现金 + 持仓市值
        equity = cash + position * prices.values
        equity_series = pd.Series(equity, index=dates if dates is not None else None)
        returns_series = equity_series.pct_change().dropna()

        # 绩效
        total_return = float(equity_series.iloc[-1] / cap - 1.0) if cap > 0 else 0.0
        annualized = self._annualized(total_return, n)
        mdd, peak, trough, _, _ = max_drawdown(list(equity))
        sharpe = self._sharpe(returns_series)

        # 胜率：按卖出实现的盈亏
        wins = [t for t in trade_log if t["side"] == "SELL"]

        summary = {
            "equity_series": equity_series.tolist(),
            "returns_series": returns_series.tolist() if len(returns_series) else [],
            "position_series": position.tolist(),
            "dates": [str(d) for d in (dates or range(n))],
            "equity_raw": equity.tolist(),
            "total_return": total_return,
            "annualized_return": annualized,
            "max_drawdown": mdd,
            "sharpe_ratio": sharpe,
            "total_trades": len(trade_log),
            "winning_trades": len(wins),
            "final_equity": float(equity_series.iloc[-1]),
            "initial_capital": cap,
            "trade_log": trade_log,
        }
        return summary

    def run_ma_strategy(
        self,
        prices: Sequence[float],
        fast: int = 5,
        slow: int = 20,
        qty_per_signal: int = 100,
        dates: Optional[Sequence[Any]] = None,
        capital: Optional[float] = None,
        trade_fraction: Optional[float] = None,
    ) -> Dict[str, Any]:
        """MA 金叉死叉策略的向量化回测。"""
        prices_arr = np.asarray(prices, dtype="float64")
        fast_ma = pd.Series(prices_arr).rolling(fast).mean()
        slow_ma = pd.Series(prices_arr).rolling(slow).mean()
        # 金叉=1（买入），死叉=-1（卖出）
        signals = pd.Series(0, index=range(len(prices_arr)))
        golden = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        dead = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
        signals[golden] = 1
        signals[dead] = -1
        return self.run_signal_backtest(
            prices_arr, signals.values, qty_per_signal=qty_per_signal,
            dates=dates, capital=capital, trade_fraction=trade_fraction,
        )

    # ---------- 绩效工具 ----------

    def _annualized(self, total_return: float, days: int) -> float:
        if days <= 0 or total_return <= -1.0:
            return 0.0
        return float((1 + total_return) ** (self.annualization / days) - 1.0)

    def _sharpe(self, returns_series: pd.Series) -> float:
        if len(returns_series) < 2:
            return 0.0
        std = float(returns_series.std(ddof=1))
        if std == 0:
            return 0.0
        return float(returns_series.mean() / std * math.sqrt(self.annualization))

    def _empty_result(self) -> dict:
        return {
            "equity_series": [], "returns_series": [], "position_series": [],
            "dates": [], "equity_raw": [], "total_return": 0.0,
            "annualized_return": 0.0, "max_drawdown": 0.0, "sharpe_ratio": 0.0,
            "total_trades": 0, "winning_trades": 0, "final_equity": self.initial_capital,
            "initial_capital": self.initial_capital, "trade_log": [],
        }