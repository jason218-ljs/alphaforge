"""策略信号模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

SignalType = Literal["BUY", "SELL", "HOLD"]


class Signal(BaseModel):
    """策略产生的信号。"""

    date: datetime
    code: str
    signal_type: SignalType
    confidence: float = Field(ge=0, le=1, default=0.5)
    score: float = 0.0
    price: float = Field(ge=0, default=0.0)
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    suggested_ratio: float = 0.0
    reason: str = ""
    is_executed: bool = False
    execution_price: Optional[float] = None
    execution_date: Optional[datetime] = None
    outcome_pnl: Optional[float] = None