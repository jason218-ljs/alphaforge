# backtest-auto-flow 回测回放

> **目标**：按日期回放交易流水，逐 bar 撮合（T+1/涨跌停/费用/滑点），重建组合权益曲线
> **入口命令**：`python -m alphaforge.cli backtest --input <records.json> --strategy <name> --output <out.json>`
> **上游**：data-auto-flow
> **下游**：analysis-auto-flow

---

## 流程图

```
records.json（数据导入产物）
   │  含 trades[]（真实流水）+ nav 快照
   ▼
Step 1 构建行情上下文（Bar 序列：成交价/实时价）
   ▼
Step 2 初始化 Portfolio（现金=Σ组员期初，positions={}）
   ▼
Step 3 逐交易日回放
   │   ├─ 新交易日：重置 T+1 记录
   │   ├─ Strategy.on_bar(date, bar)     ← external_scorer_replay 按流水发单
   │   ├─ Matcher.match(order)           ← T+1/涨跌停/费用/滑点
   │   ├─ Portfolio.apply_fill(fill)
   │   └─ EventBus.emit
   ▼
Step 4 每日 mark-to-market → 权益曲线
   ▼
Step 5 计算 summary（total_return/MDD/sharpe/win_rate）
   ▼
输出 output/backtest/backtest.json
```

---

## 步骤详解

### Step 1: 行情上下文

- `external_scorer_replay` 策略：直接消费 records.json 中每个交易日 `trades[]`，将其转换为下单指令（买入/卖出）
- 行情价优先用流水成交价；缺失时由 `MarketDataPipeline` 拉实时价，失败则用前日价兜底
- **校验**：每日有价格可 mark-to-market

### Step 2-3: 回放撮合

```bash
python -m alphaforge.cli backtest \
    --input output/data/records.json \
    --strategy external_scorer_replay \
    --output output/backtest/backtest.json
```

撮合规则（`engine/matcher.py`，值取自 config.yaml `market_rules`）：

| 规则 | 处理 |
|------|------|
| T+1 | 当日买入不可当日卖出，`today_buy_lots[code]` 跟踪 |
| 涨跌停 | 按板块 MAIN±10%/GEM·STAR±20%/BEI±30%，超界拒绝 |
| 滑点 | 买价×(1+0.0005)+0.01，卖价×(1-0.0005)-0.01 |
| 佣金 | max(金额×0.00025, 5元) |
| 印花税 | 卖出金额×0.001 |
| 过户费 | 金额×0.00002 |
| 资金/持仓校验 | 买入需现金足够，卖出需持仓足够 |

- **校验**：trade_log 每笔有撮合结果或拒单原因（buy_blocked/sell_blocked）

### Step 4-5: 权益曲线与 summary

- `equity_curve[]`：每日 `{date, nav, daily_pnl, daily_return_pct, cash, position_count}`
- `summary`：累计收益、年化、最大回撤、夏普、Sortino、胜率、盈亏比

---

## 数据模型

```python
class Fill(BaseModel):
    time: datetime
    code: str
    side: Literal["BUY", "SELL"]
    qty: int
    price: float           # 含滑点后成交价
    amount: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    total_fee: float
    cash_after: float

class TradeRecord(BaseModel):      # trade_log
    date: datetime
    side: Literal["BUY", "SELL"]
    code: str
    name: str
    requested_qty: int
    filled_qty: int
    price: float
    fee: float
    status: Literal["FILLED", "REJECTED"]
    reject_reason: str = ""

class BacktestResult(BaseModel):
    strategy_name: str
    initial_capital: float
    equity_curve: List[dict]       # date/nav/daily_pnl/daily_return_pct/cash/position_count
    trade_log: List[TradeRecord]
    signal_log: List[dict]
    summary: BacktestSummary
    config_snapshot: dict          # 撮合参数快照（可审计）
```

---

## 配置项（config.yaml）

```yaml
market_rules:
  stamp_tax_rate: 0.001
  commission_rate: 0.00025
  min_commission: 5.0
  transfer_fee_rate: 0.00002
  slippage_pct: 0.0005
  slippage_fixed: 0.01
  enable_t_plus_1: true
  price_limit: {MAIN: 0.10, GEM: 0.20, STAR: 0.20, BEI: 0.30}
backtest:
  initial_capital: 1000000
```

---

## 失败容忍

- 某日行情缺失 → 用流水成交价/前日价，记 warning
- 撮合被拒 → 记录 reject_reason 到 trade_log，不中断
- 参数缺失 → 取 config.yaml 默认值

---

## 验证方案

- 单元测试：`tests/unit/engine/`
  - `test_market_rules.py`：板块识别、涨跌停上下界
  - `test_matcher.py`：滑点/费用（含最低佣金）、T+1、超持仓拒绝
  - `test_portfolio.py`：buy 加仓加权成本、sell realized_pnl、mark-to-market
  - `test_event_engine.py`：逐日回放，equity_curve 正确
  - `test_external_scorer_replay.py`：回放真实流水，NAV 与汇总一致
- 集成测试：`tests/integration/` import→backtest 端到端