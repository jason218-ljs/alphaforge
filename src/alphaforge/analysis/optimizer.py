"""参数寻优：网格搜索 + Optuna 贝叶斯优化。

目标函数用回测绩效（默认夏普，可选 total_return/sortino/calmar），可加最大回撤约束。
"""

from __future__ import annotations

import math
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from alphaforge.logger import get_logger

logger = get_logger("analysis.optimizer")

# 目标函数别名 → 返回 summary 的键
OBJECTIVE_KEYS = {
    "sharpe": "sharpe_ratio",
    "total_return": "total_return",
    "annualized_return": "annualized_return",
    "calmar": "calmar_ratio",
}


@dataclass
class ParamSpace:
    """参数空间定义。"""

    name: str
    low: float
    high: float
    step: Optional[float] = None   # 网格用；None 则步进为 (high-low)/grid_steps
    log: bool = False              # 贝叶斯是否用 log 采样
    is_int: bool = False           # 是否为整数参数
    choices: Optional[List[Any]] = None  # 若提供，用离散取值（忽略 low/high）


@dataclass
class OptimizationResult:
    """寻优结果。"""

    best_params: Dict[str, Any]
    objective_score: float
    best_summary: Dict[str, Any]
    trials: List[Dict[str, Any]]
    method: str

    def summary_dict(self) -> dict:
        return {
            "method": self.method,
            "best_params": self.best_params,
            "objective_score": self.objective_score,
            "best_summary": self.best_summary,
            "n_trials": len(self.trials),
            "trials": self.trials,
        }


class ParameterOptimizer:
    """参数寻优器。"""

    def __init__(self, objective: str = "sharpe",
                 max_drawdown_constraint: float = 0.20, maximize: bool = True):
        self.objective = objective
        self.objective_key = OBJECTIVE_KEYS.get(objective, "sharpe_ratio")
        self.max_drawdown_constraint = max_drawdown_constraint
        self.maximize = maximize

    # ---------- 网格搜索 ----------

    def grid_search(
        self,
        objective_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        spaces: List[ParamSpace],
        grid_points: int = 10,
    ) -> OptimizationResult:
        """网格搜索。objective_fn(params) → backtest summary dict。"""
        grid = self._build_grid(spaces, grid_points)
        best_score: Optional[float] = None
        best_params: Optional[Dict[str, Any]] = None
        best_summary: Optional[Dict[str, Any]] = None
        trials: List[Dict[str, Any]] = []

        for params in grid:
            summary = objective_fn(params)
            score = self._score(summary, params)
            trials.append({
                "params": dict(params),
                "score": score,
                "summary": summary,
            })
            if best_score is None or (self.maximize and score > best_score) \
                    or (not self.maximize and score < best_score):
                best_score = score
                best_params = dict(params)
                best_summary = summary

        return OptimizationResult(
            best_params=best_params or {},
            objective_score=best_score or 0.0,
            best_summary=best_summary or {},
            trials=trials,
            method="grid",
        )

    # ---------- 贝叶斯（Optuna） ----------

    def bayesian_search(
        self,
        objective_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        spaces: List[ParamSpace],
        n_trials: int = 50,
        seed: Optional[int] = None,
    ) -> OptimizationResult:
        """Optuna TPE 贝叶斯优化。"""
        try:
            import optuna
        except ImportError:
            logger.warning("optuna 未安装，回退网格搜索")
            return self.grid_search(objective_fn, spaces, grid_points=10)

        study = optuna.create_study(direction="maximize" if self.maximize else "minimize",
                                    sampler=optuna.samplers.TPESampler(seed=seed))
        trials_data: List[Dict[str, Any]] = []

        def objective(trial) -> float:
            params = {}
            for sp in spaces:
                if sp.choices is not None:
                    params[sp.name] = trial.suggest_categorical(sp.name, sp.choices)
                elif sp.is_int:
                    params[sp.name] = trial.suggest_int(
                        sp.name, int(sp.low), int(sp.high),
                        step=int(sp.step) if sp.step else 1, log=sp.log
                    )
                else:
                    params[sp.name] = trial.suggest_float(
                        sp.name, sp.low, sp.high, log=sp.log
                    )
            summary = objective_fn(params)
            score = self._score(summary, params)
            trials_data.append({"params": dict(params), "score": score, "summary": summary})
            return score

        study.optimize(objective, n_trials=n_trials)
        best_params = study.best_params or {}
        best_score = study.best_value if study.best_trial else 0.0
        best_summary = self._find_best_summary(best_params, trials_data)

        return OptimizationResult(
            best_params=best_params,
            objective_score=best_score,
            best_summary=best_summary,
            trials=trials_data,
            method="bayesian",
        )

    # ---------- 工具 ----------

    def _build_grid(self, spaces: List[ParamSpace], grid_points: int) -> List[Dict]:
        """构造网格参数组合。"""
        if not spaces:
            return []
        all_values: List[List[Any]] = []
        for sp in spaces:
            if sp.choices is not None:
                all_values.append(list(sp.choices))
            elif sp.step and sp.step > 0:
                vals = []
                v = sp.low
                while v <= sp.high + 1e-9:
                    vals.append(int(v) if sp.is_int else v)
                    v += sp.step
                all_values.append(vals)
            else:
                n = max(grid_points, 2)
                step = (sp.high - sp.low) / (n - 1)
                vals = [sp.low + i * step for i in range(n)]
                if sp.is_int:
                    vals = sorted({int(round(v)) for v in vals})
                all_values.append(vals)
        grid = [dict(zip([sp.name for sp in spaces], combo))
                for combo in itertools.product(*all_values)]
        return grid

    def _score(self, summary: Dict[str, Any], params: Dict[str, Any]) -> float:
        """目标函数分值。含回撤约束惩罚。"""
        mdd = summary.get("max_drawdown", 0.0)
        # 回撤约束：若回撤超过限制，严重惩罚
        if self.max_drawdown_constraint and mdd < -abs(self.max_drawdown_constraint):
            # 超出部分每 1% 扣 100 分（大幅压低）
            over = abs(mdd) - abs(self.max_drawdown_constraint)
            return -100.0 - over * 100.0
        return float(summary.get(self.objective_key, 0.0))

    def _find_best_summary(self, best_params: Dict[str, Any], trials: List[dict]) -> dict:
        for t in trials:
            if t["params"] == best_params:
                return t["summary"]
        return {}


# 便捷封装：MA 策略寻优
def optimize_ma_strategy(
    prices: List[float],
    fast_range=(2, 15),
    slow_range=(10, 60),
    objective: str = "sharpe",
    method: str = "bayesian",
    n_trials: int = 30,
    qty_per_signal: int = 100,
    mdd_constraint: float = 0.20,
    seed: Optional[int] = None,
) -> OptimizationResult:
    """对 MA 金叉死叉策略做参数寻优（fast/slow)。"""
    from alphaforge.engine.vectorized_engine import VectorizedEngine

    engine = VectorizedEngine()
    spaces = [
        ParamSpace(name="fast", low=fast_range[0], high=fast_range[1], is_int=True),
        ParamSpace(name="slow", low=slow_range[0], high=slow_range[1], is_int=True),
    ]
    optimizer = ParameterOptimizer(objective=objective,
                                   max_drawdown_constraint=mdd_constraint)

    def obj(params):
        return engine.run_ma_strategy(
            prices, fast=int(params["fast"]), slow=int(params["slow"]),
            qty_per_signal=qty_per_signal,
        )

    if method == "grid":
        return optimizer.grid_search(obj, spaces, grid_points=12)
    return optimizer.bayesian_search(obj, spaces, n_trials=n_trials, seed=seed)