"""回测/分析结果模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class BacktestSummary(BaseModel):
    """回测绩效汇总。"""

    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    initial_capital: float = 0.0
    final_nav: float = 0.0
    days: int = 0


class TradeRecord(BaseModel):
    """回测 trade_log 单条。"""

    date: datetime
    side: Literal["BUY", "SELL"]
    code: str
    name: str = ""
    requested_qty: int = 0
    filled_qty: int = 0
    price: float = 0.0
    amount: float = 0.0
    fee: float = 0.0
    status: Literal["FILLED", "REJECTED"] = "FILLED"
    reject_reason: str = ""
    trader: str = ""
    reason: str = ""


class BacktestResult(BaseModel):
    """回测完整结果。"""

    strategy_name: str
    initial_capital: float
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    trade_log: List[TradeRecord] = Field(default_factory=list)
    signal_log: List[Dict[str, Any]] = Field(default_factory=list)
    summary: BacktestSummary = Field(default_factory=BacktestSummary)
    config_snapshot: Dict[str, Any] = Field(default_factory=dict)


class RiskAnalysis(BaseModel):
    """风险分析结果。"""

    max_drawdown: float = 0.0
    drawdown_duration_days: int = 0
    recovery_days: int = 0
    annualized_volatility: float = 0.0
    downside_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    single_position_max: float = 0.0
    industry_concentration: float = 0.0
    cash_ratio: float = 0.0
    position_count: int = 0
    risk_score: float = 0.0
    risk_level: Literal["LOW", "NORMAL", "MEDIUM", "HIGH"] = "NORMAL"
    alerts: List[Dict[str, Any]] = Field(default_factory=list)


class ReturnAnalysis(BaseModel):
    """收益分析结果。"""

    total_return: float = 0.0
    annualized_return: float = 0.0
    daily_return_avg: float = 0.0
    daily_return_std: float = 0.0
    winning_days: int = 0
    losing_days: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_loss_ratio: float = 0.0
    benchmark_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    tracking_error: float = 0.0
    information_ratio: float = 0.0
    return_score: float = 0.0
    return_level: Literal["EXCELLENT", "GOOD", "NORMAL", "POOR"] = "NORMAL"


class StrategyEvaluation(BaseModel):
    """策略评估结果。"""

    signal_count: int = 0
    executed_count: int = 0
    correct_count: int = 0
    signal_win_rate: float = 0.0
    avg_signal_pnl: float = 0.0
    avg_holding_period: float = 0.0
    execution_rate: float = 0.0
    strategy_drift_score: float = 0.0
    is_drifting: bool = False
    market_adaptability_score: float = 0.0
    strategy_score: float = 0.0
    strategy_level: Literal["EXCELLENT", "GOOD", "NORMAL", "POOR"] = "NORMAL"
    observations: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class AttributionResult(BaseModel):
    """归因分析结果。"""

    brinson: Dict[str, Any] = Field(default_factory=dict)
    fama: Dict[str, Any] = Field(default_factory=dict)
    industry_attribution: Dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """完整分析结果。"""

    analysis_date: datetime = Field(default_factory=datetime.now)
    risk: RiskAnalysis = Field(default_factory=RiskAnalysis)
    returns: ReturnAnalysis = Field(default_factory=ReturnAnalysis)
    strategy: StrategyEvaluation = Field(default_factory=StrategyEvaluation)
    attribution: AttributionResult = Field(default_factory=AttributionResult)
    summary: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)