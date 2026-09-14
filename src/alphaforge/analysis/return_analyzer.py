"""收益分析器（修正版）。

修正点：
- Alpha/Beta 用真实基准序列回归（非硬编码 8%）
- benchmark_return 由 BenchmarkManager 提供
- 新增跟踪误差 / 信息比率
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from alphaforge.config import AnalysisConfig
from alphaforge.logger import get_logger
from alphaforge.models.result import ReturnAnalysis
from alphaforge.utils.numeric import safe_div, safe_mean

logger = get_logger("analysis.return")


class ReturnAnalyzer:
    """收益分析器。"""

    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.rf = config.risk_free_rate / 252  # 日化
        self.annual_factor = config.annualization_factor

    def analyze(
        self,
        nav_series: List[float],
        benchmark_series: Optional[List[float]] = None,
    ) -> ReturnAnalysis:
        """分析收益。

        Args:
            nav_series: 每日 NAV 序列。
            benchmark_series: 基准每日净值序列（与 nav 等长，对齐）。

        Returns:
            ReturnAnalysis。
        """
        nav = pd.Series(nav_series, dtype="float64")
        daily_ret = self._daily_returns(nav)

        total_return = self._total_return(nav)
        annualized = self._annualized_return(nav, total_return)
        daily_avg = float(daily_ret.mean()) if len(daily_ret) > 0 else 0.0
        daily_std = float(daily_ret.std(ddof=1)) if len(daily_ret) > 1 else 0.0

        winning = int((daily_ret > 0).sum()) if len(daily_ret) > 0 else 0
        losing = int((daily_ret < 0).sum()) if len(daily_ret) > 0 else 0
        win_rate = safe_div(winning, winning + losing)
        wins = daily_ret[daily_ret > 0]
        losses = daily_ret[daily_ret < 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        profit_loss_ratio = safe_div(avg_win, abs(avg_loss)) if avg_loss != 0 else 0.0

        alpha, beta, benchmark_return, tracking_err, ir = 0.0, 0.0, 0.0, 0.0, 0.0
        if benchmark_series is not None and len(benchmark_series) >= len(nav):
            alpha, beta, benchmark_return, tracking_err, ir = self._alpha_beta(
                daily_ret, benchmark_series
            )

        score, level = self._score(annualized, win_rate, profit_loss_ratio, alpha)

        return ReturnAnalysis(
            total_return=total_return,
            annualized_return=annualized,
            daily_return_avg=daily_avg,
            daily_return_std=daily_std,
            winning_days=winning,
            losing_days=losing,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_loss_ratio=profit_loss_ratio,
            benchmark_return=benchmark_return,
            alpha=alpha,
            beta=beta,
            tracking_error=tracking_err,
            information_ratio=ir,
            return_score=score,
            return_level=level,
        )

    # ---------- 指标 ----------

    def _daily_returns(self, nav: pd.Series) -> pd.Series:
        if len(nav) < 2:
            return pd.Series([], dtype="float64")
        return nav.pct_change().dropna()

    def _total_return(self, nav: pd.Series) -> float:
        if len(nav) < 2 or nav.iloc[0] <= 0:
            return 0.0
        return float(nav.iloc[-1] / nav.iloc[0] - 1.0)

    def _annualized_return(self, nav: pd.Series, total_return: float) -> float:
        days = len(nav) - 1
        if days <= 0 or total_return <= -1.0:
            return 0.0
        return float((1.0 + total_return) ** (self.annual_factor / days) - 1.0)

    def _alpha_beta(self, daily_ret: pd.Series, benchmark_series: List[float]) -> Tuple:
        """计算 Alpha/Beta/跟踪误差/信息比率。

        CAPM: Rp - Rf = α + β(Rb - Rf) + ε
        """
        bench = pd.Series(benchmark_series, dtype="float64")
        bench_ret = bench.pct_change().dropna()
        # 对齐长度
        n = min(len(daily_ret), len(bench_ret))
        if n < 2:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        rp = daily_ret.iloc[-n:].values
        rb = bench_ret.iloc[-n:].values

        var_b = np.var(rb, ddof=1)
        if var_b == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        beta = float(np.cov(rp, rb, ddof=1)[0, 1] / var_b)
        # 年化 alpha
        excess_rp = rp - self.rf
        excess_rb = rb - self.rf
        alpha_daily = float(np.mean(excess_rp) - beta * np.mean(excess_rb))
        alpha = alpha_daily * self.annual_factor

        benchmark_return = float((bench.iloc[-1] / bench.iloc[0] - 1.0)) if len(bench) >= 2 else 0.0
        # 跟踪误差：超额收益的年化标准差
        excess = rp - rb
        tracking_err = float(np.std(excess, ddof=1) * math.sqrt(self.annual_factor)) if len(excess) > 1 else 0.0
        ir = safe_div(alpha_daily * self.annual_factor, tracking_err) if tracking_err > 0 else 0.0
        return alpha, beta, benchmark_return, tracking_err, ir

    def _score(self, annualized, win_rate, profit_loss_ratio, alpha) -> tuple:
        """收益评分（100 分制，加分制）。"""
        score = 50.0
        if annualized > 0.10:
            score += 20
        elif annualized > 0.05:
            score += 10
        elif annualized < 0:
            score -= 15
        if win_rate > 0.55:
            score += 10
        elif win_rate < 0.40:
            score -= 10
        if profit_loss_ratio > 1.5:
            score += 10
        elif profit_loss_ratio < 1.0:
            score -= 10
        if alpha > 0.03:
            score += 10
        elif alpha < -0.03:
            score -= 10
        score = max(0.0, min(100.0, score))
        if score >= 80:
            level = "EXCELLENT"
        elif score >= 60:
            level = "GOOD"
        elif score >= 40:
            level = "NORMAL"
        else:
            level = "POOR"
        return float(score), level