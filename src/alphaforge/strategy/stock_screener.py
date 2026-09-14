"""选股评分核心模块。

严格依据 `stock-scoring_auto_workflow.md` 实现：
- 基本面得分（40分）：ROE / 营收增速 / 自由现金流 / 估值 / 股息率
- 技术面得分（40分）：均线多头 / 尾盘拉升 / 量能趋势 / 换手率 / 行业热度
- 资金面得分（20分）：北向资金 / 主力资金
- 行业自适应阈值（按二级分类）
- 买卖建议：买入信号（预过滤 + 打分门槛）、卖出信号（止盈/止损/趋势消退）
- 凯利公式仓位建议（10%-30% 上下限）

硬约束：
- H1：行业自适应必须按二级分类执行
- H5：MA20 是买卖判断核心，未站上不得给买入建议
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ============================================================================
# 行业自适应阈值参考表（按二级分类）
# ============================================================================

INDUSTRY_THRESHOLDS: Dict[str, Dict[str, float]] = {
    # 金融
    "国有大行":      {"roe": 10.0, "rev_growth": 8.0,  "fcf_exempt": True,  "valuation": "PB", "div_yield": 4.0},
    "股份制银行":    {"roe": 12.0, "rev_growth": 8.0,  "fcf_exempt": True,  "valuation": "PB", "div_yield": 4.0},
    "城商行":        {"roe": 11.0, "rev_growth": 10.0, "fcf_exempt": True,  "valuation": "PB", "div_yield": 3.5},
    "保险":          {"roe": 12.0, "rev_growth": 10.0, "fcf_exempt": True,  "valuation": "PE", "div_yield": 2.5},
    "券商":          {"roe": 8.0,  "rev_growth": 15.0, "fcf_exempt": True,  "valuation": "PE", "div_yield": 1.5},
    # 消费
    "白酒":          {"roe": 18.0, "rev_growth": 10.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    "食品饮料":      {"roe": 15.0, "rev_growth": 10.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 2.0},
    "家电":          {"roe": 13.0, "rev_growth": 10.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 2.5},
    "医药生物":      {"roe": 12.0, "rev_growth": 15.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    # 科技
    "半导体":        {"roe": 3.0,  "rev_growth": 30.0, "fcf_exempt": True,  "valuation": "PS", "div_yield": 0.5},
    "消费电子":      {"roe": 8.0,  "rev_growth": 20.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    "计算机":        {"roe": 8.0,  "rev_growth": 20.0, "fcf_exempt": False, "valuation": "PS", "div_yield": 0.5},
    "通信":          {"roe": 8.0,  "rev_growth": 15.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    # 周期
    "钢铁":          {"roe": 8.0,  "rev_growth": 20.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 3.0},
    "有色金属":      {"roe": 10.0, "rev_growth": 20.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 2.0},
    "化工":          {"roe": 10.0, "rev_growth": 20.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 2.0},
    "建筑材料":      {"roe": 10.0, "rev_growth": 15.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 2.5},
    # 新能源
    "光伏设备":      {"roe": 8.0,  "rev_growth": 30.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    "电池":          {"roe": 10.0, "rev_growth": 30.0, "fcf_exempt": False, "valuation": "PE", "div_yield": 1.0},
    # 公用事业
    "电力":          {"roe": 8.0,  "rev_growth": 8.0,  "fcf_exempt": False, "valuation": "PB", "div_yield": 3.0},
    "水务":          {"roe": 8.0,  "rev_growth": 8.0,  "fcf_exempt": False, "valuation": "PB", "div_yield": 3.0},
}

DEFAULT_THRESHOLD = {"roe": 10.0, "rev_growth": 15.0, "fcf_exempt": False,
                     "valuation": "PE", "div_yield": 1.5}


def get_industry_threshold(industry: str) -> Dict[str, Any]:
    """按二级分类获取行业阈值，未知行业用默认。"""
    return INDUSTRY_THRESHOLDS.get(industry, DEFAULT_THRESHOLD)


# ============================================================================
# 评分结果数据结构
# ============================================================================

@dataclass
class FundamentalScore:
    """基本面得分明细（满分40）。"""
    roe: float = 0.0           # 10
    rev_growth: float = 0.0    # 10
    free_cash_flow: float = 0.0  # 8
    valuation: float = 0.0     # 7
    dividend: float = 0.0      # 5
    total: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roe": round(self.roe, 2),
            "rev_growth": round(self.rev_growth, 2),
            "free_cash_flow": round(self.free_cash_flow, 2),
            "valuation": round(self.valuation, 2),
            "dividend": round(self.dividend, 2),
            "total": round(self.total, 2),
            "details": self.details,
        }


@dataclass
class TechnicalScore:
    """技术面得分明细（满分40）。"""
    ma_alignment: float = 0.0    # 12
    tail_rally: float = 0.0      # 8
    volume_trend: float = 0.0    # 8
    turnover: float = 0.0        # 6
    industry_heat: float = 0.0   # 6
    total: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ma_alignment": round(self.ma_alignment, 2),
            "tail_rally": round(self.tail_rally, 2),
            "volume_trend": round(self.volume_trend, 2),
            "turnover": round(self.turnover, 2),
            "industry_heat": round(self.industry_heat, 2),
            "total": round(self.total, 2),
            "details": self.details,
        }


@dataclass
class CapitalScore:
    """资金面得分明细（满分20）。"""
    northbound: float = 0.0    # 10
    main_force: float = 0.0    # 10
    total: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "northbound": round(self.northbound, 2),
            "main_force": round(self.main_force, 2),
            "total": round(self.total, 2),
            "details": self.details,
        }


@dataclass
class StockScore:
    """单只股票综合评分。"""
    code: str
    name: str = ""
    industry: str = ""
    fundamental: FundamentalScore = field(default_factory=FundamentalScore)
    technical: TechnicalScore = field(default_factory=TechnicalScore)
    capital: CapitalScore = field(default_factory=CapitalScore)
    total: float = 0.0
    buy_signal: bool = False
    sell_signal: bool = False
    signal_reason: str = ""
    kelly_ratio: float = 0.0      # 凯利仓位建议（0-1）
    ma20_above: bool = False      # 是否站上MA20
    prefilters_passed: bool = False  # 预过滤是否通过
    price: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "industry": self.industry,
            "price": round(self.price, 4),
            "fundamental": self.fundamental.to_dict(),
            "technical": self.technical.to_dict(),
            "capital": self.capital.to_dict(),
            "total": round(self.total, 2),
            "buy_signal": self.buy_signal,
            "sell_signal": self.sell_signal,
            "signal_reason": self.signal_reason,
            "kelly_ratio": round(self.kelly_ratio, 4),
            "ma20_above": self.ma20_above,
            "prefilters_passed": self.prefilters_passed,
        }


# ============================================================================
# 基本面评分（40分）
# ============================================================================

def score_fundamental(
    roe: float,
    rev_growth: float,
    fcf_history: List[float],   # 近3年自由现金流
    pe: float,
    pb: float,
    ps: float,
    dividend_yield: float,
    industry: str,
    industry_pe_percentile: float = 0.5,  # 该股PE在行业内的分位数 0-1
    industry_pb_percentile: float = 0.5,
    industry_ps_percentile: float = 0.5,
) -> FundamentalScore:
    """计算基本面得分。

    Args:
        roe: ROE（%）
        rev_growth: 营收增速（%）
        fcf_history: 近3年自由现金流列表
        pe/pb/ps: 估值指标
        dividend_yield: 股息率（%）
        industry: 二级行业分类
        industry_*_percentile: 该股估值在行业内的分位数
    """
    thr = get_industry_threshold(industry)
    score = FundamentalScore()
    details: Dict[str, Any] = {"industry_threshold": thr}

    # 1. ROE 10分
    roe_thr = thr["roe"]
    if roe >= roe_thr:
        score.roe = 10.0
    else:
        score.roe = max(0.0, 10.0 - (roe_thr - roe) * 0.5)
    details["roe"] = {"value": roe, "threshold": roe_thr, "score": score.roe}

    # 2. 营收增速 10分
    rev_thr = thr["rev_growth"]
    if rev_growth >= rev_thr:
        score.rev_growth = 10.0
    else:
        # 线性扣分，每低于阈值1%扣0.5分
        score.rev_growth = max(0.0, 10.0 - (rev_thr - rev_growth) * 0.5)
    details["rev_growth"] = {"value": rev_growth, "threshold": rev_thr, "score": score.rev_growth}

    # 3. 自由现金流 8分
    if thr["fcf_exempt"]:
        score.free_cash_flow = 8.0  # 金融/半导体豁免
        details["fcf"] = {"exempt": True, "score": 8.0}
    else:
        positive_years = sum(1 for f in fcf_history[-3:] if f > 0)
        if positive_years >= 3:
            score.free_cash_flow = 8.0
        elif positive_years == 2:
            score.free_cash_flow = 5.0
        elif positive_years == 1:
            score.free_cash_flow = 2.0
        else:
            score.free_cash_flow = 0.0
        details["fcf"] = {"history": fcf_history[-3:], "positive_years": positive_years,
                          "score": score.free_cash_flow}

    # 4. 估值 7分（按行业看 PE/PB/PS 分位数）
    val_type = thr["valuation"]
    if val_type == "PE":
        pct = industry_pe_percentile
        val_value = pe
    elif val_type == "PB":
        pct = industry_pb_percentile
        val_value = pb
    else:  # PS
        pct = industry_ps_percentile
        val_value = ps

    if pct <= 0.25:
        score.valuation = 7.0
    elif pct <= 0.50:
        score.valuation = 5.0
    elif pct <= 0.75:
        score.valuation = 3.0
    else:
        score.valuation = 1.0
    details["valuation"] = {"type": val_type, "value": val_value, "percentile": pct,
                            "score": score.valuation}

    # 5. 股息率 5分
    div_thr = thr["div_yield"]
    if dividend_yield >= div_thr:
        score.dividend = 5.0
    else:
        score.dividend = max(0.0, 5.0 * dividend_yield / div_thr) if div_thr > 0 else 0.0
    details["dividend"] = {"value": dividend_yield, "threshold": div_thr,
                           "score": score.dividend}

    score.total = score.roe + score.rev_growth + score.free_cash_flow + score.valuation + score.dividend
    score.details = details
    return score


# ============================================================================
# 技术面评分（40分）
# ============================================================================

def score_technical(
    closes: List[float],          # 近20+日收盘价
    volumes: List[float],         # 近5日成交量
    turnover_rate: float,         # 当日换手率（%）
    tail_rally_pct: float,        # 前一日尾盘涨幅（%）
    industry_5d_rank_pct: float,  # 所属行业近5日涨幅排名分位 0-1（1=最好）
    industry_index_ma10: Optional[float] = None,  # 行业指数MA10
    industry_index_close: Optional[float] = None,  # 行业指数当日收盘
) -> TechnicalScore:
    """计算技术面得分。"""
    score = TechnicalScore()
    details: Dict[str, Any] = {}

    # 1. 均线多头排列 12分
    if len(closes) >= 20:
        ma5 = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:]))
        ma20 = float(np.mean(closes[-20:]))
        price = closes[-1]
        above = [price >= ma5, price >= ma10, price >= ma20]
        if all(above):
            score.ma_alignment = 12.0
        elif sum(above) >= 2:
            score.ma_alignment = 6.0
        else:
            score.ma_alignment = 0.0
        details["ma"] = {"price": price, "ma5": ma5, "ma10": ma10, "ma20": ma20,
                         "above": above, "score": score.ma_alignment}
    else:
        details["ma"] = {"error": "数据不足", "score": 0.0}

    # 2. 尾盘拉升 8分
    if 3.0 <= tail_rally_pct <= 5.0:
        score.tail_rally = 8.0
    elif 2.5 <= tail_rally_pct < 3.0 or 5.0 < tail_rally_pct <= 5.5:
        score.tail_rally = 5.0
    else:
        score.tail_rally = 0.0
    details["tail_rally"] = {"value": tail_rally_pct, "score": score.tail_rally}

    # 3. 成交量趋势 8分（近3日）
    if len(volumes) >= 3:
        v = volumes[-3:]
        if v[1] > v[0] and v[2] > v[1]:
            score.volume_trend = 8.0  # 逐日放大
        elif v[2] < v[1]:  # 先放量后缩量
            score.volume_trend = 4.0
        else:
            score.volume_trend = 0.0
        details["volume"] = {"recent_3d": v, "score": score.volume_trend}
    else:
        details["volume"] = {"error": "数据不足", "score": 0.0}

    # 4. 换手率 6分
    if 5.0 <= turnover_rate <= 10.0:
        score.turnover = 6.0
    elif 3.0 <= turnover_rate < 5.0 or 10.0 < turnover_rate <= 15.0:
        score.turnover = 3.0
    else:
        score.turnover = 0.0
    details["turnover"] = {"value": turnover_rate, "score": score.turnover}

    # 5. 行业热度 6分
    if industry_5d_rank_pct >= 0.7:  # 前30%
        score.industry_heat = 6.0
    elif industry_5d_rank_pct >= 0.5:  # 30%-50%
        score.industry_heat = 3.0
    else:
        score.industry_heat = 0.0
    details["industry_heat"] = {"rank_pct": industry_5d_rank_pct, "score": score.industry_heat}

    score.total = (score.ma_alignment + score.tail_rally + score.volume_trend
                   + score.turnover + score.industry_heat)
    score.details = details
    return score


# ============================================================================
# 资金面评分（20分）
# ============================================================================

def score_capital(
    northbound_3d: List[float],   # 近3日北向净流入（万元）
    main_force_net: float,        # 当日主力净流入（万元）
) -> CapitalScore:
    """计算资金面得分。"""
    score = CapitalScore()
    details: Dict[str, Any] = {}

    # 北向资金 10分：连续3日净流入满分
    if len(northbound_3d) >= 3:
        positive_days = sum(1 for x in northbound_3d[-3:] if x > 0)
        if positive_days == 3:
            score.northbound = 10.0
        elif positive_days == 2:
            score.northbound = 6.0
        elif positive_days == 1:
            score.northbound = 3.0
        else:
            score.northbound = 0.0
        details["northbound"] = {"recent_3d": northbound_3d[-3:],
                                 "positive_days": positive_days, "score": score.northbound}
    else:
        details["northbound"] = {"error": "数据不足", "score": 0.0}

    # 主力资金 10分：净流入为正满分
    if main_force_net > 0:
        score.main_force = 10.0
    elif main_force_net > -1000:  # 小幅流出
        score.main_force = 5.0
    else:
        score.main_force = 0.0
    details["main_force"] = {"value": main_force_net, "score": score.main_force}

    score.total = score.northbound + score.main_force
    score.details = details
    return score


# ============================================================================
# 预过滤门槛（第一关）
# ============================================================================

def check_prefilters(
    circ_market_cap: float,       # 流通市值（亿元）
    tail_rally_pct: float,        # 前一日尾盘涨幅（%）
    turnover_rate: float,         # 换手率（%）
    volumes: List[float],         # 近3日成交量
    closes: List[float],          # 收盘价序列（≥20）
    min_cap: float = 50.0,
    max_cap: float = 500.0,
    loose: bool = False,          # 宽松模式：适配真实市场大盘股低换手率特性
) -> Tuple[bool, List[str]]:
    """第一关预过滤门槛检查。

    Args:
        loose: 宽松模式。True 时放宽阈值（换手率下限 0.5%，尾盘 1.5-6%），
               适配真实市场（大盘股换手率常 <3%）。

    Returns:
        (是否通过, 失败原因列表)
    """
    failures: List[str] = []

    # 流通市值 ∈ [50亿, 500亿]（宽松模式扩大到 [50, 5000]）
    cap_max = 5000.0 if loose else max_cap
    if not (min_cap <= circ_market_cap <= cap_max):
        failures.append(f"流通市值 {circ_market_cap:.1f}亿 不在 [{min_cap}, {cap_max}] 亿")

    # 前一日尾盘涨幅（宽松模式 [1.5%, 6%]，严格 [3%, 5%]）
    tail_low, tail_high = (1.5, 6.0) if loose else (3.0, 5.0)
    if not (tail_low <= tail_rally_pct <= tail_high):
        failures.append(f"尾盘涨幅 {tail_rally_pct:.2f}% 不在 [{tail_low}%, {tail_high}%]")

    # 换手率（宽松模式 [0.5%, 25%]，严格 [3%, 15%]）
    turn_low, turn_high = (0.5, 25.0) if loose else (3.0, 15.0)
    if not (turn_low <= turnover_rate <= turn_high):
        failures.append(f"换手率 {turnover_rate:.2f}% 不在 [{turn_low}%, {turn_high}%]")

    # 近3日成交量逐日放大
    if len(volumes) >= 3:
        v = volumes[-3:]
        if not (v[1] > v[0] and v[2] > v[1]):
            failures.append("近3日成交量未逐日放大")
    else:
        failures.append("成交量数据不足")

    # 股价站上 MA5/MA10/MA20
    if len(closes) >= 20:
        ma5 = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:]))
        ma20 = float(np.mean(closes[-20:]))
        price = closes[-1]
        if not (price >= ma5 and price >= ma10 and price >= ma20):
            failures.append("未站上 MA5/MA10/MA20 三条均线")
    else:
        failures.append("收盘价数据不足20日")

    return (len(failures) == 0, failures)


# ============================================================================
# 凯利公式仓位
# ============================================================================

def kelly_position(
    win_rate: float,         # 历史胜率 0-1
    avg_win: float,          # 平均盈利
    avg_loss: float,         # 平均亏损（正数）
    min_ratio: float = 0.10,
    max_ratio: float = 0.30,
) -> float:
    """凯利公式仓位建议。

    f = (p * b - q) / b
    其中 p=胜率, q=1-p, b=赔率=avg_win/avg_loss

    约束：单票仓位 ∈ [10%, 30%]
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        # 无法计算时给中性下限
        return min_ratio
    p = win_rate
    q = 1.0 - p
    b = avg_win / avg_loss
    f = (p * b - q) / b
    # 截断到 [0, max_ratio]
    f = max(0.0, min(f, max_ratio))
    # 约束上下限
    f = max(min_ratio, min(f, max_ratio))
    return f


