"""数值工具：安全除法、年化、对数收益等。"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """安全除法，分母为 0 返回 default。"""
    if denominator == 0 or not math.isfinite(denominator):
        return default
    return numerator / denominator


def annualize_return(total_return: float, days: int, annualization_factor: int = 252) -> float:
    """年化收益率。

    Args:
        total_return: 区间累计收益率（小数，如 0.15 表示 15%）。
        days: 区间天数。
        annualization_factor: 年化因子（A 股 252 交易日）。

    Returns:
        年化收益率（小数）。
    """
    if days <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (annualization_factor / days) - 1.0


def log_returns(prices: Sequence[float]) -> pd.Series:
    """对数收益率序列。"""
    s = pd.Series(prices, dtype="float64")
    if len(s) < 2:
        return pd.Series([], dtype="float64")
    return np.log(s / s.shift(1)).dropna()


def pct_returns(prices: Sequence[float]) -> pd.Series:
    """百分比收益率序列。"""
    s = pd.Series(prices, dtype="float64")
    if len(s) < 2:
        return pd.Series([], dtype="float64")
    return s.pct_change().dropna()


def annualize_volatility(daily_returns: Iterable[float], annualization_factor: int = 252) -> float:
    """年化波动率。"""
    s = pd.Series(list(daily_returns), dtype="float64")
    if len(s) < 2:
        return 0.0
    return float(s.std(ddof=1) * math.sqrt(annualization_factor))


def downside_volatility(daily_returns: Iterable[float], annualization_factor: int = 252) -> float:
    """下行波动率（仅负收益）。"""
    s = pd.Series(list(daily_returns), dtype="float64")
    negative = s[s < 0]
    if len(negative) < 2:
        return 0.0
    return float(negative.std(ddof=1) * math.sqrt(annualization_factor))


def max_drawdown(nav_series: Sequence[float]) -> tuple:
    """最大回撤。

    Returns:
        (max_drawdown_pct, peak_nav, trough_nav, peak_idx, trough_idx)
        max_drawdown_pct <= 0。
    """
    s = pd.Series(nav_series, dtype="float64")
    if len(s) == 0:
        return 0.0, 0.0, 0.0, 0, 0
    running_max = s.cummax()
    drawdown = (s - running_max) / running_max.replace(0, np.nan)
    drawdown = drawdown.fillna(0.0)
    mdd_idx = int(drawdown.idxmin()) if len(drawdown) > 0 else 0
    mdd = float(drawdown.iloc[mdd_idx]) if len(drawdown) > 0 else 0.0
    peak_idx = int(running_max.iloc[: mdd_idx + 1].idxmax()) if mdd_idx >= 0 else 0
    # peak_nav 取序列历史最大值（running_max 的最大值），便于报告展示
    peak_nav = float(running_max.max()) if len(s) > 0 else 0.0
    trough_nav = float(s.iloc[mdd_idx]) if len(s) > 0 else 0.0
    return mdd, peak_nav, trough_nav, peak_idx, mdd_idx


def var_historical(returns: Sequence[float], confidence: float = 0.95) -> float:
    """历史模拟法 VaR（返回分位数，负值）。

    Args:
        returns: 日收益率序列（小数）。
        confidence: 置信度（0.95 / 0.99）。

    Returns:
        VaR 值（负数，如 -0.02 表示最坏 5% 情况日亏 2%）。
    """
    s = pd.Series(list(returns), dtype="float64")
    if len(s) == 0:
        return 0.0
    return float(s.quantile(1 - confidence))


def cvar_historical(returns: Sequence[float], confidence: float = 0.95) -> float:
    """条件 VaR（CVaR）：超出 VaR 的尾部均值。"""
    s = pd.Series(list(returns), dtype="float64")
    if len(s) == 0:
        return 0.0
    var = s.quantile(1 - confidence)
    tail = s[s <= var]
    if len(tail) == 0:
        return float(var)
    return float(tail.mean())


def safe_mean(values: Iterable[float], default: float = 0.0) -> float:
    """安全均值。"""
    lst = [v for v in values if v is not None and math.isfinite(v)]
    if not lst:
        return default
    return float(sum(lst) / len(lst))


def safe_std(values: Iterable[float], default: float = 0.0, ddof: int = 1) -> float:
    """安全标准差。"""
    lst = [v for v in values if v is not None and math.isfinite(v)]
    if len(lst) <= ddof:
        return default
    arr = np.array(lst, dtype="float64")
    return float(arr.std(ddof=ddof))