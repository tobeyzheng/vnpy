from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.trading_pipeline.live_task import LiveTaskConfig, LiveTradingPipeline
from services.strategy.registry import StrategyRegistry, StrategyDefinition


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="美股classic_multifactor实盘任务入口；默认dry-run，需三重开关才真实提交")
    parser.add_argument("--live-submit", action="store_true")
    parser.add_argument("--symbol", type=str, required=True, help="交易标的，如NVDA.US")
    parser.add_argument("--config", type=str, default="nvda_g09.json", help="策略配置文件，默认使用nvda_g09.json")
    parser.add_argument("--budget-per-trade", type=float, default=5000.0)
    parser.add_argument("--max-order-value", type=float, default=1250.0)
    parser.add_argument("--max-candidates", type=int, default=1)
    parser.add_argument("--max-selected", type=int, default=1)
    parser.add_argument("--reconciliation-max-age", type=int, default=60)
    parser.add_argument("--limit-price-buffer-pct", type=float, default=0.0)
    parser.add_argument("--no-approval-required", action="store_true")
    parser.add_argument("--simulate", action="store_true", help="使用富途模拟账户环境（SIMULATE）而非实盘环境（REAL）")
    parser.add_argument("--minute-profile", action="store_true", help="使用保守分钟级降频参数")
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
    registry = StrategyRegistry()
    registry.register(StrategyDefinition(
        strategy_id="classic_multifactor_cta",
        name="Classic MultiFactor CTA Strategy",
        min_raw_score=0.55,
        tradable_actions=("trend_following", "pullback_buy", "breakout_momentum"),
        metadata={"source": "classic_multifactor", "type": "cta"}
    ))


def main() -> None:
    args = build_parser().parse_args()

    # 加载classic_multifactor配置
    config_data = load_classic_multifactor_config(args.config)
    setting = config_data.get("setting", {})

    # 如果启用分钟级配置，覆盖部分参数
    if args.minute_profile:
        setting.update({
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
        })

    # 注册策略
    register_classic_multifactor_strategy()

    # 配置实盘任务
    gateway_env = "SIMULATE" if args.simulate else "REAL"
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

    # 保存候选池到文件
    candidates_dir = REPO_ROOT / "state" / "runs" / "candidate_inputs"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    candidate_file = candidates_dir / f"classic_multifactor_{args.symbol.replace('.', '_')}.json"
    with open(candidate_file, 'w', encoding='utf-8') as f:
        json.dump([candidate], f, ensure_ascii=False, indent=2)

    # 运行实盘pipeline
    pipeline = LiveTradingPipeline(REPO_ROOT, config, live_submit=args.live_submit)
    report = pipeline.run()

    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()