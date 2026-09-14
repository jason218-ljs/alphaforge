"""测试 Matcher（撮合器）。"""

from datetime import datetime

import pytest

from alphaforge.engine.market_rules import MarketRules
from alphaforge.engine.matcher import Matcher
from alphaforge.models.order import Order
from alphaforge.models.portfolio import Portfolio


@pytest.fixture
def matcher():
    return Matcher(MarketRules())


@pytest.fixture
def portfolio():
    return Portfolio(cash=1_000_000)


class TestBasicValidation:
    def test_reject_non_positive_qty(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=0, price=100)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"
        assert "数量非正" in fill.reject_reason

    def test_reject_non_100_lot_buy(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=150, price=100)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"
        assert "100 整数倍" in fill.reject_reason

    def test_reject_non_positive_price(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=100, price=0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"


class TestSlippage:
    def test_buy_price_includes_slippage(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "FILLED"
        assert fill.price > 100.0  # 买入加价

    def test_sell_price_includes_slippage(self, matcher, portfolio):
        from alphaforge.models.fill import Fill
        portfolio.apply_fill(Fill(time=datetime(2026, 7, 30), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        # T+1：次日卖出
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                      qty=100, price=100.0)
        portfolio.reset_day()  # 新交易日，T+1 解锁
        fill = matcher.match(order, portfolio)
        assert fill.status == "FILLED"
        assert fill.price < 100.0  # 卖出降价


class TestFees:
    def test_buy_fee_no_stamp_tax(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.stamp_tax == 0.0  # 买入无印花税
        assert fill.commission >= 5.0  # 最低佣金
        assert fill.transfer_fee > 0

    def test_sell_fee_has_stamp_tax(self, matcher, portfolio):
        from alphaforge.models.fill import Fill
        portfolio.apply_fill(Fill(time=datetime(2026, 7, 30), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        portfolio.reset_day()
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.stamp_tax > 0  # 卖出有印花税


class TestTPlus1:
    def test_cannot_sell_same_day_buy(self, matcher, portfolio):
        from alphaforge.models.fill import Fill
        # 当日买入
        portfolio.apply_fill(Fill(time=datetime(2026, 7, 31, 10), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        # 当日尝试卖出 → 拒单
        order = Order(time=datetime(2026, 7, 31, 14), code="600519.SH", side="SELL",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"
        assert "T+1" in fill.reject_reason

    def test_can_sell_next_day(self, matcher, portfolio):
        from alphaforge.models.fill import Fill
        portfolio.apply_fill(Fill(time=datetime(2026, 7, 30), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        portfolio.reset_day()  # 次日
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "FILLED"


class TestPriceLimit:
    def test_reject_buy_above_limit_up(self, matcher, portfolio):
        # 主板 ±10%，prev_close=100，涨停 110
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=100, price=115.0)
        fill = matcher.match(order, portfolio, prev_close=100.0)
        assert fill.status == "REJECTED"
        assert "涨停" in fill.reject_reason

    def test_reject_sell_below_limit_down(self, matcher, portfolio):
        from alphaforge.models.fill import Fill
        portfolio.apply_fill(Fill(time=datetime(2026, 7, 30), code="600519.SH",
                                  side="BUY", qty=100, price=100, amount=10000, total_fee=5))
        portfolio.reset_day()
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                      qty=100, price=85.0)
        fill = matcher.match(order, portfolio, prev_close=100.0)
        assert fill.status == "REJECTED"
        assert "跌停" in fill.reject_reason

    def test_allow_within_limit(self, matcher, portfolio):
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=100, price=105.0)
        fill = matcher.match(order, portfolio, prev_close=100.0)
        assert fill.status == "FILLED"


class TestCashValidation:
    def test_reject_insufficient_cash(self, matcher, portfolio):
        # 现金 100 万，买 1 万股 × 200 元 = 200 万 → 资金不足
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                      qty=10000, price=200.0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"
        assert "资金不足" in fill.reject_reason


class TestHoldingValidation:
    def test_reject_sell_without_holding(self, matcher, portfolio):
        portfolio.reset_day()
        order = Order(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                      qty=100, price=100.0)
        fill = matcher.match(order, portfolio)
        assert fill.status == "REJECTED"
        assert "持仓不足" in fill.reject_reason