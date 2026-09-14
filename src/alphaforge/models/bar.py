"""K 线数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Bar(BaseModel):
    """单根 K 线（日线）。"""

    date: datetime
    code: str
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0, default=0)
    amount: float = Field(ge=0, default=0.0)
    pre_close: Optional[float] = Field(default=None, ge=0)

    @field_validator("high")
    @classmethod
    def high_ge_low(cls, v, info):
        if "low" in info.data and v < info.data["low"]:
            raise ValueError(f"high({v}) 必须 >= low({info.data.get('low')})")
        return v

    @field_validator("high")
    @classmethod
    def high_ge_open_close(cls, v, info):
        for key in ("open", "close"):
            if key in info.data and info.data[key] > v:
                raise ValueError(f"high({v}) 必须 >= {key}({info.data[key]})")
        return v