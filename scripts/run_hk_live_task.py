from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.trading_pipeline import LiveTaskConfig, LiveTradingPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="港股实盘任务入口；默认dry-run，需三重开关才真实提交")
    parser.add_argument("--live-submit", action="store_true")
    parser.add_argument("--budget-per-trade", type=float, default=20000.0)
    parser.add_argument("--max-order-value", type=float, default=20000.0)
    parser.add_argument("--max-selected", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--reconciliation-max-age", type=int, default=60)
    parser.add_argument("--limit-price-buffer-pct", type=float, default=0.0)
    parser.add_argument("--no-approval-required", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = LiveTaskConfig(
        market="hong_kong",
        task_name="hk_live_task_v1",
        report_filename="hk_live_task_report.json",
        budget_per_trade=args.budget_per_trade,
        lot_size_default=100,
        quote_prefix="HK",
        symbol_suffix="HK",
        flow_divisor=2e9,
        catalyst_keywords=("主题", "催化"),
        max_candidates=args.max_candidates,
        max_selected=args.max_selected,
        max_order_value=args.max_order_value,
        reconciliation_max_age_minutes=args.reconciliation_max_age,
        limit_price_buffer_pct=args.limit_price_buffer_pct,
        approval_required=not args.no_approval_required,
        live_submit_enabled=os.environ.get("VNPY_LIVE_CONFIG") == "YES",
    )
    pipeline = LiveTradingPipeline(REPO_ROOT, config, live_submit=args.live_submit)
    report = pipeline.run()
    print(pipeline.report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
