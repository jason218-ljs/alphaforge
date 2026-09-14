"""测试参数寻优器（网格 + Optuna 贝叶斯）。"""

import pytest

from alphaforge.analysis.optimizer import (
    ParamSpace,
    ParameterOptimizer,
    optimize_ma_strategy,
)


@pytest.fixture
def prices():
    import numpy as np
    return list(100 + np.cumsum(np.random.RandomState(0).normal(0.05, 2, 80)))


def _fake_objective(params):
    """构造随参数变化的伪 summary。"""
    score = params.get("fast", 5) * 0.1 - params.get("slow", 20) * 0.01
    return {
        "sharpe_ratio": score,
        "total_return": score * 0.1,
        "max_drawdown": -0.1,
    }


class TestScore:
    def test_objective_key_sharpe(self):
        opt = ParameterOptimizer(objective="sharpe")
        score = opt._score({"sharpe_ratio": 1.5, "max_drawdown": -0.1}, {})
        assert score == 1.5

    def test_mdd_constraint_penalty(self):
        opt = ParameterOptimizer(objective="sharpe", max_drawdown_constraint=0.20)
        # 回撤 30% 超限 → 负惩罚
        score = opt._score({"sharpe_ratio": 2.0, "max_drawdown": -0.30}, {})
        assert score < -100

    def test_no_penalty_within_constraint(self):
        opt = ParameterOptimizer(objective="sharpe", max_drawdown_constraint=0.20)
        score = opt._score({"sharpe_ratio": 2.0, "max_drawdown": -0.10}, {})
        assert score == 2.0


class TestGridSearch:
    def test_finds_best_params(self):
        opt = ParameterOptimizer(objective="sharpe", max_drawdown_constraint=0.10)
        result = opt.grid_search(
            _fake_objective,
            [ParamSpace(name="fast", low=1, high=3, is_int=True),
             ParamSpace(name="slow", low=10, high=12, is_int=True)],
        )
        assert result.method == "grid"
        assert result.best_params is not None
        # fast 越大越好，slow 越小越好 → 应选 fast=3, slow=10
        assert result.best_params["fast"] == 3
        assert result.best_params["slow"] == 10
        assert len(result.trials) == 9

    def test_empty_choices_handled(self):
        opt = ParameterOptimizer()
        result = opt.grid_search(_fake_objective, [])
        assert result.best_params == {}
        assert result.trials == []


class TestBayesian:
    def test_bayesian_returns_result(self, prices):
        """用真实 MA 策略与合成价格，验证贝叶斯能收敛。"""
        opt = ParameterOptimizer(objective="sharpe", max_drawdown_constraint=0.10)
        result = opt.bayesian_search(
            _fake_objective,
            [ParamSpace(name="fast", low=1, high=5, is_int=True),
             ParamSpace(name="slow", low=10, high=20, is_int=True)],
            n_trials=5, seed=42,
        )
        assert result.method == "bayesian"
        assert result.best_params is not None
        assert len(result.trials) >= 1
        assert "fast" in result.best_params


class TestOptimizeMA:
    def test_smoke_grid(self, prices):
        result = optimize_ma_strategy(prices, fast_range=(2, 3), slow_range=(10, 12),
                                      objective="sharpe", method="grid", n_trials=5)
        assert result.best_params is not None
        assert result.objective_score is not None

    def test_smoke_bayesian(self, prices):
        result = optimize_ma_strategy(prices, fast_range=(2, 4), slow_range=(10, 15),
                                      objective="total_return", method="bayesian",
                                      n_trials=4, seed=1)
        assert result.best_params is not None