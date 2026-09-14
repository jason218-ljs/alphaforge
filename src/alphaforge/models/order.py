"""订单模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]


class Order(BaseModel):
    """订单。"""

    time: datetime
    code: str
    side: OrderSide
    qty: int = Field(ge=0)   # 数值校验由 Matcher 执行（允许 0 以测试拒单路径）
    price: float = Field(ge=0)
    order_type: OrderType = "LIMIT"
    reason: str = ""
    trader: str = ""  # 交易人（主流水线 回放用）

    model_config = {"frozen": False}