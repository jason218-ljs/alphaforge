"""报告门面：组合分析结果 + 快照，生成 txt 与 json 文件。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from alphaforge.logger import get_logger
from alphaforge.models.portfolio import PortfolioSnapshot
from alphaforge.models.result import AnalysisResult
from alphaforge.reporting.daily_report import DailyReport
from alphaforge.utils.date import format_date_compact

logger = get_logger("reporting.report_generator")


class ReportGenerator:
    """报告生成门面。"""

    def __init__(self, output_dir: Path | str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_and_save(
        self,
        result: AnalysisResult,
        snapshot: Optional[PortfolioSnapshot] = None,
        date: Optional[datetime] = None,
    ) -> Dict[str, str]:
        """生成日报 txt + json。

        Returns:
            {"txt": path, "json": path}
        """
        date = date or result.analysis_date
        report = DailyReport(result, snapshot)
        date_str = format_date_compact(date)

        txt_path = self.output_dir / f"{date_str}-日报.txt"
        json_path = self.output_dir / f"{date_str}-详细数据.json"

        txt_path.write_text(report.to_text(), encoding="utf-8")
        json_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("报告已生成：%s / %s", txt_path.name, json_path.name)
        return {"txt": str(txt_path), "json": str(json_path)}