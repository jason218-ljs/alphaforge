"""测试 numeric 工具。"""

import math

import pandas as pd

from alphaforge.utils.numeric import (
    annualize_return,
    annualize_volatility,
    cvar_historical,
    downside_volatility,
    log_returns,
    max_drawdown,
    pct_returns,
    safe_div,
    safe_mean,
    safe_std,
    var_historical,
)


class TestSafeDiv:
    def test_normal(self):
        assert safe_div(10, 2) == 5.0

    def test_zero_denominator(self):
        assert safe_div(10, 0) == 0.0

    def test_custom_default(self):
        assert safe_div(10, 0, default=-1) == -1


class TestAnnualizeReturn:
    def test_zero_days(self):
        assert annualize_return(0.1, 0) == 0.0

    def test_one_year(self):
        # 252 天 10% 收益 → 年化 10%
        assert abs(annualize_return(0.10, 252) - 0.10) < 1e-6

    def test_half_year(self):
        # 126 天 5% → 年化约 10.25%
        assert annualize_return(0.05, 126) > 0.05


class TestMaxDrawdown:
    def test_monotonic_increase(self):
        # 单调上升无回撤
        mdd, peak, trough, _, _ = max_drawdown([100, 110, 120, 130])
        assert mdd == 0.0
        assert peak == 130

    def test_single_drawdown(self):
        mdd, peak, trough, pidx, tidx = max_drawdown([100, 120, 90, 110])
        assert mdd < 0
        assert abs(mdd - (-0.25)) < 1e-6  # 90/120 - 1 = -0.25
        assert peak == 120
        assert trough == 90

    def test_empty(self):
        mdd, _, _, _, _ = max_drawdown([])
        assert mdd == 0.0


class TestVolatility:
    def test_annualize_volatility(self):
        rets = [0.01, -0.02, 0.005, 0.015, -0.01]
        vol = annualize_volatility(rets, 252)
        assert vol > 0

    def test_downside_only_negative(self):
        rets = [0.01, -0.02, 0.005, -0.01]
        dv = downside_volatility(rets, 252)
        assert dv > 0

    def test_all_positive_no_downside(self):
        rets = [0.01, 0.02, 0.005]
        assert downside_volatility(rets, 252) == 0.0


class TestVarCvar:
    def test_var_negative(self):
        rets = [-0.02, -0.01, 0.0, 0.01, 0.02]
        v = var_historical(rets, 0.95)
        assert v < 0  # 5% 分位为负

    def test_cvar_le_var(self):
        rets = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.02]
        v = var_historical(rets, 0.95)
        c = cvar_historical(rets, 0.95)
        assert c <= v

    def test_empty(self):
        assert var_historical([], 0.95) == 0.0
        assert cvar_historical([], 0.95) == 0.0


class TestSafeStats:
    def test_safe_mean(self):
        assert safe_mean([1, 2, 3]) == 2.0
        assert safe_mean([]) == 0.0
        assert safe_mean([float("nan"), 2, 3]) == 2.5

    def test_safe_std(self):
        assert safe_std([1, 2, 3, 4]) > 0
        assert safe_std([5]) == 0.0  # 单元素 ddof=1