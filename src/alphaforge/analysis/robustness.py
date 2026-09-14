"""稳健性检验：Walk-Forward 分析 + Monte Carlo 重采样 + 参数敏感性。"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Optional

from alphaforge.logger import get_logger

logger = get_logger("analysis.robustness")


class Robustness:
    """稳健性检验器。"""

    def __init__(self, seed: Optional[int] = None):
        self.seed = seed
        self._rng = random.Random(seed)

    # ---------- Walk-Forward 分析 ----------

    def walk_forward(
        self,
        objective_fn: Callable[[Dict[str, Any], List[float]], Dict[str, Any]],
        prices: List[float],
        params: Dict[str, Any],
        window: int = 30,
        step: int = 5,
        metric: str = "sharpe_ratio",
    ) -> Dict[str, Any]:
        """滚动训练-测试窗口，检查参数稳定性。

        Args:
            objective_fn: (params, train_prices) → summary dict。
            prices: 全期价格序列。
            params: 待验证的参数。
            window: 训练窗口长度。
            step: 每步前进的天数。
            metric: 跟踪的指标。

        Returns:
            {windows: [{train_range, test_range, in_sample, out_sample,
                        ratio, stable}], avg_ratio, is_stable, turnover}
        """
        n = len(prices)
        if n < window + step:
            return {"windows": [], "avg_ratio": 0.0, "is_stable": False,
                    "turnover": 0.0}

        windows = []
        train_start = 0
        ratios = []
        while train_start + window < n:
            train_end = train_start + window
            test_end = min(train_end + step, n)

            train = prices[train_start:train_end]
            test = prices[train_end:test_end]
            if len(test) < 2:
                break

            in_sample = objective_fn(params, train).get(metric, 0.0)
            out_sample = objective_fn(params, test).get(metric, 0.0)
            ratio = abs(out_sample) / abs(in_sample) if abs(in_sample) > 1e-9 else \
                (1.0 if abs(out_sample) <= 1e-9 else 0.0)
            ratios.append(ratio)
            windows.append({
                "train_range": [train_start, train_end],
                "test_range": [train_end, test_end],
                "in_sample": in_sample,
                "out_sample": out_sample,
                "ratio": ratio,
                "stable": ratio >= 0.6,   # 样本外保持 60% 以上表现视为稳定
            })
            train_start += step

        avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
        stable_count = sum(1 for w in windows if w["stable"])
        is_stable = (len(windows) > 0 and stable_count / len(windows) >= 0.6)

        return {
            "windows": windows,
            "avg_ratio": avg_ratio,
            "is_stable": is_stable,
            "turnover": stable_count / len(windows) if windows else 0.0,
        }

    # ---------- Monte Carlo 重采样 ----------

    def monte_carlo(
        self,
        returns: List[float],
        n_runs: int = 1000,
        horizon_days: int = 60,
        initial_capital: float = 1_000_000.0,
        pct: List[float] = (5, 25, 50, 75, 95),
    ) -> Dict[str, Any]:
        """对收益序列重采样（有放回）生成最终权益分布。

        Returns:
            {runs, percentiles, mean, median, std, pnl_percentiles}
        """
        if not returns or n_runs <= 0:
            return {"runs": [], "percentiles": {}, "mean": 0.0, "median": 0.0,
                    "std": 0.0, "pnl_percentiles": {}}
        results = []
        rng = self._rng
        for _ in range(n_runs):
            path = [rng.choice(returns) for _ in range(horizon_days)]
            nav = initial_capital
            for r in path:
                nav *= (1 + r)
            results.append(nav)
        results.sort()
        pct_vals = {}
        for p in pct:
            idx = int((p / 100.0) * (len(results) - 1))
            pct_vals[p] = results[idx]
        mean = sum(results) / len(results)
        std = (sum((x - mean) ** 2 for x in results) / len(results)) ** 0.5
        median = results[len(results) // 2]
        pnl_pct = {k: (v / initial_capital - 1.0) for k, v in pct_vals.items()}
        return {
            "runs": results,
            "percentiles": pct_vals,
            "mean": mean,
            "median": median,
            "std": std,
            "pnl_percentiles": pnl_pct,
        }

    # ---------- 参数敏感性 ----------

    def sensitivity(
        self,
        objective_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        base_params: Dict[str, Any],
        perturbation: Optional[Dict[str, list]] = None,
        metric: str = "sharpe_ratio",
    ) -> Dict[str, Any]:
        """邻域扰动，输出指标变化率。"""
        base_summary = objective_fn(base_params)
        base_score = base_summary.get(metric, 0.0)
        sensitivity = {}
        for name, base_val in base_params.items():
            if not isinstance(base_val, (int, float)):
                continue
            deltas = perturbation.get(name) if perturbation else [-10, -5, 5, 10]
            deltas = [d for d in deltas if isinstance(d, (int, float)) and d != 0]
            scores = []
            for delta in deltas:
                new_val = base_val * (1 + delta / 100.0)
                cand = dict(base_params)
                cand[name] = int(new_val) if isinstance(base_val, int) else new_val
                try:
                    score = objective_fn(cand).get(metric, 0.0)
                except Exception as e:
                    logger.warning("敏感性参数 %s=%s 评估失败：%s", name, new_val, e)
                    score = 0.0
                scores.append({"delta_pct": delta, "score": score})
            # 变化率 = 各扰动 |score-base|/max(|base|,eps) 的平均
            rates = [
                abs(s["score"] - base_score) / max(abs(base_score), 1e-9)
                for s in scores if scores
            ]
            sensitivity[name] = {
                "base_score": base_score,
                "samples": scores,
                "avg_abs_change": sum(rates) / len(rates) if rates else 0.0,
                "is_robust": (sum(rates) / len(rates)) < 0.5 if rates else True,
            }
        return {
            "base_score": base_score,
            "base_params": dict(base_params),
            "sensitivity": sensitivity,
            "is_robust": all(v["is_robust"] for v in sensitivity.values()) if sensitivity else True,
        }