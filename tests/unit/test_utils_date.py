"""测试 date 工具。"""

from datetime import datetime

from alphaforge.utils.date import (
    format_date,
    format_date_compact,
    parse_sheet_date,
    to_date,
)


class TestParseSheetDate:
    def test_chinese_month_day(self):
        assert parse_sheet_date("7月31日", 2026) == datetime(2026, 7, 31)

    def test_with_extra_text(self):
        assert parse_sheet_date("  07月05日 报表", 2026) == datetime(2026, 7, 5)

    def test_invalid_returns_none(self):
        assert parse_sheet_date("无效", 2026) is None

    def test_invalid_date_returns_none(self):
        assert parse_sheet_date("13月40日", 2026) is None


class TestFormatDate:
    def test_format_date(self):
        assert format_date(datetime(2026, 7, 31)) == "2026-07-31"

    def test_compact(self):
        assert format_date_compact(datetime(2026, 7, 31)) == "20260731"

    def test_to_date_from_datetime(self):
        from datetime import date

        assert to_date(datetime(2026, 7, 31)) == date(2026, 7, 31)