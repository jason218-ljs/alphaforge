"""图表数据组装：从 output/ 的 JSON 读取，组装 ECharts 所需数据结构。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from alphaforge.logger import get_logger

logger = get_logger("viz.charts")


class ChartData:
    """图表数据组装器（只读 output/ 下 JSON）。"""

    def __init__(self, output_dir: Path | str = "./output"):
        self.output_dir = Path(output_dir)

    # ---------- 文件定位 ----------

    def _find_analysis(self) -> Optional[dict]:
        p = self.output_dir / "analysis" / "analysis.json"
        if p.exists():
            return self._load(p)
        return None

    def _find_backtest(self) -> Optional[dict]:
        p = self.output_dir / "backtest" / "backtest.json"
        if p.exists():
            return self._load(p)
        return None

    def _find_screener_backtest(self) -> Optional[dict]:
        """优先真实数据版，回退合成数据版。"""
        for name in ("screener_backtest_real.json", "screener_backtest.json"):
            p = self.output_dir / "backtest" / name
            if p.exists():
                return self._load(p)
        return None

    def _find_screener_scores(self) -> Optional[dict]:
        """优先真实数据版，回退合成数据版。"""
        for name in ("screener_scores_real.json", "screener_scores.json"):
            p = self.output_dir / "backtest" / name
            if p.exists():
                return self._load(p)
        return None

    def _find_records(self) -> Optional[dict]:
        p = self.output_dir / "data" / "records.json"
        if p.exists():
            return self._load(p)
        return None

    def _find_report(self, date: Optional[str] = None) -> Optional[dict]:
        """查找日报 json。优先指定日期，否则找最新。"""
        report_dir = self.output_dir
        candidates = sorted(report_dir.glob("*-详细数据.json"))
        if not candidates:
            return None
        if date:
            target = report_dir / f"{date}-详细数据.json"
            if target.exists():
                return self._load(target)
        return self._load(candidates[-1])

    def _load(self, path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- Dashboard 数据 ----------

    def dashboard(self, date: Optional[str] = None) -> Dict[str, Any]:
        """组装仪表盘所有数据。"""
        analysis = self._find_analysis()
        backtest = self._find_backtest()
        report = self._find_report(date)
        records = self._find_records()

        if not analysis and not records:
            return {"error": "暂无数据，请先运行 sync 或 run-all 生成 output/"}

        # 权益曲线优先用 records 的真实组合快照（回测回放模式下更准）
        equity_curve, drawdown = self._equity_and_drawdown(records, backtest)

        dashboard = {
            "dates": self._available_dates_from_records(records) or self._available_dates(backtest),
            "kpi": self._kpi(analysis, backtest, records),
            "equity_curve": equity_curve,
            "drawdown": drawdown,
            "positions": self._positions(records, date),
            "industry_exposure": self._industry_exposure(records, date),
            "alerts": (analysis or {}).get("risk", {}).get("alerts", []),
            "recommendations": (analysis or {}).get("recommendations", []),
            "attribution_summary": self._attribution_summary(analysis or {}),
            "summary": (analysis or {}).get("summary", {}),
        }
        return dashboard

    # ---------- 各区块 ----------

    def _available_dates(self, backtest: Optional[dict]) -> List[str]:
        if not backtest:
            return []
        return [e["date"][:10] for e in backtest.get("equity_curve", [])]

    def _available_dates_from_records(self, records: Optional[dict]) -> List[str]:
        """从 records 的组合快照提取可用日期列表。"""
        if not records:
            return []
        snaps = records.get("portfolio_snapshots", [])
        return [s["date"][:10] for s in snaps if s.get("date")]

    def _equity_and_drawdown(
        self,
        records: Optional[dict],
        backtest: Optional[dict],
    ) -> tuple:
        """权益曲线 + 回撤：优先用 records 真实快照，回退到 backtest。

        当 records 快照数 >= backtest 权益点数时用 records，否则用 backtest。

        Returns:
            (equity_curve_dict, drawdown_dict)
        """
        rec_snaps = records.get("portfolio_snapshots", []) if records else []
        bt_curve = backtest.get("equity_curve", []) if backtest else []

        # 优先 records（真实组合净值），但要求点数不少于 backtest
        if rec_snaps and len(rec_snaps) >= len(bt_curve):
            dates = [s["date"][:10] for s in rec_snaps]
            navs = [s.get("nav", 0) for s in rec_snaps]
            values = []
            peak = navs[0] if navs else 0
            for v in navs:
                if peak > 0:
                    peak = max(peak, v)
                    values.append((v - peak) / peak * 100)
                else:
                    values.append(0)
            return (
                {"dates": dates, "nav": navs},
                {"dates": dates, "values": values},
            )
        # 回退到 backtest
        return self._equity_curve(backtest), self._drawdown(backtest)

    def _kpi(
        self,
        analysis: Optional[dict],
        backtest: Optional[dict],
        records: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """KPI：优先 analysis，缺失字段从 records 补。"""
        analysis = analysis or {}
        rets = analysis.get("returns", {})
        risk = analysis.get("risk", {})

        # 期末净值：优先 records 最后一个快照
        final_nav = 0.0
        if records:
            snaps = records.get("portfolio_snapshots", [])
            if snaps:
                final_nav = snaps[-1].get("nav", 0)
        if not final_nav and backtest:
            final_nav = backtest.get("summary", {}).get("final_nav", 0)

        # 累计收益：从 records 算（更准）
        total_return = rets.get("total_return", 0.0)
        if records and not total_return:
            snaps = records.get("portfolio_snapshots", [])
            if len(snaps) >= 2:
                first_nav = snaps[0].get("nav", 0)
                last_nav = snaps[-1].get("nav", 0)
                if first_nav > 0:
                    total_return = last_nav / first_nav - 1.0

        return {
            "total_return": total_return * 100,
            "annualized": rets.get("annualized_return", 0.0) * 100,
            "max_drawdown": risk.get("max_drawdown", 0.0) * 100,
            "sharpe": risk.get("sharpe_ratio", 0.0),
            "win_rate": rets.get("win_rate", 0.0) * 100,
            "sortino": risk.get("sortino_ratio", 0.0),
            "calmar": risk.get("calmar_ratio", 0.0),
            "volatility": risk.get("annualized_volatility", 0.0) * 100,
            "final_nav": final_nav,
        }

    def _equity_curve(self, backtest: Optional[dict]) -> Dict[str, Any]:
        if not backtest:
            return {"dates": [], "nav": []}
        curve = backtest.get("equity_curve", [])
        return {
            "dates": [e["date"][:10] for e in curve],
            "nav": [e["nav"] for e in curve],
        }

    def _drawdown(self, backtest: Optional[dict]) -> Dict[str, Any]:
        if not backtest:
            return {"dates": [], "values": []}
        curve = backtest.get("equity_curve", [])
        if not curve:
            return {"dates": [], "values": []}
        dates = [e["date"][:10] for e in curve]
        navs = [e["nav"] for e in curve]
        values = []
        peak = navs[0]
        for v in navs:
            peak = max(peak, v)
            values.append((v - peak) / peak * 100) if peak > 0 else values.append(0)
        return {"dates": dates, "values": values}

    # ---------- 选股打分策略回测（scoring_backtest） ----------

    def _find_scoring_backtest(self) -> Optional[dict]:
        """定位选股策略回测结果（output/scoring_backtest/backtest_*.json）。"""
        d = self.output_dir / "scoring_backtest"
        if not d.exists():
            return None
        files = sorted(d.glob("backtest_*.json"))
        if not files:
            return None
        return self._load(files[-1])

    def scoring(self) -> Dict[str, Any]:
        """选股策略回测可视化数据。"""
        bt = self._find_scoring_backtest()
        if not bt:
            return {"error": "暂无选股策略回测数据，请先运行 scripts/backtest_scoring.py"}
        summary = bt.get("summary", {})
        equity_curve = bt.get("equity_curve", [])
        dates = [e["date"][:10] for e in equity_curve]
        nav = [e["nav"] for e in equity_curve]
        # NAV 归一化到 1000
        nav0 = nav[0] if nav and nav[0] > 0 else 1000000
        nav_norm = [round(v / nav0 * 1000, 1) for v in nav]
        # 回撤
        peak = 0
        drawdown = []
        for v in nav:
            peak = max(peak, v)
            drawdown.append(round((v - peak) / peak * 100, 2) if peak > 0 else 0)
        # 交易记录
        trades = []
        for t in bt.get("trade_log", []):
            trades.append({
                "date": t["date"][:10], "side": t["side"], "code": t["code"],
                "qty": t.get("filled_qty", 0), "price": round(t.get("price", 0), 2),
                "reason": t.get("reason", ""),
            })
        # 信号
        signals = []
        for s in bt.get("signal_log", []):
            signals.append({
                "date": s["date"][:10], "side": s["side"], "code": s["code"],
                "qty": s.get("qty", 0), "price": round(s.get("price", 0), 2),
                "reason": s.get("reason", ""),
            })
        return {
            "start_date": dates[0] if dates else "",
            "end_date": dates[-1] if dates else "",
            "days": summary.get("days", 0),
            "pool_size": bt.get("pool_size", 0),
            "benchmark": bt.get("benchmark", {}),
            "kpi": {
                "total_return": round(summary.get("total_return", 0) * 100, 2),
                "annualized": round(summary.get("annualized_return", 0) * 100, 2),
                "max_drawdown": round(summary.get("max_drawdown", 0) * 100, 2),
                "sharpe": round(summary.get("sharpe_ratio", 0), 2),
                "win_rate": round(summary.get("win_rate", 0) * 100, 1),
                "profit_loss_ratio": round(summary.get("profit_loss_ratio", 0), 2),
                "total_trades": summary.get("total_trades", 0),
                "final_nav": summary.get("final_nav", 0),
            },
            "equity_curve": {"dates": dates, "nav": nav_norm},
            "drawdown": {"dates": dates, "values": drawdown},
            "trades": trades,
            "signals": signals,
            "pool_stocks": bt.get("pool_stocks", []),
        }

    def _positions(self, records: Optional[dict], date: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取某日持仓（默认最新）。"""
        if not records:
            return []
        snaps = records.get("portfolio_snapshots", [])
        if not snaps:
            return []
        # 按日期匹配，找不到就用最新
        target = None
        if date:
            for s in snaps:
                if s.get("date", "")[:10] == date:
                    target = s
                    break
        if target is None:
            target = snaps[-1]
        total_mv = sum(p.get("market_value", 0) or 0 for p in target.get("positions", {}).values())
        out = []
        for code, pos in target.get("positions", {}).items():
            mv = pos.get("market_value", 0) or (pos.get("qty", 0) * pos.get("current_price", 0))
            name = pos.get("name") or code
            out.append({
                "code": code,
                "name": name,
                "market_value": mv,
                "pct": (mv / total_mv * 100) if total_mv > 0 else 0,
                "unrealized_pnl_pct": (pos.get("unrealized_pnl_pct", 0) or 0) * 100,
            })
        out.sort(key=lambda x: x["market_value"], reverse=True)
        return out

    def _industry_exposure(self, records: Optional[dict], date: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取某日行业暴露（默认最新）。"""
        if not records:
            return []
        snaps = records.get("portfolio_snapshots", [])
        if not snaps:
            return []
        target = None
        if date:
            for s in snaps:
                if s.get("date", "")[:10] == date:
                    target = s
                    break
        if target is None:
            target = snaps[-1]
        total_mv = sum(p.get("market_value", 0) or p.get("qty", 0) * p.get("current_price", 0)
                       for p in target.get("positions", {}).values())
        buckets = {}
        for code, pos in target.get("positions", {}).items():
            ind = pos.get("industry") or self._board(code)
            mv = pos.get("market_value", 0) or pos.get("qty", 0) * pos.get("current_price", 0)
            buckets[ind] = buckets.get(ind, 0) + mv
        return [{"industry": k, "pct": (v / total_mv * 100) if total_mv > 0 else 0}
                for k, v in buckets.items()]

    def _board(self, code: str) -> str:
        if code.startswith(("60", "00")):
            return "主板"
        if code.startswith("30"):
            return "创业板"
        if code.startswith("68"):
            return "科创板"
        return "其他"

    def _attribution_summary(self, analysis: dict) -> Dict[str, Any]:
        att = analysis.get("attribution", {})
        return {
            "brinson": att.get("brinson", {}),
            "fama": att.get("fama", {}),
        }

    # ---------- 归因瀑布图数据 ----------

    def attribution(self) -> Dict[str, Any]:
        """归因页数据：Brinson 瀑布 + Fama 分解。"""
        analysis = self._find_analysis()
        if not analysis:
            return {"error": "暂无归因数据"}
        att = analysis.get("attribution", {})
        brinson = att.get("brinson", {})
        fama = att.get("fama", {})
        return {
            "brinson_waterfall": self._brinson_waterfall(brinson),
            "fama_pie": self._fama_pie(fama),
            "brinson_detail": brinson.get("industry_detail", []),
            "fama_regression": fama.get("regression", {}),
        }

    def _brinson_waterfall(self, brinson: dict) -> List[Dict[str, Any]]:
        """转瀑布图数据：起点[基准] → 配置 → 选股 → 交互 → 终点[超额]。"""
        alloc = brinson.get("allocation_effect", 0.0) * 100
        select = brinson.get("selection_effect", 0.0) * 100
        interact = brinson.get("interaction_effect", 0.0) * 100
        total = brinson.get("total_effect", 0.0) * 100
        # 瀑布图每一步累计
        step1 = 0.0 + alloc
        step2 = step1 + select
        step3 = step2 + interact
        return [
            {"label": "基准收益", "value": 0.0, "cumulative": 0.0, "type": "base"},
            {"label": "配置效应", "value": alloc, "cumulative": step1, "type": "positive" if alloc >= 0 else "negative"},
            {"label": "选股效应", "value": select, "cumulative": step2, "type": "positive" if select >= 0 else "negative"},
            {"label": "交互效应", "value": interact, "cumulative": step3, "type": "positive" if interact >= 0 else "negative"},
            {"label": "超额收益", "value": total, "cumulative": total, "type": "total"},
        ]

    def _fama_pie(self, fama: dict) -> List[Dict[str, Any]]:
        dec = fama.get("decomposition", {}) if fama else {}
        # 单因子回归下 systematic == factor_exposure，避免饼图重复扇区，
        # 用 market(系统性) + selection(选股) 两核心构成
        items = [
            {"name": "系统性收益(市场)", "value": round(dec.get("systematic", 0) * 100, 4)},
            {"name": "选股回报(Alpha)", "value": round(dec.get("selection", 0) * 100, 4)},
        ]
        # 过滤接近 0 的项，避免饼图空扇区
        return [it for it in items if abs(it["value"]) > 0.001]

    # ---------- Backtest 详情 ----------

    def backtest_detail(self) -> Dict[str, Any]:
        backtest = self._find_backtest()
        if not backtest:
            return {"error": "暂无回测数据"}
        return {
            "trade_log": backtest.get("trade_log", []),
            "signal_log": backtest.get("signal_log", []),
            "summary": backtest.get("summary", {}),
            "config_snapshot": backtest.get("config_snapshot", {}),
        }

    # ---------- 选股策略回测 ----------

    def screener(self) -> Dict[str, Any]:
        """选股策略回测数据：KPI + 权益 + 评分明细 + 信号 + 行业分布。"""
        bt = self._find_screener_backtest()
        scores = self._find_screener_scores()
        if not bt:
            return {"error": "暂无选股回测数据，请先运行 screener-backtest"}

        equity = bt.get("equity_curve", [])
        summary = bt.get("summary", {})
        config = bt.get("strategy_config", {})

        # KPI
        kpi = {
            "total_return": summary.get("total_return", 0.0) * 100,
            "annualized": summary.get("annualized_return", 0.0) * 100,
            "max_drawdown": summary.get("max_drawdown", 0.0) * 100,
            "sharpe": summary.get("sharpe_ratio", 0.0),
            "sortino": summary.get("sortino_ratio", 0.0),
            "calmar": summary.get("calmar_ratio", 0.0),
            "win_rate": summary.get("win_rate", 0.0) * 100,
            "total_trades": summary.get("total_trades", 0),
            "final_nav": summary.get("final_nav", 0.0),
            "initial_capital": summary.get("initial_capital", 0.0),
        }

        # 权益曲线 + 回撤
        equity_curve, drawdown = self._screener_equity_drawdown(equity)

        # 评分明细（最新日 Top N）
        latest_scores, score_history_dates = self._screener_latest_scores(scores)

        # 买卖信号时间线
        signals = self._screener_signals(scores)

        # 行业得分分布（最新日按行业聚合）
        industry_dist = self._screener_industry_distribution(latest_scores)

        # 持仓（最新日）
        latest_held = self._screener_latest_held(scores)

        # 候选池
        universe = scores.get("universe", []) if scores else []

        # 信号统计
        buy_count = sum(1 for s in signals if s["side"] == "BUY")
        sell_count = sum(1 for s in signals if s["side"] == "SELL")

        return {
            "kpi": kpi,
            "equity_curve": equity_curve,
            "drawdown": drawdown,
            "latest_scores": latest_scores,
            "score_history_dates": score_history_dates,
            "signals": signals,
            "signal_stats": {"buy": buy_count, "sell": sell_count},
            "industry_distribution": industry_dist,
            "latest_held": latest_held,
            "universe": universe,
            "strategy_config": config,
            "note": bt.get("note", ""),
        }

    def _screener_equity_drawdown(self, equity: List[Dict[str, Any]]) -> tuple:
        if not equity:
            return {"dates": [], "nav": []}, {"dates": [], "values": []}
        dates = [e["date"][:10] for e in equity]
        navs = [e.get("nav", 0) for e in equity]
        values = []
        peak = navs[0] if navs else 0
        for v in navs:
            if peak > 0:
                peak = max(peak, v)
                values.append((v - peak) / peak * 100)
            else:
                values.append(0)
        return ({"dates": dates, "nav": navs},
                {"dates": dates, "values": values})

    def _screener_latest_scores(self, scores: Optional[dict]) -> tuple:
        """取最新日的 Top N 评分明细。"""
        if not scores:
            return [], []
        daily = scores.get("daily_scores", [])
        if not daily:
            return [], []
        dates = [d["date"][:10] for d in daily]
        latest = daily[-1]
        top_n = latest.get("top_n", [])
        # 补充行业（top_n 里已有）
        return top_n, dates

    def _screener_signals(self, scores: Optional[dict]) -> List[Dict[str, Any]]:
        if not scores:
            return []
        return scores.get("daily_signals", [])

    def _screener_industry_distribution(self, latest_scores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按行业聚合最新日 Top N 的平均得分。"""
        if not latest_scores:
            return []
        buckets: Dict[str, List[float]] = {}
        for s in latest_scores:
            ind = s.get("industry", "未知")
            buckets.setdefault(ind, []).append(s.get("total", 0))
        out = []
        for ind, totals in buckets.items():
            out.append({
                "industry": ind,
                "count": len(totals),
                "avg_score": round(sum(totals) / len(totals), 2),
                "max_score": round(max(totals), 2),
            })
        out.sort(key=lambda x: x["avg_score"], reverse=True)
        return out

    def _screener_latest_held(self, scores: Optional[dict]) -> List[Dict[str, Any]]:
        if not scores:
            return []
        daily = scores.get("daily_scores", [])
        if not daily:
            return []
        return daily[-1].get("held", [])