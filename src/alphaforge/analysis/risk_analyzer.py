"""风险分析器（修正版）。

修正点：
- 夏普比率减无风险利率（对齐 CAPM 定义）
- Calmar 用真实年化收益（非 mean*252 近似）
- 新增 VaR/CVaR/下行波动率/回撤持续期
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import pandas as pd

from alphaforge.config import AnalysisConfig
from alphaforge.logger import get_logger
from alphaforge.models.portfolio import PortfolioSnapshot
from alphaforge.models.result import RiskAnalysis
from alphaforge.utils.numeric import (
    annualize_volatility,
    cvar_historical,
    downside_volatility,
    max_drawdown,
    safe_div,
    safe_mean,
    safe_std,
    var_historical,
)

logger = get_logger("analysis.risk")


class RiskAnalyzer:
    """风险分析器。"""

    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.rf = config.risk_free_rate / 252  # 日化无风险利率
        self.annual_factor = config.annualization_factor

    def analyze(
        self,
        nav_series: List[float],
        snapshots: Optional[List[PortfolioSnapshot]] = None,
        industry_lookup: Optional[dict] = None,
    ) -> RiskAnalysis:
        """分析风险。

        Args:
            nav_series: 每日 NAV 序列。
            snapshots: 组合快照序列（用于集中度计算）。
            industry_lookup: {code: industry} 行业映射。

        Returns:
            RiskAnalysis。
        """
        nav = pd.Series(nav_series, dtype="float64")
        daily_returns = self._daily_returns(nav)

        mdd, peak_nav, trough_nav, peak_idx, trough_idx = max_drawdown(nav_series)
        dd_duration, recovery_days = self._drawdown_periods(nav, peak_idx, trough_idx)

        vol = annualize_volatility(daily_returns, self.annual_factor)
        downside_vol = downside_volatility(daily_returns, self.annual_factor)
        sharpe = self._sharpe(daily_returns)
        sortino = self._sortino(daily_returns)
        annualized_return = self._annualized_return(nav)
        calmar = safe_div(annualized_return, abs(mdd)) if mdd < 0 else 0.0
        var95 = var_historical(daily_returns, 0.95)
        cvar95 = cvar_historical(daily_returns, 0.95)

        single_max, ind_conc, cash_ratio, pos_count = 0.0, 0.0, 0.0, 0
        if snapshots and snapshots[-1].nav > 0:
            last = snapshots[-1]
            weights = last.position_weights()
            single_max = max(weights.values()) if weights else 0.0
            ind_weights = last.industry_weights(industry_lookup or {})
            ind_conc = max(ind_weights.values()) if ind_weights else 0.0
            cash_ratio = last.cash_ratio
            pos_count = last.position_count

        risk_score, risk_level = self._score(mdd, vol, sharpe, single_max, ind_conc)
        alerts = self._alerts(mdd, vol, sharpe, single_max, ind_conc)

        return RiskAnalysis(
            max_drawdown=mdd,
            drawdown_duration_days=dd_duration,
            recovery_days=recovery_days,
            annualized_volatility=vol,
            downside_volatility=downside_vol,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            var_95=var95,
            cvar_95=cvar95,
            single_position_max=single_max,
            industry_concentration=ind_conc,
            cash_ratio=cash_ratio,
            position_count=pos_count,
            risk_score=risk_score,
            risk_level=risk_level,
            alerts=alerts,
        )

    # ---------- 指标计算 ----------

    def _daily_returns(self, nav: pd.Series) -> pd.Series:
        if len(nav) < 2:
            return pd.Series([], dtype="float64")
        return nav.pct_change().dropna()

    def _sharpe(self, daily_returns: pd.Series) -> float:
        if len(daily_returns) < 2:
            return 0.0
        std = daily_returns.std(ddof=1)
        if std == 0 or not math.isfinite(std):
            return 0.0
        return float((daily_returns.mean() - self.rf) / std * math.sqrt(self.annual_factor))

    def _sortino(self, daily_returns: pd.Series) -> float:
        if len(daily_returns) < 2:
            return 0.0
        negative = daily_returns[daily_returns < 0]
        if len(negative) < 2:
            return 0.0
        downside = negative.std(ddof=1)
        if downside == 0:
            return 0.0
        return float(
            (daily_returns.mean() - self.rf) / downside * math.sqrt(self.annual_factor)
        )

    def _annualized_return(self, nav: pd.Series) -> float:
        if len(nav) < 2 or nav.iloc[0] <= 0:
            return 0.0
        total = nav.iloc[-1] / nav.iloc[0] - 1.0
        days = len(nav) - 1
        if days <= 0:
            return 0.0
        if total <= -1.0:
            return -1.0
        return float((1.0 + total) ** (self.annual_factor / days) - 1.0)

    def _drawdown_periods(self, nav: pd.Series, peak_idx: int, trough_idx: int) -> tuple:
        """回撤持续期与恢复期（天数）。"""
        if len(nav) == 0 or peak_idx >= len(nav):
            return 0, 0
        running_max = nav.cummax()
        in_dd = nav < running_max
        # 持续期：从 peak 到 trough
        duration = max(0, trough_idx - peak_idx) if trough_idx >= peak_idx else 0
        # 恢复期：从 trough 到恢复到 peak 的时间
        recovery = 0
        peak_val = running_max.iloc[trough_idx] if trough_idx < len(nav) else 0
        for i in range(trough_idx + 1, len(nav)):
            if nav.iloc[i] >= peak_val:
                recovery = i - trough_idx
                break
        return int(duration), int(recovery)

    def _score(self, mdd, vol, sharpe, single_max, ind_conc) -> tuple:
        """风险评分（100 分制，扣分制）。"""
        score = 100.0
        if mdd < self.config.max_drawdown_warning:
            score -= min(40, abs(mdd) * 100)
        if vol > self.config.max_volatility_warning:
            score -= 20
        if sharpe < self.config.min_sharpe_ratio:
            score -= 15
        if single_max > self.config.max_single_position:
            score -= 15
        if ind_conc > self.config.max_industry_exposure:
            score -= 10
        score = max(0.0, min(100.0, score))
        if score >= 80:
            level = "LOW"
        elif score >= 60:
            level = "NORMAL"
        elif score >= 40:
            level = "MEDIUM"
        else:
            level = "HIGH"
        return float(score), level

    def _alerts(self, mdd, vol, sharpe, single_max, ind_conc) -> List[dict]:
        alerts: List[dict] = []
        if mdd < self.config.max_drawdown_warning:
            alerts.append({
                "level": "HIGH", "type": "DRAWDOWN_EXCEEDED",
                "message": f"最大回撤 {mdd*100:.2f}% 超过阈值 {self.config.max_drawdown_warning*100:.0f}%",
            })
        if vol > self.config.max_volatility_warning:
            alerts.append({
                "level": "MEDIUM", "type": "VOLATILITY_EXCEEDED",
                "message": f"年化波动率 {vol*100:.2f}% 超过阈值 {self.config.max_volatility_warning*100:.0f}%",
            })
        if sharpe < self.config.min_sharpe_ratio:
            alerts.append({
                "level": "MEDIUM", "type": "SHARPE_LOW",
                "message": f"夏普比率 {sharpe:.2f} 低于阈值 {self.config.min_sharpe_ratio}",
            })
        if single_max > self.config.max_single_position:
            alerts.append({
                "level": "MEDIUM", "type": "SINGLE_POSITION_OVERWEIGHT",
                "message": f"单票最高仓位 {single_max*100:.1f}% 超过 {self.config.max_single_position*100:.0f}%",
            })
        if ind_conc > self.config.max_industry_exposure:
            alerts.append({
                "level": "LOW", "type": "INDUSTRY_OVERWEIGHT",
                "message": f"行业集中度 {ind_conc*100:.1f}% 超过 {self.config.max_industry_exposure*100:.0f}%",
            })
        return alerts