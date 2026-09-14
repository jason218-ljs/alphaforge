"""测试 viz 服务器与 API 端到端（用 Flask test client，无需真实端口）。"""

import json
from pathlib import Path

import pytest

from alphaforge.viz.server import DashServer


@pytest.fixture
def server(tmp_path):
    """构造带数据的 output 目录与 DashServer。"""
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "analysis.json").write_text(json.dumps({
        "analysis_date": "2026-08-03T12:00:00",
        "risk": {"max_drawdown": -0.05, "annualized_volatility": 0.15,
                 "sharpe_ratio": 1.2, "sortino_ratio": 1.5, "calmar_ratio": 2.0,
                 "alerts": []},
        "returns": {"total_return": 0.08, "annualized_return": 0.20,
                    "win_rate": 0.6, "benchmark_return": 0.05},
        "strategy": {}, "attribution": {
            "brinson": {"allocation_effect": 0.01, "selection_effect": 0.02,
                        "interaction_effect": 0.0, "total_effect": 0.03,
                        "industry_detail": []},
            "fama": {"decomposition": {"systematic": 0.03, "selection": 0.05},
                     "regression": {"alpha": 0.001, "beta_market": 0.9,
                                    "r_squared": 0.85, "n_observations": 100}},
        },
        "summary": {}, "recommendations": [],
    }), encoding="utf-8")
    (tmp_path / "backtest").mkdir()
    (tmp_path / "backtest" / "backtest.json").write_text(json.dumps({
        "equity_curve": [{"date": "2026-07-27", "nav": 1000000},
                         {"date": "2026-07-28", "nav": 1010000}],
        "summary": {"final_nav": 1010000, "total_return": 0.01,
                    "max_drawdown": 0, "sharpe_ratio": 1.0,
                    "win_rate": 1.0, "total_trades": 1, "days": 1},
        "trade_log": [], "signal_log": [], "config_snapshot": {},
    }), encoding="utf-8")
    return DashServer(output_dir=tmp_path)


@pytest.fixture
def client(server):
    return server.app.test_client()


class TestPages:
    def test_dashboard_page_200(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"AlphaForge" in r.data

    def test_attribution_page_200(self, client):
        r = client.get("/attribution")
        assert r.status_code == 200
        assert b"Brinson" in r.data

    def test_backtest_page_200(self, client):
        r = client.get("/backtest")
        assert r.status_code == 200

    def test_echarts_vendor_served(self, client):
        r = client.get("/static/vendor/echarts.min.js")
        assert r.status_code == 200
        assert len(r.data) > 100000  # ECharts ~1MB


class TestAPI:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.get_json()["status"] == "ok"

    def test_dashboard_api(self, client):
        r = client.get("/api/dashboard")
        d = r.get_json()
        assert "kpi" in d
        assert "equity_curve" in d
        assert d["kpi"]["total_return"] == pytest.approx(8.0)

    def test_attribution_api(self, client):
        r = client.get("/api/attribution")
        a = r.get_json()
        assert "brinson_waterfall" in a
        assert "fama_pie" in a

    def test_backtest_api(self, client):
        r = client.get("/api/backtest")
        assert "summary" in r.get_json()

    def test_dates_api(self, client):
        r = client.get("/api/dates")
        assert "dates" in r.get_json()


class TestEmptyOutput:
    def test_dashboard_without_data_returns_error(self, tmp_path):
        server = DashServer(output_dir=tmp_path)
        client = server.app.test_client()
        r = client.get("/api/dashboard")
        assert "error" in r.get_json()