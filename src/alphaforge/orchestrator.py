"""工作流编排器：被 main-auto-workflow.md 调用，CLI 转发到此。

每个方法对应 workflow 的一个 Stage，输出 JSON 文件路径，Stage 间通过文件解耦。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from alphaforge.config import AlphaForgeConfig, get_config
from alphaforge.logger import get_logger, setup_logging
from alphaforge.analysis.risk_analyzer import RiskAnalyzer
from alphaforge.analysis.return_analyzer import ReturnAnalyzer
from alphaforge.data.external_scorer_importer import Record, 主流水线Importer
from alphaforge.models.portfolio import PortfolioSnapshot
from alphaforge.models.result import AnalysisResult, StrategyEvaluation
from alphaforge.reporting.report_generator import ReportGenerator

logger = get_logger("orchestrator")


class Orchestrator:
    """工作流编排器。"""

    def __init__(self, config: Optional[AlphaForgeConfig] = None):
        self.config = config or get_config()
        setup_logging(self.config.app.get("log_level", "INFO"))
        self.output_dir = Path(self.config.paths.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "data").mkdir(exist_ok=True)
        (self.output_dir / "backtest").mkdir(exist_ok=True)
        (self.output_dir / "analysis").mkdir(exist_ok=True)

    # ---------- Stage 1: 数据导入 ----------

    def run_data_import(self, excel_path: str, output: Optional[str] = None) -> str:
        """导入 主流水线 Excel，输出 records.json。"""
        out_path = Path(output) if output else self.output_dir / "data" / "records.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        importer = 主流水线Importer(
            base_year=self.config.data.base_year,
            filter_example=self.config.data.filter_example_rows,
            use_numeric_row=self.config.data.use_numeric_summary_row,
        )
        records = importer.import_file(excel_path)

        # 同时构建组合快照序列
        snapshots = importer.build_portfolio_history(list(records.values()))

        payload = {
            "generated_at": datetime.now().isoformat(),
            "source": str(excel_path),
            "trade_days": len(records),
            "records": [
                {
                    "date": r.date.isoformat(),
                    "sheet_name": r.sheet_name,
                    "nav": r.nav,
                    "cash": r.cash,
                    "team_summary": r.team_summary.model_dump(),
                    "members": [m.model_dump() for m in r.members],
                    "trades": [t.model_dump() for t in r.trades],
                }
                for r in [records[k] for k in sorted(records.keys())]
            ],
            "portfolio_snapshots": [
                {
                    "date": s.date.isoformat(),
                    "cash": s.cash,
                    "nav": s.nav,
                    "daily_pnl": s.daily_pnl,
                    "daily_return_pct": s.daily_return_pct,
                    "position_count": s.position_count,
                    "positions": {k: v.model_dump() for k, v in s.positions.items()},
                }
                for s in snapshots
            ],
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("数据导入完成：%d 个交易日 → %s", len(records), out_path)
        return str(out_path)

    # ---------- Stage 2: 事件驱动回测（Phase 2） ----------

    def run_backtest(self, records_path: str, strategy: str = "external_scorer_replay",
                     output: Optional[str] = None) -> str:
        """事件驱动回测：逐日回放 主流水线 流水，经 Matcher 撮合（T+1/涨跌停/费用/滑点）。

        Args:
            records_path: data/records.json 路径。
            strategy: 策略名（默认 external_scorer_replay）。
            output: 输出 backtest.json 路径。
        """
        from alphaforge.engine.event_engine import EventEngine
        from alphaforge.engine.market_rules import from_config as rules_from_config
        from alphaforge.strategy.external_scorer_replay import (
            主流水线ReplayStrategy, load_trades_from_records,
        )

        out_path = Path(output) if output else self.output_dir / "backtest" / "backtest.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = json.loads(Path(records_path).read_text(encoding="utf-8"))
        records = data.get("records", [])
        snapshots_meta = data.get("portfolio_snapshots", [])
        if not records:
            raise ValueError("records.json 无 records，无法回测")

        # 构造 bars：每日 {date, quotes, prev_close}
        trades_by_date = load_trades_from_records(records)
        bars = self._build_bars_from_records(records, snapshots_meta)

        # 策略：回放真实流水
        rules = rules_from_config(self.config.market_rules)
        # 回放模式下，滑点设为 0（流水已是真实成交价），保留费用计算
        replay_rules = self._replay_rules(rules)
        engine = EventEngine(replay_rules, initial_capital=self.config.backtest.initial_capital)

        strat = 主流水线ReplayStrategy(trades_by_date)
        result = engine.run(strat, bars, initial_capital=self.config.backtest.initial_capital)

        payload = json.loads(result.model_dump_json())
        payload["note"] = "Phase 2 事件驱动回测：T+1/涨跌停/费用/滑点（回放模式滑点=0）"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(
            "回测完成 → %s | 权益 %.0f→%.0f | 收益 %.2f%% | 夏普 %.2f | 回撤 %.2f%% | 成交 %d 笔",
            out_path, result.summary.initial_capital, result.summary.final_nav,
            result.summary.total_return * 100, result.summary.sharpe_ratio,
            result.summary.max_drawdown * 100, result.summary.total_trades,
        )
        return str(out_path)

    def _build_bars_from_records(self, records, snapshots_meta):
        """从 records 构造 bars，quotes 用当日最后一笔成交价 + 快照持仓价。"""
        bars = []
        # 建一个 date→snapshot 的索引，取持仓的 current_price 作为行情
        snap_by_date = {}
        for s in snapshots_meta:
            snap_by_date[s["date"]] = s
        prev_close: Dict[str, float] = {}
        for rec in sorted(records, key=lambda r: r["date"]):
            date = rec["date"]
            if isinstance(date, str):
                from datetime import datetime as _dt
                date = _dt.fromisoformat(date)
            quotes: Dict[str, float] = {}
            # 从当日流水取每只股票最后一笔成交价
            for t in rec.get("trades", []):
                code = t.get("code", "")
                if code:
                    quotes[code] = float(t.get("price", 0))
            # 从快照持仓补充现价（覆盖当日无交易但有持仓的股票）
            snap = snap_by_date.get(rec["date"])
            if snap:
                for code, pos in snap.get("positions", {}).items():
                    if code not in quotes and pos.get("current_price", 0) > 0:
                        quotes[code] = float(pos["current_price"])
            bars.append({
                "date": date,
                "quotes": quotes,
                "prev_close": dict(prev_close),
            })
            prev_close = dict(quotes)
        return bars

    def _replay_rules(self, rules):
        """回放模式：滑点设 0（流水已是真实成交价），保留费用与 T+1。"""
        from alphaforge.engine.market_rules import MarketRules
        return MarketRules(
            stamp_tax_rate=rules.stamp_tax_rate,
            commission_rate=rules.commission_rate,
            min_commission=rules.min_commission,
            transfer_fee_rate=rules.transfer_fee_rate,
            slippage_pct=0.0,           # 回放不加滑点
            slippage_fixed=0.0,
            enable_t_plus_1=rules.enable_t_plus_1,
            price_limit=rules.price_limit,
        )

    # ---------- Stage 2.5: 选股策略回测（基于 stock-scoring_auto_workflow.md） ----------

    def run_screener_backtest(
        self,
        trading_days: int = 120,
        seed: int = 42,
        max_positions: int = 5,
        top_n_candidates: int = 10,
        initial_capital: Optional[float] = None,
        output: Optional[str] = None,
    ) -> Dict[str, str]:
        """选股策略回测：生成合成市场数据 → 评分 → 买卖 → 回测绩效。

        严格依据 `stock-scoring_auto_workflow.md`：
        - 基本面40 + 技术面40 + 资金面20 = 综合100分
        - 行业自适应阈值（按二级分类）
        - 买入信号：预过滤 + 打分门槛 + MA20（硬约束H5）
        - 卖出信号：止盈15-20% / 止损破MA20放量 / 行业指数破MA10
        - 凯利公式仓位（10%-30%）

        Args:
            trading_days: 回测交易日数。
            seed: 随机种子（可复现）。
            max_positions: 最大持仓数。
            top_n_candidates: 每日候选数。
            initial_capital: 初始资金。
            output: 输出路径（可选）。

        Returns:
            {"backtest": path, "screener_scores": path}
        """
        from alphaforge.data.synthetic_market import (
            SyntheticMarketGenerator, market_to_backtest_bars, build_market_index,
        )
        from alphaforge.engine.event_engine import EventEngine
        from alphaforge.engine.market_rules import from_config as rules_from_config
        from alphaforge.strategy.stock_scoring_strategy import StockScoringStrategy

        capital = initial_capital or self.config.backtest.initial_capital
        out_dir = self.output_dir / "backtest"
        out_dir.mkdir(parents=True, exist_ok=True)
        bt_path = Path(output) if output else out_dir / "screener_backtest.json"
        scores_path = out_dir / "screener_scores.json"

        # 1. 生成合成市场
        gen = SyntheticMarketGenerator(trading_days=trading_days, seed=seed)
        markets = gen.generate()
        market_index = build_market_index(markets)
        universe = gen.universe

        # 2. 构建 bars
        bars = market_to_backtest_bars(markets)

        # 3. 策略与引擎
        rules = rules_from_config(self.config.market_rules)
        engine = EventEngine(rules, initial_capital=capital)
        strategy = StockScoringStrategy(
            universe=universe,
            market_index=market_index,
            max_positions=max_positions,
            top_n_candidates=top_n_candidates,
            initial_capital=capital,
        )

        result = engine.run(strategy, bars, initial_capital=capital)

        # 4. 序列化回测结果
        payload = json.loads(result.model_dump_json())
        payload["note"] = ("选股策略回测：基本面40+技术面40+资金面20，"
                           "行业自适应阈值，凯利公式仓位，MA20硬约束")
        payload["strategy_config"] = {
            "max_positions": max_positions,
            "top_n_candidates": top_n_candidates,
            "trading_days": trading_days,
            "seed": seed,
            "universe_size": len(universe),
            "industries": sorted({m.industry for m in universe}),
        }
        bt_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # 5. 序列化评分明细
        scores_payload = {
            "generated_at": datetime.now().isoformat(),
            "strategy_name": strategy.name,
            "trading_days": trading_days,
            "universe": [
                {"code": m.code, "name": m.name, "industry": m.industry,
                 "quality": round(m.quality, 3), "board": m.board}
                for m in universe
            ],
            "daily_scores": strategy.daily_scores,
            "daily_signals": strategy.daily_signals,
            "summary": {
                "total_return": result.summary.total_return,
                "annualized_return": result.summary.annualized_return,
                "max_drawdown": result.summary.max_drawdown,
                "sharpe_ratio": result.summary.sharpe_ratio,
                "win_rate": result.summary.win_rate,
                "total_trades": result.summary.total_trades,
                "final_nav": result.summary.final_nav,
                "initial_capital": result.summary.initial_capital,
            },
        }
        scores_path.write_text(
            json.dumps(scores_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "选股回测完成 → %s | 权益 %.0f→%.0f | 收益 %.2f%% | 夏普 %.2f | "
            "回撤 %.2f%% | 成交 %d 笔 | 候选池 %d 只",
            bt_path, result.summary.initial_capital, result.summary.final_nav,
            result.summary.total_return * 100, result.summary.sharpe_ratio,
            result.summary.max_drawdown * 100, result.summary.total_trades,
            len(universe),
        )
        return {"backtest": str(bt_path), "screener_scores": str(scores_path)}

    def run_screener_backtest_real(
        self,
        trading_days: int = 120,
        max_positions: int = 5,
        top_n_candidates: int = 10,
        initial_capital: Optional[float] = None,
        output: Optional[str] = None,
        cache_db: str = "./data/real_data.db",
    ) -> Dict[str, str]:
        """选股策略回测（真实数据版）。

        从腾讯/东财获取真实K线与基本面，转换为 DailyMarket 后驱动选股回测。

        Args:
            trading_days: 回测交易日数。
            max_positions: 最大持仓数。
            top_n_candidates: 每日候选数。
            initial_capital: 初始资金。
            output: 输出路径。
            cache_db: 数据缓存DB路径。

        Returns:
            {"backtest": path, "screener_scores": path}
        """
        from alphaforge.data.real_market import RealMarketLoader
        from alphaforge.data.synthetic_market import build_market_index
        from alphaforge.engine.event_engine import EventEngine
        from alphaforge.engine.market_rules import from_config as rules_from_config
        from alphaforge.strategy.stock_scoring_strategy import StockScoringStrategy

        capital = initial_capital or self.config.backtest.initial_capital
        out_dir = self.output_dir / "backtest"
        out_dir.mkdir(parents=True, exist_ok=True)
        bt_path = Path(output) if output else out_dir / "screener_backtest_real.json"
        scores_path = out_dir / "screener_scores_real.json"

        # 1. 加载真实市场数据
        loader = RealMarketLoader(trading_days=trading_days, cache_db=cache_db)
        markets = loader.load()
        market_index = build_market_index(markets)
        universe = loader.universe

        # 2. 构建 bars
        from alphaforge.data.synthetic_market import market_to_backtest_bars
        bars = market_to_backtest_bars(markets)

        # 3. 策略与引擎
        rules = rules_from_config(self.config.market_rules)
        engine = EventEngine(rules, initial_capital=capital)
        strategy = StockScoringStrategy(
            universe=universe,
            market_index=market_index,
            max_positions=max_positions,
            top_n_candidates=top_n_candidates,
            initial_capital=capital,
            loose_prefilter=True,  # 真实市场模式：放宽预过滤（大盘股换手率低）
        )

        result = engine.run(strategy, bars, initial_capital=capital)

        # 4. 序列化
        payload = json.loads(result.model_dump_json())
        payload["note"] = ("选股策略回测（真实数据版）：K线来自腾讯，基本面来自东方财富。"
                           "资金面用换手率+涨跌幅代理（真实北向/主力需付费API）。"
                           "基本面为最新快照非PIT，存在轻微未来函数偏差。")
        payload["data_source"] = {
            "kline": "腾讯 web.ifzq.gtimg.cn（前复权日K）",
            "fundamental": "东方财富 F10（PE/PB/ROE/营收增速/流通市值/换手率）",
            "capital": "代理：换手率+涨跌幅（真实北向/主力需付费API）",
            "industry_index": "行业成分股等权近似",
        }
        payload["strategy_config"] = {
            "max_positions": max_positions,
            "top_n_candidates": top_n_candidates,
            "trading_days": trading_days,
            "universe_size": len(universe),
            "industries": sorted({m.industry for m in universe}),
            "data_type": "real",
        }
        bt_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # 5. 评分明细
        scores_payload = {
            "generated_at": datetime.now().isoformat(),
            "strategy_name": strategy.name,
            "trading_days": trading_days,
            "data_type": "real",
            "universe": [
                {"code": m.code, "name": m.name, "industry": m.industry,
                 "quality": round(m.quality, 3), "board": m.board}
                for m in universe
            ],
            "daily_scores": strategy.daily_scores,
            "daily_signals": strategy.daily_signals,
            "summary": {
                "total_return": result.summary.total_return,
                "annualized_return": result.summary.annualized_return,
                "max_drawdown": result.summary.max_drawdown,
                "sharpe_ratio": result.summary.sharpe_ratio,
                "win_rate": result.summary.win_rate,
                "total_trades": result.summary.total_trades,
                "final_nav": result.summary.final_nav,
                "initial_capital": result.summary.initial_capital,
            },
        }
        scores_path.write_text(
            json.dumps(scores_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "真实数据选股回测完成 → %s | 权益 %.0f→%.0f | 收益 %.2f%% | 夏普 %.2f | "
            "回撤 %.2f%% | 成交 %d 笔",
            bt_path, result.summary.initial_capital, result.summary.final_nav,
            result.summary.total_return * 100, result.summary.sharpe_ratio,
            result.summary.max_drawdown * 100, result.summary.total_trades,
        )
        return {"backtest": str(bt_path), "screener_scores": str(scores_path)}

    # ---------- Stage 3: 分析评估 ----------

    def run_analysis(self, backtest_path: str, output: Optional[str] = None) -> str:
        """对回测结果做风险/收益分析。"""
        out_path = Path(output) if output else self.output_dir / "analysis" / "analysis.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        bt = json.loads(Path(backtest_path).read_text(encoding="utf-8"))
        equity = bt.get("equity_curve", [])
        nav_series = [e["nav"] for e in equity]
        if not nav_series:
            raise ValueError("backtest.json 无 equity_curve")

        risk_analyzer = RiskAnalyzer(self.config.analysis)
        return_analyzer = ReturnAnalyzer(self.config.analysis)

        # 获取基准序列（沪深300），用于 Alpha/Beta 与 Fama 归因
        benchmark_nav = None
        dates = [e["date"] for e in equity]
        if dates:
            benchmark_nav = self._get_benchmark(dates)

        risk = risk_analyzer.analyze(nav_series)
        returns = return_analyzer.analyze(nav_series, benchmark_series=benchmark_nav)

        # 策略评估（用 trade_log / signal_log）
        strategy_eval = self._evaluate_strategy(bt)

        # 归因分析（Brinson + Fama-French）
        attribution = self._attribution(bt, nav_series, benchmark_nav, dates)

        # 汇总与建议
        overall = self._overall_health(risk.risk_score, returns.return_score)
        recommendations = self._recommendations(risk, returns)

        result = AnalysisResult(
            analysis_date=datetime.now(),
            risk=risk,
            returns=returns,
            strategy=strategy_eval,
            attribution=attribution,
            summary={"overall_health": overall, "risk_score": risk.risk_score,
                     "return_score": returns.return_score,
                     "strategy_score": strategy_eval.strategy_score},
            recommendations=recommendations,
        )
        out_path.write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("分析完成 → %s（归因：Brinson + Fama-French）", out_path)
        return str(out_path)

    # ---------- 归因子流程 ----------

    def _get_benchmark(self, dates: List[str]) -> Optional[List[float]]:
        """获取与 equity 日期对齐的沪深300基准净值。"""
        from alphaforge.data.benchmark import BenchmarkManager
        from datetime import datetime as _dt
        try:
            parsed = [_dt.fromisoformat(d) for d in dates]
        except ValueError:
            return None
        if not parsed:
            return None
        try:
            mgr = BenchmarkManager(cache_db=self.config.paths.cache_db)
            return mgr.get_aligned(parsed, code=self.config.analysis.benchmark,
                                   use_network=True)
        except Exception as e:
            logger.warning("基准加载失败：%s", e)
            return None

    def _evaluate_strategy(self, bt: dict):
        """用 trade_log/signal_log 评估策略。"""
        from alphaforge.analysis.strategy_evaluator import StrategyEvaluator
        from alphaforge.models.result import TradeRecord

        trade_log = []
        for t in bt.get("trade_log", []):
            trade_log.append(TradeRecord(**t))
        signal_log = bt.get("signal_log", [])
        evaluator = StrategyEvaluator(self.config.analysis)
        return evaluator.evaluate(
            trade_log=trade_log,
            signal_log=signal_log,
            nav_series=[e["nav"] for e in bt.get("equity_curve", [])],
        )

    def _attribution(self, bt: dict, nav_series: List[float],
                     benchmark_nav: Optional[List[float]], dates: List[str]):
        """计算 Brinson 行业归因 + Fama-French 因子归因。"""
        from alphaforge.analysis.attribution import AttributionAnalyzer
        from alphaforge.models.result import AttributionResult

        analyzer = AttributionAnalyzer()
        n = len(nav_series)

        # 日收益序列
        def daily_ret(series):
            out = []
            for i in range(1, len(series)):
                if series[i - 1] > 0:
                    out.append(series[i] / series[i - 1] - 1.0)
            return out

        port_rets = daily_ret(nav_series)
        bench_rets = daily_ret(benchmark_nav) if benchmark_nav else None

        # Brinson 行业归因：用导入快照的行业权重
        brinson = self._brinson_from_snapshots()

        # Fama-French 三因子（无 SMB/HML 因子库时用市场单因子，仍输出回归结构）
        fama = {}
        if bench_rets:
            fama = analyzer.fama_french_attribution(
                portfolio_returns=port_rets,
                market_returns=bench_rets,
                smb_returns=None,
                hml_returns=None,
                risk_free_returns=None,
                annualization=self.config.analysis.annualization_factor,
            )

        return AttributionResult(brinson=brinson, fama=fama,
                                 industry_attribution=brinson)

    def _brinson_from_snapshots(self) -> dict:
        """从 records.json 的 portfolio_snapshots 权重计算 Brinson（组合 vs 等权基准）。"""
        from alphaforge.analysis.attribution import AttributionAnalyzer

        records_path = self.output_dir / "data" / "records.json"
        if not records_path.exists():
            return {}
        try:
            data = json.loads(records_path.read_text(encoding="utf-8"))
            snaps = data.get("portfolio_snapshots", [])
            if not snaps:
                return {}
            last = snaps[-1]
            total_nav = last.get("nav", 0)
            if total_nav <= 0:
                return {}
            # 组合行业权重（从持仓市值 + 行业）
            port_weights: Dict[str, float] = {}   # {industry: weight}
            for code, pos in last.get("positions", {}).items():
                ind = self._industry_of(pos, code)
                mv = pos.get("market_value", 0) or pos.get("qty", 0) * pos.get("current_price", 0)
                port_weights[ind] = port_weights.get(ind, 0.0) + mv / total_nav
            # 简化：等权基准（实际应取指数行业权重，这里用持仓均值近似）
            inds = list(port_weights.keys())
            bench_w = {i: 1.0 / len(inds) for i in inds} if inds else {}
            # 组合/基准行业收益：用快照的第一天到末天的价格变化（简化）
            first = snaps[0]
            port_ret, bench_ret = {}, {}
            for ind in inds:
                # 简化：用组合整体收益作为行业收益代理
                port_ret[ind] = (snaps[-1]["nav"] / first["nav"] - 1.0) if first["nav"] > 0 else 0.0
                bench_ret[ind] = port_ret[ind]  # 无基准行业收益，用组合近似
            analyzer = AttributionAnalyzer()
            return analyzer.brinson_attribution(port_weights, bench_w, port_ret, bench_ret)
        except Exception as e:
            logger.warning("Brinson 归因计算失败：%s", e)
            return {}

    def _industry_of(self, pos: dict, code: str) -> str:
        """获取持仓行业（优先 pos.industry，否则查本地映射）。"""
        ind = pos.get("industry")
        if ind:
            return ind
        # 简化映射：沪/深主板 → 未知（Phase 5 接入行业数据源）
        if code.startswith(("60", "00")):
            return "主板"
        if code.startswith("30"):
            return "创业板"
        if code.startswith("68"):
            return "科创板"
        return "未知"

    # ---------- Stage 4: 报告生成 ----------

    def run_report(self, analysis_path: str, output_dir: Optional[str] = None) -> Dict[str, str]:
        """从 analysis.json 生成日报。"""
        data = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
        from alphaforge.models.result import (
            RiskAnalysis, ReturnAnalysis, StrategyEvaluation,
            AttributionResult, AnalysisResult,
        )
        result = AnalysisResult(
            analysis_date=datetime.fromisoformat(data["analysis_date"]),
            risk=RiskAnalysis(**data["risk"]),
            returns=ReturnAnalysis(**data["returns"]),
            strategy=StrategyEvaluation(**data["strategy"]),
            attribution=AttributionResult(**data["attribution"]),
            summary=data["summary"],
            recommendations=data["recommendations"],
        )
        gen = ReportGenerator(output_dir or self.output_dir)
        # 从 records.json 恢复最新快照（若存在）
        snapshot = self._load_latest_snapshot()
        return gen.generate_and_save(result, snapshot, result.analysis_date)

    # ---------- Stage 5: 参数寻优（Phase 4） ----------

    def run_optimize(self, output: Optional[str] = None, method: Optional[str] = None,
                     n_trials: Optional[int] = None, fast_range=(2, 15),
                     slow_range=(10, 60), seed: Optional[int] = None) -> str:
        """对 MA 策略做参数寻优（用合成/历史行情）。"""
        from alphaforge.analysis.optimizer import optimize_ma_strategy

        out_path = Path(output) if output else self.output_dir / "optimize" / "optimize.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取行情：优先用基准管理器的沪深300（若可获取），否则合成
        prices = self._get_prices_for_optimize()

        method = method or self.config.optimizer.method
        n_trials = n_trials or self.config.optimizer.n_trials
        objective = self.config.optimizer.objective
        mdd_constraint = self.config.optimizer.max_drawdown_constraint

        result = optimize_ma_strategy(
            prices,
            fast_range=fast_range, slow_range=slow_range,
            objective=objective, method=method, n_trials=n_trials,
            mdd_constraint=mdd_constraint, seed=seed,
        )
        payload = result.summary_dict()
        payload["price_count"] = len(prices)
        payload["objective"] = objective
        payload["mdd_constraint"] = mdd_constraint
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(
            "参数寻优完成（%s）→ %s | best=%s 得分=%.4f",
            method, out_path, payload["best_params"], payload["objective_score"],
        )
        return str(out_path)

    def run_walkforward(self, output: Optional[str] = None, params: Optional[Dict] = None,
                        window: Optional[int] = None) -> str:
        """对指定参数做 Walk-Forward 稳健性检验。"""
        from alphaforge.analysis.robustness import Robustness
        from alphaforge.engine.vectorized_engine import VectorizedEngine

        out_path = Path(output) if output else self.output_dir / "robustness" / "walkforward.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        prices = self._get_prices_for_optimize()
        params = params or {"fast": 5, "slow": 20}
        window = window or self.config.robustness.walk_forward_window

        engine = VectorizedEngine()
        robustness = Robustness(seed=42)

        def obj_fn(p, train_prices):
            return engine.run_ma_strategy(train_prices, fast=int(p["fast"]),
                                          slow=int(p["slow"]))
        result = robustness.walk_forward(obj_fn, prices, params, window=window)
        payload = {
            "params": params,
            "window": window,
            "result": result,
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Walk-Forward 完成 → %s | 稳定=%s avg_ratio=%.2f",
                    out_path, result["is_stable"], result["avg_ratio"])
        return str(out_path)

    def _get_prices_for_optimize(self) -> List[float]:
        """获取寻优用价格序列（沪深300 或合成）。"""
        from alphaforge.data.benchmark import BenchmarkManager
        from datetime import datetime, timedelta
        try:
            end = datetime.now()
            start = end - timedelta(days=180)
            mgr = BenchmarkManager(cache_db=self.config.paths.cache_db)
            series = mgr.get_index_series(self.config.analysis.benchmark, start, end,
                                          use_network=True)
            close = [s["close"] for s in series]
            if len(close) >= 60:
                return close
        except Exception as e:
            logger.warning("获取行情用于寻优失败，改用合成：%s", e)
        # 合成序列兜底
        import numpy as np
        np.random.seed(42)
        rets = np.random.normal(0.0003, 0.015, 300)
        return list(np.cumprod(1 + rets) * 100.0)

    # ---------- 一键全链路 ----------

    def run_full_cycle(self, excel_path: str) -> Dict[str, Any]:
        """main-auto-workflow 全链路：import → backtest → analyze → report。"""
        logger.info("=== AlphaForge 全链路启动 ===")
        records_path = self.run_data_import(excel_path)
        backtest_path = self.run_backtest(records_path)
        analysis_path = self.run_analysis(backtest_path)
        report_paths = self.run_report(analysis_path)
        logger.info("=== 全链路完成 ===")
        return {
            "records": records_path,
            "backtest": backtest_path,
            "analysis": analysis_path,
            "report_paths": report_paths,
        }

    # ---------- 数据自动同步 ----------

    def _resolve_excel_path(self, excel_arg: Optional[str] = None) -> Path:
        """解析 Excel 路径：优先 CLI 参数 > external_scorer_file > external_scorer_dir/excel_filename。"""
        if excel_arg:
            return Path(excel_arg)
        if self.config.paths.external_scorer_file:
            return Path(self.config.paths.external_scorer_file)
        if self.config.paths.external_scorer_dir:
            return Path(self.config.paths.external_scorer_dir) / self.config.sync.excel_filename
        raise ValueError(
            "未配置数据源：请设置 config.paths.external_scorer_dir 或 external_scorer_file，"
            "或通过 --excel 参数指定"
        )

    def run_sync(
        self,
        excel: Optional[str] = None,
        watch: bool = False,
        interval: Optional[int] = None,
    ) -> Dict[str, Any]:
        """增量同步 主流水线 Excel 数据。

        流程：
        1. 检测新增 sheet
        2. 增量导入，更新 records.json
        3. 若配置 auto_run_pipeline，自动跑回测+分析+报告

        Args:
            excel: Excel 路径，None 用配置默认。
            watch: 是否持续监听。
            interval: 监听间隔秒，None 用配置默认。

        Returns:
            同步结果（watch 模式下不会返回，直到用户中断）。
        """
        from alphaforge.data.auto_sync import 主流水线Sync

        excel_path = self._resolve_excel_path(excel)
        state_db = self.config.paths.sync_state_db
        interval_sec = interval or self.config.sync.interval_seconds

        syncer = 主流水线Sync(
            excel_path=str(excel_path),
            state_db=state_db,
            importer=主流水线Importer(
                base_year=self.config.data.base_year,
                filter_example=self.config.data.filter_example_rows,
                use_numeric_row=self.config.data.use_numeric_summary_row,
            ),
            detect_by=self.config.sync.detect_by,
        )

        # 非监听模式：同步一次
        if not watch:
            result = syncer.sync()
            # 若有新数据且配置自动跑全链路，则触发
            if result["imported"] > 0 and self.config.sync.auto_run_pipeline:
                logger.info("同步到新数据，自动触发全链路...")
                pipeline_result = self.run_full_cycle(str(excel_path))
                result["pipeline"] = pipeline_result
            return result

        # 监听模式
        logger.info("进入监听模式（间隔 %d 秒），按 Ctrl+C 退出", interval_sec)

        def on_sync(sync_result: Dict[str, Any]) -> None:
            """每次同步到新数据后自动跑全链路。"""
            if sync_result["imported"] > 0 and self.config.sync.auto_run_pipeline:
                try:
                    logger.info("监听到新数据，自动触发全链路...")
                    self.run_full_cycle(str(excel_path))
                except Exception as e:
                    logger.error("自动全链路失败：%s", e, exc_info=True)

        syncer.watch(interval=interval_sec, on_sync=on_sync)
        return {"status": "watch_stopped", "excel": str(excel_path)}

    def run_sync_status(self, excel: Optional[str] = None) -> Dict[str, Any]:
        """查询同步状态（不执行同步）。"""
        from alphaforge.data.auto_sync import 主流水线Sync

        excel_path = self._resolve_excel_path(excel)
        state_db = self.config.paths.sync_state_db
        syncer = 主流水线Sync(
            excel_path=str(excel_path),
            state_db=state_db,
            detect_by=self.config.sync.detect_by,
        )
        return syncer.status()

    # ---------- 环境检查 ----------

    def doctor(self) -> Dict[str, Any]:
        """环境自检。"""
        result = {"config": "OK", "deps": "OK", "external_scorer_source": "未配置"}
        try:
            self.config.validate()
        except Exception as e:
            result["config"] = f"FAIL: {e}"
        # 依赖检查
        for mod in ("pandas", "numpy", "openpyxl", "pydantic", "yaml"):
            try:
                __import__(mod)
            except ImportError:
                result["deps"] = f"FAIL: 缺少 {mod}"
                break
        # 数据源：优先 external_scorer_file，其次 external_scorer_dir
        path = self.config.paths.external_scorer_file
        if not path and self.config.paths.external_scorer_dir:
            path = str(Path(self.config.paths.external_scorer_dir) / self.config.sync.excel_filename)
        if path and Path(path).exists():
            try:
                importer = 主流水线Importer(base_year=self.config.data.base_year)
                sheets = importer.sheet_names(path)
                result["external_scorer_source"] = f"找到 {len(sheets)} 个 sheet：{sheets[:5]}"
            except Exception as e:
                result["external_scorer_source"] = f"FAIL: {e}"
        elif path:
            result["external_scorer_source"] = f"FAIL: 文件不存在 {path}"
        return result

    # ---------- 内部 ----------

    def _overall_health(self, risk_score: float, return_score: float) -> str:
        avg = (risk_score + return_score) / 2
        if avg >= 80:
            return "优秀（≥80）"
        if avg >= 60:
            return "良好（60-80）"
        if avg >= 40:
            return "一般（40-60）"
        return "较差（<40）"

    def _recommendations(self, risk, returns) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        if risk.risk_level == "HIGH":
            recs.append({
                "priority": "HIGH", "category": "风控",
                "title": "组合风险偏高，建议减仓或对冲",
                "description": f"风险评分 {risk.risk_score:.0f}，最大回撤 {risk.max_drawdown*100:.1f}%",
            })
        if returns.return_level == "POOR":
            recs.append({
                "priority": "HIGH", "category": "收益",
                "title": "收益表现不佳，建议复盘策略",
                "description": f"收益评分 {returns.return_score:.0f}，累计收益 {returns.total_return*100:.1f}%",
            })
        for a in risk.alerts:
            recs.append({
                "priority": a["level"], "category": "预警",
                "title": a["type"], "description": a["message"],
            })
        if not recs:
            recs.append({
                "priority": "LOW", "category": "维持",
                "title": "组合运行正常，维持当前策略",
                "description": "无触发预警，各项指标在阈值内",
            })
        return recs

    def _load_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        """从 records.json 加载最新组合快照（用于报告持仓明细）。"""
        records_path = self.output_dir / "data" / "records.json"
        if not records_path.exists():
            return None
        try:
            data = json.loads(records_path.read_text(encoding="utf-8"))
            snaps = data.get("portfolio_snapshots", [])
            if not snaps:
                return None
            last = snaps[-1]
            from alphaforge.models.position import Position
            positions = {
                k: Position(**v) for k, v in last.get("positions", {}).items()
            }
            return PortfolioSnapshot(
                date=datetime.fromisoformat(last["date"]),
                cash=last["cash"],
                positions=positions,
                nav=last["nav"],
                daily_pnl=last["daily_pnl"],
                daily_return_pct=last["daily_return_pct"],
            )
        except Exception as e:
            logger.warning("加载最新快照失败：%s", e)
            return None