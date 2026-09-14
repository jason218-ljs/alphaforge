"""测试 viz/charts.py 图表数据组装。"""

import json
from pathlib import Path

import pytest

from alphaforge.viz.charts import ChartData


@pytest.fixture
def output_dir(tmp_path):
    """构造最小 output 目录。"""
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "analysis.json").write_text(json.dumps({
        "analysis_date": "2026-08-03T12:00:00",
        "risk": {
            "max_drawdown": -0.05, "annualized_volatility": 0.15,
            "sharpe_ratio": 1.2, "sortino_ratio": 1.5, "calmar_ratio": 2.0,
            "alerts": [{"level": "MEDIUM", "message": "波动超限"}],
        },
        "returns": {
            "total_return": 0.08, "annualized_return": 0.20,
            "win_rate": 0.6, "benchmark_return": 0.05,
        },
        "strategy": {}, "attribution": {
            "brinson": {
                "allocation_effect": 0.01, "selection_effect": 0.02,
                "interaction_effect": 0.0, "total_effect": 0.03,
                "industry_detail": [{"industry": "白酒", "portfolio_weight": 0.6,
                                     "benchmark_weight": 0.5,
                                     "portfolio_return": 0.1, "benchmark_return": 0.08,
                                     "allocation_effect": 0.002, "selection_effect": 0.01,
                                     "interaction_effect": 0.001}],
            },
            "fama": {"decomposition": {"systematic": 0.03, "selection": 0.05,
                                       "factor_exposure": 0.03},
                     "regression": {"alpha": 0.001, "beta_market": 0.9,
                                    "beta_smb": 0.0, "beta_hml": 0.0,
                                    "r_squared": 0.85, "n_observations": 100}},
        },
        "summary": {"overall_health": "良好"},
        "recommendations": [{"priority": "LOW", "title": "维持", "description": "正常"}],
    }), encoding="utf-8")
    (tmp_path / "backtest").mkdir()
    (tmp_path / "backtest" / "backtest.json").write_text(json.dumps({
        "equity_curve": [
            {"date": "2026-07-27", "nav": 1000000},
            {"date": "2026-07-28", "nav": 1010000},
            {"date": "2026-07-29", "nav": 990000},
            {"date": "2026-07-30", "nav": 1020000},
        ],
        "summary": {"final_nav": 1020000, "total_return": 0.02,
                    "max_drawdown": -0.03, "sharpe_ratio": 1.0,
                    "win_rate": 0.5, "total_trades": 5, "days": 4},
        "trade_log": [{"date": "2026-07-27", "side": "BUY", "code": "600519.SH",
                       "requested_qty": 100, "filled_qty": 100, "price": 100.0,
                       "fee": 5.0, "status": "FILLED"}],
        "signal_log": [], "config_snapshot": {"stamp_tax_rate": 0.001},
    }), encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "records.json").write_text(json.dumps({
        "portfolio_snapshots": [{
            "date": "2026-07-30", "cash": 500000, "nav": 1020000,
            "positions": {
                "600519.SH": {"name": "", "qty": 100, "avg_cost": 100,
                              "current_price": 110, "market_value": 11000,
                              "unrealized_pnl_pct": 0.1},
                "601288.SH": {"name": "", "qty": 200, "avg_cost": 5,
                              "current_price": 5.5, "market_value": 1100,
                              "unrealized_pnl_pct": 0.1},
            },
        }],
    }), encoding="utf-8")
    return tmp_path


@pytest.fixture
def charts(output_dir):
    return ChartData(output_dir)


class TestDashboard:
    def test_kpi_values(self, charts):
        d = charts.dashboard()
        assert d["kpi"]["total_return"] == pytest.approx(8.0)
        assert d["kpi"]["max_drawdown"] == pytest.approx(-5.0)
        assert d["kpi"]["sharpe"] == pytest.approx(1.2)

    def test_equity_curve(self, charts):
        d = charts.dashboard()
        assert len(d["equity_curve"]["nav"]) == 4
        assert d["equity_curve"]["nav"][0] == 1000000

    def test_drawdown_negative(self, charts):
        d = charts.dashboard()
        # 第三天 nav 跌至 990000，回撤应为负
        assert min(d["drawdown"]["values"]) < 0

    def test_positions_with_code_fallback(self, charts):
        d = charts.dashboard()
        names = [p["name"] for p in d["positions"]]
        # 持仓名称为空时应回退到代码
        assert "600519.SH" in names

    def test_alerts_passed_through(self, charts):
        d = charts.dashboard()
        assert len(d["alerts"]) == 1
        assert d["alerts"][0]["level"] == "MEDIUM"


class TestAttribution:
    def test_brinson_waterfall(self, charts):
        a = charts.attribution()
        wf = a["brinson_waterfall"]
        assert len(wf) == 5  # 基准/配置/选股/交互/总
        assert wf[0]["label"] == "基准收益"
        assert wf[-1]["label"] == "超额收益"
        # 累计应等于总效应
        assert wf[-1]["cumulative"] == pytest.approx(3.0)  # 0.03 * 100

    def test_fama_pie_two_items(self, charts):
        a = charts.attribution()
        pie = a["fama_pie"]
        # 单因子回归下应只有 系统性 + 选股 两项
        assert len(pie) == 2
        names = [p["name"] for p in pie]
        assert any("系统性" in n for n in names)
        assert any("选股" in n for n in names)

    def test_industry_detail_passed(self, charts):
        a = charts.attribution()
        assert len(a["brinson_detail"]) == 1
        assert a["brinson_detail"][0]["industry"] == "白酒"


class TestBacktestDetail:
    def test_trade_log_returned(self, charts):
        b = charts.backtest_detail()
        assert len(b["trade_log"]) == 1
        assert b["summary"]["total_trades"] == 5


class TestEmptyOutput:
    def test_no_analysis_returns_error(self, tmp_path):
        c = ChartData(tmp_path)
        d = c.dashboard()
        assert "error" in d