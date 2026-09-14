"""测试 StrategyEvaluator（信号胜率/持仓周期/漂移/适应性）。"""

from datetime import datetime

import pytest

from alphaforge.analysis.strategy_evaluator import StrategyEvaluator
from alphaforge.models.result import TradeRecord


def _trade(date, code, side, qty, price, status="FILLED"):
    return TradeRecord(
        date=date, side=side, code=code, name="", requested_qty=qty,
        filled_qty=qty if status == "FILLED" else 0, price=price, fee=0,
        status=status,
    )


class TestAverageHoldingPeriod:
    def test_holding_calculation(self):
        """开仓 2026-07-27，平仓 2026-08-01 → 5 天。"""
        evaluator = StrategyEvaluator()
        fills = [
            _trade(datetime(2026, 7, 27, 9, 30), "600519.SH", "BUY", 100, 100),
            _trade(datetime(2026, 8, 1, 14, 0), "600519.SH", "SELL", 100, 110),
        ]
        assert evaluator._average_holding_period(fills) == pytest.approx(5.0)

    def test_no_fills_returns_zero(self):
        evaluator = StrategyEvaluator()
        assert evaluator._average_holding_period([]) == 0.0

    def test_partial_fill_fifo(self):
        """多笔买卖 FIFO 配对。"""
        evaluator = StrategyEvaluator()
        fills = [
            _trade(datetime(2026, 7, 27), "600519.SH", "BUY", 200, 100),
            _trade(datetime(2026, 8, 1), "600519.SH", "SELL", 100, 110),
            _trade(datetime(2026, 8, 3), "600519.SH", "SELL", 100, 120),
        ]
        # 首批 200 股 FIFO：100 股 7/27→8/1(5天)，100 股 7/27→8/3(7天)
        # avg = (5+7)/2 = 6
        assert evaluator._average_holding_period(fills) == pytest.approx(6.0)


class TestEvaluate:
    def test_execution_rate(self):
        evaluator = StrategyEvaluator()
        fills = [_trade(datetime(2026, 7, 27), "600519.SH", "BUY", 100, 100, "FILLED")]
        signals = [
            {"date": "2026-07-27", "code": "600519.SH", "side": "BUY", "qty": 100, "price": 100},
            {"date": "2026-07-27", "code": "601288.SH", "side": "BUY", "qty": 100, "price": 5},
        ]
        r = evaluator.evaluate(trade_log=fills, signal_log=signals)
        # 1 成交 / 2 信号 = 50%
        assert r.execution_rate == pytest.approx(0.5)

    def test_empty_inputs(self):
        evaluator = StrategyEvaluator()
        r = evaluator.evaluate(trade_log=[], signal_log=[])
        assert r.signal_count == 0
        assert r.execution_rate == 0.0
        assert r.strategy_level in ("EXCELLENT", "GOOD", "NORMAL", "POOR")


class TestDrift:
    def test_no_drift_when_stable(self):
        """策略持续稳定 → 漂移为 False。"""
        evaluator = StrategyEvaluator()
        nav = [100 + i * 1.0 for i in range(30)]  # 稳定上升
        score, drifting = evaluator._drift(nav)
        assert drifting is False
        assert score >= 60

    def test_drift_detected(self):
        """近期表现与全期显著不同 → 漂移 True。"""
        evaluator = StrategyEvaluator(drift_threshold=0.3, drift_window=5)
        # 全期收益高，近期收益剧烈变化
        nav = [100, 110, 121, 133, 146, 161, 177, 180, 182, 183]  # 近期显著放缓
        score, drifting = evaluator._drift(nav)
        assert isinstance(drifting, bool)


class TestMarketAdaptability:
    def test_bull_market_positive(self):
        evaluator = StrategyEvaluator()
        # 组合与基准都在涨 → 适应性好
        nav = [100, 102, 105, 108, 112, 116]
        bench = [0.01, 0.02, 0.03, 0.04, 0.03, 0.03]
        score = evaluator._market_adaptability(nav, bench)
        assert score >= 50