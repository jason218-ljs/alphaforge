# AlphaForge · 多因子选股与回测系统

> 面向 A 股的**三层 100 分制**选股评分体系 + **事件驱动回测引擎** + **Web 可视化看板**
> 星环科技（Transwarp）实习期间主导开发 ｜ 配套 214 个自动化测试

---

## 设计目标

传统多因子选股有两个常见毛病：

1. **评分"一刀切"** —— 用同一套财务阈值筛所有行业，会误杀高负债的银行、漏选周期底部 ROE 为负的半导体；
2. **结论不可追溯** —— 选出来一只股票，说不出为什么，也没法复盘。

AlphaForge 要解决的就是这两件事：**让选股结论可解释、可追溯、可复盘**，而不是黑箱输出。

---

## 三层 100 分制评分体系

| 层面 | 分值 | 主要指标 |
|---|---|---|
| **基本面** | 40 | ROE、营收增速、自由现金流、估值分位数、股息率 |
| **技术面** | 40 | 均线多头排列、尾盘拉升、量能趋势、换手率、行业热度 |
| **资金面** | 20 | 北向资金、主力资金流向 |

### 21 个二级行业自适应阈值

不同行业的财务刻度天然不同——银行的高杠杆是常态，半导体的周期底部 ROE 可能为负。因此评分体系对 **21 个二级行业分别设定阈值**，把专家经验与统计规律结合起来，显著降低统一阈值带来的系统性误判。

### 两段式选股

```
形态预过滤  →  质量打分
（先筛掉明显不合格的形态）  （再对候选做 100 分制精细评分）
```

---

## 事件驱动回测引擎

回测要有研究价值，就必须**诚实建模市场摩擦**。引擎完整实现了 A 股特有的制度规则：

| 摩擦 | 实现 |
|---|---|
| 交易制度 | T+1 交割 |
| 涨跌停 | 按板块区分：主板 10% / 创业板与科创板 20% / 北交所 30% |
| 税费 | 印花税（仅卖出征收）、双边佣金、过户费 |
| 冲击 | 滑点与成交量冲击建模 |
| 特殊状态 | 停牌、一字板锁定 |
| 数据对齐 | 财务因子采用 **PIT（Point-in-Time）** 对齐——因子可用时点取**公告发布日期**而非报表截止日，从根本上避免前瞻偏差 |

引擎同时提供**事件驱动**与**向量化**两条实现路径（`engine/event_engine.py` 与 `engine/vectorized_engine.py`），便于在可读性与速度之间取舍。

### 仓位管理

采用凯利公式的**工程约束化实现**：以历史同类信号胜率估计 p、以平均盈亏比估计 b 计算原始凯利比例，再叠加两层约束：

- 单票仓位限制在安全区间（而非满仓凯利）
- 行业风险系数调整（如银行、白酒、半导体系数不同）

---

## Web 可视化看板

内置轻量 Web 看板（ECharts 渲染），包含选股、评分、回测、归因、仪表盘五个视图：

| 视图 | 文件 |
|---|---|
| 仪表盘 | `src/alphaforge/viz/templates/dashboard.html` |
| 选股结果 | `scoring.html` / `screener.html` |
| 回测明细 | `backtest.html` |
| 业绩归因 | `attribution.html` |

---

## 技术栈与代码结构

`Python` · `pandas` · `NumPy` · `pytest` · `ECharts`

```
src/alphaforge/
├── data/         # 数据层：行情获取、复权、缓存、交易日历、PIT 导入
├── engine/       # 回测引擎：事件总线、撮合、市场规则、向量化实现
├── strategy/     # 策略层：基类、内置策略、打分策略、选股器
├── analysis/     # 分析层：业绩归因、风险、收益分析、鲁棒性、策略对比、优化器
├── models/       # 领域模型：Bar / Order / Fill / Position / Portfolio / Signal / Result
├── reporting/    # 日报与报告生成
├── viz/          # Web 可视化（ECharts 模板 + 轻量服务）
├── config.py     # 配置（配置驱动 + 强类型）
└── cli.py        # 命令行入口
tests/            # 214 个自动化测试
docs/             # 系统设计方案、开发规范、落地报告、工作流说明
```

---

## 快速开始

```bash
pip install -e .
pytest                 # 跑全部测试

# 命令行入口
python -m alphaforge --help
```

Windows 下另附便捷启动脚本：`start_scoring_backtest.bat`、`start_viz.ps1`。

> ⚠️ **本仓库为公开存档版**：已移除运行数据缓存（`data/*.db`）、模拟盘记录、回测输出产物与全部本机路径信息。运行前需自行配置数据源。

---

## 关于测试

**214 个自动化测试**覆盖分析层、数据层、引擎层、模型层、策略层与可视化层，是这套系统能被信任的基础——尤其是撮合逻辑与市场规则（涨跌停、T+1）这类"错了也不报错、但结论全废"的地方。

---

## 声明

- 本项目为**星环科技（Transwarp）实习期间**的工作产出，本人主导开发。
- 仓库已移除公司内部文档、内部系统桥接、运行数据与全部凭据，**不含任何商业秘密**。
- 仅供学习与交流，**不构成任何投资建议**。

---

## English Summary

**AlphaForge** is a multi-factor stock-screening and backtesting system for the A-share market.

It combines a **three-layer 100-point scoring framework** (fundamental 40 / technical 40 / money-flow 20) with **adaptive thresholds across 21 sub-industries** — because a single set of financial thresholds will wrongly reject high-leverage banks and miss cyclical-bottom semiconductors. Stock selection runs as a two-stage pipeline: *pattern pre-filter → quality scoring*.

The **event-driven backtesting engine** honestly models A-share market frictions: T+1 settlement, board-specific price limits (10% / 20% / 30%), stamp duty, commissions and transfer fees, slippage and volume impact, trading halts, and one-word limit boards. Fundamental factors use **PIT (point-in-time)** alignment — availability keyed to announcement date rather than report period end — eliminating look-ahead bias. Position sizing applies a constrained Kelly criterion with per-stock and per-industry risk limits.

**214 automated tests** back the system, covering analysis, data, engine, model, strategy and visualization layers. A lightweight ECharts dashboard provides screening, scoring, backtesting and attribution views.

Developed as the lead author during an internship at Transwarp Technology. Company-internal documents, internal system bridges, runtime data and all credentials have been removed. For learning purposes only; not investment advice.
