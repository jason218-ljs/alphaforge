"""撮合器：将 Order 转为 Fill（或拒单）。

撮合流程：校验 → T+1 检查 → 涨跌停检查 → 滑点 → 费用 → 资金/持仓校验 → Fill。
所有拒单原因记录到 Fill.reject_reason，不中断回测。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from alphaforge.engine.market_rules import MarketRules
from alphaforge.logger import get_logger
from alphaforge.models.fill import Fill
from alphaforge.models.order import Order
from alphaforge.models.portfolio import Portfolio

logger = get_logger("engine.matcher")


class Matcher:
    """订单撮合器。"""

    def __init__(self, rules: MarketRules):
        self.rules = rules

    def match(
        self,
        order: Order,
        portfolio: Portfolio,
        prev_close: Optional[float] = None,
        bar: Optional[Any] = None,
    ) -> Fill:
        """撮合单笔订单。

        Args:
            order: 订单。
            portfolio: 当前组合状态（用于资金/持仓/T+1 校验）。
            prev_close: 前收盘价（用于涨跌停校验）。None 则跳过涨跌停检查。
            bar: 当日 Bar（用于停牌校验与成交量滑点）。None 则跳过。

        Returns:
            Fill（status=FILLED 或 REJECTED，含 reject_reason）。
        """
        # 1. 基础校验
        reject = self._basic_validate(order)
        if reject:
            return self._reject(order, reject)

        # 1.5 停牌校验（成交量为 0 且价格未变）
        if bar is not None:
            reject = self._check_suspended(order, bar)
            if reject:
                return self._reject(order, reject)

        # 2. 卖出校验（先持仓，再 T+1，语义更清晰）
        if order.side == "SELL":
            reject = self._check_holding(order, portfolio)
            if reject:
                return self._reject(order, reject)
            reject = self._check_t_plus_1(order, portfolio)
            if reject:
                return self._reject(order, reject)

        # 3. 涨跌停检查
        if prev_close is not None and prev_close > 0:
            reject = self._check_price_limit(order, prev_close)
            if reject:
                return self._reject(order, reject)
            # 涨停/跌停封死（一字板）检查
            if bar is not None:
                reject = self._check_limit_locked(order, bar, prev_close)
                if reject:
                    return self._reject(order, reject)

        # 4. 滑点（含成交量冲击）
        bar_volume = getattr(bar, "volume", 0) if bar else 0
        if bar_volume > 0:
            filled_price = self.rules.apply_volume_slippage(
                order.price, order.side, order.qty, bar_volume
            )
        else:
            filled_price = self.rules.apply_slippage(order.price, order.side)
        if filled_price <= 0:
            return self._reject(order, "滑点后价格非正")

        amount = round(filled_price * order.qty, 2)

        # 5. 费用
        commission = self.rules.calc_commission(amount)
        stamp_tax = self.rules.calc_stamp_tax(amount, order.side)
        transfer_fee = self.rules.calc_transfer_fee(amount)
        total_fee = round(commission + stamp_tax + transfer_fee, 4)

        # 6. 资金校验（买入）
        if order.side == "BUY":
            cost = amount + total_fee
            if portfolio.cash < cost:
                return self._reject(
                    order,
                    f"资金不足：需要 {cost:.2f}，现金 {portfolio.cash:.2f}",
                )

        # 7. 生成成交
        cash_after = self._estimate_cash_after(order, portfolio, amount, total_fee)
        return Fill(
            time=order.time,
            code=order.code,
            side=order.side,
            qty=order.qty,
            price=filled_price,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
            total_fee=total_fee,
            cash_after=cash_after,
            status="FILLED",
        )

    # ---------- 校验 ----------

    def _basic_validate(self, order: Order) -> Optional[str]:
        if order.qty <= 0:
            return f"数量非正：{order.qty}"
        if order.price <= 0:
            return f"价格非正：{order.price}"
        # A 股买入需 100 股整手（卖出可不足 100 清仓）
        if order.side == "BUY" and order.qty % 100 != 0:
            return f"买入数量非 100 整数倍：{order.qty}"
        return None

    def _check_t_plus_1(self, order: Order, portfolio: Portfolio) -> Optional[str]:
        if not self.rules.enable_t_plus_1:
            return None
        today_bought = portfolio.today_buy_lots.get(order.code, 0)
        pos = portfolio.get_position(order.code)
        held = pos.qty if pos else 0
        # 可卖 = 总持仓 - 当日买入（T+1 约束）
        sellable = held - today_bought
        if sellable < order.qty:
            return (
                f"T+1 限制：当日买入 {today_bought} 不可卖，"
                f"可卖 {sellable} < 申报 {order.qty}"
            )
        return None

    def _check_holding(self, order: Order, portfolio: Portfolio) -> Optional[str]:
        pos = portfolio.get_position(order.code)
        held = pos.qty if pos else 0
        if held < order.qty:
            return f"持仓不足：持有 {held} < 申报 {order.qty}"
        return None

    def _check_price_limit(self, order: Order, prev_close: float) -> Optional[str]:
        lower, upper = self.rules.price_limit_bounds(order.code, prev_close)
        if order.side == "BUY" and order.price > upper + 1e-6:
            return f"超涨停：申报 {order.price} > 涨停价 {upper}"
        if order.side == "SELL" and order.price < lower - 1e-6:
            return f"超跌停：申报 {order.price} < 跌停价 {lower}"
        return None

    def _check_suspended(self, order: Order, bar: Any) -> Optional[str]:
        """停牌校验：当日成交量为 0 且价格未变。"""
        from alphaforge.utils.validators import is_suspended
        if is_suspended(bar):
            return f"停牌：{order.code} 当日无成交"
        return None

    def _check_limit_locked(self, order: Order, bar: Any, prev_close: float) -> Optional[str]:
        """涨停/跌停一字板校验。

        涨停一字板（开=高=低=涨停价）→ 买入拒单
        跌停一字板（开=高=低=跌停价）→ 卖出拒单
        """
        open_p = getattr(bar, "open", 0) or 0
        high = getattr(bar, "high", 0) or 0
        low = getattr(bar, "low", 0) or 0
        if open_p <= 0:
            return None

        if order.side == "BUY":
            if self.rules.is_limit_up_locked(order.code, open_p, high, low, prev_close):
                return f"涨停封死：{order.code} 一字板无法买入"
        elif order.side == "SELL":
            if self.rules.is_limit_down_locked(order.code, open_p, high, low, prev_close):
                return f"跌停封死：{order.code} 一字板无法卖出"
        return None

    def _estimate_cash_after(
        self, order: Order, portfolio: Portfolio, amount: float, fee: float
    ) -> float:
        """估算成交后现金（仅供 Fill 记录，真实更新由 Portfolio.apply_fill 完成）。"""
        if order.side == "BUY":
            return portfolio.cash - amount - fee
        return portfolio.cash + amount - fee

    def _reject(self, order: Order, reason: str) -> Fill:
        logger.debug("拒单 %s %s： %s", order.side, order.code, reason)
        return Fill(
            time=order.time,
            code=order.code,
            side=order.side,
            qty=0,
            price=order.price,
            amount=0.0,
            status="REJECTED",
            reject_reason=reason,
        )