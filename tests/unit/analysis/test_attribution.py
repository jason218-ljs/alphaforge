"""测试 Brinson 行业归因与 Fama-French 因子归因。"""

import pytest

from alphaforge.analysis.attribution import AttributionAnalyzer


@pytest.fixture
def analyzer():
    return AttributionAnalyzer()


class TestBrinson:
    def test_allocation_effect(self, analyzer):
        """超配上涨行业 → 正配置效应。"""
        result = analyzer.brinson_attribution(
            portfolio_weights={"白酒": 0.6, "银行": 0.4},
            benchmark_weights={"白酒": 0.5, "银行": 0.5},
            portfolio_returns={"白酒": 0.1, "银行": 0.02},
            benchmark_returns={"白酒": 0.1, "银行": 0.02},
        )
        # 配置效应 = (0.6-0.5)*0.1 + (0.4-0.5)*0.02 = 0.01 - 0.002 = 0.008
        assert abs(result["allocation_effect"] - 0.008) < 1e-6
        # 选股/交互 = 0（组合收益=基准收益）
        assert abs(result["selection_effect"]) < 1e-9
        assert abs(result["interaction_effect"]) < 1e-9

    def test_selection_effect(self, analyzer):
        """组合行业收益高于基准 → 正选股效应。"""
        result = analyzer.brinson_attribution(
            portfolio_weights={"白酒": 0.5, "银行": 0.5},
            benchmark_weights={"白酒": 0.5, "银行": 0.5},
            portfolio_returns={"白酒": 0.15, "银行": 0.02},
            benchmark_returns={"白酒": 0.10, "银行": 0.02},
        )
        # 选股效应 = 0.5*(0.15-0.10) + 0.5*(0.02-0.02) = 0.025
        assert abs(result["selection_effect"] - 0.025) < 1e-6

    def test_empty_weights(self, analyzer):
        result = analyzer.brinson_attribution({}, {}, {}, {})
        assert result["allocation_effect"] == 0.0
        assert result["industry_detail"] == []

    def test_detail_contains_industries(self, analyzer):
        result = analyzer.brinson_attribution(
            {"白酒": 0.5}, {"银行": 0.5},
            {"白酒": 0.1}, {"银行": 0.05},
        )
        inds = [d["industry"] for d in result["industry_detail"]]
        assert "白酒" in inds and "银行" in inds


class TestFamaFrench:
    def test_market_beta_one_when_identical(self, analyzer):
        """组合收益与市场完全一致 → β_m≈1，α≈0。"""
        rets = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01, 0.012, 0.018, -0.008, 0.006]
        result = analyzer.fama_french_attribution(
            portfolio_returns=rets, market_returns=rets,
            smb_returns=None, hml_returns=None,
        )
        assert abs(result["regression"]["beta_market"] - 1.0) < 0.05
        assert abs(result["regression"]["alpha"]) < 0.01

    def test_three_factor_points_to_market(self, analyzer):
        """SMB/HML 为零序列时，β 仍正确，贡献仅来自市场。"""
        rets = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01, 0.012, 0.018, -0.008, 0.006]
        zero = [0.0] * len(rets)
        result = analyzer.fama_french_attribution(
            portfolio_returns=rets, market_returns=rets,
            smb_returns=zero, hml_returns=zero,
        )
        reg = result["regression"]
        assert "smb" in reg["factor_names"]
        assert "hml" in reg["factor_names"]
        assert abs(reg["beta_market"] - 1.0) < 0.05

    def test_insufficient_data(self, analyzer):
        result = analyzer.fama_french_attribution([0.01], [0.01], None, None)
        assert result["regression"]["n_observations"] == 0
        assert result["decomposition"]["total"] == 0.0

    def test_decomposition_has_all_keys(self, analyzer):
        rets = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01, 0.012, 0.018, -0.008, 0.006]
        result = analyzer.fama_french_attribution(rets, rets, None, None)
        dec = result["decomposition"]
        for key in ("systematic", "factor_exposure", "selection", "total"):
            assert key in dec
        # total = systematic + selection
        assert abs(dec["total"] - (dec["systematic"] + dec["selection"])) < 1e-6


class TestFamaSimple:
    def test_simple_decomposition(self, analyzer):
        """无 SMB/HML 时退化为市场单因子。"""
        rets = [0.01, -0.02, 0.015, -0.005, 0.02]
        result = analyzer.fama_simple_decomposition(rets, rets)
        assert "regression" in result
        assert "decomposition" in result