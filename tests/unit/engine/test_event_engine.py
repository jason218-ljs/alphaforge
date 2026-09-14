"""测试 EventEngine 事件驱动回测。"""

from datetime import datetime
from typing import List

import pytest

from alphaforge.engine.event_engine import EventEngine
from alphaforge.engine.market_rules import MarketRules
from alphaforge.models.order import Order
from alphaforge.strategy.base import Context, StrategyBase


class BuyAndHoldStrategy(StrategyBase):
    """简单买入持有策略：首日买入，之后不动。"""

    name = "buy_and_hold"

    def __init__(self, code: str = "600519.SH", qty: int = 100):
        self.code = code
        self.qty = qty
        self._bought = False

    def on_bar(self, ctx: Context) -> List[Order]:
        if self._bought:
            return []
        price = ctx.quotes.get(self.code)
        if price is None or price <= 0:
            return []
        self._bought = True
        return [Order(time=ctx.date, code=self.code, side="BUY",
                      qty=self.qty, price=price, reason="首日买入")]


def _make_bars(prices: List[float], code: str = "600519.SH"):
    """构造 bars，每个 bar 一个收盘价。"""
    bars = []
    prev_close = {}
    for i, p in enumerate(prices):
        date = datetime(2026, 7, 27 + i)
        bars.append({
            "date": date,
            "quotes": {code: p},
            "prev_close": dict(prev_close),
        })
        prev_close = {code: p}
    return bars


@pytest.fixture
def engine():
    # 测试用：无滑点、无 T+1（便于简单断言）
    return EventEngine(MarketRules(slippage_pct=0, slippage_fixed=0, enable_t_plus_1=False),
                       initial_capital=1_000_000)


class TestEventEngine:
    def test_buy_and_hold_nav(self, engine):
        bars = _make_bars([100, 105, 110])
        result = engine.run(BuyAndHoldStrategy(), bars)
        # 买 100 股 @100（含佣金 5 元）：现金 = 1,000,000 - 10,000 - 5 = 989,995
        # 末日持仓市值 = 100 × 110 = 11,000
        # NAV = 989,995 + 11,000 = 1,000,995
        assert abs(result.summary.final_nav - 1_000_995) < 1.0
        assert result.summary.total_return > 0
        assert result.summary.total_trades == 1

    def test_equity_curve_length_matches_bars(self, engine):
        bars = _make_bars([100, 105, 110, 108, 112])
        result = engine.run(BuyAndHoldStrategy(), bars)
        assert len(result.equity_curve) == 5

    def test_trade_log_records_filled(self, engine):
        bars = _make_bars([100, 105])
        result = engine.run(BuyAndHoldStrategy(), bars)
        assert len(result.trade_log) == 1
        assert result.trade_log[0].status == "FILLED"
        assert result.trade_log[0].side == "BUY"

    def test_no_trades_when_no_signal(self, engine):
        class EmptyStrategy(StrategyBase):
            name = "empty"
            def on_bar(self, ctx: Context) -> List[Order]:
                return []
        bars = _make_bars([100, 105, 110])
        result = engine.run(EmptyStrategy(), bars)
        assert result.summary.total_trades == 0
        assert result.summary.final_nav == 1_000_000  # 纯现金

    def test_t_plus_1_prevents_same_day_sell(self):
        """T+1 开启时，当日买入 100 股 + 当日卖出 200 股会被拒（可卖仅 100）。"""
        class DayTradeStrategy(StrategyBase):
            name = "day_trade"
            def __init__(self):
                self._day = 0
            def on_bar(self, ctx: Context) -> List[Order]:
                self._day += 1
                price = ctx.quotes.get("600519.SH")
                if not price:
                    return []
                if self._day == 1:
                    return [Order(time=ctx.date, code="600519.SH", side="BUY",
                                  qty=100, price=price, reason="买")]
                elif self._day == 2:
                    # 当日买入 100 + 当日卖出 200 → 卖出 200 > 可卖 100（T+1）
                    return [
                        Order(time=ctx.date, code="600519.SH", side="BUY",
                              qty=100, price=price, reason="加仓"),
                        Order(time=ctx.date, code="600519.SH", side="SELL",
                              qty=200, price=price, reason="T+1测试卖"),
                    ]
                return []
        engine = EventEngine(MarketRules(slippage_pct=0, slippage_fixed=0, enable_t_plus_1=True),
                             initial_capital=1_000_000)
        bars = _make_bars([100, 105])
        result = engine.run(DayTradeStrategy(), bars)
        # Day 2 的卖出应被拒（T+1：可卖 100 < 申报 200）
        rejected = [t for t in result.trade_log if t.status == "REJECTED"]
        assert len(rejected) >= 1
        assert any("T+1" in t.reject_reason for t in rejected)

    def test_config_snapshot_in_result(self, engine):
        bars = _make_bars([100, 105])
        result = engine.run(BuyAndHoldStrategy(), bars)
        assert "stamp_tax_rate" in result.config_snapshot
        assert "enable_t_plus_1" in result.config_snapshot