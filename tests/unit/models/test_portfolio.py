"""测试 Position / Portfolio 模型。"""

from datetime import datetime

from alphaforge.models.fill import Fill
from alphaforge.models.portfolio import Portfolio


class TestPositionApplyBuy:
    def test_single_buy(self):
        p = Portfolio(cash=1_000_000)
        pos = p.ensure_position("600519.SH")
        pos.apply_buy(qty=100, amount_inc_fee=100 * 50 + 5, fee=5)
        assert pos.qty == 100
        assert abs(pos.avg_cost - 50.05) < 1e-6  # (0 + 5005) / 100
        # apply_buy 只更新 Position 状态，不扣 Portfolio.cash（由 apply_fill 负责）

    def test_multiple_buys_weighted(self):
        p = Portfolio(cash=1_000_000)
        pos = p.ensure_position("600519.SH")
        pos.apply_buy(100, 100 * 50, 0)        # avg 50
        pos.apply_buy(100, 100 * 60, 0)        # avg (5000+6000)/200 = 55
        assert pos.qty == 200
        assert abs(pos.avg_cost - 55.0) < 1e-6


class TestPositionApplySell:
    def test_sell_realized_pnl(self):
        p = Portfolio(cash=1_000_000)
        pos = p.ensure_position("600519.SH")
        pos.apply_buy(100, 100 * 50, 0)        # avg 50
        realized = pos.apply_sell(50, 60, 0)   # 卖 50 股 @60
        assert abs(realized - 500.0) < 1e-6    # (60-50)*50
        assert pos.qty == 50
        assert abs(pos.realized_pnl - 500.0) < 1e-6


class TestPortfolioApplyFill:
    def test_buy_fill_updates_cash(self):
        p = Portfolio(cash=1_000_000)
        fill = Fill(time=datetime(2026, 7, 31), code="600519.SH", side="BUY",
                    qty=100, price=50, amount=5000, total_fee=5)
        p.apply_fill(fill)
        assert p.cash == 1_000_000 - 5005
        assert p.positions["600519.SH"].qty == 100

    def test_sell_fill_updates_cash(self):
        p = Portfolio(cash=1_000_000)
        # 先买
        p.apply_fill(Fill(time=datetime(2026, 7, 30), code="600519.SH", side="BUY",
                          qty=100, price=50, amount=5000, total_fee=0))
        # 再卖
        p.apply_fill(Fill(time=datetime(2026, 7, 31), code="600519.SH", side="SELL",
                          qty=50, price=60, amount=3000, total_fee=3))
        assert p.cash == 1_000_000 - 5000 + 3000 - 3
        assert p.positions["600519.SH"].qty == 50


class TestPortfolioNavAndSnapshot:
    def test_nav_calc(self):
        p = Portfolio(cash=500_000)
        pos = p.ensure_position("600519.SH")
        pos.apply_buy(100, 100 * 50, 0)
        pos.current_price = 55
        # nav = cash + 100*55 = 500000 + 5500
        assert p.nav == 505_500

    def test_snapshot_records_history(self):
        p = Portfolio(cash=1_000_000)
        snap = p.snapshot(datetime(2026, 7, 27))
        assert len(p.history) == 1
        assert snap.nav == 1_000_000
        assert snap.daily_pnl == 0.0  # 首日无前日

    def test_snapshot_daily_return(self):
        p = Portfolio(cash=1_000_000)
        p.snapshot(datetime(2026, 7, 27))
        # 次日现金不变
        snap2 = p.snapshot(datetime(2026, 7, 28))
        assert snap2.daily_return_pct == 0.0