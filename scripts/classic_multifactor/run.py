from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _add_common_backtest_args(sub: argparse.ArgumentParser, *, with_submit_sim: bool = False) -> None:
    """Shared args for cta-backtest / legacy-backtest / sim-once."""
    sub.add_argument("--symbol", default="NVDA.US")
    sub.add_argument("--start", default="2024-01-01")
    sub.add_argument("--end", default="2024-12-31")
    sub.add_argument("--capital", type=float, default=20000.0)
    sub.add_argument("--interval", choices=["1d", "1m"], default="1d")
    sub.add_argument("--max-order-value", type=float, default=5000.0)
    sub.add_argument("--minute-profile", action="store_true", help="使用保守分钟级降频参数")
    if with_submit_sim:
        sub.add_argument("--submit-sim", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified classic multifactor runner")
    subparsers = parser.add_subparsers(dest="mode", required=True, help="运行模式")

    # 回测相关子命令
    _add_common_backtest_args(subparsers.add_parser("cta-backtest", help="CTA策略回测"))

    alpha_parser = subparsers.add_parser("alpha-backtest", help="Alpha策略回测")
    alpha_parser.add_argument("--symbol", default="NVDA.US")
    alpha_parser.add_argument("--symbols", nargs="+", default=[])
    alpha_parser.add_argument("--start", default="2024-01-01")
    alpha_parser.add_argument("--end", default="2024-12-31")
    alpha_parser.add_argument("--capital", type=float, default=20000.0)
    alpha_parser.add_argument("--interval", choices=["1d", "1m"], default="1d")

    _add_common_backtest_args(subparsers.add_parser("legacy-backtest", help="传统回测模式"), with_submit_sim=True)
    _add_common_backtest_args(subparsers.add_parser("sim-once", help="单次模拟交易"), with_submit_sim=True)

    # 实盘子命令
    live_parser = subparsers.add_parser("live", help="美股实盘交易")
    live_parser.add_argument("--live-submit", action="store_true")
    live_parser.add_argument("--symbol", type=str, required=True, help="交易标的，如NVDA.US")
    live_parser.add_argument("--config", type=str, default="nvda_g09.json", help="策略配置文件，默认使用nvda_g09.json")
    live_parser.add_argument("--budget-per-trade", type=float, default=5000.0)
    live_parser.add_argument("--max-order-value", type=float, default=1250.0)
    live_parser.add_argument("--max-candidates", type=int, default=1)
    live_parser.add_argument("--max-selected", type=int, default=1)
    live_parser.add_argument("--reconciliation-max-age", type=int, default=60)
    live_parser.add_argument("--limit-price-buffer-pct", type=float, default=0.0)
    live_parser.add_argument("--no-approval-required", action="store_true")
    live_parser.add_argument("--simulate", action="store_true", help="使用富途模拟账户环境（SIMULATE）而非实盘环境（REAL）")
    live_parser.add_argument("--minute-profile", action="store_true", help="使用保守分钟级降频参数")

    return parser


def load_classic_multifactor_config(config_path: str) -> dict[str, Any]:
    """加载classic_multifactor策略配置"""
    config_file = REPO_ROOT / "configs" / "classic_multifactor" / config_path
    if not config_file.exists():
        raise FileNotFoundError(f"Classic multifactor config not found: {config_file}")

    with open(config_file, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    return config_data


def register_classic_multifactor_strategy() -> None:
    """注册classic_multifactor策略到策略注册表"""
    from services.strategy.registry import StrategyRegistry, StrategyDefinition

    registry = StrategyRegistry()
    registry.register(StrategyDefinition(
        strategy_id="classic_multifactor_cta",
        name="Classic MultiFactor CTA Strategy",
        min_raw_score=0.55,
        tradable_actions=("trend_following", "pullback_buy", "breakout_momentum"),
        metadata={"source": "classic_multifactor", "type": "cta"}
    ))


def run_live_mode(args) -> None:
    """运行实盘模式"""
    from services.trading_pipeline.live_task import LiveTaskConfig, LiveTradingPipeline

    # 加载classic_multifactor配置
    config_data = load_classic_multifactor_config(args.config)
    setting = config_data.get("setting", {})

    # 如果启用分钟级配置，仅在 json 未提供对应字段时填入默认值，
    # 避免把 nvda_g09.json 等策略 JSON 里已调优过的冠军参数（如 signal_interval_minutes=12,
    # entry_score=0.66, max_intraday_trades=4 等）被硬编码默认值覆盖。
    if args.minute_profile:
        minute_defaults = {
            "fast_window": 6,
            "slow_window": 24,
            "momentum_window": 12,
            "atr_window": 14,
            "signal_interval_minutes": 5,
            "confirm_bars": 1,
            "entry_score": 0.66,
            "min_volume_ratio": 0.8,
            "min_atr_pct": 0.0012,
            "min_trend_score": 0.55,
            "max_intraday_trades": 4,
            "entry_cooldown_minutes": 30,
            "min_hold_minutes": 20,
            "no_new_entry_after": "15:30",
            "stop_atr": 1.5,
            "take_profit_atr": 2.5,
            "trailing_atr": 2.0,
        }
        for k, v in minute_defaults.items():
            setting.setdefault(k, v)

    # 注册策略
    register_classic_multifactor_strategy()

    # 配置实盘任务
    gateway_env = "SIMULATE" if args.simulate else "REAL"
    live_account_strict = not args.simulate  # Plan C: REAL 路径强制严格账户校验
    config = LiveTaskConfig(
        market="us",
        task_name=f"classic_multifactor_{args.symbol.replace('.', '_')}",
        report_filename=f"classic_multifactor_{args.symbol.replace('.', '_')}_live_report.json",
        budget_per_trade=args.budget_per_trade,
        lot_size_default=1,
        quote_prefix="US",
        symbol_suffix="US",
        flow_divisor=5e9,
        catalyst_keywords=("AI", "催化"),
        max_candidates=args.max_candidates,
        max_selected=args.max_selected,
        max_order_value=args.max_order_value,
        reconciliation_max_age_minutes=args.reconciliation_max_age,
        limit_price_buffer_pct=args.limit_price_buffer_pct,
        approval_required=not args.no_approval_required,
        live_submit_enabled=os.environ.get("VNPY_LIVE_CONFIG") == "YES",
        env_var_name="VNPY_LIVE_SUBMIT",
        gateway_env=gateway_env,
        live_account_strict=live_account_strict,
    )

    # 创建候选池（单标的）
    candidate = {
        "symbol": args.symbol,
        "market": "us",
        "name": args.symbol.split('.')[0],
        "raw_score": 0.8,  # 默认高分，确保被选中
        "signals": [{"score": 0.8}],
        "rationale": f"classic_multifactor策略标的: {args.symbol}",
        "strategy_selection": {"strategy_id": "classic_multifactor_cta", "allow_trade": True},
        "minute_profile": args.minute_profile,
        "strategy_config": setting
    }

    # 同时写两个位置：
    # 1) candidate_inputs/ 下的归档文件（便于追溯每个 symbol 的独立候选快照）
    # 2) candidate_inputs.dynamic.json —— 真正被 UnifiedCandidateProvider 消费的文件，
    #    否则 strategy_config（来自 nvda_g09.json）永远不会被 live_task 读取。
    #    为了安全，这里只覆盖与当前 symbol 相同 symbol 的条目，其它标的保留。
    runs_dir = REPO_ROOT / "state" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    candidates_dir = runs_dir / "candidate_inputs"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    candidate_file = candidates_dir / f"classic_multifactor_{args.symbol.replace('.', '_')}.json"
    with open(candidate_file, 'w', encoding='utf-8') as f:
        json.dump([candidate], f, ensure_ascii=False, indent=2)

    dynamic_path = runs_dir / "candidate_inputs.dynamic.json"
    dynamic_items: list[dict[str, Any]] = []
    if dynamic_path.exists():
        try:
            raw = json.loads(dynamic_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                dynamic_items = [row for row in raw["items"] if isinstance(row, dict)]
            elif isinstance(raw, list):
                dynamic_items = [row for row in raw if isinstance(row, dict)]
        except Exception:
            dynamic_items = []
    # 覆盖当前 symbol 的条目，其它 symbol 保留
    dynamic_items = [
        row for row in dynamic_items
        if str(row.get("symbol", "")).upper() != str(args.symbol).upper()
    ]
    dynamic_items.append(candidate)
    dynamic_path.write_text(
        json.dumps({"items": dynamic_items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 运行实盘pipeline
    pipeline = LiveTradingPipeline(REPO_ROOT, config, live_submit=args.live_submit)
    report = pipeline.run()

    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = build_parser().parse_args()

    if args.mode == "live":
        run_live_mode(args)
        return

    # 原有的回测和模拟逻辑
    python = sys.executable
    minute_args = []
    if args.minute_profile:
        minute_args = [
            "--fast-window", "6",
            "--slow-window", "24",
            "--momentum-window", "12",
            "--atr-window", "14",
            "--signal-interval-minutes", "5",
            "--confirm-bars", "1",
            "--entry-score", "0.66",
            "--min-volume-ratio", "0.8",
            "--min-atr-pct", "0.0012",
            "--min-trend-score", "0.55",
            "--max-intraday-trades", "4",
            "--entry-cooldown-minutes", "30",
            "--min-hold-minutes", "20",
            "--no-new-entry-after", "15:30",
            "--stop-atr", "1.5",
            "--take-profit-atr", "2.5",
            "--trailing-atr", "2.0",
        ]

    if args.mode == "cta-backtest":
        script = REPO_ROOT / "scripts" / "classic_multifactor" / "run_vnpy_cta_backtest.py"
        cmd = [python, str(script), "--symbol", args.symbol, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital), "--max-order-value", str(args.max_order_value), *minute_args]

    elif args.mode == "alpha-backtest":
        script = REPO_ROOT / "scripts" / "classic_multifactor" / "run_alpha_backtest.py"
        symbols = args.symbols or [args.symbol]
        cmd = [python, str(script), "--symbols", *symbols, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital)]

    else:
        script = REPO_ROOT / "scripts" / "classic_multifactor" / "us_single_symbol_multifactor.py"
        mode = "backtest" if args.mode == "legacy-backtest" else "sim-once"
        cmd = [python, str(script), "--mode", mode, "--symbol", args.symbol, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital), "--max-order-value", str(args.max_order_value), *minute_args]

        if hasattr(args, 'submit_sim') and args.submit_sim:
            cmd.append("--submit-sim")

    raise SystemExit(subprocess.call(cmd, cwd=str(REPO_ROOT)))


if __name__ == "__main__":
    main()
