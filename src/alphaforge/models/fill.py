"""成交模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FillSide = Literal["BUY", "SELL"]
FillStatus = Literal["FILLED", "REJECTED"]


class Fill(BaseModel):
    """成交回报。"""

    time: datetime
    code: str
    side: FillSide
    qty: int = Field(ge=0)
    price: float = Field(ge=0)  # 含滑点后的实际成交价
    amount: float = Field(ge=0)  # qty * price
    commission: float = Field(ge=0, default=0.0)
    stamp_tax: float = Field(ge=0, default=0.0)
    transfer_fee: float = Field(ge=0, default=0.0)
    total_fee: float = Field(ge=0, default=0.0)
    cash_after: float = 0.0
    status: FillStatus = "FILLED"
    reject_reason: str = ""

    @property
    def fee(self) -> float:
        return self.total_fee