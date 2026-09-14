"""AlphaForge — 企业级量化分析回测引擎。

分层：
- models：数据模型（pydantic v2）
- data：数据接入
- engine：回测引擎（事件驱动 + 向量化）
- strategy：策略层
- analysis：分析层（风险/收益/策略/归因）
- reporting：报告层
- integration：外部系统桥接（可选）
"""

__version__ = "1.0.0"

from alphaforge.orchestrator import Orchestrator
from alphaforge.config import AlphaForgeConfig, load_config

__all__ = ["__version__", "Orchestrator", "AlphaForgeConfig", "load_config"]