"""事件驱动回测引擎。

逐交易日回放：Strategy.on_bar → Matcher.match → Portfolio.apply_fill → mark-to-market → snapshot。
支持 T+1、涨跌停、费用、滑点（均由 MarketRules + Matcher 实现）。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from alphaforge.engine.event_bus import EventBus
from alphaforge.engine.market_rules import MarketRules
from alphaforge.engine.matcher import Matcher
from alphaforge.logger import get_logger
from alphaforge.models.order import Order
from alphaforge.models.portfolio import Portfolio, PortfolioSnapshot
from alphaforge.models.result import BacktestResult, BacktestSummary, TradeRecord
from alphaforge.strategy.base import Context, StrategyBase

logger = get_logger("engine.event_engine")


class EventEngine:
    """事件驱动回测引擎。"""

    def __init__(self, rules: MarketRules, initial_capital: float = 1_000_000.0):
        self.rules = rules
        self.matcher = Matcher(rules)
        self.initial_capital = initial_capital
        self.event_bus = EventBus()

    def run(
        self,
        strategy: StrategyBase,
        bars: List[Dict[str, Any]],
        initial_capital: Optional[float] = None,
        benchmark_series: Optional[List[float]] = None,
    ) -> BacktestResult:
        """运行回测。

        Args:
            strategy: 策略实例。
            bars: 每日 bar 列表，每个 bar 为 dict，至少含：
                  {date, quotes: {code: close}, prev_close: {code: close}}
            initial_capital: 初始资金（覆盖默认）。
            benchmark_series: 基准净值序列（与 bars 等长，用于 summary）。

        Returns:
            BacktestResult。
        """
        capital = initial_capital if initial_capital is not None else self.initial_capital
        portfolio = Portfolio(cash=capital)
        matcher = self.matcher

        # 初始化策略
        init_ctx = Context(date=bars[0]["date"] if bars else datetime.now(),
                           portfolio=portfolio, rules=self.rules)
        strategy.initialize(init_ctx)

        trade_log: List[TradeRecord] = []
        signal_log: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []

        prev_close: Dict[str, float] = {}
        for i, bar in enumerate(bars):
            date = bar["date"]
            quotes: Dict[str, float] = bar.get("quotes", {})
            bar_prev_close: Dict[str, float] = bar.get("prev_close", prev_close)

            # 新交易日：重置 T+1
            portfolio.reset_day()

            # mark-to-market（用当日收盘价更新持仓市值）
            portfolio.mark_to_market(quotes)

            # 生成 context
            ctx = Context(
                date=date,
                bar_index=i,
                portfolio=portfolio,
                history=list(portfolio.history),
                quotes=quotes,
                prev_close=bar_prev_close,
                rules=self.rules,
            )

            # 策略产生订单
            orders = strategy.on_bar(ctx) or []
            for order in orders:
                signal_log.append({
                    "date": date.isoformat(),
                    "code": order.code,
                    "side": order.side,
                    "qty": order.qty,
                    "price": order.price,
                    "reason": order.reason,
                    "trader": order.trader,
                })
                self.event_bus.publish("order", order)

                # 撮合
                pc = bar_prev_close.get(order.code)
                fill = matcher.match(order, portfolio, prev_close=pc)

                if fill.status == "FILLED":
                    portfolio.apply_fill(fill)
                    trade_log.append(TradeRecord(
                        date=date,
                        side=fill.side,
                        code=fill.code,
                        name=order.reason and "" or "",
                        requested_qty=order.qty,
                        filled_qty=fill.qty,
                        price=fill.price,
                        amount=fill.amount,
                        fee=fill.total_fee,
                        status="FILLED",
                        trader=order.trader,
                        reason=order.reason,
                    ))
                else:
                    trade_log.append(TradeRecord(
                        date=date,
                        side=order.side,
                        code=order.code,
                        requested_qty=order.qty,
                        filled_qty=0,
                        price=order.price,
                        amount=0.0,
                        fee=0.0,
                        status="REJECTED",
                        reject_reason=fill.reject_reason,
                        trader=order.trader,
                        reason=order.reason,
                    ))
                self.event_bus.publish("fill", fill)
                strategy.on_fill(ctx, fill)

            # 日终 mark-to-market（订单可能改变了持仓，但价格用当日收盘）
            portfolio.mark_to_market(quotes)
            snap = portfolio.snapshot(date)
            equity_curve.append({
                "date": date.isoformat(),
                "nav": snap.nav,
                "cash": snap.cash,
                "daily_pnl": snap.daily_pnl,
                "daily_return_pct": snap.daily_return_pct,
                "position_count": snap.position_count,
            })

            # 更新 prev_close 供下一日使用
            prev_close = dict(quotes)

            strategy.on_day_end(ctx)

        summary = self._summary(equity_curve, trade_log, capital, benchmark_series)
        return BacktestResult(
            strategy_name=strategy.name,
            initial_capital=capital,
            start_date=bars[0]["date"] if bars else None,
            end_date=bars[-1]["date"] if bars else None,
            equity_curve=equity_curve,
            trade_log=trade_log,
            signal_log=signal_log,
            summary=summary,
            config_snapshot=self.rules.snapshot(),
        )

    # ---------- 绩效汇总 ----------

    def _summary(
        self,
        equity_curve: List[Dict[str, Any]],
        trade_log: List[TradeRecord],
        initial_capital: float,
        benchmark_series: Optional[List[float]] = None,
    ) -> BacktestSummary:
        nav_series = [e["nav"] for e in equity_curve]
        days = len(nav_series) - 1
        final_nav = nav_series[-1] if nav_series else initial_capital
        total_return = (final_nav / initial_capital - 1.0) if initial_capital > 0 else 0.0

        # 年化
        annualized = 0.0
        if days > 0 and total_return > -1.0:
            annualized = (1 + total_return) ** (252 / days) - 1.0

        # 日收益
        daily_rets = np.array([]) if len(nav_series) < 2 else np.diff(nav_series) / np.array(nav_series[:-1])

        # 最大回撤
        mdd = 0.0
        peak = nav_series[0] if nav_series else 0.0
        for v in nav_series:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, (v - peak) / peak)

        # 夏普（减无风险利率，年化）
        sharpe = 0.0
        if len(daily_rets) > 1:
            std = float(np.std(daily_rets, ddof=1))
            if std > 0:
                rf_daily = 0.03 / 252
                sharpe = float((np.mean(daily_rets) - rf_daily) / std * math.sqrt(252))

        # Sortino
        sortino = 0.0
        if len(daily_rets) > 1:
            neg = daily_rets[daily_rets < 0]
            if len(neg) > 1:
                ds = float(np.std(neg, ddof=1))
                if ds > 0:
                    sortino = float((np.mean(daily_rets) - 0.03 / 252) / ds * math.sqrt(252))

        # Calmar
        calmar = float(annualized / abs(mdd)) if mdd < 0 else 0.0

        # 胜率（按日）
        winning = int((daily_rets > 0).sum()) if len(daily_rets) > 0 else 0
        losing = int((daily_rets < 0).sum()) if len(daily_rets) > 0 else 0
        win_rate = winning / max(winning + losing, 1)
        wins = daily_rets[daily_rets > 0]
        losses = daily_rets[daily_rets < 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        profit_loss_ratio = float(avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0

        # 成交统计
        filled = [t for t in trade_log if t.status == "FILLED"]
        winning_trades = sum(1 for t in filled if t.side == "SELL" and t.price > 0)

        return BacktestSummary(
            total_return=total_return,
            annualized_return=annualized,
            max_drawdown=mdd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            total_trades=len(filled),
            winning_trades=winning_trades,
            losing_trades=len(filled) - winning_trades,
            initial_capital=initial_capital,
            final_nav=final_nav,
            days=days,
        )