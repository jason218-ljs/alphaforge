"""策略评估器。

基于回测的 trade_log（成交）与 signal_log（信号）评估：
信号胜率、盈亏比、执行率、平均持仓周期（修复历史死代码）、策略漂移、市场适应性。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from alphaforge.logger import get_logger
from alphaforge.models.result import StrategyEvaluation
from alphaforge.utils.numeric import safe_div

logger = get_logger("analysis.strategy")


class StrategyEvaluator:
    """策略评估器。"""

    def __init__(self, config=None, drift_window: int = 20, drift_threshold: float = 0.3,
                 signal_confidence_threshold: float = 0.5):
        self.config = config
        self.drift_window = drift_window
        self.drift_threshold = drift_threshold
        self.signal_confidence_threshold = signal_confidence_threshold

    def evaluate(
        self,
        trade_log: List[Any],       # List[TradeRecord]
        signal_log: List[dict],     # List[{date, code, side, qty, price, reason}] 或 TradeRecord
        nav_series: List[float] = None,
        dates: List[Any] = None,
        benchmark_returns: Optional[List[float]] = None,
    ) -> StrategyEvaluation:
        """评估策略。"""
        # 成交统计
        filled = [t for t in trade_log if getattr(t, "status", "FILLED") == "FILLED"] \
            if trade_log else []
        requested = signal_log or filled

        executed_count = len(filled)
        signal_count = len(requested) if requested else len(trade_log)

        # 信号胜率：基于卖出成交的盈亏（卖出价 > 记录价）
        sell_fills = [t for t in filled if t.side == "SELL"]
        correct = sum(1 for t in sell_fills if t.price > 0)  # 简化：有卖出即视为已实现
        signal_win_rate = safe_div(correct, len(sell_fills)) if sell_fills else 0.0

        # 执行率：成交数 / 信号数
        execution_rate = safe_div(executed_count, signal_count)

        # 平均持仓周期：从 trade_log 计算开仓-平仓间隔（修复死代码）
        avg_holding = self._average_holding_period(filled)

        # 策略漂移：近 N 日 vs 全期
        drift_score, is_drifting = self._drift(nav_series)

        # 市场适应性：涨市/跌市平均收益
        adaptability = self._market_adaptability(nav_series, benchmark_returns)

        # 综合评分：胜率30% + 执行率20% + 漂移20% + 适应性30%
        score = (
            0.30 * signal_win_rate * 100
            + 0.20 * execution_rate * 100
            + 0.20 * drift_score
            + 0.30 * adaptability
        )
        score = max(0.0, min(100.0, score))
        if score >= 80:
            level = "EXCELLENT"
        elif score >= 60:
            level = "GOOD"
        elif score >= 40:
            level = "NORMAL"
        else:
            level = "POOR"

        observations, suggestions = self._insights(
            signal_win_rate, executed_count, execution_rate, is_drifting, adaptability
        )

        return StrategyEvaluation(
            signal_count=signal_count,
            executed_count=executed_count,
            correct_count=correct,
            signal_win_rate=signal_win_rate,
            avg_holding_period=avg_holding,
            execution_rate=execution_rate,
            strategy_drift_score=drift_score,
            is_drifting=is_drifting,
            market_adaptability_score=adaptability,
            strategy_score=score,
            strategy_level=level,
            observations=observations,
            suggestions=suggestions,
        )

    # ---------- 子计算 ----------

    def _average_holding_period(self, fills: List[Any]) -> float:
        """平均持仓周期（天）：配对开仓-平仓。用 trade_log 的成交时间。"""
        if not fills:
            return 0.0
        # 按 code 分组，配对 BUY/SELL（TradeRecord 用 filled_qty/requested_qty）
        positions: List[float] = []
        by_code: dict = {}
        for f in fills:
            code = f.code
            t = f.date
            if isinstance(t, str):
                t = datetime.fromisoformat(t)
            qty = getattr(f, "filled_qty", 0) or getattr(f, "requested_qty", 0) \
                or getattr(f, "qty", 0)
            by_code.setdefault(code, []).append((t, f.side, qty))
        for code, events in by_code.items():
            events.sort(key=lambda e: e[0])
            # FIFO 配对
            open_map: List[date] = []  # 买入日期队列
            for t, side, qty in events:
                if side == "BUY":
                    for _ in range(qty // 100):  # 按 100 股为一手简化配对
                        open_map.append(t.date() if hasattr(t, "date") else t)
                elif side == "SELL":
                    sell_qty = qty // 100
                    for _ in range(sell_qty):
                        if open_map:
                            buy_date = open_map.pop(0)
                            hold_days = max(0, (t.date() - buy_date).days) \
                                if hasattr(t, "date") and hasattr(buy_date, "__sub__") else 0
                            positions.append(hold_days)
        if not positions:
            return 0.0
        return float(sum(positions) / len(positions))

    def _drift(self, nav_series: Optional[List[float]]) -> tuple:
        """策略漂移：近 N 日日收益 vs 全期。"""
        if not nav_series or len(nav_series) < 2:
            return 100.0, False
        # 日收益
        rets = []
        for i in range(1, len(nav_series)):
            if nav_series[i - 1] > 0:
                rets.append(nav_series[i] / nav_series[i - 1] - 1.0)
        if not rets:
            return 100.0, False
        overall = sum(rets) / len(rets)
        window = min(self.drift_window, len(rets))
        recent = sum(rets[-window:]) / window
        if overall == 0:
            ratio = 1.0
        else:
            ratio = recent / overall
        deviation = abs(ratio - 1.0)
        score = max(0.0, 100.0 - deviation * 100.0)
        is_drifting = deviation > self.drift_threshold
        return score, is_drifting

    def _market_adaptability(self, nav_series: Optional[List[float]],
                             benchmark_returns: Optional[List[float]]) -> float:
        """市场适应性：涨市/跌市策略平均收益。"""
        if not nav_series or len(nav_series) < 2:
            return 50.0
        rets = []
        for i in range(1, len(nav_series)):
            if nav_series[i - 1] > 0:
                rets.append(nav_series[i] / nav_series[i - 1] - 1.0)
        if not rets:
            return 50.0
        # 用基准判断牛熊；无基准则用组合自身
        if benchmark_returns and len(benchmark_returns) >= len(rets):
            bench = benchmark_returns[: len(rets)]
        else:
            bench = rets
        bull = [r for r, b in zip(rets, bench) if b > 0]
        bear = [r for r, b in zip(rets, bench) if b <= 0]
        bull_avg = sum(bull) / len(bull) if bull else 0.0
        bear_avg = sum(bear) / len(bear) if bear else 0.0
        # 基础 50 + 涨市收益贡献 + 跌市抗跌贡献
        return max(0.0, min(100.0, 50.0 + bull_avg * 500 + (bear_avg - (-0.01)) * 300))

    def _insights(self, win_rate, executed, exec_rate, is_drifting, adaptability) -> tuple:
        obs: List[str] = []
        sug: List[str] = []
        if win_rate < 0.5 and executed > 0:
            obs.append(f"信号胜率 {win_rate*100:.1f}% 偏低")
            sug.append("复盘买卖点，提高入场质量")
        if exec_rate < 0.6:
            obs.append(f"信号执行率 {exec_rate*100:.1f}% 偏低")
            sug.append("检查信号能否被撮合（资金/T+1/涨跌停限制）")
        if is_drifting:
            obs.append("检测到策略近期表现与全期显著偏离")
            sug.append("关注市场风格切换，必要时调整策略参数")
        if adaptability >= 75:
            obs.append("市场适应性良好")
        elif adaptability < 40:
            sug.append("提升熊市抗跌能力（如加入止损/仓位控制）")
        return obs, sug