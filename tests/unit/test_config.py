"""测试 config 加载。"""

from pathlib import Path

import pytest
import yaml

from alphaforge.config import (
    AlphaForgeConfig,
    AnalysisConfig,
    CapitalConstraintsConfig,
    load_config,
    reset_config,
    get_config,
)


class TestLoadConfig:
    def test_loads_default(self, tmp_path):
        # 无 config.yaml 时用默认值
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.analysis.risk_free_rate == 0.03
        assert cfg.market_rules.stamp_tax_rate == 0.001
        assert cfg.capital_constraints.weekly_max == 250_000

    def test_loads_project_yaml(self):
        # 项目根有 config.yaml
        reset_config()
        cfg = load_config()
        assert cfg.app["name"] == "AlphaForge"
        # 资金约束被 config.yaml 覆盖
        assert cfg.capital_constraints.weekly_max == 250_000

    def test_yaml_overrides_default(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump({
            "analysis": {"risk_free_rate": 0.05},
            "capital_constraints": {"weekly_max": 500_000, "daily_max": 100_000, "cycle_days": 30},
        }), encoding="utf-8")
        cfg = load_config(p)
        assert cfg.analysis.risk_free_rate == 0.05
        assert cfg.capital_constraints.weekly_max == 500_000

    def test_get_config_singleton(self):
        reset_config()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2


class TestValidation:
    def test_negative_capital_raises(self):
        cfg = AlphaForgeConfig()
        cfg.capital_constraints = CapitalConstraintsConfig(weekly_max=-1, daily_max=50000, cycle_days=30)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_negative_fee_raises(self):
        cfg = AlphaForgeConfig()
        cfg.market_rules.commission_rate = -0.001
        with pytest.raises(ValueError):
            cfg.validate()