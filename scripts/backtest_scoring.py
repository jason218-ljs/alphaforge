"""选股打分策略回测运行脚本。

流程：定义候选池 → 拉取K线+基本面 → 对齐日期构造bars → EventEngine回测 → 对比沪深300 → 输出报告
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from alphaforge.config import get_config
from alphaforge.data.benchmark import BenchmarkManager
from alphaforge.data.stock_fetcher import StockFetcher
from alphaforge.engine.event_engine import EventEngine
from alphaforge.engine.market_rules import MarketRules
from alphaforge.logger import get_logger
from alphaforge.strategy.scoring_strategy import ScoringStrategy

logger = get_logger("scripts.backtest_scoring")


def _date_gap(a: str, b: str) -> int:
    """两个日期字符串的间隔天数。"""
    from datetime import date
    da = date.fromisoformat(a[:10])
    db = date.fromisoformat(b[:10])
    return abs((da - db).days)

# ---------- 候选池（中盘活跃股，换手率≥3%，适配"尾盘拉升+量能放大"形态）----------

POOL = [
    {"code": "600745", "industry": "电子", "name": "闻泰科技"},
    {"code": "600363", "industry": "电子", "name": "联创光电"},
    {"code": "002138", "industry": "电子", "name": "顺络电子"},
    {"code": "300229", "industry": "传媒", "name": "拓尔思"},
    {"code": "300088", "industry": "电子", "name": "长信科技"},
    {"code": "300759", "industry": "医药", "name": "康龙化成"},
    {"code": "002156", "industry": "半导体", "name": "通富微电"},
    {"code": "300223", "industry": "半导体", "name": "北京君正"},
    {"code": "600584", "industry": "半导体", "name": "长电科技"},
    {"code": "300134", "industry": "电子", "name": "大富科技"},
    {"code": "300146", "industry": "食品", "name": "汤臣倍健"},
    {"code": "002405", "industry": "传媒", "name": "四维图新"},
    {"code": "000050", "industry": "电子", "name": "深天马A"},
    {"code": "300207", "industry": "电力设备", "name": "欣旺达"},
    {"code": "300316", "industry": "半导体", "name": "晶盛机电"},
    {"code": "002180", "industry": "电子", "name": "纳思达"},
    {"code": "002236", "industry": "电子", "name": "大华股份"},
    {"code": "002547", "industry": "电子", "name": "春兴精工"},
    {"code": "000988", "industry": "机械", "name": "华工科技"},
    {"code": "300474", "industry": "电子", "name": "景嘉微"},
]


def main(days: int = 150, rebalance: int = 3, top_n: int = 5,
         initial_capital: float = 1_000_000.0) -> dict:
    cfg = get_config()
    fetcher = StockFetcher(cache_db=cfg.paths.cache_db if hasattr(cfg.paths, "cache_db") else "data/scoring_cache.db")
    bench_mgr = BenchmarkManager(cache_db="data/scoring_cache.db")

    # 1. 拉取K线 + 基本面
    logger.info("拉取候选池数据（%d 只）...", len(POOL))
    klines: dict = {}
    fundamentals: dict = {}
    for stock in POOL:
        code = stock["code"]
        bars = fetcher.get_kline(code, days + 30)
        if len(bars) < 60:
            logger.warning("%s K线不足，跳过", code)
            continue
        klines[code] = bars
        fundamentals[code] = fetcher.get_fundamentals(code)
        fundamentals[code]["industry"] = stock["industry"]
        fundamentals[code]["name"] = stock.get("name", code)

    # 过滤断档股票：最新 K 线日期远早于池子最晚日期（如停用/重组代码）
    if klines:
        latest_overall = max(bars[-1]["date"] for bars in klines.values())
        stale = [c for c, bars in klines.items()
                 if (latest_overall[:10] > bars[-1]["date"][:10] and
                     _date_gap(latest_overall, bars[-1]["date"]) > 60)]
        for c in stale:
            logger.warning("%s K线断档(%s)，剔除候选池", c, klines[c][-1]["date"])
            klines.pop(c, None)
            fundamentals.pop(c, None)

    logger.info("成功获取 %d 只股票数据", len(klines))

    # 2. 对齐日期：取所有股票共有的交易日
    first_code = next(iter(klines))
    common_dates = {b["date"] for b in klines[first_code]} if klines else set()
    for code, bars in klines.items():
        common_dates &= {b["date"] for b in bars}
    dates = sorted(common_dates)
    if len(dates) < 40:
        raise RuntimeError(f"共同交易日不足: {len(dates)}")
    # 取最近 days 天
    dates = dates[-days:]
    logger.info("回测区间: %s ~ %s（%d 个交易日）", dates[0], dates[-1], len(dates))

    # 3. 构造 bars（EventEngine 输入）
    bars_input = []
    for d in dates:
        quotes = {}
        prev_close = {}
        for code, kl in klines.items():
            idx = {b["date"]: i for i, b in enumerate(kl)}
            i = idx.get(d)
            if i is None:
                continue
            quotes[code] = kl[i]["close"]
            if i > 0:
                prev_close[code] = kl[i - 1]["close"]
            else:
                prev_close[code] = kl[i]["close"]
        bars_input.append({
            "date": datetime.strptime(d, "%Y-%m-%d"),
            "quotes": quotes,
            "prev_close": prev_close,
        })

    # 4. 基准（沪深300）
    bench_dates = [b["date"] for b in bars_input]
    bench_nav = bench_mgr.get_aligned(bench_dates, code="000300.SH", use_network=True)
    bench_series = bench_nav

    # 5. 运行回测
    rules = MarketRules()
    engine = EventEngine(rules, initial_capital=initial_capital)
    strategy = ScoringStrategy(
        pool=POOL, klines=klines, fundamentals=fundamentals,
        rebalance_days=rebalance, top_n=top_n,
    )
    result = engine.run(strategy, bars_input, benchmark_series=bench_series)

    # 6. 输出
    out_dir = Path("output/scoring_backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json")
    payload["pool_size"] = len(klines)
    payload["pool_stocks"] = [{"code": s["code"], "industry": s["industry"],
                                "name": s.get("name", "")} for s in POOL]
    # 基准对比
    if bench_series:
        bench_return = bench_series[-1] / bench_series[0] - 1 if bench_series[0] > 0 else 0
        payload["benchmark"] = {
            "code": "000300.SH", "name": "沪深300",
            "return": bench_return,
            "excess_return": result.summary.total_return - bench_return,
        }
    out_path = out_dir / f"backtest_{dates[-1].replace('-','')}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    # 打印摘要
    s = result.summary
    print("\n" + "=" * 60)
    print("选股打分策略回测结果")
    print("=" * 60)
    print(f"候选池: {len(klines)} 只 | 回测区间: {dates[0]} ~ {dates[-1]} ({s.days}天)")
    print(f"初始资金: ¥{s.initial_capital:,.0f} → 期末: ¥{s.final_nav:,.0f}")
    print(f"总收益: {s.total_return:.2%} | 年化: {s.annualized_return:.2%}")
    print(f"夏普: {s.sharpe_ratio:.2f} | Sortino: {s.sortino_ratio:.2f} | Calmar: {s.calmar_ratio:.2f}")
    print(f"最大回撤: {s.max_drawdown:.2%} | 胜率: {s.win_rate:.1%} | 盈亏比: {s.profit_loss_ratio:.2f}")
    print(f"成交: {s.total_trades} 笔（盈{ s.winning_trades}/亏{s.losing_trades}）")
    if bench_series:
        print(f"沪深300: {payload['benchmark']['return']:.2%} | 超额: {payload['benchmark']['excess_return']:.2%}")
    print(f"\n结果已保存: {out_path}")
    return payload


if __name__ == "__main__":
    main()
