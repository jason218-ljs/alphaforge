"""测试内置策略与 主流水线Replay。"""

from datetime import datetime
from typing import List

import pytest

from alphaforge.engine.market_rules import MarketRules
from alphaforge.models.order import Order
from alphaforge.strategy.base import Context
from alphaforge.strategy.builtins import (
    BollingerStrategy,
    MACrossoverStrategy,
    RSIStrategy,
)
from alphaforge.strategy.external_scorer_replay import (
    主流水线ReplayStrategy,
    load_trades_from_records,
)


def _ctx(date, quotes, portfolio=None):
    return Context(
        date=date,
        quotes=quotes,
        portfolio=portfolio,
        rules=MarketRules(),
    )


class TestMACrossover:
    def test_golden_cross_buy(self):
        s = MACrossoverStrategy(fast=2, slow=3, code="600519.SH", trade_qty=100)
        date = datetime(2026, 7, 27)
        # 模拟从下跌转上涨：价格序列触发金叉
        prices = [100, 99, 98, 97, 101, 105]
        outputs = []
        for p in prices:
            orders = s.on_bar(_ctx(date, {"600519.SH": p}))
            if orders:
                outputs.extend(orders)
        # 金叉应触发至少一次买入
        buys = [o for o in outputs if o.side == "BUY"]
        assert len(buys) >= 1

    def test_no_trade_before_enough_data(self):
        s = MACrossoverStrategy(fast=2, slow=3, code="600519.SH")
        # 只有 1 天数据，slow=3 不足 → 无订单
        orders = s.on_bar(_ctx(datetime(2026, 7, 27), {"600519.SH": 100}))
        assert orders == []


class TestRSI:
    def test_oversold_triggers_buy(self):
        s = RSIStrategy(period=5, oversold=30, code="600519.SH", trade_qty=100)
        # 制造超卖：连续下跌
        prices = [100, 95, 90, 85, 82, 80]
        outputs = []
        for p in prices:
            orders = s.on_bar(_ctx(datetime(2026, 7, 27), {"600519.SH": p}))
            if orders:
                outputs.extend(orders)
        buys = [o for o in outputs if o.side == "BUY"]
        assert len(buys) >= 1


class TestBollinger:
    def test_lower_band_triggers_buy(self):
        s = BollingerStrategy(period=5, num_std=1.0, code="600519.SH", trade_qty=100)
        # 先横向震荡再单日暴跌 → 触发下轨买入
        prices = [100, 100, 100, 100, 100, 80]
        outputs = []
        for p in prices:
            orders = s.on_bar(_ctx(datetime(2026, 7, 27), {"600519.SH": p}))
            if orders:
                outputs.extend(orders)
        buys = [o for o in outputs if o.side == "BUY"]
        assert len(buys) >= 1


class Test主流水线Replay:
    def test_replays_trades_by_date(self):
        d1 = datetime(2026, 7, 27)
        d2 = datetime(2026, 7, 28)
        trades = {
            d1: [{"code": "600519.SH", "side": "BUY", "qty": 100, "price": 100.0,
                  "trader": "A", "reason": "x"}],
            d2: [{"code": "601288.SH", "side": "SELL", "qty": 200, "price": 5.0,
                  "trader": "B", "reason": "y"}],
        }
        s = 主流水线ReplayStrategy(trades)
        o1 = s.on_bar(_ctx(d1, {}))
        assert len(o1) == 1
        assert o1[0].code == "600519.SH"
        assert o1[0].side == "BUY"

        o2 = s.on_bar(_ctx(d2, {}))
        assert len(o2) == 1
        assert o2[0].code == "601288.SH"

        # 无交易日期返回空
        o3 = s.on_bar(_ctx(datetime(2026, 7, 29), {}))
        assert o3 == []

    def test_load_trades_from_records(self):
        records = [
            {"date": "2026-07-27T00:00:00",
             "trades": [{"code": "600519.SH", "side": "BUY", "qty": 100, "price": 100}]},
            {"date": "2026-07-28T00:00:00",
             "trades": [{"code": "601288.SH", "side": "BUY", "qty": 100, "price": 5}]},
        ]
        trades_by_date = load_trades_from_records(records)
        assert len(trades_by_date) == 2
        dates = sorted(trades_by_date.keys())
        assert dates[0].day == 27
        assert dates[1].day == 28