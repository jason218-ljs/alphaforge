"""组合状态机：现金 + 持仓 + 历史 + T+1 跟踪。"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from alphaforge.models.fill import Fill
from alphaforge.models.position import Position


class PortfolioSnapshot(BaseModel):
    """组合在某交易日的快照（不可变）。"""

    date: datetime
    cash: float
    positions: Dict[str, Position]  # code → Position（仅 qty>0）
    nav: float
    daily_pnl: float = 0.0
    daily_return_pct: float = 0.0
    prev_nav: Optional[float] = None
    peak_nav: Optional[float] = None
    max_drawdown: Optional[float] = None

    @property
    def position_count(self) -> int:
        return sum(1 for p in self.positions.values() if p.qty > 0)

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def cash_ratio(self) -> float:
        if self.nav <= 0:
            return 0.0
        return self.cash / self.nav

    def position_weights(self) -> Dict[str, float]:
        if self.nav <= 0:
            return {}
        return {k: p.market_value / self.nav for k, p in self.positions.items() if p.qty > 0}

    def industry_weights(self, industry_lookup) -> Dict[str, float]:
        """按行业聚合权重。industry_lookup: Dict[str code, str industry]。"""
        weights: Dict[str, float] = {}
        total_mv = sum(p.market_value for p in self.positions.values() if p.qty > 0)
        if total_mv <= 0:
            return {}
        for code, p in self.positions.items():
            if p.qty <= 0:
                continue
            ind = p.industry or industry_lookup.get(code, "未知")
            weights[ind] = weights.get(ind, 0.0) + p.market_value / total_mv
        return weights


class Portfolio(BaseModel):
    """组合状态机（可变）。"""

    cash: float = Field(ge=0, default=0.0)
    positions: Dict[str, Position] = Field(default_factory=dict)
    history: List[PortfolioSnapshot] = Field(default_factory=list)
    today_buy_lots: Dict[str, int] = Field(default_factory=dict)  # T+1 跟踪

    model_config = {"validate_assignment": True}

    @property
    def nav(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values() if p.qty > 0)

    def get_position(self, code: str) -> Optional[Position]:
        return self.positions.get(code)

    def ensure_position(self, code: str, name: str = "", industry: str = "") -> Position:
        if code not in self.positions:
            self.positions[code] = Position(code=code, name=name, industry=industry)
        return self.positions[code]

    def apply_fill(self, fill: Fill) -> None:
        """应用成交，更新现金/持仓。

        注意：卖出时若超过当前持仓量（超仓/卖空），只按实际可卖数量成交，
        仅回笼实际成交部分的现金，杜绝"现金已计、股票未扣"的双重记账。
        """
        pos = self.ensure_position(fill.code)
        if fill.side == "BUY":
            pos.apply_buy(fill.qty, fill.amount + fill.total_fee, fill.total_fee)
            self.cash -= fill.amount + fill.total_fee
            self.today_buy_lots[fill.code] = self.today_buy_lots.get(fill.code, 0) + fill.qty
        else:  # SELL
            # 实际可卖数量 = min(委托卖出量, 当前持仓量)，避免超仓卖出虚增现金
            sellable = min(fill.qty, pos.qty)
            if sellable <= 0:
                # 无持仓却卖出：不撮合，不改变现金与持仓
                return
            actual_price = fill.price
            # 按实际成交数量等比折算成交金额与手续费
            actual_amount = fill.amount * (sellable / fill.qty) if fill.qty > 0 else 0.0
            actual_fee = fill.total_fee * (sellable / fill.qty) if fill.qty > 0 else 0.0
            pos.apply_sell(sellable, actual_price, actual_fee)
            self.cash += actual_amount - actual_fee

    def mark_to_market(self, quotes: Dict[str, float]) -> None:
        """按行情价更新所有持仓 current_price。"""
        for code, pos in self.positions.items():
            if code in quotes:
                pos.current_price = quotes[code]

    def reset_day(self) -> None:
        """新交易日：清空 T+1 记录。"""
        self.today_buy_lots.clear()

    def snapshot(self, date: datetime) -> PortfolioSnapshot:
        """生成当日快照并加入历史。"""
        prev = self.history[-1] if self.history else None
        prev_nav = prev.nav if prev else None
        nav = self.nav
        daily_pnl = nav - prev_nav if prev_nav is not None else 0.0
        daily_ret = daily_pnl / prev_nav if prev_nav and prev_nav > 0 else 0.0
        peak = max(prev.peak_nav or nav, nav) if prev else nav
        mdd = (nav - peak) / peak if peak > 0 else 0.0
        snap = PortfolioSnapshot(
            date=date,
            cash=self.cash,
            positions={k: v.model_copy() for k, v in self.positions.items() if v.qty > 0},
            nav=nav,
            daily_pnl=daily_pnl,
            daily_return_pct=daily_ret,
            prev_nav=prev_nav,
            peak_nav=peak,
            max_drawdown=mdd,
        )
        self.history.append(snap)
        return snap