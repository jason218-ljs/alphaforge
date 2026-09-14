"""测试多策略对比器。"""

import pytest

from alphaforge.analysis.strategy_comparator import (
    StrategyComparator,
    StrategyMetrics,
)
from alphaforge.models.result import BacktestResult, BacktestSummary


def _make_result(name: str, total_return: float, sharpe: float,
                  mdd: float, nav_curve: list = None) -> BacktestResult:
    """构造一个最小可用的 BacktestResult。"""
    summary = BacktestSummary(
        total_return=total_return,
        annualized_return=total_return * 0.8,
        max_drawdown=mdd,
        sharpe_ratio=sharpe,
        sortino_ratio=sharpe * 1.1,
        calmar_ratio=abs(total_return / mdd) if mdd != 0 else 0.0,
        win_rate=0.55,
        profit_loss_ratio=1.2,
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        initial_capital=1_000_000,
        final_nav=1_000_000 * (1 + total_return),
        days=60,
    )
    navs = nav_curve or [1.0, 1.01, 1.02, 1.03, 1.04]
    equity_curve = [{"date": f"2026-08-0{i}", "nav": n * 1_000_000} for i, n in enumerate(navs, 1)]
    return BacktestResult(
        strategy_name=name,
        initial_capital=1_000_000,
        equity_curve=equity_curve,
        summary=summary,
    )


class TestStrategyComparator:
    def test_add_and_list(self):
        comp = StrategyComparator()
        comp.add("MA_5_20", _make_result("MA_5_20", 0.1, 1.2, -0.05))
        comp.add("RSI_14", _make_result("RSI_14", 0.15, 1.5, -0.08))
        assert set(comp.list_names()) == {"MA_5_20", "RSI_14"}

    def test_remove(self):
        comp = StrategyComparator()
        comp.add("A", _make_result("A", 0.1, 1.0, -0.05))
        assert comp.remove("A") is True
        assert comp.list_names() == []
        assert comp.remove("A") is False

    def test_rank_by_sharpe(self):
        comp = StrategyComparator()
        comp.add("low", _make_result("low", 0.05, 0.5, -0.03))
        comp.add("high", _make_result("high", 0.20, 2.0, -0.08))
        comp.add("mid", _make_result("mid", 0.10, 1.0, -0.05))
        ranking = comp.rank(metric="sharpe_ratio")
        assert ranking[0]["name"] == "high"
        assert ranking[1]["name"] == "mid"
        assert ranking[2]["name"] == "low"
        assert ranking[0]["rank"] == 1

    def test_rank_empty(self):
        comp = StrategyComparator()
        assert comp.rank() == []

    def test_compare_returns_best_worst(self):
        comp = StrategyComparator()
        comp.add("A", _make_result("A", 0.05, 0.5, -0.03))
        comp.add("B", _make_result("B", 0.20, 2.0, -0.08))
        comp.add("C", _make_result("C", 0.10, 1.0, -0.05))
        report = comp.compare()
        assert report["best_strategy"]["name"] == "B"
        assert report["worst_strategy"]["name"] == "A"
        assert report["total_strategies"] == 3

    def test_correlation_matrix(self):
        comp = StrategyComparator()
        # 两个收益序列完全相同的策略 → 相关性 1.0
        nav_a = [1.0, 1.01, 1.02, 1.03, 1.04]
        nav_b = [1.0, 1.01, 1.02, 1.03, 1.04]
        comp.add("A", _make_result("A", 0.04, 1.0, -0.01, nav_a))
        comp.add("B", _make_result("B", 0.04, 1.0, -0.01, nav_b))
        corr = comp.returns_correlation()
        assert len(corr["matrix"]) == 2
        assert corr["pairs"][0]["corr"] == pytest.approx(1.0, abs=1e-6)

    def test_homogeneous_groups(self):
        comp = StrategyComparator()
        nav = [1.0, 1.01, 1.02, 1.03, 1.04]
        comp.add("A", _make_result("A", 0.04, 1.0, -0.01, nav))
        comp.add("B", _make_result("B", 0.04, 1.0, -0.01, nav))  # 与 A 完全相同
        comp.add("C", _make_result("C", 0.10, 1.5, -0.05,
                                    nav_curve=[1.0, 0.99, 1.05, 1.08, 1.10]))
        report = comp.compare()
        assert len(report["homogeneous_groups"]) >= 1
        group = report["homogeneous_groups"][0]
        assert "A" in group and "B" in group

    def test_to_markdown(self):
        comp = StrategyComparator()
        comp.add("A", _make_result("A", 0.05, 0.5, -0.03))
        comp.add("B", _make_result("B", 0.20, 2.0, -0.08))
        md = comp.to_markdown()
        assert "多策略对比报告" in md
        assert "B" in md
        assert "最优策略" in md
