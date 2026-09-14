"""每日报告（文本 + JSON）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from alphaforge.models.portfolio import PortfolioSnapshot
from alphaforge.models.result import AnalysisResult


class DailyReport:
    """日报生成器。"""

    def __init__(self, result: AnalysisResult, snapshot: Optional[PortfolioSnapshot] = None,
                 records: Optional[List[Dict[str, Any]]] = None):
        self.result = result
        self.snapshot = snapshot
        self.records = records or []

    def to_text(self) -> str:
        """渲染纯文本报告。"""
        r = self.result
        lines: List[str] = []
        lines.append("=" * 64)
        lines.append(f"  AlphaForge 每日分析报告")
        lines.append(f"  生成时间：{r.analysis_date.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 64)
        lines.append("")

        # 持仓概览
        if self.snapshot:
            lines.append("【持仓概览】")
            lines.append(f"  NAV：{self.snapshot.nav:,.2f}")
            lines.append(f"  现金：{self.snapshot.cash:,.2f}（占比 {self.snapshot.cash_ratio*100:.1f}%）")
            lines.append(f"  持仓数：{self.snapshot.position_count}")
            if self.snapshot.positions:
                lines.append(f"  {'代码':<12}{'名称':<10}{'数量':>8}{'成本':>12}{'现价':>12}{'浮盈亏%':>10}")
                for code, p in self.snapshot.positions.items():
                    name = (p.name or code)[:8]
                    lines.append(
                        f"  {code:<12}{name:<10}{p.qty:>8}{p.avg_cost:>12.2f}"
                        f"{p.current_price:>12.2f}{p.unrealized_pnl_pct*100:>10.2f}"
                    )
            lines.append("")

        # 风险评估
        risk = r.risk
        lines.append("【风险评估】")
        lines.append(f"  最大回撤：{risk.max_drawdown*100:.2f}%  等级：{risk.risk_level}（评分 {risk.risk_score:.0f}）")
        lines.append(f"  年化波动率：{risk.annualized_volatility*100:.2f}%  下行波动率：{risk.downside_volatility*100:.2f}%")
        lines.append(f"  夏普比率：{risk.sharpe_ratio:.2f}  Sortino：{risk.sortino_ratio:.2f}  Calmar：{risk.calmar_ratio:.2f}")
        lines.append(f"  VaR95：{risk.var_95*100:.2f}%  CVaR95：{risk.cvar_95*100:.2f}%")
        lines.append(f"  单票最高仓位：{risk.single_position_max*100:.1f}%  行业集中度：{risk.industry_concentration*100:.1f}%")
        if risk.alerts:
            lines.append("  预警：")
            for a in risk.alerts:
                lines.append(f"    [{a['level']}] {a['message']}")
        lines.append("")

        # 收益分析
        ret = r.returns
        lines.append("【收益分析】")
        lines.append(f"  累计收益：{ret.total_return*100:.2f}%  年化收益：{ret.annualized_return*100:.2f}%  等级：{ret.return_level}")
        lines.append(f"  胜率：{ret.win_rate*100:.1f}%（盈 {ret.winning_days} / 亏 {ret.losing_days}）  盈亏比：{ret.profit_loss_ratio:.2f}")
        lines.append(f"  Alpha：{ret.alpha*100:.2f}%  Beta：{ret.beta:.2f}  基准收益：{ret.benchmark_return*100:.2f}%")
        lines.append(f"  跟踪误差：{ret.tracking_error*100:.2f}%  信息比率：{ret.information_ratio:.2f}")
        lines.append("")

        # 策略评估
        strat = r.strategy
        lines.append("【策略评估】")
        lines.append(f"  信号数：{strat.signal_count}  执行率：{strat.execution_rate*100:.1f}%  信号胜率：{strat.signal_win_rate*100:.1f}%")
        lines.append(f"  平均持仓周期：{strat.avg_holding_period:.1f} 天  漂移：{'是' if strat.is_drifting else '否'}")
        if strat.observations:
            lines.append("  观察：")
            for o in strat.observations:
                lines.append(f"    - {o}")
        lines.append("")

        # 归因
        if r.attribution.brinson or r.attribution.fama:
            lines.append("【归因分析】")
            if r.attribution.brinson:
                b = r.attribution.brinson
                lines.append(f"  Brinson：配置 {b.get('allocation_effect', 0)*100:.2f}% | "
                             f"选股 {b.get('selection_effect', 0)*100:.2f}% | "
                             f"交互 {b.get('interaction_effect', 0)*100:.2f}%")
            if r.attribution.fama:
                fama = r.attribution.fama
                dec = fama.get("decomposition", {})
                reg = fama.get("regression", {})
                lines.append(f"  Fama分解：系统性 {dec.get('systematic', 0)*100:.2f}% | "
                             f"选股 {dec.get('selection', 0)*100:.2f}% | "
                             f"总计 {dec.get('total', 0)*100:.2f}%")
                lines.append(f"  回归：Alpha {reg.get('alpha', 0)*100:.2f}% | "
                             f"β市场 {reg.get('beta_market', 0):.2f} | "
                             f"R² {reg.get('r_squared', 0):.2f}")
            lines.append("")

        # 操作建议
        if r.recommendations:
            lines.append("【操作建议】")
            for rec in r.recommendations:
                lines.append(f"  [{rec.get('priority', '')}] {rec.get('title', '')}")
                if rec.get("description"):
                    lines.append(f"      {rec['description']}")
            lines.append("")

        # 汇总
        if r.summary:
            lines.append("【综合健康度】")
            lines.append(f"  {r.summary.get('overall_health', '')}")
            lines.append("")

        lines.append("-" * 64)
        lines.append("  免责声明：本报告由 AlphaForge 自动生成，仅供复盘参考，不构成投资建议。")
        lines.append("-" * 64)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """渲染 JSON 结构。"""
        return {
            "report_date": self.result.analysis_date.strftime("%Y-%m-%d"),
            "generated_at": self.result.analysis_date.isoformat(),
            "portfolio": {
                "nav": self.snapshot.nav if self.snapshot else 0.0,
                "cash": self.snapshot.cash if self.snapshot else 0.0,
                "cash_ratio": self.snapshot.cash_ratio if self.snapshot else 0.0,
                "position_count": self.snapshot.position_count if self.snapshot else 0,
            },
            "risk": self.result.risk.model_dump(),
            "returns": self.result.returns.model_dump(),
            "strategy": self.result.strategy.model_dump(),
            "attribution": self.result.attribution.model_dump(),
            "positions": [
                {
                    "code": code,
                    "name": p.name,
                    "qty": p.qty,
                    "avg_cost": p.avg_cost,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
                for code, p in (self.snapshot.positions.items() if self.snapshot else [])
            ],
            "recommendations": self.result.recommendations,
            "summary": self.result.summary,
        }