"""测试 ReturnAnalyzer（修正 Alpha/Beta 用真实基准）。"""

import pytest

from alphaforge.analysis.return_analyzer import ReturnAnalyzer
from alphaforge.config import AnalysisConfig


@pytest.fixture
def analyzer():
    return ReturnAnalyzer(AnalysisConfig(risk_free_rate=0.03, annualization_factor=252))


class TestTotalReturn:
    def test_positive_return(self, analyzer):
        nav = [1_000_000, 1_050_000, 1_100_000]
        result = analyzer.analyze(nav)
        assert result.total_return == pytest.approx(0.10, abs=1e-6)

    def test_zero_return_flat(self, analyzer):
        nav = [1_000_000, 1_000_000, 1_000_000]
        result = analyzer.analyze(nav)
        assert result.total_return == 0.0


class TestWinRate:
    def test_all_winning_days(self, analyzer):
        nav = [100, 101, 102, 103]
        result = analyzer.analyze(nav)
        assert result.winning_days == 3
        assert result.losing_days == 0
        assert result.win_rate == 1.0

    def test_mixed(self, analyzer):
        nav = [100, 102, 100, 103]  # +2, -2, +3
        result = analyzer.analyze(nav)
        assert result.winning_days == 2
        assert result.losing_days == 1
        assert result.win_rate == pytest.approx(2 / 3, abs=1e-3)


class TestAlphaBeta:
    def test_beta_one_when_identical(self, analyzer):
        """组合与基准完全一致时 β≈1，α≈0。"""
        nav = [100, 101, 102, 103, 104, 105]
        result = analyzer.analyze(nav, benchmark_series=nav)
        assert abs(result.beta - 1.0) < 0.1
        assert abs(result.alpha) < 0.05  # α 接近 0

    def test_beta_zero_uncorrelated(self, analyzer):
        """组合常数 vs 基准波动 → β 接近 0。"""
        nav = [100, 100, 100, 100, 100, 100]
        bench = [100, 110, 90, 105, 95, 108]
        result = analyzer.analyze(nav, benchmark_series=bench)
        # 常数 nav 无日收益，beta 应为 0 或接近
        assert abs(result.beta) < 0.5

    def test_no_benchmark_returns_zero(self, analyzer):
        """无基准时 alpha/beta 为 0（不崩溃）。"""
        nav = [100, 101, 102]
        result = analyzer.analyze(nav, benchmark_series=None)
        assert result.alpha == 0.0
        assert result.beta == 0.0


class TestProfitLossRatio:
    def test_all_positive(self, analyzer):
        nav = [100, 101, 102, 103]
        result = analyzer.analyze(nav)
        # 全盈利日，avg_loss=0 → ratio=0（约定）
        assert result.profit_loss_ratio == 0.0

    def test_mixed(self, analyzer):
        nav = [100, 102, 100, 103]  # wins +2,+3; loss -2
        result = analyzer.analyze(nav)
        # avg_win=2.5, avg_loss=-2 → ratio=1.25
        assert result.profit_loss_ratio == pytest.approx(1.25, abs=0.1)