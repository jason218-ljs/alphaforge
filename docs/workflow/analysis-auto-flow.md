# analysis-auto-flow 分析评估

> **目标**：对回测结果做风险/收益/策略/归因四维分析，输出量化结论
> **入口命令**：`python -m alphaforge.cli analyze --input <backtest.json> --output <out.json>`
> **上游**：backtest-auto-flow
> **下游**：report-auto-flow
> **依赖**：`config.yaml` 的 `analysis` + `capital_constraints` 段

---

## 流程图

```
backtest.json（回测产物）
   │  equity_curve + trade_log + signal_log
   ▼
Step 1 RiskAnalyzer         → risk{MDD, volatility, sharpe, sortino, calmar, var, cvar, 集中度}
   ▼
Step 2 ReturnAnalyzer       → returns{total_return, annualized, alpha, beta, win_rate, 盈亏比}
   ▼
Step 3 StrategyEvaluator    → strategy{signal_win_rate, avg_holding_period, 漂移, 适应性}
   ▼
Step 4 BenchmarkManager     → 基准（沪深300）超额收益 / IR
   ▼
Step 5 AttributionAnalyzer  → attribution{brinson{配置/选股/交互}, fama{系统/选股/风险}}
   ▼
输出 output/analysis/analysis.json
```

---

## 步骤详解

### Step 1: 风险分析

```bash
python -m alphaforge.cli analyze --input output/backtest/backtest.json --output output/analysis/analysis.json
```

指标与公式（**已对齐 CAPM 定义，修正历史 bug**）：

| 指标 | 公式 |
|------|------|
| 最大回撤 MDD | `min((nav-peak)/peak)`，另算持续期/恢复期 |
| 年化波动率 | `std(daily_ret) × sqrt(252)` |
| 下行波动率 | 仅负收益的标准差 |
| 夏普 | `(mean(Rp) - rf) / std(Rp) × sqrt(252)`，rf=0.03 |
| Sortino | `(mean(Rp) - rf) / downside_std × sqrt(252)` |
| Calmar | `annualized_return / |MDD|` |
| VaR 95% / CVaR | 历史模拟法，日收益分位 |
| 单票/行业集中度 | `max(position_weights)` / `max(industry_weights)` |

- **校验**：MDD ≤ 0，夏普/Sortino 数值合理

### Step 2: 收益分析

| 指标 | 公式 |
|------|------|
| 累计收益 | `(nav_end - nav_start) / nav_start` |
| 年化收益 | `(1+total)^(252/days) - 1` |
| 胜率 | 盈利日占比 |
| 盈亏比 | `avg_win / |avg_loss|` |
| Beta | `cov(Rp,Rb) / var(Rb)`，Rb=真实基准序列 |
| Alpha | `mean(Rp) - β × mean(Rb)`（CAPM） |

- **校验**：Beta/Alpha 用真实基准，非硬编码 8%

### Step 3: 策略评估

- 信号胜率：`correct/signals`（correct=pnl>0）
- 平均持仓周期：**从 trade_log 计算开仓-平仓间隔**（修复死代码）
- 策略漂移：近 20 日 avg vs 全期 avg，`|ratio-1|>0.3 → is_drifting`
- 市场适应性：按基准涨跌分桶，涨市/跌市平均收益

### Step 4: 基准与超额

- `BenchmarkManager` 加载沪深300（config `analysis.benchmark`）
- 计算超额收益、跟踪误差、信息比率 `IR = alpha / tracking_error`

### Step 5: 归因分析

- **Brinson 行业归因**：`配置效应`（行业权重差×基准收益）+ `选股效应`（行业内收益差×策略权重）+ `交互效应`
- **Fama 分解**：系统性收益 + 选股回报（非系统性）+ 风险回报（差异化风险敞口）

---

## 数据模型

```python
class RiskAnalysis(BaseModel):
    max_drawdown: float
    drawdown_duration_days: int
    recovery_days: int
    annualized_volatility: float
    downside_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    var_95: float
    cvar_95: float
    single_position_max: float
    industry_concentration: float
    risk_score: float
    risk_level: Literal["LOW","NORMAL","MEDIUM","HIGH"]
    alerts: List[dict]

class ReturnAnalysis(BaseModel):
    total_return: float
    annualized_return: float
    daily_return_avg: float
    daily_return_std: float
    winning_days: int
    losing_days: int
    win_rate: float
    profit_loss_ratio: float
    alpha: float
    beta: float
    benchmark_return: float
    return_score: float
    return_level: str

class AttributionResult(BaseModel):
    brinson: dict   # {allocation_effect, selection_effect, interaction_effect, total}
    fama: dict      # {systematic, selection, risk, total}

class AnalysisResult(BaseModel):
    date: datetime
    risk: RiskAnalysis
    returns: ReturnAnalysis
    strategy: dict
    attribution: AttributionResult
    summary: dict          # overall_health 分级
    recommendations: List[dict]
```

---

## 配置项（config.yaml）

```yaml
analysis:
  risk_free_rate: 0.03
  annualization_factor: 252
  benchmark: "000300.SH"
  max_drawdown_warning: -0.15
  max_volatility_warning: 0.30
  min_sharpe_ratio: 0.5
  max_single_position: 0.25
  max_industry_exposure: 0.25
capital_constraints:
  weekly_max: 250000
  daily_max: 50000
  cycle_days: 30
```

---

## 失败容忍

- 基准缺失 → 跳过 Alpha/Beta/归因，记 warning，其余指标照常
- 数据不足（<2 日）→ 波动/夏普返回 0，注明"数据不足"
- 行业未知 → 归因归入"未知行业"桶

---

## 验证方案

- 单元测试：`tests/unit/analysis/`
  - `test_risk_analyzer.py`：空数据/单峰/MDD/夏普（含无风险利率）/VaR
  - `test_return_analyzer.py`：累计收益/年化/胜率/Beta/Alpha（用构造序列）
  - `test_strategy_evaluator.py`：信号胜率/持仓周期/漂移/适应性
  - `test_attribution.py`：Brinson/Fama 手工验证样例
- 集成测试：backtest→analyze 端到端，指标合理性断言