"""日期与交易日历工具。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional


def parse_sheet_date(sheet_name: str, base_year: int = 2026) -> Optional[datetime]:
    """解析 "7月31日" / "7月31日" sheet 名为 datetime。

    Args:
        sheet_name: Sheet 名。
        base_year: 基准年（Excel 未含年份信息时用此值）。

    Returns:
        datetime 或 None（无法解析时）。
    """
    m = re.search(r"(\d{1,2})月(\d{1,2})日", str(sheet_name))
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        return datetime(base_year, month, day)
    except ValueError:
        return None


def to_date(d: datetime | date) -> date:
    """datetime/date → date。"""
    return d.date() if isinstance(d, datetime) else d


def date_range(start: date, end: date) -> list:
    """[start, end] 闭区间日期列表（含非交易日）。"""
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def format_date(d: datetime | date, fmt: str = "%Y-%m-%d") -> str:
    """格式化日期。"""
    return to_date(d).strftime(fmt)


def format_date_compact(d: datetime | date) -> str:
    """YYYYMMDD 紧凑格式（用于文件名）。"""
    return to_date(d).strftime("%Y%m%d")