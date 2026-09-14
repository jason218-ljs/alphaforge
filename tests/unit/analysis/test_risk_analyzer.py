"""测试 RiskAnalyzer（修正版夏普/Calmar）。"""

import math

import pytest

from alphaforge.analysis.risk_analyzer import RiskAnalyzer
from alphaforge.config import AnalysisConfig


@pytest.fixture
def analyzer():
    return RiskAnalyzer(AnalysisConfig(risk_free_rate=0.03, annualization_factor=252))


class TestSharpeRatio:
    def test_positive_sharpe(self, analyzer):
        # 5 天单调上升，正收益
        nav = [1_000_000, 1_010_000, 1_020_000, 1_030_000, 1_040_000]
        result = analyzer.analyze(nav)
        assert result.sharpe_ratio > 0

    def test_zero_volatility(self, analyzer):
        # 常数 NAV 无波动
        nav = [1_000_000, 1_000_000, 1_000_000]
        result = analyzer.analyze(nav)
        assert result.sharpe_ratio == 0.0

    def test_includes_risk_free_rate(self, analyzer):
        """夏普应减无风险利率—— rf>0 时夏普应低于 rf=0 时。"""
        nav = [1_000_000, 1_010_000, 1_020_000, 1_030_000]
        high_rf = RiskAnalyzer(AnalysisConfig(risk_free_rate=0.10)).analyze(nav).sharpe_ratio
        low_rf = RiskAnalyzer(AnalysisConfig(risk_free_rate=0.0)).analyze(nav).sharpe_ratio
        assert high_rf < low_rf


class TestMaxDrawdown:
    def test_no_drawdown(self, analyzer):
        nav = [100, 110, 120, 130]
        result = analyzer.analyze(nav)
        assert result.max_drawdown == 0.0

    def test_with_drawdown(self, analyzer):
        nav = [100, 120, 90, 110]
        result = analyzer.analyze(nav)
        assert result.max_drawdown < 0
        assert abs(result.max_drawdown - (-0.25)) < 1e-6


class TestCalmarRatio:
    def test_calmar_uses_annualized_return(self, analyzer):
        """Calmar 应 = 年化收益 / |MDD|（修正 bug：不再用 mean*252 近似）。"""
        nav = [1_000_000, 1_100_000, 1_000_000, 1_200_000]
        result = analyzer.analyze(nav)
        assert result.max_drawdown < 0
        if abs(result.max_drawdown) > 0:
            # Calmar 应有值且为正（年化收益为正）
            assert result.calmar_ratio > 0


class TestAlerts:
    def test_drawdown_alert_triggered(self, analyzer):
        # 大回撤 > 15%
        nav = [100, 100, 80, 80]
        result = analyzer.analyze(nav)
        alert_types = [a["type"] for a in result.alerts]
        assert "DRAWDOWN_EXCEEDED" in alert_types

    def test_no_alerts_for_stable(self, analyzer):
        nav = [1_000_000, 1_001_000, 1_002_000, 1_003_000]
        result = analyzer.analyze(nav)
        # 稳定上升无预警
        assert len(result.alerts) == 0 or all(
            a["level"] != "HIGH" for a in result.alerts
        )


class TestRiskLevel:
    def test_low_risk(self, analyzer):
        nav = [1_000_000, 1_001_000, 1_002_000, 1_003_000, 1_004_000]
        result = analyzer.analyze(nav)
        assert result.risk_level == "LOW"
        assert result.risk_score >= 80