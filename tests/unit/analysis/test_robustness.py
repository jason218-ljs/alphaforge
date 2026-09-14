"""测试稳健性：Walk-Forward / Monte Carlo / 敏感性。"""

import numpy as np
import pytest

from alphaforge.analysis.robustness import Robustness


@pytest.fixture
def robustness():
    return Robustness(seed=42)


@pytest.fixture
def prices():
    return list(100 + np.cumsum(np.random.RandomState(7).normal(0.05, 2, 120)))


def _ma_objective(params, train_prices):
    from alphaforge.engine.vectorized_engine import VectorizedEngine
    engine = VectorizedEngine()
    result = engine.run_ma_strategy(train_prices, fast=int(params["fast"]),
                                    slow=int(params["slow"]))
    return result


class TestWalkForward:
    def test_returns_windows(self, robustness, prices):
        result = robustness.walk_forward(_ma_objective, prices,
                                         {"fast": 3, "slow": 10},
                                         window=30, step=5)
        assert "windows" in result
        assert "is_stable" in result
        assert isinstance(result["is_stable"], bool)
        assert "avg_ratio" in result

    def test_insufficient_data(self, robustness):
        result = robustness.walk_forward(_ma_objective, [100, 101],
                                         {"fast": 3, "slow": 10},
                                         window=30, step=5)
        assert result["windows"] == []
        assert result["is_stable"] is False

    def test_stable_flag_logic(self, robustness, prices):
        # 让 obj 始终返回相同值 → ratio=1 → stable
        def const_obj(params, train):
            return {"sharpe_ratio": 1.0, "max_drawdown": -0.05}
        result = robustness.walk_forward(const_obj, prices, {"a": 1},
                                         window=20, step=10)
        assert result["is_stable"] is True
        assert result["avg_ratio"] == pytest.approx(1.0)


class TestMonteCarlo:
    def test_returns_distribution(self, robustness):
        result = robustness.monte_carlo([0.01, -0.02, 0.005, 0.02, -0.005],
                                        n_runs=200, horizon_days=20)
        assert len(result["runs"]) == 200
        assert result["mean"] > 0
        assert result["median"] > 0
        for p in (5, 25, 50, 75, 95):
            assert p in result["percentiles"]

    def test_empty_returns(self, robustness):
        result = robustness.monte_carlo([], n_runs=100, horizon_days=10)
        assert result["runs"] == []

    def test_percentile_ordering(self, robustness):
        result = robustness.monte_carlo([0.01, -0.01, 0.02, -0.02, 0.005],
                                        n_runs=500, horizon_days=10)
        assert result["percentiles"][5] <= result["percentiles"][50] <= result["percentiles"][95]


class TestSensitivity:
    def test_returns_sensitivity(self, robustness, prices):
        from alphaforge.engine.vectorized_engine import VectorizedEngine
        engine = VectorizedEngine()
        def obj(params):
            return engine.run_ma_strategy(prices, fast=int(params["fast"]),
                                          slow=int(params["slow"]))
        result = robustness.sensitivity(obj, {"fast": 5, "slow": 20})
        assert "sensitivity" in result
        assert "is_robust" in result
        for name in ("fast", "slow"):
            assert name in result["sensitivity"]

    def test_base_score(self, robustness):
        def obj(params):
            return {"sharpe_ratio": 1.0, "max_drawdown": -0.05}
        result = robustness.sensitivity(obj, {"a": 10})
        assert result["base_score"] == 1.0