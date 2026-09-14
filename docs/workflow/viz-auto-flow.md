# viz-auto-flow 可视化展示

> **目标**：启动 Web 仪表盘，用 ECharts + Tailwind 将报告数据直观美观呈现
> **入口命令**：`python -m alphaforge.cli viz --port 8050`
> **上游**：report-auto-flow（消费 `output/` 下的 JSON）
> **依赖**：flask（可选，缺失时降级提示安装）

---

## 流程图

```
output/{date}-详细数据.json + analysis.json + backtest.json
   │
   ▼
Step 1 启动 Flask 服务器（viz/server.py）
   ▼
Step 2 浏览器访问 / → dashboard.html
   │  前端 JS 调用 /api/dashboard?date=xxx
   ▼
Step 3 api.py 读取 output/ JSON，组装图表数据
   ▼
Step 4 ECharts 渲染：KPI/权益曲线/回撤/持仓饼图/预警
   ▼
用户可视化交互（切换日期/查看回测/归因详情）
```

---

## 页面与图表清单

### 仪表盘主页 `dashboard.html`

| 区域 | 图表类型 | 数据来源 |
|------|---------|---------|
| 顶部 KPI | 卡片 ×5（总收益/年化/MDD/夏普/胜率） | `analysis.json` 的 `returns` + `risk` |
| 权益曲线 | ECharts line（组合 vs 沪深300） | `backtest.json` 的 `equity_curve` + 基准 |
| 回撤水下 | area（红色填充负值） | 从权益曲线计算 |
| 持仓分布 | pie（按市值） | `records.json` 当日 positions |
| 行业暴露 | bar | positions 按行业聚合 |
| 风险预警 | 卡片列表（🔴🟠🟡） | `analysis.json` 的 `risk.alerts` |
| 操作建议 | 表格 | `analysis.json` 的 `recommendations` |

### 回测详情页 `backtest.html`

| 区域 | 图表类型 |
|------|---------|
| 逐笔交易 | 表格（可排序，方向/代码/数量/价格/费用/状态） |
| 月度收益 | 热力图（年×月，红绿） |
| 个股贡献 | 横向柱图（正绿负红） |
| 参数敏感性 | line（若跑了寻优） |

### 归因详情页 `attribution.html`

| 区域 | 图表类型 |
|------|---------|
| Brinson 归因 | 瀑布图（配置→选股→交互→总超额） |
| Fama 分解 | 饼图（系统性/选股/风险回报） |
| 行业归因 | 矩阵热力图 |

---

## 步骤详解

### Step 1: 启动服务器

```bash
python -m alphaforge.cli viz --port 8050
```

- 加载 `config.yaml` 的 `paths.output_dir`
- Flask 静态资源映射 `viz/templates/`
- 默认端口 8050，可 `--port` 覆盖
- **校验**：日志 `Running on http://127.0.0.1:8050`

### Step 2-3: API 数据组装

`api.py` 端点：

```python
GET /api/dashboard?date=YYYYMMDD
  → {kpi, equity_curve, drawdown, positions, industry_exposure, alerts, recommendations}

GET /api/backtest?name=xxx
  → {trade_log, monthly_heatmap, stock_contribution, sensitivity}

GET /api/attribution?date=xxx
  → {brinson_waterfall, fama_pie, industry_matrix}

GET /api/dates
  → ["2026-07-27", "2026-07-28", ...]  # 可选日期列表
```

### Step 4: 前端渲染

- 单页应用，Tailwind CSS 布局，ECharts 渲染
- 顶部日期选择器，切换日期触发 API 调用
- 响应式布局，适配 1280px+ 桌面
- 离线友好：ECharts/Tailwind 用本地 vendor 或 CDN（可配置）

---

## 数据模型（前端契约）

```typescript
interface Dashboard {
  date: string;
  kpi: {
    total_return: number;     // %
    annualized: number;       // %
    max_drawdown: number;     // %（负值）
    sharpe: number;
    win_rate: number;         // %
  };
  equity_curve: { date: string; nav: number; benchmark: number }[];
  drawdown: { date: string; value: number }[];  // 负值
  positions: { code: string; name: string; market_value: number; pct: number }[];
  industry_exposure: { industry: string; pct: number }[];
  alerts: { level: "HIGH"|"MEDIUM"|"LOW"; type: string; message: string }[];
  recommendations: { priority: string; title: string; description: string }[];
}
```

---

## 配置项（config.yaml）

```yaml
viz:
  enabled: true
  host: "127.0.0.1"
  port: 8050
  debug: false
  cdn: false    # true 用 CDN，false 用本地 vendor
```

---

## 失败容忍

- `output/` 无报告 → 首页显示「暂无数据，请先运行 import/backtest/analyze/report」
- 某日期 JSON 缺失 → API 返回 404，前端提示
- flask 未安装 → CLI 提示 `pip install flask` 并退出

---

## 验证方案

- 单元测试：`tests/unit/viz/`
  - `test_charts.py`：权益曲线/回撤/月度热力数据组装正确
  - `test_api.py`：各端点返回 schema 校验
- 集成测试：`tests/integration/test_viz_server.py`
  - 启动服务器，GET `/` 返回 200
  - GET `/api/dashboard` 返回合法 JSON
- e2e：`tests/e2e/test_viz_full.py` 跑完整链路后启动 viz，校验可访问