# main-auto-workflow 主工作流

> **目标**：从 主流水线 模拟盘 Excel 出发，驱动 Trae 完成「数据导入 → 回测回放 → 分析评估 → 报告生成 → 可视化展示」的全链路自动化
> **驱动方式**：Trae 读取本文件，按 Step 顺序调用 `python -m alphaforge.cli <子命令>`；每个 Step 有明确校验，通过则进入下一步
> **关联子 flow**：`data-auto-flow.md` → `backtest-auto-flow.md` → `analysis-auto-flow.md` → `report-auto-flow.md` → `viz-auto-flow.md`

---

## 流程图

```
主流水线 模拟盘 Excel
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 0 环境检查（config / 依赖 / 数据源）    │
└──────────────┬──────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 1 数据导入  ──► data-auto-flow.md     │
│    python -m alphaforge.cli import          │
│    输出: output/data/records.json           │
└──────────────┬──────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 2 回测回放  ──► backtest-auto-flow.md │
│    python -m alphaforge.cli backtest        │
│    输出: output/backtest/backtest.json      │
└──────────────┬──────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 3 分析评估  ──► analysis-auto-flow.md │
│    python -m alphaforge.cli analyze         │
│    输出: output/analysis/analysis.json      │
└──────────────┬──────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 4 报告生成  ──► report-auto-flow.md   │
│    python -m alphaforge.cli report          │
│    输出: output/{date}-日报.{txt,json}      │
└──────────────┬──────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 5 可视化展示  ──► viz-auto-flow.md    │
│    python -m alphaforge.cli viz --port 8050 │
│    浏览器访问 http://localhost:8050          │
└─────────────────────────────────────────────┘
```

---

## 前置条件

- 已安装 Python 3.10+ 与全部核心依赖（`pip install -e .`）
- `config.yaml` 存在且 `paths.external_scorer_file` 已配置（或用 `--excel` 参数覆盖）
- 主流水线 模拟盘 Excel 可访问（多 sheet，每日 1 sheet）
- Trae 工作目录：`AlphaForge/`

```bash
pip install -e .
```

---

## Stage 0: 环境检查

### 目的
确认配置、依赖、数据源可用，避免后续 Stage 失败。

### 调用

```bash
python -m alphaforge.cli doctor
```

### 校验
输出包含：`config: OK` / `deps: OK` / `external_scorer_source: 找到 <N> 个 sheet`。任一 FAIL 需先解决。

---

## Stage 1: 数据导入（委托 data-auto-flow.md）

### 目的
解析 主流水线 多 sheet Excel，重建每日持仓序列。**细节见 `data-auto-flow.md`。**

### 调用

```bash
python -m alphaforge.cli import \
    --excel "C:\...\投资流水记录表.xlsx" \
    --output output/data/records.json
```

### 校验
- 退出码 0，日志显示 `导入成功：N 个交易日`
- `output/data/records.json` 存在，`N` = sheet 数（数据源当前为 5）
- JSON 中每个 record 含 `date / nav / cash / positions[] / trades[] / members[]`

---

## Stage 2: 回测回放（委托 backtest-auto-flow.md）

### 目的
按日期回放 主流水线 真实交易流水，逐 bar 撮合（T+1/涨跌停/费用/滑点），重建组合权益曲线。**细节见 `backtest-auto-flow.md`。**

### 调用

```bash
python -m alphaforge.cli backtest \
    --input output/data/records.json \
    --strategy external_scorer_replay \
    --output output/backtest/backtest.json
```

### 校验
- `output/backtest/backtest.json` 存在
- 含 `equity_curve[]`（每日 NAV）、`trade_log[]`（逐笔成交）、`summary{total_return, max_drawdown, sharpe, win_rate}`
- 权益曲线首日 NAV ≈ 初始本金（¥1,000,000 × 组员数）

---

## Stage 3: 分析评估（委托 analysis-auto-flow.md）

