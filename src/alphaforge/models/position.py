"""持仓模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, computed_field


class Position(BaseModel):
    """单只股票持仓（可变，由 Portfolio 维护）。"""

    code: str
    name: str = ""
    industry: str = ""
    qty: int = Field(ge=0, default=0)
    avg_cost: float = Field(ge=0, default=0.0)
    total_buy_cost: float = Field(ge=0, default=0.0)  # 累计买入成本（含手续费）
    total_buy_qty: int = Field(ge=0, default=0)
    realized_pnl: float = 0.0
    current_price: float = Field(ge=0, default=0.0)
    fees_paid: float = Field(ge=0, default=0.0)  # 累计手续费

    model_config = {"validate_assignment": True}

    @computed_field  # type: ignore[misc]
    @property
    def market_value(self) -> float:
        return self.current_price * self.qty

    @computed_field  # type: ignore[misc]
    @property
    def total_cost(self) -> float:
        return self.avg_cost * self.qty + self.fees_paid

    @computed_field  # type: ignore[misc]
    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.qty - self.fees_paid

    @computed_field  # type: ignore[misc]
    @property
    def unrealized_pnl_pct(self) -> float:
        cost = self.avg_cost * self.qty
        if cost <= 0:
            return 0.0
        return (self.current_price - self.avg_cost) * self.qty / cost

    @computed_field  # type: ignore[misc]
    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def apply_buy(self, qty: int, amount_inc_fee: float, fee: float) -> None:
        """应用买入。"""
        self.total_buy_qty += qty
        self.total_buy_cost += amount_inc_fee
        self.fees_paid += fee
        old_mv = self.avg_cost * self.qty
        self.qty += qty
        self.avg_cost = (old_mv + amount_inc_fee) / self.qty if self.qty > 0 else 0.0

    def apply_sell(self, qty: int, price: float, fee: float) -> float:
        """应用卖出，返回 realized_pnl 增量。"""
        if qty <= 0 or self.qty < qty:
            return 0.0
        realized = (price - self.avg_cost) * qty
        self.realized_pnl += realized
        self.fees_paid += fee
        self.qty -= qty
        if self.qty == 0:
            self.avg_cost = 0.0
        return realized