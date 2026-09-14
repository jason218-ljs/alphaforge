"""选股策略：对接 EventEngine。

每日 on_bar 流程：
1. 获取当日全市场数据（DailyMarket）
2. 对所有候选股票计算综合评分（基本面40 + 技术面40 + 资金面20）
3. 对当前持仓检查卖出信号（止盈/止损/趋势消退）→ SELL
4. 对非持仓候选股票：预过滤 + 买入信号 + 凯利仓位 → BUY
5. 限制最大持仓数（默认5只），按综合得分排序选取

严格遵守硬约束：
- H1：行业自适应按二级分类
- H5：MA20 未站上不给买入建议
- H6：所有操作可追溯（订单 reason 记录完整）
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from alphaforge.data.synthetic_market import DailyMarket, StockMeta
from alphaforge.logger import get_logger
from alphaforge.models.order import Order
from alphaforge.strategy.base import Context, StrategyBase
from alphaforge.strategy.stock_screener import (
    StockScore, check_sell_signal, check_prefilters, score_stock,
)

logger = get_logger("strategy.stock_scoring")


class StockScoringStrategy(StrategyBase):
    """选股评分策略。

    每日对所有候选股票评分，按综合得分选 Top N 买入，对持仓按卖出信号止损/止盈。
    """

    name = "stock_scoring"

    def __init__(
        self,
        universe: List[StockMeta],
        market_index: Dict[datetime, DailyMarket],
        max_positions: int = 5,
        top_n_candidates: int = 10,
        per_position_capital_ratio: float = 0.20,  # 单票默认资金比例上限
        initial_capital: float = 1_000_000.0,
        min_holding_days: int = 3,
        win_rate_window: int = 20,        # 凯利公式用：近N次交易统计胜率
        loose_prefilter: bool = False,    # 宽松预过滤（真实市场模式）
    ):
        """
        Args:
            universe: 候选股票池元信息。
            market_index: {date: DailyMarket} 索引。
            max_positions: 最大同时持仓数。
            top_n_candidates: 每日选股候选数。
            per_position_capital_ratio: 单票资金比例上限。
            initial_capital: 初始资金。
            min_holding_days: 最小持仓天数（避免频繁交易）。
            win_rate_window: 凯利胜率统计窗口。
            loose_prefilter: 宽松预过滤模式，适配真实市场（大盘股换手率低）。
        """
        self.universe = {s.code: s for s in universe}
        self.market_index = market_index
        self.max_positions = max_positions
        self.top_n_candidates = top_n_candidates
        self.per_position_capital_ratio = per_position_capital_ratio
        self.initial_capital = initial_capital
        self.min_holding_days = min_holding_days
        self.win_rate_window = win_rate_window
        self.loose_prefilter = loose_prefilter

        # 状态
        self._price_history: Dict[str, List[float]] = {code: [] for code in self.universe}
        self._vol_history: Dict[str, List[float]] = {code: [] for code in self.universe}
        self._entry_date: Dict[str, datetime] = {}   # code → 入场日期
        self._entry_price: Dict[str, float] = {}     # code → 入场均价
        # 凯利统计：近N次平仓盈亏
        self._trade_outcomes: deque = deque(maxlen=win_rate_window)

        # 每日评分记录（供可视化）
        self.daily_scores: List[Dict[str, Any]] = []
        self.daily_signals: List[Dict[str, Any]] = []

    # ---------- 策略回调 ----------

    def on_bar(self, ctx: Context) -> List[Order]:
        market = self.market_index.get(ctx.date)
        if market is None:
            return []

        # 更新价格历史
        for code in self.universe:
            bar = market.bars.get(code)
            if bar:
                self._price_history[code].append(bar.close)
                self._vol_history[code].append(bar.volume)

        # 1. 对所有股票评分
        scores = self._score_all(market, ctx)

        # 2. 持仓检查卖出
        sell_orders = self._gen_sell_orders(ctx, market, scores)

        # 3. 候选买入
        buy_orders = self._gen_buy_orders(ctx, market, scores)

        orders = sell_orders + buy_orders

        # 记录每日评分（Top N + 持仓）
        self._record_daily(ctx, market, scores, orders)

        return orders

    def on_fill(self, ctx: Context, fill) -> None:
        """成交回报：更新入场记录与凯利统计。"""
        if fill.status != "FILLED":
            return
        if fill.side == "BUY":
            self._entry_date[fill.code] = ctx.date
            self._entry_price[fill.code] = fill.price
        else:  # SELL
            entry = self._entry_price.pop(fill.code, 0.0)
            self._entry_date.pop(fill.code, None)
            if entry > 0:
                pnl_pct = (fill.price - entry) / entry
                self._trade_outcomes.append(pnl_pct)

    # ---------- 评分 ----------

    def _score_all(self, market: DailyMarket, ctx: Context) -> List[StockScore]:
        """对全市场股票评分。"""
        scores: List[StockScore] = []
        for code, meta in self.universe.items():
            bar = market.bars.get(code)
            fund = market.fundamentals.get(code)
            cap = market.capitals.get(code)
            if not bar or not fund or not cap:
                continue

            closes = self._price_history[code]
            vols = self._vol_history[code]

            # 行业指数
            ind_idx = market.industry_indices.get(meta.industry)
            industry_ma10 = ind_idx.ma10 if ind_idx else None
            industry_close = ind_idx.close if ind_idx else None

            # 行业近5日涨幅排名分位（简化：用该行业指数近5日涨幅在所有行业中排名）
            industry_5d_pct = self._industry_5d_rank(market, meta.industry)

            # 持仓均价
            pos = ctx.portfolio.get_position(code) if ctx.portfolio else None
            avg_cost = pos.avg_cost if pos and pos.qty > 0 else 0.0

            # 凯利参数
            win_rate, avg_win, avg_loss = self._kelly_stats()

            score = score_stock(
                code=code,
                name=meta.name,
                industry=meta.industry,
                roe=fund.roe,
                rev_growth=fund.rev_growth,
                fcf_history=fund.fcf_history,
                pe=fund.pe,
                pb=fund.pb,
                ps=fund.ps,
                dividend_yield=fund.dividend_yield,
                industry_pe_pct=fund.industry_pe_pct,
                industry_pb_pct=fund.industry_pb_pct,
                industry_ps_pct=fund.industry_ps_pct,
                closes=closes,
                volumes=vols,
                turnover_rate=bar.turnover_rate,
                tail_rally_pct=bar.tail_rally_pct,
                industry_5d_rank_pct=industry_5d_pct,
                industry_index_ma10=industry_ma10,
                industry_index_close=industry_close,
                northbound_3d=cap.northbound_3d,
                main_force_net=cap.main_force_net,
                circ_market_cap=fund.circ_market_cap,
                avg_cost=avg_cost,
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss,
                loose_prefilter=self.loose_prefilter,
            )
            scores.append(score)
        return scores

    def _industry_5d_rank(self, market: DailyMarket, industry: str) -> float:
        """计算行业近5日涨幅排名分位。

        简化：用该行业指数当日相对5日前的涨幅在所有行业中的排名。
        """
        # 用市场数据的日期回溯5日
        dates = sorted(self.market_index.keys())
        idx = dates.index(market.date) if market.date in dates else len(dates) - 1
        if idx < 5:
            return 0.5
        start_date = dates[idx - 5]
        end_date = market.date
        rets: Dict[str, float] = {}
        for ind, series in self._industry_returns.items():
            if start_date in series and end_date in series:
                rets[ind] = series[end_date] / series[start_date] - 1.0
        if not rets or industry not in rets:
            return 0.5
        sorted_rets = sorted(rets.values())
        rank = sorted_rets.index(rets[industry])
        return rank / max(1, len(sorted_rets) - 1)

    @property
    def _industry_returns(self) -> Dict[str, Dict[datetime, float]]:
        """缓存行业指数收盘序列。"""
        if not hasattr(self, "_ind_ret_cache"):
            cache: Dict[str, Dict[datetime, float]] = {}
            for date, mkt in self.market_index.items():
                for ind, idx in mkt.industry_indices.items():
                    cache.setdefault(ind, {})[date] = idx.close
            self._ind_ret_cache = cache
        return self._ind_ret_cache

    def _kelly_stats(self) -> tuple:
        """从近N次交易计算胜率、平均盈亏。"""
        if not self._trade_outcomes:
            return 0.5, 0.0, 0.0
        outs = list(self._trade_outcomes)
        wins = [o for o in outs if o > 0]
        losses = [o for o in outs if o < 0]
        win_rate = len(wins) / len(outs) if outs else 0.5
        avg_win = float(sum(wins) / len(wins)) if wins else 0.0
        avg_loss = float(sum(-l for l in losses) / len(losses)) if losses else 0.0
        return win_rate, avg_win, avg_loss

    # ---------- 卖出 ----------

    def _gen_sell_orders(self, ctx: Context, market: DailyMarket,
                         scores: List[StockScore]) -> List[Order]:
        """对持仓生成卖出订单。"""
        if not ctx.portfolio:
            return []
        orders: List[Order] = []
        score_map = {s.code: s for s in scores}

        for code, pos in list(ctx.portfolio.positions.items()):
            if pos.qty <= 0:
                continue
            # 最小持仓天数
            entry_date = self._entry_date.get(code)
            if entry_date is not None:
                days_held = (ctx.date - entry_date).days
                if days_held < self.min_holding_days:
                    continue

            closes = self._price_history[code]
            vols = self._vol_history[code]
            meta = self.universe.get(code)
            ind_idx = market.industry_indices.get(meta.industry) if meta else None

            sell_ok, reason = check_sell_signal(
                current_price=closes[-1] if closes else 0.0,
                avg_cost=pos.avg_cost,
                closes=closes,
                volumes=vols,
                industry_index_close=ind_idx.close if ind_idx else None,
                industry_index_ma10=ind_idx.ma10 if ind_idx else None,
            )
            if sell_ok:
                orders.append(Order(
                    time=ctx.date,
                    code=code,
                    side="SELL",
                    qty=int(pos.qty),
                    price=float(closes[-1]) if closes else 0.0,
                    reason=f"卖出信号：{reason}",
                ))
                self.daily_signals.append({
                    "date": ctx.date.isoformat(),
                    "code": code,
                    "name": meta.name if meta else code,
                    "side": "SELL",
                    "price": float(closes[-1]) if closes else 0.0,
                    "qty": int(pos.qty),
                    "reason": reason,
                })
        return orders

    # ---------- 买入 ----------

    def _gen_buy_orders(self, ctx: Context, market: DailyMarket,
                        scores: List[StockScore]) -> List[Order]:
        """对候选买入股票生成订单。"""
        if not ctx.portfolio:
            return []

        # 当前持仓数
        held = sum(1 for p in ctx.portfolio.positions.values() if p.qty > 0)
        slots = self.max_positions - held
        if slots <= 0:
            return []

        # 过滤：通过预过滤 + 买入信号 + 非持仓
        candidates = [
            s for s in scores
            if s.buy_signal and s.prefilters_passed
            and s.code not in {c for c, p in ctx.portfolio.positions.items() if p.qty > 0}
        ]
        # 按综合得分排序
        candidates.sort(key=lambda s: s.total, reverse=True)
        candidates = candidates[:slots]

        orders: List[Order] = []
        for s in candidates:
            meta = self.universe.get(s.code)
            if not meta:
                continue
            # 凯利仓位决定资金
            ratio = s.kelly_ratio if s.kelly_ratio > 0 else self.per_position_capital_ratio
            ratio = min(ratio, self.per_position_capital_ratio)
            target_capital = ctx.portfolio.nav * ratio
            price = s.price
            if price <= 0:
                continue
            # 数量按 100 股取整
            qty = int(target_capital / price / 100) * 100
            if qty < 100:
                continue
            # 资金检查
            if ctx.portfolio.cash < qty * price * 1.003:  # 预留费用
                # 减少数量
                qty = int(ctx.portfolio.cash * 0.95 / price / 100) * 100
                if qty < 100:
                    continue
            orders.append(Order(
                time=ctx.date,
                code=s.code,
                side="BUY",
                qty=qty,
                price=float(price),
                reason=(f"买入：综合{s.total:.1f}分 "
                        f"(基本{s.fundamental.total:.0f}/技术{s.technical.total:.0f}/"
                        f"资金{s.capital.total:.0f}) 凯利{ratio*100:.1f}%"),
            ))
            self.daily_signals.append({
                "date": ctx.date.isoformat(),
                "code": s.code,
                "name": meta.name,
                "side": "BUY",
                "price": float(price),
                "qty": qty,
                "reason": f"综合得分 {s.total:.1f}，凯利仓位 {ratio*100:.1f}%",
                "score": s.to_dict(),
            })
        return orders

    # ---------- 记录 ----------

    def _record_daily(self, ctx: Context, market: DailyMarket,
                      scores: List[StockScore], orders: List[Order]) -> None:
        """记录每日评分 Top N（供可视化）。"""
        top = sorted(scores, key=lambda s: s.total, reverse=True)[:self.top_n_candidates]
        self.daily_scores.append({
            "date": ctx.date.isoformat(),
            "top_n": [s.to_dict() for s in top],
            "held": [
                {
                    "code": code,
                    "name": self.universe[code].name if code in self.universe else code,
                    "qty": int(pos.qty),
                    "avg_cost": float(pos.avg_cost),
                    "current_price": float(pos.current_price),
                    "unrealized_pnl_pct": float(pos.unrealized_pnl_pct) if pos.qty > 0 else 0.0,
                }
                for code, pos in (ctx.portfolio.positions.items() if ctx.portfolio else [])
                if pos.qty > 0
            ],
            "orders": [
                {
                    "code": o.code,
                    "side": o.side,
                    "qty": o.qty,
                    "price": o.price,
                    "reason": o.reason,
                }
                for o in orders
            ],
        })
