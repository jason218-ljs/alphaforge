"""校验工具：股票代码、数量、价格、特殊标记。"""

from __future__ import annotations

import re
from typing import Literal, Optional

# 板块识别：前缀 → 板块
_PREFIX_TO_BOARD = {
    "60": "MAIN",   # 沪市主板
    "00": "MAIN",   # 深市主板
    "30": "GEM",    # 创业板
    "68": "STAR",   # 科创板
    "8": "BEI",     # 北交所
    "4": "BEI",     # 北交所
    "9": "BEI",     # B 股（历史）
}

CODE_PATTERN = re.compile(r"^\d{6}(\.(SH|SZ|BJ))?$")


def normalize_code(code: str) -> str:
    """规范化股票代码为 6 位 + 后缀。

    "600519" → "600519.SH"
    "000001" → "000001.SZ"
    "300750.SZ" → "300750.SZ"（不变）
    "2558" → "002558.SZ"（4-5 位纯数字补前导 0 到 6 位，A 股代码均为 6 位）
    """
    if not code:
        return code
    code = str(code).strip().upper()
    if "." in code:
        return code
    pure = code.lstrip("'").strip()
    if not pure.isdigit():
        return code  # 非纯数字，原样返回
    # A 股代码均为 6 位；短码补前导 0
    if len(pure) < 6:
        pure = pure.zfill(6)
    if len(pure) != 6:
        return code  # 仍非 6 位，原样返回
    suffix = _suffix_for(pure)
    return f"{pure}.{suffix}"


def _suffix_for(pure6: str) -> Literal["SH", "SZ", "BJ"]:
    """根据前缀返回交易所后缀。"""
    if pure6.startswith(("60", "68", "9")):
        return "SH"
    if pure6.startswith(("00", "30")):
        return "SZ"
    if pure6.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def get_board(code: str) -> str:
    """根据代码识别板块：MAIN / GEM / STAR / BEI。"""
    pure = code.split(".")[0]
    for prefix, board in _PREFIX_TO_BOARD.items():
        if pure.startswith(prefix):
            return board
    return "MAIN"


def is_valid_code(code: str) -> bool:
    """校验股票代码格式。"""
    return bool(CODE_PATTERN.match(str(code).strip()))


def is_valid_qty(qty: int) -> bool:
    """校验数量（正整数，A 股 100 股整手买入，卖出可不足 100）。"""
    return isinstance(qty, int) and qty > 0


def is_valid_price(price: float) -> bool:
    """校验价格（正数）。"""
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


# ---------- 特殊标记识别（ST / 退市 / 停牌） ----------

# ST 关键词（含 *ST、S*ST 等）
_ST_PATTERN = re.compile(r"(\*?ST|S\*?ST|退市)", re.IGNORECASE)


def is_st_stock(name: str) -> bool:
    """判断是否为 ST 股（含 *ST、S*ST）。

    Args:
        name: 股票名称，如 "*ST 金泰" / "贵州茅台"。

    Returns:
        True 表示是 ST 股。
    """
    if not name:
        return False
    return bool(_ST_PATTERN.search(name))


def is_delisting_stock(name: str) -> bool:
    """判断是否为退市整理期股票。"""
    if not name:
        return False
    return "退" in name or "退市" in name


def is_tradable(name: str, code: str = "") -> bool:
    """判断是否可交易（非 ST、非退市）。"""
    if is_st_stock(name) or is_delisting_stock(name):
        return False
    return True


def is_suspended(bar) -> bool:
    """判断某日是否停牌（成交量为 0 且开盘价=收盘价=前收）。

    Args:
        bar: Bar 对象，含 volume/open/close/pre_close 字段。

    Returns:
        True 表示疑似停牌。
    """
    if bar is None:
        return False
    volume = getattr(bar, "volume", 0) or 0
    open_p = getattr(bar, "open", 0) or 0
    close_p = getattr(bar, "close", 0) or 0
    pre_close = getattr(bar, "pre_close", None) or 0
    if volume > 0:
        return False
    # 无成交量且价格未变（开盘=收盘=前收）→ 疑似停牌
    if open_p > 0 and close_p > 0 and pre_close > 0:
        return abs(open_p - close_p) < 1e-6 and abs(close_p - pre_close) < 1e-6
    return volume == 0 and open_p == 0 and close_p == 0


def check_blacklist(
    name: str = "",
    code: str = "",
    bar=None,
    min_listing_days: int = 0,
    max_turnover_rate: Optional[float] = None,
) -> Optional[str]:
    """黑名单综合校验。

    Args:
        name: 股票名称。
        code: 股票代码。
        bar: 当日 Bar（用于停牌/换手率判断）。
        min_listing_days: 最小上市天数，0 不校验。
        max_turnover_rate: 最大换手率（%），None 不校验。

    Returns:
        命中规则时返回原因字符串，否则返回 None。
    """
    if is_st_stock(name):
        return f"ST 股：{name}"
    if is_delisting_stock(name):
        return f"退市股：{name}"
    if bar is not None and is_suspended(bar):
        return f"停牌：{code or getattr(bar, 'code', '')}"
    if min_listing_days > 0 and getattr(bar, "listing_days", 0) < min_listing_days:
        return f"上市不足 {min_listing_days} 天"
    if max_turnover_rate is not None:
        turnover = getattr(bar, "turnover_rate", 0) or 0
        if turnover > max_turnover_rate:
            return f"换手率过高：{turnover:.1f}% > {max_turnover_rate}%"
    return None