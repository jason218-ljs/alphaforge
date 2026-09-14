"""AlphaForge CLI 入口。

子命令：doctor / import / backtest / analyze / report / run-all / viz（Phase 6）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from alphaforge import __version__
from alphaforge.config import get_config
from alphaforge.logger import get_logger, setup_logging
from alphaforge.orchestrator import Orchestrator

logger = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alphaforge",
        description="AlphaForge 企业级量化分析回测引擎",
    )
    parser.add_argument("--version", action="version", version=f"alphaforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # doctor
    sub.add_parser("doctor", help="环境自检")

    # import
    p_import = sub.add_parser("import", help="导入 主流水线 Excel")
    p_import.add_argument("--excel", required=True, help="Excel 文件路径")
    p_import.add_argument("--output", default=None, help="输出 JSON 路径")

    # backtest
    p_bt = sub.add_parser("backtest", help="回测回放")
    p_bt.add_argument("--input", required=True, help="records.json 路径")
    p_bt.add_argument("--strategy", default="external_scorer_replay")
    p_bt.add_argument("--output", default=None)

    # analyze
    p_an = sub.add_parser("analyze", help="分析评估")
    p_an.add_argument("--input", required=True, help="backtest.json 路径")
    p_an.add_argument("--output", default=None)

    # report
    p_rp = sub.add_parser("report", help="生成报告")
    p_rp.add_argument("--input", required=True, help="analysis.json 路径")
    p_rp.add_argument("--output-dir", default=None)

    # run-all
    p_all = sub.add_parser("run-all", help="一键全链路")
    p_all.add_argument("--excel", required=True)

    # sync（自动同步）
    p_sync = sub.add_parser("sync", help="增量同步 主流水线 Excel 数据")
    p_sync.add_argument("--excel", default=None, help="Excel 路径（默认用配置）")
    p_sync.add_argument("--watch", action="store_true", help="持续监听模式")
    p_sync.add_argument("--interval", type=int, default=None, help="监听间隔秒（默认 300）")
    p_sync.add_argument("--no-pipeline", action="store_true", help="同步后不自动跑全链路")
    p_sync.add_argument("--status", action="store_true", help="仅查询同步状态")

    # optimize
    p_opt = sub.add_parser("optimize", help="参数寻优（网格/Optuna 贝叶斯）")
    p_opt.add_argument("--method", default=None, choices=["grid", "bayesian"])
    p_opt.add_argument("--n-trials", type=int, default=None)
    p_opt.add_argument("--fast-min", type=int, default=2)
    p_opt.add_argument("--fast-max", type=int, default=15)
    p_opt.add_argument("--slow-min", type=int, default=10)
    p_opt.add_argument("--slow-max", type=int, default=60)
    p_opt.add_argument("--seed", type=int, default=None)
    p_opt.add_argument("--output", default=None)

    # walkforward
    p_wf = sub.add_parser("walkforward", help="Walk-Forward 稳健性检验")
    p_wf.add_argument("--fast", type=int, default=5)
    p_wf.add_argument("--slow", type=int, default=20)
    p_wf.add_argument("--window", type=int, default=None)
    p_wf.add_argument("--output", default=None)

    # viz (Phase 6 预留)
    p_viz = sub.add_parser("viz", help="启动可视化仪表盘（Phase 6）")
    p_viz.add_argument("--port", type=int, default=8050)
    p_viz.add_argument("--report", default=None)

    # screener-backtest：选股策略回测（合成数据）
    p_scr = sub.add_parser("screener-backtest", help="选股策略回测（基于 stock-scoring 工作流，合成数据）")
    p_scr.add_argument("--trading-days", type=int, default=120, help="回测交易日数")
    p_scr.add_argument("--seed", type=int, default=42, help="随机种子")
    p_scr.add_argument("--max-positions", type=int, default=5, help="最大持仓数")
    p_scr.add_argument("--top-n", type=int, default=10, help="每日候选数")
    p_scr.add_argument("--capital", type=float, default=None, help="初始资金")
    p_scr.add_argument("--output", default=None, help="输出路径")

    # screener-backtest-real：选股策略回测（真实数据）
    p_scr_real = sub.add_parser("screener-backtest-real", help="选股策略回测（真实数据：腾讯K线+东财基本面）")
    p_scr_real.add_argument("--trading-days", type=int, default=120, help="回测交易日数")
    p_scr_real.add_argument("--max-positions", type=int, default=5, help="最大持仓数")
    p_scr_real.add_argument("--top-n", type=int, default=10, help="每日候选数")
    p_scr_real.add_argument("--capital", type=float, default=None, help="初始资金")
    p_scr_real.add_argument("--cache-db", default="./data/real_data.db", help="数据缓存DB路径")
    p_scr_real.add_argument("--output", default=None, help="输出路径")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(get_config().app.get("log_level", "INFO"))

    orch = Orchestrator()

    if args.command == "doctor":
        result = orch.doctor()
        for k, v in result.items():
            print(f"{k}: {v}")
        return 0 if all("FAIL" not in str(v) for v in result.values()) else 1

    if args.command == "import":
        out = orch.run_data_import(args.excel, args.output)
        print(f"导入完成：{out}")
        return 0

    if args.command == "backtest":
        out = orch.run_backtest(args.input, args.strategy, args.output)
        print(f"回测完成：{out}")
        return 0

    if args.command == "analyze":
        out = orch.run_analysis(args.input, args.output)
        print(f"分析完成：{out}")
        return 0

    if args.command == "report":
        paths = orch.run_report(args.input, args.output_dir)
        print(f"报告生成：{paths}")
        return 0

    if args.command == "run-all":
        result = orch.run_full_cycle(args.excel)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "sync":
        # 临时覆盖 auto_run_pipeline 配置
        if args.no_pipeline:
            orch.config.sync.auto_run_pipeline = False
        if args.status:
            result = orch.run_sync_status(args.excel)
        else:
            result = orch.run_sync(
                excel=args.excel,
                watch=args.watch,
                interval=args.interval,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "optimize":
        out = orch.run_optimize(
            output=args.output, method=args.method, n_trials=args.n_trials,
            fast_range=(args.fast_min, args.fast_max),
            slow_range=(args.slow_min, args.slow_max),
            seed=args.seed,
        )
        print(f"参数寻优完成：{out}")
        return 0

    if args.command == "walkforward":
        out = orch.run_walkforward(
            output=args.output, params={"fast": args.fast, "slow": args.slow},
            window=args.window,
        )
        print(f"Walk-Forward 完成：{out}")
        return 0

    if args.command == "viz":
        from pathlib import Path
        from alphaforge.viz.server import DashServer
        from alphaforge.config import PROJECT_ROOT, VizConfig
        cfg = get_config()
        # 用项目根目录定位 output，避免 CWD 差异
        output_dir = Path(cfg.paths.output_dir)
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir
        output_dir = output_dir.resolve()
        # CLI --port 覆盖配置端口
        viz_cfg = VizConfig(enabled=cfg.viz.enabled, host=cfg.viz.host,
                            port=args.port, debug=cfg.viz.debug, cdn=cfg.viz.cdn)
        server = DashServer(output_dir=str(output_dir), config=viz_cfg)
        print(f"启动 AlphaForge 仪表盘：http://{server.host}:{server.port}")
        print(f"  选股回测页面：http://{server.host}:{server.port}/screener")
        print(f"  output_dir: {output_dir}")
        server.run()
        return 0

    if args.command == "screener-backtest":
        result = orch.run_screener_backtest(
            trading_days=args.trading_days,
            seed=args.seed,
            max_positions=args.max_positions,
            top_n_candidates=args.top_n,
            initial_capital=args.capital,
            output=args.output,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("提示：运行 alphaforge viz 后访问 /screener 查看选股回测可视化")
        return 0

    if args.command == "screener-backtest-real":
        result = orch.run_screener_backtest_real(
            trading_days=args.trading_days,
            max_positions=args.max_positions,
            top_n_candidates=args.top_n,
            initial_capital=args.capital,
            cache_db=args.cache_db,
            output=args.output,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("提示：运行 alphaforge viz 后访问 /screener 查看真实数据回测可视化")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())