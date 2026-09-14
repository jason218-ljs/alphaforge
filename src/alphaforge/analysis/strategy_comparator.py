"""多策略对比器。

将多个策略的回测结果放在一起对比，输出排名表、胜率矩阵、相关性矩阵，
帮助快速识别哪个策略最优、哪些策略高度同质化。

典型用途：
    comparator = StrategyComparator()
    comparator.add("MA_5_20", result_a)
    comparator.add("MA_10_60", result_b)
    comparator.add("RSI_14", result_c)
    report = comparator.compare()
    # report 包含 ranking / correlation / risk_return_matrix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from alphaforge.logger import get_logger
from alphaforge.models.result import BacktestResult

logger = get_logger("analysis.comparator")


@dataclass
class StrategyMetrics:
    """单策略关键指标快照（用于对比）。"""

    name: str
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    final_nav: float = 0.0
    days: int = 0
    daily_returns: List[float] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    @classmethod
    def from_result(cls, name: str, result: BacktestResult) -> "StrategyMetrics":
        """从 BacktestResult 提取关键指标。"""
        s = result.summary
        # 从权益曲线计算日收益率
        nav_list = [e.get("nav", 0.0) for e in result.equity_curve]
        daily_rets: List[float] = []
        for i in range(1, len(nav_list)):
            prev = nav_list[i - 1]
            if prev > 0:
                daily_rets.append(nav_list[i] / prev - 1.0)
            else:
                daily_rets.append(0.0)
        return cls(
            name=name,
            total_return=s.total_return,
            annualized_return=s.annualized_return,
            max_drawdown=s.max_drawdown,
            sharpe_ratio=s.sharpe_ratio,
            sortino_ratio=s.sortino_ratio,
            calmar_ratio=s.calmar_ratio,
            win_rate=s.win_rate,
            profit_loss_ratio=s.profit_loss_ratio,
            total_trades=s.total_trades,
            final_nav=s.final_nav,
            days=s.days,
            daily_returns=daily_rets,
            equity_curve=nav_list,
        )


class StrategyComparator:
    """多策略对比器。"""

    def __init__(self):
        self._strategies: Dict[str, StrategyMetrics] = {}

    def add(self, name: str, result: BacktestResult) -> None:
        """添加一个策略结果。"""
        self._strategies[name] = StrategyMetrics.from_result(name, result)

    def add_metrics(self, metrics: StrategyMetrics) -> None:
        """直接添加指标对象。"""
        self._strategies[metrics.name] = metrics

    def remove(self, name: str) -> bool:
        """移除策略。"""
        return self._strategies.pop(name, None) is not None

    def list_names(self) -> List[str]:
        """列出所有策略名。"""
        return list(self._strategies.keys())

    # ---------- 排名 ----------

    def rank(
        self,
        metric: str = "sharpe_ratio",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """按指定指标排名。

        Args:
            metric: 排序指标，如 sharpe_ratio / total_return / max_drawdown。
            ascending: True 表示升序（max_drawdown 用升序，越大越差，绝对值小者优）。

        Returns:
            排名表，每项含 rank / name / metric_value / 全部指标。
        """
        items = list(self._strategies.values())
        if not items:
            return []

        # max_drawdown 是负数，越小（绝对值越大）越差，默认按升序（从最差到最好）
        items.sort(
            key=lambda m: getattr(m, metric, 0.0),
            reverse=not ascending,
        )

        ranking: List[Dict[str, Any]] = []
        for i, m in enumerate(items, 1):
            ranking.append({
                "rank": i,
                "name": m.name,
                "metric_value": getattr(m, metric, 0.0),
                "total_return": m.total_return,
                "annualized_return": m.annualized_return,
                "max_drawdown": m.max_drawdown,
                "sharpe_ratio": m.sharpe_ratio,
                "sortino_ratio": m.sortino_ratio,
                "calmar_ratio": m.calmar_ratio,
                "win_rate": m.win_rate,
                "profit_loss_ratio": m.profit_loss_ratio,
                "total_trades": m.total_trades,
            })
        return ranking

    # ---------- 相关性 ----------

    def returns_correlation(self) -> Dict[str, Any]:
        """策略间日收益率相关性矩阵。

        Returns:
            {"matrix": [[...]], "names": [...], "pairs": [{name_a, name_b, corr}]}
            pairs 是按相关性绝对值降序排列的策略对。
        """
        names = list(self._strategies.keys())
        if len(names) < 2:
            return {"matrix": [], "names": names, "pairs": []}

        # 对齐长度（取最短）
        min_len = min(len(m.daily_returns) for m in self._strategies.values())
        if min_len < 2:
            return {"matrix": [], "names": names, "pairs": []}

        series = np.array([
            np.array(m.daily_returns[:min_len]) for m in self._strategies.values()
        ])
        # 相关系数矩阵
        corr = np.corrcoef(series)

        # 提取策略对
        pairs = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                pairs.append({
                    "name_a": names[i],
                    "name_b": names[j],
                    "corr": float(corr[i, j]) if np.isfinite(corr[i, j]) else 0.0,
                })
        pairs.sort(key=lambda p: abs(p["corr"]), reverse=True)

        return {
            "matrix": corr.tolist(),
            "names": names,
            "pairs": pairs,
        }

    # ---------- 综合 ----------

    def compare(self, rank_metric: str = "sharpe_ratio") -> Dict[str, Any]:
        """生成完整对比报告。

        Args:
            rank_metric: 排名指标。

        Returns:
            {ranking, correlation, best_strategy, worst_strategy,
             homogeneous_groups}
        """
        ranking = self.rank(metric=rank_metric, ascending=False)
        corr_data = self.returns_correlation()

        best = ranking[0] if ranking else None
        worst = ranking[-1] if ranking else None

        # 同质化分组：相关性 > 0.85 的策略对
        homogeneous: List[List[str]] = []
        high_corr_pairs = [p for p in corr_data["pairs"] if abs(p["corr"]) > 0.85]
        if high_corr_pairs:
            # 简单的并查集分组
            parent = {n: n for n in self._strategies.keys()}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for p in high_corr_pairs:
                union(p["name_a"], p["name_b"])

            groups: Dict[str, List[str]] = {}
            for n in self._strategies.keys():
                root = find(n)
                groups.setdefault(root, []).append(n)
            homogeneous = [g for g in groups.values() if len(g) > 1]

        return {
            "ranking": ranking,
            "correlation": corr_data,
            "best_strategy": best,
            "worst_strategy": worst,
            "homogeneous_groups": homogeneous,
            "total_strategies": len(self._strategies),
        }

    # ---------- 输出 ----------

    def to_markdown(self, rank_metric: str = "sharpe_ratio") -> str:
        """生成 Markdown 对比表。"""
        report = self.compare(rank_metric=rank_metric)
        if not report["ranking"]:
            return "无策略可对比"

        lines = [
            "## 多策略对比报告",
            "",
            f"共对比 {report['total_strategies']} 个策略，按 {rank_metric} 排名：",
            "",
            "| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 卡玛 | 胜率 | 交易数 |",
            "|------|------|--------|------|----------|------|------|------|--------|",
        ]
        for r in report["ranking"]:
            lines.append(
                f"| {r['rank']} | {r['name']} | {r['total_return']*100:.2f}% | "
                f"{r['annualized_return']*100:.2f}% | {r['max_drawdown']*100:.2f}% | "
                f"{r['sharpe_ratio']:.2f} | {r['calmar_ratio']:.2f} | "
                f"{r['win_rate']*100:.1f}% | {r['total_trades']} |"
            )

        if report["best_strategy"]:
            lines.append("")
            lines.append(f"**最优策略**：{report['best_strategy']['name']}")
        if report["worst_strategy"]:
            lines.append(f"**最差策略**：{report['worst_strategy']['name']}")

        if report["homogeneous_groups"]:
            lines.append("")
            lines.append("**同质化策略组**（相关性 > 0.85）：")
            for g in report["homogeneous_groups"]:
                lines.append(f"- {' / '.join(g)}")

        return "\n".join(lines)
