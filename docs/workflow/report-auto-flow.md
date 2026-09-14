# report-auto-flow 报告生成

> **目标**：将分析结果渲染为可读报告（txt + json），供投决会/复盘使用
> **入口命令**：`python -m alphaforge.cli report --input <analysis.json> --output-dir <dir>`
> **上游**：analysis-auto-flow
> **依赖**：`config.yaml` 的 `report` 段

---

## 流程图

```
analysis.json（分析产物）
   │  risk + returns + strategy + attribution
   ▼
Step 1 加载分析结果 + 关联原始记录/回测数据
   ▼
Step 2 渲染文本报告（txt）
   │   章节：Header / 数据来源 / 持仓概览 / 风险评估 / 收益分析 /
   │        策略评估 / 归因分析 / 操作建议 / Footer
   ▼
Step 3 渲染结构化 JSON（详细数据）
   ▼
输出 output/{date}-日报.txt + output/{date}-详细数据.json
```

---

## 步骤详解

### Step 1: 加载

```bash
python -m alphaforge.cli report \
    --input output/analysis/analysis.json \
    --output-dir output/
```

- 读取 `analysis.json`，关联同一批次的 records（`output/data/records.json`）与 backtest（`output/backtest/backtest.json`）用于持仓明细/交易复盘
- **校验**：文件存在且 JSON 可解析

### Step 2: 文本报告（txt）

章节与优先级标记：

| 章节 | 内容 | 优先级标记 |
|------|------|-----------|
| Header | 生成时间、数据来源、批次日期 | — |
| 持仓概览 | 各股票仓位/成本/市值/浮盈亏/建议 | 轻/半/重仓 |
| 风险评估 | MDD/波动/夏普/Sortino/Calmar/VaR/集中度 | 🔴高 🟠中 🟡低 |
| 风险预警 | 触发列表（回撤超限/波动超限/持仓超限/夏普过低） | 按优先级 |
| 收益分析 | 累计/年化/胜率/盈亏比/Alpha/Beta | 等级 |
| 策略评估 | 信号胜率/持仓周期/漂移/适应性 | 观察项 |
| 归因分析 | Brinson 配置/选股/交互 + Fama 分解 | 图表化文本 |
| 操作建议 | 守/调仓建议（对齐资金约束） | 🔴🟠🟡🔵 |
| Footer | 免责声明、数据截止 | — |

### Step 3: 结构化 JSON

```json
{
  "report_date": "2026-07-31",
  "generated_at": "2026-08-03T12:00:00",
  "portfolio": {"nav": ..., "cash_ratio": ..., "position_count": ...},
  "risk": {...}, "returns": {...}, "strategy": {...}, "attribution": {...},
  "positions": [{"code", "name", "qty", "avg_cost", "market_value", "unrealized_pnl_pct", "suggestion"}],
  "trades": [...], "members": [...],
  "recommendations": [...]
}
```

---

## 数据模型（输出）

```python
# txt: 纯文本，章节由【】包裹，emoji 标记优先级
# json:
class DailyReportOutput(BaseModel):
    report_date: str
    generated_at: str
    portfolio: dict
    risk: dict
    returns: dict
    strategy: dict
    attribution: dict
    positions: List[dict]
    trades: List[dict]
    members: List[dict]
    recommendations: List[dict]
```

---

## 报告命名规范

| 类型 | 文件名 |
|------|--------|
| 日报 txt | `output/{YYYYMMDD}-日报.txt` |
| 日报 json | `output/{YYYYMMDD}-详细数据.json` |
| 周报 | `output/{YYYYMMDD}-周报.txt` |
| 回测报告 | `output/backtest-{strategy}-{timestamp}.json` |
| 归因报告 | `output/attribution-{date}.txt` |

---

## 配置项（config.yaml）

```yaml
report:
  daily_enabled: true
  weekly_enabled: true
  backtest_enabled: true
  output_format: ["txt", "json"]
```

---

## 失败容忍

- 关联数据缺失 → 报告列"数据缺失"，不崩
- 归因不可用 → 省略归因章节，记 warning
- 输出目录不存在 → 自动创建（`output/`）

---

## 验证方案

- 单元测试：`tests/unit/reporting/`
  - `test_daily_report.py`：章节齐全、优先级标记、JSON schema
  - `test_report_generator.py`：门面组合、命名规范、目录自动创建
- 集成测试：analyze→report 端到端，txt/json 均产出且可读