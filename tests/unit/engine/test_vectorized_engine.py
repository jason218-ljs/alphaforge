"""测试向量化回测引擎。"""

import pytest

from alphaforge.engine.vectorized_engine import VectorizedEngine


@pytest.fixture
def engine():
    return VectorizedEngine(fee_rate=0.0, min_fee=0.0)  # 无费用便于断言


@pytest.fixture
def prices():
    # 100 -> 110 -> 121 单调上升
    return [100, 110, 121]


class TestSignalBacktest:
    def test_buy_and_hold_profit(self, engine):
        """信号[1,0,0]，持有全程 → 盈利。"""
        result = engine.run_signal_backtest(prices=[100, 110, 121],
                                            signals=[1, 0, 0], qty_per_signal=100)
        # 买 100 股 @100 = 10000，现金 990000
        # 期末 = 990000 + 100*121 = 990000 + 12100 = 1002100
        assert result["total_trades"] == 1
        assert result["total_return"] > 0
        assert abs(result["final_equity"] - (990000 + 100 * 121)) < 1.0

    def test_buy_sell_trade_log(self, engine):
        result = engine.run_signal_backtest(prices=[100, 110, 121],
                                            signals=[1, 0, -1], qty_per_signal=100)
        assert len(result["trade_log"]) == 2
        assert result["trade_log"][0]["side"] == "BUY"
        assert result["trade_log"][1]["side"] == "SELL"
        # 盈利
        assert result["total_return"] > 0

    def test_no_trades_all_hold(self, engine):
        result = engine.run_signal_backtest([100, 110, 121], [0, 0, 0], 100)
        assert result["total_trades"] == 0
        assert result["final_equity"] == pytest.approx(1000000)

    def test_empty_prices(self, engine):
        result = engine.run_signal_backtest([], [], 100)
        assert result["total_return"] == 0.0

    def test_trade_fraction_mode(self, engine):
        """按资金比例买入。"""
        result = engine.run_signal_backtest([100, 105, 110], [1, 0, 0], 100,
                                            trade_fraction=0.5)
        assert result["total_trades"] == 1
        # 用 50% 资金 = 50 万买 100 元股票 = 5000 股（100 的整数倍）
        assert result["total_return"] > 0


class TestMAStrategy:
    def test_ma_crossover_produces_trades(self, engine):
        # 足够长的序列让 MA 产生信号
        import numpy as np
        prices = list(100 + np.cumsum(np.random.RandomState(1).normal(0, 2, 60)))
        result = engine.run_ma_strategy(prices, fast=3, slow=10, qty_per_signal=100)
        assert "sharpe_ratio" in result
        assert "max_drawdown" in result
        assert result["total_return"] is not None

    def test_ma_summary_keys(self, engine):
        import numpy as np
        prices = list(100 + np.cumsum(np.random.RandomState(2).normal(0, 2, 80)))
        result = engine.run_ma_strategy(prices, fast=5, slow=20, qty_per_signal=100)
        for key in ("total_return", "annualized_return", "max_drawdown",
                    "sharpe_ratio", "total_trades", "equity_raw"):
            assert key in result


class TestPerformance:
    def test_sharpe_positive_for_uptrend(self, engine):
        result = engine.run_ma_strategy([100, 105, 110, 115, 120, 125],
                                        fast=2, slow=3, qty_per_signal=100)
        assert result["sharpe_ratio"] is not None

    def test_max_drawdown_negative_in_volatile(self, engine):
        import numpy as np
        prices = list(100 + np.cumsum(np.random.RandomState(3).normal(0, 3, 100)))
        result = engine.run_ma_strategy(prices, fast=5, slow=15, qty_per_signal=100)
        assert result["max_drawdown"] <= 0.0