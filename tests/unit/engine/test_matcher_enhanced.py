"""测试 Matcher 增强功能：涨停封死、跌停封死、停牌、成交量滑点。"""

from datetime import datetime

import pytest

from alphaforge.engine.market_rules import MarketRules
from alphaforge.engine.matcher import Matcher
from alphaforge.models.bar import Bar
from alphaforge.models.fill import Fill
from alphaforge.models.order import Order
from alphaforge.models.portfolio import Portfolio


@pytest.fixture
def matcher():
    return Matcher(MarketRules())


@pytest.fixture
def portfolio():
    return Portfolio(cash=1_000_000)


class TestSuspendedRejection:
    def test_reject_buy_on_suspended(self, matcher, portfolio):
        # 停牌：成交量 0，价格未变
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=100, low=100, close=100, volume=0,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="BUY", qty=100, price=100.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "REJECTED"
        assert "停牌" in fill.reject_reason

    def test_reject_sell_on_suspended(self, matcher, portfolio):
        # 先买入并解锁 T+1
        portfolio.apply_fill(Fill(time=datetime(2026, 8, 2), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        portfolio.reset_day()
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=100, low=100, close=100, volume=0,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="SELL", qty=100, price=100.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "REJECTED"
        assert "停牌" in fill.reject_reason


class TestLimitUpLocked:
    def test_reject_buy_on_limit_up_locked(self, matcher, portfolio):
        # 主板 ±10%，prev_close=100，涨停 110
        # 一字板：开=高=低=110
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=110, high=110, low=110, close=110, volume=1000,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="BUY", qty=100, price=110.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "REJECTED"
        assert "涨停封死" in fill.reject_reason

    def test_allow_buy_when_not_locked(self, matcher, portfolio):
        # 涨停但非一字板（开盘没封住），允许买入
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=105, high=110, low=104, close=110, volume=1000,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="BUY", qty=100, price=110.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "FILLED"


class TestLimitDownLocked:
    def test_reject_sell_on_limit_down_locked(self, matcher, portfolio):
        # 持仓解锁
        portfolio.apply_fill(Fill(time=datetime(2026, 8, 2), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        portfolio.reset_day()
        # 跌停一字板：开=高=低=90
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=90, high=90, low=90, close=90, volume=1000,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="SELL", qty=100, price=90.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "REJECTED"
        assert "跌停封死" in fill.reject_reason


class TestVolumeSlippage:
    def test_large_order_gets_impact(self, matcher, portfolio):
        # 当日成交量 1000 股，订单 5000 股（5 倍 cap）→ 应有冲击
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=105, low=99, close=102, volume=1000,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="BUY", qty=5000, price=100.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        # 现金 100 万，5000 股 × 100 = 50 万 + 费用，资金够
        assert fill.status == "FILLED"
        # 含冲击的成交价应高于纯滑点价
        pure_slipped = matcher.rules.apply_slippage(100.0, "BUY")
        assert fill.price > pure_slipped

    def test_small_order_no_impact(self, matcher, portfolio):
        # 订单小于 cap（10% of volume = 100），无冲击
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=105, low=99, close=102, volume=10000,
                  pre_close=100)
        order = Order(time=datetime(2026, 8, 3), code="600519.SH",
                      side="BUY", qty=100, price=100.0)
        fill = matcher.match(order, portfolio, prev_close=100.0, bar=bar)
        assert fill.status == "FILLED"
        pure_slipped = matcher.rules.apply_slippage(100.0, "BUY")
        assert abs(fill.price - pure_slipped) < 1e-6