### 目的
对回测结果做风险/收益/策略/归因四维分析，输出量化结论。**细节见 `analysis-auto-flow.md`。**

### 调用

```bash
python -m alphaforge.cli analyze \
    --input output/backtest/backtest.json \
    --output output/analysis/analysis.json
```

### 校验
- `output/analysis/analysis.json` 存在
- 含 `risk{max_drawdown, volatility, sharpe, sortino, calmar, var, cvar}` / `returns{total_return, annualized, alpha, beta, win_rate, profit_loss_ratio}` / `strategy{signal_win_rate, avg_holding_period, ...}` / `attribution{brinson, fama}`
- 关键指标值合理（如夏普为正、MDD 为负）

---

## Stage 4: 报告生成（委托 report-auto-flow.md）

### 目的
将分析结果渲染为可读报告（txt + json），供投决会/复盘使用。**细节见 `report-auto-flow.md`。**

### 调用

```bash
python -m alphaforge.cli report \
    --input output/analysis/analysis.json \
    --output-dir output/
```

### 校验
- `output/{date}-日报.txt` 与 `output/{date}-详细数据.json` 存在
- txt 含「持仓概览 / 风险评估 / 收益分析 / 归因分析 / 操作建议」章节

---

## Stage 5: 可视化展示（委托 viz-auto-flow.md）

### 目的
启动 Web 仪表盘，用 ECharts 将报告数据直观呈现。**细节见 `viz-auto-flow.md`。**

### 调用

```bash
python -m alphaforge.cli viz --port 8050
```

### 校验
- 服务启动，日志显示 `Running on http://127.0.0.1:8050`
- 浏览器访问首页返回 200，含 KPI 卡片/权益曲线/回撤图/持仓饼图
- `GET /api/dashboard?date=YYYYMMDD` 返回合法 JSON

---

## 全链路一键执行

Trae 可跳过逐 Stage，直接调用完整周期（等价于 Stage 1→4）：

```bash
python -m alphaforge.cli run-all \
    --excel "C:\...\投资流水记录表.xlsx"
```

内部委托 `Orchestrator.run_full_cycle(excel_path)`，返回 `{"records", "backtest", "analysis", "report_paths"}`。

---

## 数据流与中间产物

| Stage | 输入 | 输出 | 消费方 |
|-------|------|------|--------|
| 1 import | 主流水线 Excel | `output/data/records.json` | Stage 2 |
| 2 backtest | records.json + 行情 | `output/backtest/backtest.json` | Stage 3 |
| 3 analyze | backtest.json | `output/analysis/analysis.json` | Stage 4 |
| 4 report | analysis.json | `output/{date}-日报.{txt,json}` | 人工/投决会 |

中间产物均为 JSON，Stage 间通过文件解耦，可独立重跑。

---

## 失败容忍与降级

| 场景 | 处理 |
|------|------|
| 行情 API 不可用 | 用 主流水线 流水自带成交价，缓存兜底，日志告警 |
| 某 sheet 解析失败 | 跳过该 sheet，记录 warning，不阻断其他 sheet |
| 参数缺失 | 使用 `config.yaml` 默认值 |
| 报告生成失败 | 保留 analysis.json，提示可手动渲染 |

---

## 验证方案

- 单元测试：`tests/unit/`（importer / analyzers / engine）
- 集成测试：`tests/integration/`（import→backtest→analyze 链路）
- 端到端测试：`tests/e2e/` 覆盖每个 CLI 子命令
- 覆盖率门禁：`pytest tests/ --cov=alphaforge --cov-fail-under=80`

---

## 执行清单（Trae 逐项确认）

- [ ] Stage 0 环境检查通过
- [ ] Stage 1 导入成功，records.json 生成，sheet 数一致
- [ ] Stage 2 回测成功，权益曲线合理
- [ ] Stage 3 分析成功，四维指标齐全且合理
- [ ] Stage 4 报告生成，txt/json 均产出
- [ ] Stage 5 可视化启动，仪表盘可访问，图表渲染正常