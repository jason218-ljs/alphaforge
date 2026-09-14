"""pytest 公共夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"


@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录。"""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """原始数据目录。"""
    return DATA_DIR


@pytest.fixture(scope="session")
def real_external_scorer_excel() -> Path:
    """真实 主流水线 模拟盘 Excel（若存在）。"""
    excel = DATA_DIR / "投资流水记录表.xlsx"
    if not excel.exists():
        pytest.skip("真实模拟盘数据不存在，跳过集成测试")
    return excel