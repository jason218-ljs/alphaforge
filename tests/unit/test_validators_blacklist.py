"""测试 validators 中的 ST / 退市 / 停牌识别。"""

from datetime import datetime

import pytest

from alphaforge.models.bar import Bar
from alphaforge.utils.validators import (
    check_blacklist,
    is_delisting_stock,
    is_st_stock,
    is_suspended,
    is_tradable,
)


class TestSTDetection:
    def test_normal_stock(self):
        assert is_st_stock("贵州茅台") is False

    def test_st_stock(self):
        assert is_st_stock("ST 金泰") is True

    def test_star_st_stock(self):
        assert is_st_stock("*ST 金泰") is True

    def test_s_st_stock(self):
        assert is_st_stock("S*ST 金泰") is True

    def test_empty_name(self):
        assert is_st_stock("") is False


class TestDelistingDetection:
    def test_normal_stock(self):
        assert is_delisting_stock("贵州茅台") is False

    def test_delisting_stock(self):
        assert is_delisting_stock("退市金泰") is True

    def test_delisting_prefix(self):
        assert is_delisting_stock("金泰退") is True


class TestTradable:
    def test_normal_is_tradable(self):
        assert is_tradable("贵州茅台") is True

    def test_st_not_tradable(self):
        assert is_tradable("ST 金泰") is False

    def test_delisting_not_tradable(self):
        assert is_tradable("退市金泰") is False


class TestSuspended:
    def test_normal_bar_not_suspended(self):
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=105, low=99, close=102, volume=1000)
        assert is_suspended(bar) is False

    def test_zero_volume_with_unchanged_price(self):
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=100, low=100, close=100, volume=0,
                  pre_close=100)
        assert is_suspended(bar) is True

    def test_zero_volume_with_changed_price_not_suspended(self):
        # 成交量为 0 但价格变了，不算停牌（可能是涨跌停封死）
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=110, high=110, low=110, close=110, volume=0,
                  pre_close=100)
        assert is_suspended(bar) is False

    def test_none_bar(self):
        assert is_suspended(None) is False


class TestCheckBlacklist:
    def test_st_hit(self):
        reason = check_blacklist(name="ST 金泰", code="600519.SH")
        assert reason is not None
        assert "ST" in reason

    def test_delisting_hit(self):
        reason = check_blacklist(name="退市金泰", code="600519.SH")
        assert reason is not None
        assert "退市" in reason

    def test_suspended_hit(self):
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=100, low=100, close=100, volume=0,
                  pre_close=100)
        reason = check_blacklist(name="贵州茅台", code="600519.SH", bar=bar)
        assert reason is not None
        assert "停牌" in reason

    def test_clean_stock_passes(self):
        bar = Bar(date=datetime(2026, 8, 3), code="600519.SH",
                  open=100, high=105, low=99, close=102, volume=1000)
        reason = check_blacklist(name="贵州茅台", code="600519.SH", bar=bar)
        assert reason is None
