"""测试 DailyReport 生成。"""

from datetime import datetime
from pathlib import Path

from alphaforge.models.portfolio import Portfolio, PortfolioSnapshot
from alphaforge.models.position import Position
from alphaforge.models.result import (
    AnalysisResult,
    RiskAnalysis,
    ReturnAnalysis,
    StrategyEvaluation,
    AttributionResult,
)
from alphaforge.reporting.daily_report import DailyReport
from alphaforge.reporting.report_generator import ReportGenerator


def _make_result() -> AnalysisResult:
    return AnalysisResult(
        analysis_date=datetime(2026, 7, 31),
        risk=RiskAnalysis(
            max_drawdown=-0.05,
            annualized_volatility=0.15,
            sharpe_ratio=1.2,
            risk_level="NORMAL",
            risk_score=70,
        ),
        returns=ReturnAnalysis(
            total_return=0.08,
            annualized_return=0.20,
            win_rate=0.6,
            profit_loss_ratio=1.5,
            return_level="GOOD",
            return_score=65,
        ),
        strategy=StrategyEvaluation(),
        attribution=AttributionResult(),
        summary={"overall_health": "良好（60-80）"},
        recommendations=[{"priority": "LOW", "title": "维持", "description": "运行正常"}],
    )


def _make_snapshot() -> PortfolioSnapshot:
    pos = Position(code="600519.SH", name="贵州茅台", qty=100, avg_cost=1500, current_price=1600)
    return PortfolioSnapshot(
        date=datetime(2026, 7, 31),
        cash=840_000,
        positions={"600519.SH": pos},
        nav=1_000_000,
    )


class TestDailyReport:
    def test_to_text_contains_sections(self):
        report = DailyReport(_make_result(), _make_snapshot())
        text = report.to_text()
        assert "AlphaForge 每日分析报告" in text
        assert "【持仓概览】" in text
        assert "【风险评估】" in text
        assert "【收益分析】" in text
        assert "【策略评估】" in text
        assert "免责声明" in text

    def test_to_text_shows_position(self):
        report = DailyReport(_make_result(), _make_snapshot())
        text = report.to_text()
        assert "600519.SH" in text
        assert "贵州茅台" in text

    def test_to_dict_schema(self):
        report = DailyReport(_make_result(), _make_snapshot())
        d = report.to_dict()
        assert d["report_date"] == "2026-07-31"
        assert d["portfolio"]["nav"] == 1_000_000
        assert d["risk"]["max_drawdown"] == -0.05
        assert len(d["positions"]) == 1
        assert d["positions"][0]["code"] == "600519.SH"


class TestReportGenerator:
    def test_generate_and_save_creates_files(self, tmp_path):
        gen = ReportGenerator(tmp_path)
        paths = gen.generate_and_save(_make_result(), _make_snapshot(), datetime(2026, 7, 31))
        assert Path(paths["txt"]).exists()
        assert Path(paths["json"]).exists()
        assert "20260731-日报.txt" in paths["txt"]
        assert "20260731-详细数据.json" in paths["json"]

    def test_creates_output_dir(self, tmp_path):
        out = tmp_path / "nested" / "output"
        gen = ReportGenerator(out)
        paths = gen.generate_and_save(_make_result(), None, datetime(2026, 7, 31))
        assert out.exists()
        assert Path(paths["txt"]).exists()