# ============================================================================
# 买卖信号判断
# ============================================================================

def check_buy_signal(
    score: StockScore,
    fundamental_threshold: float = 24.0,
    technical_threshold: float = 28.0,
    total_threshold: float = 65.0,
    industry_rank_pct: float = 0.7,
) -> Tuple[bool, str]:
    """第二关：打分门槛买入信号检查。

    全部满足才给买入：
    - 基本面 ≥ 24/40
    - 技术面 ≥ 28/40
    - 综合 ≥ 65/100
    - 行业近5日涨幅前30%
    - MA20 站上（硬约束 H5）
    """
    reasons = []
    if score.fundamental.total < fundamental_threshold:
        reasons.append(f"基本面 {score.fundamental.total:.1f} < {fundamental_threshold}")
    if score.technical.total < technical_threshold:
        reasons.append(f"技术面 {score.technical.total:.1f} < {technical_threshold}")
    if score.total < total_threshold:
        reasons.append(f"综合 {score.total:.1f} < {total_threshold}")
    if industry_rank_pct < 0.7:
        reasons.append("行业近5日涨幅未进前30%")
    # 硬约束 H5：MA20
    if not score.ma20_above:
        reasons.append("未站上 MA20（硬约束H5）")

    if reasons:
        return False, "；".join(reasons)
    return True, "全部门槛通过"


