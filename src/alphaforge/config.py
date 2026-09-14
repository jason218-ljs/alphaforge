"""AlphaForge 配置加载。

优先级：默认值 < config.yaml < 环境变量 ALPHAFORGE_* < CLI 参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class PathsConfig:
    data_dir: str = "./data"
    output_dir: str = "./output"
    cache_db: str = "./data/cache.db"
    pit_db: str = "./data/pit.db"
    external_scorer_file: str = ""        # 单个 Excel 文件路径（手动指定）
    external_scorer_dir: str = ""         # 数据源目录（自动同步用，含 Excel 文件）
    sync_state_db: str = "./data/sync_state.db"  # 同步状态库（记录已导入 sheet）


@dataclass
class DataConfig:
    base_year: int = 2026
    filter_example_rows: bool = True
    use_numeric_summary_row: bool = True


@dataclass
class MarketRulesConfig:
    stamp_tax_rate: float = 0.001
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    transfer_fee_rate: float = 0.00002
    slippage_pct: float = 0.0005
    slippage_fixed: float = 0.01
    enable_t_plus_1: bool = True
    price_limit: Dict[str, float] = field(
        default_factory=lambda: {"MAIN": 0.10, "GEM": 0.20, "STAR": 0.20, "BEI": 0.30}
    )


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    default_period_days: int = 60
    target_ma_schedule: list = field(
        default_factory=lambda: [250_000.0, 500_000.0, 750_000.0, 1_000_000.0]
    )


@dataclass
class AnalysisConfig:
    risk_free_rate: float = 0.03
    annualization_factor: int = 252
    benchmark: str = "000300.SH"
    max_drawdown_warning: float = -0.15
    max_volatility_warning: float = 0.30
    min_sharpe_ratio: float = 0.5
    max_single_position: float = 0.25
    max_industry_exposure: float = 0.25


@dataclass
class OptimizerConfig:
    method: str = "bayesian"
    n_trials: int = 100
    objective: str = "sharpe"
    max_drawdown_constraint: float = 0.20


@dataclass
class RobustnessConfig:
    walk_forward_window: int = 30
    monte_carlo_runs: int = 1000


@dataclass
class CapitalConstraintsConfig:
    weekly_max: float = 250_000.0
    daily_max: float = 50_000.0
    cycle_days: int = 30


@dataclass
class ReportConfig:
    daily_enabled: bool = True
    weekly_enabled: bool = True
    backtest_enabled: bool = True
    output_format: list = field(default_factory=lambda: ["txt", "json"])


@dataclass
class VizConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8050
    debug: bool = False
    cdn: bool = False


@dataclass
class SyncConfig:
    """自动同步配置。"""
    excel_filename: str = "投资流水记录表.xlsx"  # 数据源 Excel 文件名
    interval_seconds: int = 300         # watch 模式检查间隔（秒）
    auto_run_pipeline: bool = True      # 同步后自动跑回测+分析+报告
    detect_by: str = "sheet"            # "sheet"（新增 sheet）或 "mtime"（文件修改时间）


@dataclass
class AlphaForgeConfig:
    app: Dict[str, Any] = field(default_factory=lambda: {"name": "AlphaForge", "version": "1.0.0", "log_level": "INFO"})
    paths: PathsConfig = field(default_factory=PathsConfig)
    data: DataConfig = field(default_factory=DataConfig)
    market_rules: MarketRulesConfig = field(default_factory=MarketRulesConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    robustness: RobustnessConfig = field(default_factory=RobustnessConfig)
    capital_constraints: CapitalConstraintsConfig = field(default_factory=CapitalConstraintsConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    viz: VizConfig = field(default_factory=VizConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        """校验配置合法性（资金约束 > 0 等）。"""
        cc = self.capital_constraints
        if cc.weekly_max <= 0 or cc.daily_max <= 0 or cc.cycle_days <= 0:
            raise ValueError(
                f"capital_constraints 必须全为正：weekly_max={cc.weekly_max}, "
                f"daily_max={cc.daily_max}, cycle_days={cc.cycle_days}"
            )
        if self.market_rules.commission_rate < 0 or self.market_rules.stamp_tax_rate < 0:
            raise ValueError("费率不能为负")
        if self.analysis.risk_free_rate < 0:
            raise ValueError("无风险利率不能为负")


def _deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归更新字典。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def _apply_env_overrides(raw: Dict[str, Any]) -> Dict[str, Any]:
    """应用 ALPHAFORGE_* 环境变量覆盖（点号路径，如 ALPHAFORGE_ANALYSIS__RISK_FREE_RATE）。"""
    for key, val in os.environ.items():
        if not key.startswith("ALPHAFORGE_"):
            continue
        path = key[len("ALPHAFORGE_"):].lower().split("__")
        node = raw
        for p in path[:-1]:
            node = node.setdefault(p, {})
        # 尝试数值转换
        try:
            val_parsed: Any = float(val)
            if val_parsed.is_integer():
                val_parsed = int(val_parsed)
        except ValueError:
            if val.lower() in ("true", "false"):
                val_parsed = val.lower() == "true"
            else:
                val_parsed = val
        node[path[-1]] = val_parsed
    return raw


def _build_config(raw: Dict[str, Any]) -> AlphaForgeConfig:
    """从原始字典构建 AlphaForgeConfig。"""
    cfg = AlphaForgeConfig()
    if "app" in raw:
        cfg.app = {**cfg.app, **raw["app"]}
    if "paths" in raw:
        cfg.paths = PathsConfig(**{**asdict(cfg.paths), **raw["paths"]})
    if "data" in raw:
        cfg.data = DataConfig(**{**asdict(cfg.data), **raw["data"]})
    if "market_rules" in raw:
        cfg.market_rules = MarketRulesConfig(**{**asdict(cfg.market_rules), **raw["market_rules"]})
    if "backtest" in raw:
        cfg.backtest = BacktestConfig(**{**asdict(cfg.backtest), **raw["backtest"]})
    if "analysis" in raw:
        cfg.analysis = AnalysisConfig(**{**asdict(cfg.analysis), **raw["analysis"]})
    if "optimizer" in raw:
        cfg.optimizer = OptimizerConfig(**{**asdict(cfg.optimizer), **raw["optimizer"]})
    if "robustness" in raw:
        cfg.robustness = RobustnessConfig(**{**asdict(cfg.robustness), **raw["robustness"]})
    if "capital_constraints" in raw:
        cfg.capital_constraints = CapitalConstraintsConfig(
            **{**asdict(cfg.capital_constraints), **raw["capital_constraints"]}
        )
    if "report" in raw:
        cfg.report = ReportConfig(**{**asdict(cfg.report), **raw["report"]})
    if "viz" in raw:
        cfg.viz = VizConfig(**{**asdict(cfg.viz), **raw["viz"]})
    if "sync" in raw:
        cfg.sync = SyncConfig(**{**asdict(cfg.sync), **raw["sync"]})
    return cfg


def load_config(path: Optional[Path] = None) -> AlphaForgeConfig:
    """加载配置。

    Args:
        path: config.yaml 路径，默认为项目根 config.yaml。

    Returns:
        AlphaForgeConfig：完整配置对象。
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: Dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw = _apply_env_overrides(raw)
    cfg = _build_config(raw)
    cfg.validate()
    return cfg


# 模块级单例（懒加载）
_CACHED: Optional[AlphaForgeConfig] = None


def get_config() -> AlphaForgeConfig:
    """获取配置单例。"""
    global _CACHED
    if _CACHED is None:
        _CACHED = load_config()
    return _CACHED


def reset_config() -> None:
    """重置缓存（测试用）。"""
    global _CACHED
    _CACHED = None