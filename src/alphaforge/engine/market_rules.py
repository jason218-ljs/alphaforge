"""A 股市场规则常量与校验（配置驱动）。

包含：板块识别、涨跌停上下界、T+1、费用（印花税/佣金/过户费）、滑点。
所有常量从 config.yaml 的 market_rules 段读取，默认值对齐 A 股现行规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from alphaforge.utils.validators import get_board


@dataclass
class MarketRules:
    """A 股市场规则（从 MarketRulesConfig 构造）。"""

    stamp_tax_rate: float = 0.001          # 印花税 0.1%（卖出征收）
    commission_rate: float = 0.00025       # 佣金 0.025%
    min_commission: float = 5.0            # 最低佣金 5 元
    transfer_fee_rate: float = 0.00002     # 过户费 0.002%
    slippage_pct: float = 0.0005           # 滑点 0.05%
    slippage_fixed: float = 0.01           # 固定滑点 0.01 元
    enable_t_plus_1: bool = True           # T+1：当日买入次日可卖
    price_limit: Dict[str, float] = field(
        default_factory=lambda: {"MAIN": 0.10, "GEM": 0.20, "STAR": 0.20, "BEI": 0.30}
    )

    # ---------- 板块与涨跌停 ----------

    def board_of(self, code: str) -> str:
        """识别板块：MAIN / GEM / STAR / BEI。"""
        return get_board(code)

    def limit_ratio(self, code: str) -> float:
        """涨跌停比例（小数）。"""
        board = self.board_of(code)
        return self.price_limit.get(board, 0.10)

    def price_limit_bounds(self, code: str, prev_close: float) -> tuple:
        """计算涨跌停上下界。

        Args:
            code: 股票代码。
            prev_close: 前收盘价。

        Returns:
            (lower_limit, upper_limit)。
        """
        if prev_close <= 0:
            return 0.0, float("inf")
        ratio = self.limit_ratio(code)
        # A 股涨跌停价按 0.01 元四舍五入
        lower = round(prev_close * (1 - ratio), 2)
        upper = round(prev_close * (1 + ratio), 2)
        return lower, upper

    def is_at_limit_up(self, code: str, price: float, prev_close: float) -> bool:
        """是否触及涨停价。"""
        if prev_close <= 0:
            return False
        _, upper = self.price_limit_bounds(code, prev_close)
        return price >= upper - 1e-6

    def is_at_limit_down(self, code: str, price: float, prev_close: float) -> bool:
        """是否触及跌停价。"""
        if prev_close <= 0:
            return False
        lower, _ = self.price_limit_bounds(code, prev_close)
        return price <= lower + 1e-6

    def is_limit_up_locked(self, code: str, open_price: float, high: float,
                            low: float, prev_close: float) -> bool:
        """涨停一字板：开盘=最高=最低=涨停价（无法买入）。

        Args:
            code: 股票代码。
            open_price: 当日开盘价。
            high: 当日最高价。
            low: 当日最低价。
            prev_close: 前收盘价。

        Returns:
            True 表示涨停封死，买入无法成交。
        """
        if prev_close <= 0 or open_price <= 0:
            return False
        _, upper = self.price_limit_bounds(code, prev_close)
        return (
            abs(open_price - upper) < 1e-6
            and abs(high - upper) < 1e-6
            and abs(low - upper) < 1e-6
        )

    def is_limit_down_locked(self, code: str, open_price: float, high: float,
                              low: float, prev_close: float) -> bool:
        """跌停一字板：开盘=最高=最低=跌停价（无法卖出）。"""
        if prev_close <= 0 or open_price <= 0:
            return False
        lower, _ = self.price_limit_bounds(code, prev_close)
        return (
            abs(open_price - lower) < 1e-6
            and abs(high - lower) < 1e-6
            and abs(low - lower) < 1e-6
        )

    # ---------- 费用 ----------

    def calc_commission(self, amount: float) -> float:
        """佣金：max(amount × rate, min_commission)。"""
        if amount <= 0:
            return 0.0
        return max(amount * self.commission_rate, self.min_commission)

    def calc_stamp_tax(self, amount: float, side: str) -> float:
        """印花税：仅卖出征收。"""
        if side != "SELL" or amount <= 0:
            return 0.0
        return amount * self.stamp_tax_rate

    def calc_transfer_fee(self, amount: float) -> float:
        """过户费。"""
        if amount <= 0:
            return 0.0
        return amount * self.transfer_fee_rate

    def calc_total_fee(self, amount: float, side: str) -> float:
        """总费用：佣金 + 印花税 + 过户费。"""
        return (
            self.calc_commission(amount)
            + self.calc_stamp_tax(amount, side)
            + self.calc_transfer_fee(amount)
        )

    # ---------- 滑点 ----------

    def apply_slippage(self, price: float, side: str) -> float:
        """应用滑点：买入加价，卖出降价（基础固定+比例滑点）。

        Returns:
            含滑点后的成交价。
        """
        if price <= 0:
            return 0.0
        if side == "BUY":
            return round(price * (1 + self.slippage_pct) + self.slippage_fixed, 4)
        elif side == "SELL":
            return round(price * (1 - self.slippage_pct) - self.slippage_fixed, 4)
        return price

    def apply_volume_slippage(
        self,
        price: float,
        side: str,
        order_qty: int,
        bar_volume: int = 0,
        volume_cap_pct: float = 0.1,
        impact_coeff: float = 0.1,
    ) -> float:
        """成交量相关的滑点模型（市场冲击）。

        当下单量超过当日成交量 volume_cap_pct 比例时，按线性冲击模型加价。
        公式：impact = impact_coeff × min(order_qty / max(bar_volume × cap, 1) - 1, cap_max)

        Args:
            price: 基准价格。
            side: 买卖方向。
            order_qty: 订单数量。
            bar_volume: 当日成交量（0 表示不应用冲击模型）。
            volume_cap_pct: 单笔订单占当日成交量的上限比例（默认 10%）。
            impact_coeff: 冲击系数（每超出 1 倍 cap 增加的滑点比例）。

        Returns:
            含成交量滑点的成交价。
        """
        # 先应用基础滑点
        slipped = self.apply_slippage(price, side)
        if slipped <= 0 or bar_volume <= 0 or order_qty <= 0:
            return slipped

        # 计算冲击：order_qty / (bar_volume × cap)
        cap_volume = max(bar_volume * volume_cap_pct, 1)
        ratio = order_qty / cap_volume
        if ratio <= 1.0:
            return slipped  # 未超 cap，无冲击

        # 超出部分按 impact_coeff 线性增加滑点
        impact_ratio = min((ratio - 1.0) * impact_coeff, 0.05)  # 上限 5%
        if side == "BUY":
            return round(slipped * (1 + impact_ratio), 4)
        elif side == "SELL":
            return round(slipped * (1 - impact_ratio), 4)
        return slipped

    # ---------- T+1 ----------

    def can_sell_today(self, code: str, today_buy_lots: Dict[str, int]) -> bool:
        """T+1 规则下，当日买入的股票当日不可卖出。

        Args:
            code: 股票代码。
            today_buy_lots: 当日已买入股数字典 {code: qty}。

        Returns:
            是否可卖（若该股当日有买入且开启 T+1，则不可卖）。
        """
        if not self.enable_t_plus_1:
            return True
        return today_buy_lots.get(code, 0) == 0

    def snapshot(self) -> dict:
        """生成规则快照（用于回测结果审计）。"""
        return {
            "stamp_tax_rate": self.stamp_tax_rate,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_pct": self.slippage_pct,
            "slippage_fixed": self.slippage_fixed,
            "enable_t_plus_1": self.enable_t_plus_1,
            "price_limit": dict(self.price_limit),
        }


def from_config(config) -> MarketRules:
    """从 AlphaForgeConfig.market_rules 构造 MarketRules。"""
    return MarketRules(
        stamp_tax_rate=config.stamp_tax_rate,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        transfer_fee_rate=config.transfer_fee_rate,
        slippage_pct=config.slippage_pct,
        slippage_fixed=config.slippage_fixed,
        enable_t_plus_1=config.enable_t_plus_1,
        price_limit=dict(config.price_limit),
    )