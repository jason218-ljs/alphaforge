"""主流水线 流水还原策略。

回放模拟盘交易流水：每个交易日按 records.json 中的 trades[] 生成订单。
用于把真实流水接入事件驱动回测引擎，校验撮合规则与持仓重建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from alphaforge.models.order import Order
from alphaforge.strategy.base import Context, StrategyBase


class 主流水线ReplayStrategy(StrategyBase):
    """主流水线 流水还原策略。

    预先加载 {date: List[Order]}，on_bar 时返回当日的订单。
    订单价格用流水的成交价（不加滑点，因为是真实成交回放）。
    """

    name = "external_scorer_replay"

    def __init__(self, trades_by_date: Dict[datetime, List[Dict[str, Any]]],
                 skip_slippage: bool = True):
        """
        Args:
            trades_by_date: {date: [{code, side, qty, price, trader, reason}, ...]}
            skip_slippage: True 时订单价格即成交价（回放真实流水，不再加滑点）。
        """
        self.trades_by_date = trades_by_date
        self.skip_slippage = skip_slippage

    def on_bar(self, ctx: Context) -> List[Order]:
        trades = self.trades_by_date.get(ctx.date)
        if not trades:
            return []
        orders: List[Order] = []
        for t in trades:
            code = t.get("code", "")
            if not code:
                continue
            # 滑点由 Matcher 控制；回放模式建议把 rules.slippage_pct 设 0
            orders.append(Order(
                time=ctx.date,
                code=code,
                side=t["side"],
                qty=int(t.get("qty", 0)),
                price=float(t.get("price", 0)),
                reason=t.get("reason", "主流水线流水"),
                trader=t.get("trader", ""),
            ))
        return orders


def load_trades_from_records(records: List[Dict[str, Any]]) -> Dict[datetime, List[Dict[str, Any]]]:
    """从 records.json 的 records 数组构造 {date: [trade dict]}。

    每个 record 含 trades[] 与 date。
    """
    out: Dict[datetime, List[Dict[str, Any]]] = {}
    for rec in records:
        date = rec["date"]
        if isinstance(date, str):
            date = datetime.fromisoformat(date)
        out[date] = list(rec.get("trades", []))
    return out