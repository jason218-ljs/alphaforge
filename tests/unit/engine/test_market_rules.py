"""测试 MarketRules。"""

import pytest

from alphaforge.engine.market_rules import MarketRules


@pytest.fixture
def rules():
    return MarketRules()


class TestBoardAndLimit:
    def test_main_board(self, rules):
        assert rules.board_of("600519.SH") == "MAIN"
        assert rules.limit_ratio("600519.SH") == 0.10

    def test_gem_board(self, rules):
        assert rules.board_of("300750.SZ") == "GEM"
        assert rules.limit_ratio("300750.SZ") == 0.20

    def test_star_board(self, rules):
        assert rules.board_of("688981.SH") == "STAR"
        assert rules.limit_ratio("688981.SH") == 0.20

    def test_price_limit_bounds_main(self, rules):
        lower, upper = rules.price_limit_bounds("600519.SH", 100.0)
        assert abs(lower - 90.0) < 0.01
        assert abs(upper - 110.0) < 0.01

    def test_price_limit_bounds_gem(self, rules):
        lower, upper = rules.price_limit_bounds("300750.SZ", 100.0)
        assert abs(lower - 80.0) < 0.01
        assert abs(upper - 120.0) < 0.01

    def test_zero_prev_close(self, rules):
        lower, upper = rules.price_limit_bounds("600519.SH", 0.0)
        assert lower == 0.0
        assert upper == float("inf")


class TestFees:
    def test_commission_min(self, rules):
        # 小额交易触发最低佣金 5 元
        assert rules.calc_commission(1000) == 5.0

    def test_commission_normal(self, rules):
        # 10 万 × 0.025% = 25 元
        assert abs(rules.calc_commission(100_000) - 25.0) < 1e-6

    def test_stamp_tax_only_on_sell(self, rules):
        assert rules.calc_stamp_tax(10_000, "SELL") == 10.0  # 0.1%
        assert rules.calc_stamp_tax(10_000, "BUY") == 0.0

    def test_transfer_fee(self, rules):
        assert abs(rules.calc_transfer_fee(10_000) - 0.2) < 1e-6  # 0.002%

    def test_total_fee_buy(self, rules):
        # 买入：佣金 + 过户费（无印花税）
        fee = rules.calc_total_fee(10_000, "BUY")
        assert fee == pytest.approx(5.0 + 0.2)

    def test_total_fee_sell(self, rules):
        # 卖出：佣金 + 印花税 + 过户费
        fee = rules.calc_total_fee(10_000, "SELL")
        assert fee == pytest.approx(5.0 + 10.0 + 0.2)


class TestSlippage:
    def test_buy_slippage_increases_price(self, rules):
        p = rules.apply_slippage(100.0, "BUY")
        assert p > 100.0

    def test_sell_slippage_decreases_price(self, rules):
        p = rules.apply_slippage(100.0, "SELL")
        assert p < 100.0

    def test_zero_price(self, rules):
        assert rules.apply_slippage(0.0, "BUY") == 0.0


class TestTPlus1:
    def test_can_sell_when_no_today_buy(self, rules):
        assert rules.can_sell_today("600519.SH", {}) is True

    def test_cannot_sell_today_buy(self, rules):
        assert rules.can_sell_today("600519.SH", {"600519.SH": 100}) is False

    def test_disabled_t_plus_1(self):
        r = MarketRules(enable_t_plus_1=False)
        assert r.can_sell_today("600519.SH", {"600519.SH": 100}) is True


class TestSnapshot:
    def test_snapshot_contains_all_fields(self, rules):
        snap = rules.snapshot()
        for key in ("stamp_tax_rate", "commission_rate", "min_commission",
                    "transfer_fee_rate", "slippage_pct", "slippage_fixed",
                    "enable_t_plus_1", "price_limit"):
            assert key in snap