# data-auto-flow 数据导入

> **目标**：解析 主流水线 多 sheet Excel 流水，重建每日持仓序列，输出标准 JSON
> **入口命令**：`python -m alphaforge.cli import --excel <path> --output <out.json>`
> **上游**：main-auto-workflow Stage 1
> **下游**：backtest-auto-flow

---

## 数据源格式（实测确认）

```
文件：投资流水记录表.xlsx
Sheet：每个交易日 1 个 sheet（如 "7月31日"，正则解析为 2026-07-31）
```

| 区域 | 行 | 内容 | 关键处理 |
|------|----|------|---------|
| 元数据 | R4 | 小组编号 / 交易日期 / 所处阶段 / 本周资金上限 | 正则提取交易日 |
| 一、小组当日汇总 | R7（表头）/ R9（原始数值） | 期初/买入/卖出/手续费/期末总资产/当日盈亏/当日收益率 | **用 R9 数值行**，不用 R8 格式化字符串 |
| 二、组员个人资产快照 | R12（表头）/ R14-18 | 5 名组员期初/期末现金/持仓市值/总资产/盈亏 | 过滤 R13 示例行（以"示例"开头） |
| 三、个人交易流水明细 | R21（表头）/ R22+ | 序号/交易人/时间/代码/名称/方向/数量/价格/金额/手续费/现金/备注 | 过滤示例行；代码可能含 `.SH/.SZ` 后缀 |

---

## 流程图

```
主流水线 Excel
   │
   ▼
Step 1 按 sheet 名解析日期
   ▼
Step 2 定位各区域表头（扫描，非行号）
   ▼
Step 3 解析小组汇总（R9）与组员快照（R14-18）
   ▼
Step 4 解析交易流水（过滤示例行）
   ▼
Step 5 按日期排序，增量重建持仓（买入加权/卖出 realized_pnl）
   ▼
Step 6 mark-to-market（行情接口 or 成交价）→ PortfolioSnapshot
   ▼
输出 output/data/records.json
```

---

## 步骤详解

### Step 1: 解析 sheet 名日期

```bash
python -c "from alphaforge.data.external_scorer_importer import 主流水线Importer; p=主流水线Importer(); print([s for s in p.sheet_names(r'<excel>')])"
```

- 正则 `(\d+)月(\d+)日` → `2026年基准年`
- 失败 sheet 记 warning，跳过，不阻断
- **校验**：打印的 sheet 数 = 实际交易日数

### Step 2-4: 解析各区域

- 用表头关键字扫描定位区域起始行（如"一、小组当日汇总"/"二、组员个人资产快照"/"三、个人交易流水明细"）
- 流水区域从表头下一行开始，直到空行或下一区域
- **校验**：解析出 N 名组员（≤6）、M 笔流水（≥0）

### Step 5: 增量重建持仓

核心算法（`build_portfolio_history`）：

```
positions = {}   # code → {qty, avg_cost, total_buy_qty, total_buy_cost}
for date, record in 按日期排序:
    for trade in record.trades:
        if 买入:
            positions[trade.code].qty += trade.qty
            positions[trade.code].total_buy_cost += trade.amount(含手续费)
        elif 卖出:
            realized_pnl += (trade.price - avg_cost) * trade.qty
            positions[trade.code].qty -= trade.qty
    snapshot = PortfolioSnapshot(date, cash, positions(筛 qty>0), nav,
                                 daily_pnl, daily_return_pct)
```

**校验**：权益曲线 NAV 单调合理；`期末总资产` 与汇总行 R9 一致（误差 <1%）

### Step 6: 输出

```bash
python -m alphaforge.cli import --excel <path> --output output/data/records.json
```

---

## 数据模型

```python
class Trade(BaseModel):
    seq: int
    trader: str
    time: datetime
    code: str                # 600519.SH
    name: str
    side: Literal["BUY", "SELL"]
    qty: int
    price: float
    amount: float
    fee: float
    cash_after: float
    note: str

class MemberSnapshot(BaseModel):
    name: str
    role: str
    opening_cash: float
    opening_mv: float
    closing_cash: float
    closing_mv: float
    closing_nav: float
    daily_pnl: float
    daily_return_pct: float

class Record(BaseModel):
    date: datetime
    team_summary: dict
    members: List[MemberSnapshot]
    trades: List[Trade]
    nav: float
    cash: float

class PortfolioSnapshot(BaseModel):
    date: datetime
    cash: float
    positions: Dict[str, Position]   # code → Position(qty, avg_cost, market_value, unrealized_pnl)
    nav: float
    daily_pnl: float
    daily_return_pct: float
```

---

## 配置项（config.yaml）

```yaml
paths:
  external_scorer_file: ""          # Excel 路径（可被 --excel 覆盖）
data:
  base_year: 2026
  filter_example_rows: true    # 过滤"示例"行
  use_numeric_summary_row: true # 用 R9 数值行
```

---

## 失败容忍

- Excel 不存在 → `FileNotFoundError`，明确提示
- 某 sheet 无流水 → 记 warning，生成空 trades 的 snapshot
- 代码无后缀 → 自动补 `.SH/.SZ`（按前缀 6/9→SH，其余→SZ）

---

## 验证方案

- 单元测试：`tests/unit/data/test_external_scorer_importer.py`
  - 构造 5 sheet mini Excel，断言日期/组员/流水解析正确
  - 断言示例行被过滤
  - 断言增量持仓（买入加权成本、卖出 realized_pnl）正确
- 集成测试：真实 Excel 跑通 import，NAV 与汇总行一致