def check_sell_signal(
    current_price: float,
    avg_cost: float,
    closes: List[float],
    volumes: List[float],
    industry_index_close: Optional[float] = None,
    industry_index_ma10: Optional[float] = None,
    take_profit_low: float = 0.15,
    take_profit_high: float = 0.20,
    stop_loss_ma20_vol_ratio: float = 1.2,
) -> Tuple[bool, str]:
    """卖出信号检查（任一触发即卖出）。

    - 止盈：涨幅达 15%-20%
    - 止损：跌破 MA20 且放量
    - 趋势消退：行业指数跌破 10 日均线
    """
    if avg_cost <= 0:
        return False, ""

    gain = (current_price - avg_cost) / avg_cost

    # 止盈
    if gain >= take_profit_high:
        return True, f"止盈：涨幅 {gain*100:.1f}% ≥ {take_profit_high*100:.0f}%"
    if gain >= take_profit_low:
        return True, f"止盈（分批）：涨幅 {gain*100:.1f}% 进入 [{take_profit_low*100:.0f}%, {take_profit_high*100:.0f}%]"

    # 止损：跌破 MA20 且放量
    if len(closes) >= 20:
        ma20 = float(np.mean(closes[-20:]))
        if current_price < ma20:
            if len(volumes) >= 5:
                avg_vol_5 = float(np.mean(volumes[-5:]))
                if avg_vol_5 > 0 and volumes[-1] > avg_vol_5 * stop_loss_ma20_vol_ratio:
                    return True, (f"止损：跌破MA20({ma20:.2f}) 且放量 "
                                  f"(vol={volumes[-1]:.0f} > 5日均量×{stop_loss_ma20_vol_ratio})")

    # 趋势消退：行业指数跌破 MA10
    if industry_index_close is not None and industry_index_ma10 is not None:
        if industry_index_close < industry_index_ma10:
            return True, "趋势消退：行业指数跌破MA10"

    return False, ""


