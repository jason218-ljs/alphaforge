"""选股打分策略（基于 stock-scoring_auto_workflow.md）。

打分体系（满分100）：
  基本面40 = ROE(10)+营收增速(10)+FCF(8)+估值(7)+股息率(5)，行业自适应
  技术面40 = 均线多头(12)+尾盘拉升(8)+量能趋势(8)+换手率(6)+行业热度(6)
  资金面20 = 北向(10)+主力(10)，当前默认10（待开发）

买入：预过滤全满足 + 基本面≥24 + 技术面≥28 + 综合≥65
卖出：止盈15-20% / 止损(跌破MA20且放量) / 趋势消退(跌破MA10)
仓位：凯利公式，单票10%-30%

数据说明：
  - 技术面/预过滤/MA20：基于历史K线，无前视
  - 基本面：最新快照（非PIT），存在前视偏差，仅用于池内排序区分
  - 尾盘涨幅：无分时数据，用日涨幅(close/prev_close-1)代理
  - 行业热度/趋势消退：无行业指数，用个股近5日涨幅代理
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from alphaforge.logger import get_logger
from alphaforge.models.order import Order
from alphaforge.strategy.base import Context, StrategyBase

logger = get_logger("strategy.scoring")


# ---------- 行业自适应阈值表 ----------

INDUSTRY_THRESHOLDS = {
    "银行":   {"roe": 10, "rev": 8,  "div": 4, "val_type": "pb"},
    "证券":   {"roe": 8,  "rev": 15, "div": 2, "val_type": "pb"},
    "保险":   {"roe": 10, "rev": 10, "div": 2, "val_type": "pb"},
    "白酒":   {"roe": 18, "rev": 10, "div": 1, "val_type": "pe"},
    "食品":   {"roe": 12, "rev": 10, "div": 2, "val_type": "pe"},
    "医药":   {"roe": 12, "rev": 15, "div": 1, "val_type": "pe"},
    "电子":   {"roe": 8,  "rev": 20, "div": 1, "val_type": "pe"},
    "半导体": {"roe": 3,  "rev": 30, "div": 1, "val_type": "ps"},
    "机械":   {"roe": 10, "rev": 15, "div": 2, "val_type": "pe"},
    "传媒":   {"roe": 8,  "rev": 15, "div": 2, "val_type": "pe"},
    "电力设备": {"roe": 10, "rev": 20, "div": 1, "val_type": "pe"},
    "物流":   {"roe": 10, "rev": 15, "div": 2, "val_type": "pe"},
    "默认":   {"roe": 10, "rev": 15, "div": 2, "val_type": "pe"},
}


def _industry_threshold(industry: str) -> Dict[str, Any]:
    return INDUSTRY_THRESHOLDS.get(industry) or INDUSTRY_THRESHOLDS["默认"]


class ScoringStrategy(StrategyBase):
    """选股打分策略。"""

    name = "external_scorer_scoring"

    def __init__(
        self,
        pool: List[Dict[str, str]],          # [{code, industry}]
        klines: Dict[str, List[Dict]],       # {code: [{date,open,close,high,low,volume}]}
        fundamentals: Dict[str, Dict],       # {code: {pe,pb,roe,float_mv,...}}
        rebalance_days: int = 5,
        top_n: int = 5,
        capital_per_stock: float = 0.20,     # 单票默认资金比例（凯利约束在10-30%）
        stop_profit_low: float = 0.15,
        stop_profit_high: float = 0.20,
        stop_loss_ma20_vol_ratio: float = 1.2,
    ):
        self.pool = pool
        self.klines = klines
        self.fundamentals = fundamentals
        self.rebalance_days = rebalance_days
        self.top_n = top_n
        self.capital_per_stock = capital_per_stock
        self.stop_profit_low = stop_profit_low
        self.stop_profit_high = stop_profit_high
        self.stop_loss_ma20_vol_ratio = stop_loss_ma20_vol_ratio
        # 建立日期索引：code -> {date_str: bar_index}
        self._date_idx: Dict[str, Dict[str, int]] = {}
        for code, bars in klines.items():
            self._date_idx[code] = {b["date"]: i for i, b in enumerate(bars)}
        # 持仓买入价记录（用于止盈止损）
        self._buy_prices: Dict[str, float] = {}
        self._bar_counter = 0
        self._last_rebalance = -999

    def initialize(self, ctx: Context) -> None:
        logger.info("打分策略初始化：候选池 %d 只", len(self.pool))

    def on_bar(self, ctx: Context) -> List[Order]:
        orders: List[Order] = []
        date_str = ctx.date.strftime("%Y-%m-%d") if hasattr(ctx.date, "strftime") else str(ctx.date)
        self._bar_counter += 1

        # 1. 每日检查卖出条件（止盈/止损/趋势消退）
        orders.extend(self._check_sells(ctx, date_str))

        # 2. 调仓日：打分选股买入
        if self._bar_counter - self._last_rebalance >= self.rebalance_days:
            self._last_rebalance = self._bar_counter
            orders.extend(self._check_buys(ctx, date_str))

        return orders

    # ---------- 卖出 ----------

    def _check_sells(self, ctx: Context, date_str: str) -> List[Order]:
        orders: List[Order] = []
        pf = ctx.portfolio
        if not pf or not pf.positions:
            return orders
        for code, pos in list(pf.positions.items()):
            if pos.qty <= 0:
                continue
            bars = self.klines.get(code, [])
            idx_map = self._date_idx.get(code, {})
            i = idx_map.get(date_str)
            if i is None or i < 20:
                continue
            close = bars[i]["close"]
            prev_close = bars[i - 1]["close"]
            ma20 = self._ma(bars, i, 20)
            ma10 = self._ma(bars, i, 10)
            vol = bars[i]["volume"]
            avg5_vol = self._avg(bars, i, 5, "volume")
            buy_price = pos.avg_cost if pos.avg_cost > 0 else bars[i]["close"]

            reason = None
            # 止盈：涨幅达15-20%（卖一半）或>20%（全卖）
            gain = (close - buy_price) / buy_price if buy_price > 0 else 0
            if gain >= self.stop_profit_high:
                reason = f"止盈{gain:.1%}≥{self.stop_profit_high:.0%}"
                qty = pos.qty
            elif gain >= self.stop_profit_low:
                reason = f"止盈{gain:.1%}分批减仓"
                qty = pos.qty // 2
                if qty < 100:
                    continue
            # 止损：跌破MA20且放量
            elif close < ma20 and vol > avg5_vol * self.stop_loss_ma20_vol_ratio:
                reason = f"止损:跌破MA20({ma20:.2f})且放量"
                qty = pos.qty
            # 趋势消退：跌破MA10
            elif close < ma10 and prev_close >= ma10:
                reason = f"趋势消退:跌破MA10({ma10:.2f})"
                qty = pos.qty
            else:
                continue

            orders.append(Order(
                time=ctx.date, code=code, side="SELL", qty=qty, price=close,
                trader="scoring", reason=reason,
            ))
        return orders

    # ---------- 买入 ----------

    def _check_buys(self, ctx: Context, date_str: str) -> List[Order]:
        orders: List[Order] = []
        scored: List[Tuple[float, str, Dict[str, Any]]] = []

        for stock in self.pool:
            code = stock["code"]
            industry = stock.get("industry", "默认")
            bars = self.klines.get(code, [])
            idx_map = self._date_idx.get(code, {})
            i = idx_map.get(date_str)
            if i is None or i < 20:
                continue

            # 预过滤
            ok, pre_reason = self._prefilter(code, industry, bars, i)
            if not ok:
                continue

            # 打分
            fund = self._score_fundamental(code, industry)
            tech = self._score_technical(code, bars, i)
            capital = 10.0  # 资金面默认中性
            total = fund + tech + capital

            # 买入门槛
            if fund < 24 or tech < 28 or total < 65:
                continue

            scored.append((total, code, {"fund": fund, "tech": tech,
                                          "capital": capital, "total": total}))

        # 取 Top N
        scored.sort(reverse=True)
        pf = ctx.portfolio
        for total, code, detail in scored[: self.top_n]:
            # 已持仓的跳过
            if pf and code in pf.positions and pf.positions[code].qty > 0:
                continue
            # 资金约束：凯利仓位
            close = self.klines[code][self._date_idx[code][date_str]]["close"]
            target_value = pf.nav * self._kelly_fraction()
            qty = int(target_value / close / 100) * 100
            if qty < 100:
                continue
            if pf.cash < qty * close * 1.003:  # 留足费用
                # 资金不足，按可用资金买
                qty = int(pf.cash * 0.98 / close / 100) * 100
                if qty < 100:
                    continue
            orders.append(Order(
                time=ctx.date, code=code, side="BUY", qty=qty, price=close,
                trader="scoring", reason=f"打分{total:.0f}(基{detail['fund']:.0f}/技{detail['tech']:.0f})",
            ))
        return orders

    # ---------- 预过滤 ----------

    def _prefilter(self, code: str, industry: str, bars: List[Dict], i: int) -> Tuple[bool, str]:
        fd = self.fundamentals.get(code, {})
        close = bars[i]["close"]
        prev_close = bars[i - 1]["close"] if i > 0 else close

        # 1. 流通市值 ∈ [50亿, 500亿]
        float_mv_yi = (fd.get("float_mv", 0) or 0) / 1e8
        if not (50 <= float_mv_yi <= 500):
            return False, f"流通市值{float_mv_yi:.0f}亿不在[50,500]"

        # 2. 尾盘拉升 ∈ [3%,5%]（用日涨幅代理；近3日内任一天涨幅∈[3%,5%]即视为近期形态达标）
        tail_hit = False
        for j in range(max(0, i - 2), i + 1):
            if j < 1:
                continue
            cj, pj = bars[j]["close"], bars[j - 1]["close"]
            rj = (cj - pj) / pj if pj > 0 else 0
            if 0.03 <= rj <= 0.05:
                tail_hit = True
                break
        if not tail_hit:
            return False, "近3日无尾盘拉升(涨幅3-5%)"

        # 3. 换手率 ∈ [3%,15%]（近3日均值，避免单日快照失真）
        turnover = self._turnover(code, bars, i, window=3)
        if not (3 <= turnover <= 15):
            return False, f"换手率{turnover:.1f}%不在[3,15]"

        # 4. 近3日成交量逐日放大
        v0, v1, v2 = bars[i - 2]["volume"], bars[i - 1]["volume"], bars[i]["volume"]
        if not (v2 > v1 > v0):
            return False, "近3日量能未逐日放大"

        # 5. 站上 MA5/MA10/MA20
        ma5 = self._ma(bars, i, 5)
        ma10 = self._ma(bars, i, 10)
        ma20 = self._ma(bars, i, 20)
        if not (close > ma5 > 0 and close > ma10 > 0 and close > ma20 > 0):
            return False, "未站上MA5/10/20"

        return True, "通过"

    # ---------- 基本面打分（40）----------

    def _score_fundamental(self, code: str, industry: str) -> float:
        fd = self.fundamentals.get(code, {})
        th = _industry_threshold(industry)
        score = 0.0

        # ROE (10分)：达阈值满分，每降1%扣0.5
        roe = fd.get("roe", 0) or 0
        if roe >= th["roe"]:
            score += 10
        else:
            score += max(0, 10 - (th["roe"] - roe) * 0.5)

        # 营收增速 (10分)：达阈值满分，缺失给中位5分，否则按比例
        rev = fd.get("revenue_growth", 0) or 0
        if rev == 0:  # F10接口未返回，按合格池给中位分
            score += 5
        elif rev >= th["rev"]:
            score += 10
        else:
            score += max(0, 10 * rev / th["rev"]) if th["rev"] > 0 else 5

        # FCF (8分)：无PIT数据，金融/半导体豁免满分，其他合格池给中等偏上6分
        # （策略文档：data-collection 已排除现金流为负的差公司，池内 FCF 视为合格）
        if industry in ("银行", "证券", "保险", "半导体"):
            score += 8
        else:
            score += 6

        # 估值 (7分)：PE/PB 处于低位满分（简化分档）
        if th["val_type"] == "pb":
            pb = fd.get("pb", 0) or 0
            if 0 < pb < 1: score += 7
            elif pb < 2: score += 5
            elif pb < 4: score += 3
            else: score += 1
        else:
            pe = fd.get("pe", 0) or 0
            if 0 < pe < 15: score += 7
            elif pe < 25: score += 5
            elif pe < 40: score += 3
            else: score += 1

        # 股息率 (5分)：无可靠数据，合格池给中等偏上3分
        score += 3

        return min(score, 40.0)

    # ---------- 技术面打分（40）----------

    def _score_technical(self, code: str, bars: List[Dict], i: int) -> float:
        close = bars[i]["close"]
        prev_close = bars[i - 1]["close"] if i > 0 else close
        score = 0.0

        # 均线多头排列 (12分)
        ma5 = self._ma(bars, i, 5)
        ma10 = self._ma(bars, i, 10)
        ma20 = self._ma(bars, i, 20)
        stands = sum(1 for m in (ma5, ma10, ma20) if m > 0 and close > m)
        if stands == 3:
            score += 12
        elif stands >= 1:
            score += 6
        else:
            score += 0

        # 尾盘拉升 (8分)：日涨幅∈[3%,5%]满分，接近边界5，区间外0
        daily_ret = (close - prev_close) / prev_close if prev_close > 0 else 0
        if 0.03 <= daily_ret <= 0.05:
            score += 8
        elif 0.025 <= daily_ret <= 0.055:
            score += 5

        # 成交量趋势 (8分)：近3日逐日放大满分8
        v0, v1, v2 = bars[i - 2]["volume"], bars[i - 1]["volume"], bars[i]["volume"]
        if v2 > v1 > v0:
            score += 8
        elif v2 > v0:
            score += 4

        # 换手率 (6分)：[5%,10%]满分，[3%,5%]或[10%,15%]3分
        turnover = self._turnover_by_bars(bars, i, close, code=code)
        if 5 <= turnover <= 10:
            score += 6
        elif 3 <= turnover <= 15:
            score += 3

        # 行业热度 (6分)：用个股近5日涨幅代理（无行业指数）
        ret5 = (close - bars[i - 5]["close"]) / bars[i - 5]["close"] if i >= 5 and bars[i - 5]["close"] > 0 else 0
        if ret5 >= 0.05:
            score += 6
        elif ret5 >= 0.02:
            score += 3

        return min(score, 40.0)

    # ---------- 工具 ----------

    def _ma(self, bars: List[Dict], i: int, period: int) -> float:
        if i < period - 1:
            return 0.0
        return sum(bars[j]["close"] for j in range(i - period + 1, i + 1)) / period

    def _avg(self, bars: List[Dict], i: int, period: int, key: str) -> float:
        if i < period - 1:
            return 0.0
        return sum(bars[j][key] for j in range(i - period + 1, i + 1)) / period

    def _turnover(self, code: str, bars: List[Dict], i: int,
                  window: int = 1,
                  current_close: Optional[float] = None) -> float:
        """换手率%：流通股本=最新流通市值/最新价（近似固定），历史日成交量取近 window 日均值。"""
        fd = self.fundamentals.get(code, {})
        float_mv = fd.get("float_mv", 0) or 0
        # 用最新K线价反推流通股本（股本相对稳定），避免历史价失真
        latest_close = bars[-1]["close"] if bars else (current_close or 0)
        if float_mv <= 0 or latest_close <= 0:
            return 0.0
        float_shares = float_mv / latest_close      # 流通股本（约，固定）
        start = max(0, i - window + 1)
        avg_vol = sum(bars[j]["volume"] for j in range(start, i + 1)) / (i - start + 1)
        vol_shares = avg_vol * 100                  # 腾讯volume单位为手
        return vol_shares / float_shares * 100 if float_shares > 0 else 0.0

    def _turnover_by_bars(self, bars: List[Dict], i: int, close: float,
                          code: str = "") -> float:
        return self._turnover(code, bars, i, current_close=close)

    def _kelly_fraction(self) -> float:
        """凯利公式：f=(p*b-q)/b，简化 p=0.5, b=1.5，约束[10%,30%]。"""
        p, b = 0.5, 1.5
        q = 1 - p
        f = (p * b - q) / b
        return max(0.10, min(0.30, f))