# ============================================================================
# 综合评分入口
# ============================================================================

def score_stock(
    code: str,
    name: str,
    industry: str,
    # 基本面
    roe: float,
    rev_growth: float,
    fcf_history: List[float],
    pe: float,
    pb: float,
    ps: float,
    dividend_yield: float,
    industry_pe_pct: float = 0.5,
    industry_pb_pct: float = 0.5,
    industry_ps_pct: float = 0.5,
    # 技术面
    closes: List[float] = None,
    volumes: List[float] = None,
    turnover_rate: float = 0.0,
    tail_rally_pct: float = 0.0,
    industry_5d_rank_pct: float = 0.5,
    industry_index_ma10: Optional[float] = None,
    industry_index_close: Optional[float] = None,
    # 资金面
    northbound_3d: List[float] = None,
    main_force_net: float = 0.0,
    # 预过滤
    circ_market_cap: float = 100.0,
    # 持仓（用于卖出判断）
    avg_cost: float = 0.0,
    # 凯利
    win_rate: float = 0.5,
    avg_win: float = 0.0,
    avg_loss: float = 0.0,
    # 预过滤宽松模式
    loose_prefilter: bool = False,
) -> StockScore:
    """对单只股票做完整评分 + 买卖信号判断。"""
    closes = closes or []
    volumes = volumes or []
    northbound_3d = northbound_3d or []

    score = StockScore(code=code, name=name, industry=industry, price=closes[-1] if closes else 0.0)

    # 基本面
    score.fundamental = score_fundamental(
        roe, rev_growth, fcf_history, pe, pb, ps, dividend_yield,
        industry, industry_pe_pct, industry_pb_pct, industry_ps_pct,
    )

    # 技术面
    score.technical = score_technical(
        closes, volumes, turnover_rate, tail_rally_pct,
        industry_5d_rank_pct, industry_index_ma10, industry_index_close,
    )

    # 资金面
    score.capital = score_capital(northbound_3d, main_force_net)

    # 综合
    score.total = score.fundamental.total + score.technical.total + score.capital.total

    # MA20 状态
    if len(closes) >= 20:
        ma20 = float(np.mean(closes[-20:]))
        score.ma20_above = closes[-1] >= ma20
    else:
        score.ma20_above = False

    # 预过滤
    passed, _ = check_prefilters(circ_market_cap, tail_rally_pct, turnover_rate,
                                 volumes, closes, loose=loose_prefilter)
    score.prefilters_passed = passed

    # 买入信号
    buy_ok, buy_reason = check_buy_signal(score, industry_rank_pct=industry_5d_rank_pct)
    score.buy_signal = buy_ok
    score.signal_reason = buy_reason

    # 卖出信号（仅对持仓判断）
    if avg_cost > 0:
        sell_ok, sell_reason = check_sell_signal(
            closes[-1] if closes else 0.0, avg_cost, closes, volumes,
            industry_index_close, industry_index_ma10,
        )
        score.sell_signal = sell_ok
        if sell_ok:
            score.signal_reason = sell_reason

    # 凯利仓位
    score.kelly_ratio = kelly_position(win_rate, avg_win, avg_loss)

    return score


def rank_stocks(scores: List[StockScore], top_n: int = 10) -> List[StockScore]:
    """按综合得分排序取 Top N。"""
    sorted_scores = sorted(scores, key=lambda s: s.total, reverse=True)
    return sorted_scores[:top_n]
