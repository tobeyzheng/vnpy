from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.quant_workflow import QuantWorkflowService

PRESET_WORKFLOWS = {
    "trading_full": {"workflow": "quant_trading", "mode": "plan", "stage": "readiness", "task_type": "simulation"},
    "health_snapshot": {"workflow": "quant_trading_health_snapshot", "mode": "healthcheck_only", "stage": "healthcheck", "task_type": "simulation"},
    "simulation_readiness": {"workflow": "quant_trading_simulation_readiness", "mode": "stage_only", "stage": "readiness", "task_type": "simulation"},
    "live_readiness": {"workflow": "quant_trading_live_readiness", "mode": "stage_only", "stage": "readiness", "task_type": "live"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Quant workflow entry focused on candidate preparation, unified healthcheck, candidate framework, "
            "backtest evidence, and readiness review."
        )
    )
    parser.add_argument("--workflow", default=None)
    parser.add_argument("--preset", choices=sorted(PRESET_WORKFLOWS.keys()), default="trading_full")
    parser.add_argument("--mode", default=None, choices=["plan", "healthcheck_only", "stage_only"])
    parser.add_argument("--stage", default=None, choices=["healthcheck", "candidate_framework", "backtest", "readiness"])
    parser.add_argument("--task-type", default=None, choices=["simulation", "live"])
    parser.add_argument("--preferred-market", action="append", dest="preferred_markets", default=[])
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--hours-per-week", type=float, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)
    parser.add_argument("--risk-profile", default="balanced", choices=["conservative", "moderate", "balanced", "aggressive"])
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--prepare-candidates", action="store_true")
    parser.add_argument("--prepare-include-market-data", action="store_true")
    parser.add_argument("--prepare-knot-runtime", choices=["off", "local", "remote", "auto"], default="auto")
    parser.add_argument("--auto-execute-backtests", action="store_true")
    parser.add_argument("--backtest-optimize-mode", choices=["bf", "ga"], default="ga")
    parser.add_argument("--backtest-start", default=None)
    parser.add_argument("--backtest-end", default=None)
    parser.add_argument("--backtest-rate", type=float, default=0.0003)
    parser.add_argument("--backtest-slippage", type=float, default=0.05)
    parser.add_argument("--backtest-size", type=int, default=1)
    parser.add_argument("--backtest-pricetick", type=float, default=0.01)
    parser.add_argument("--backtest-top-n", type=int, default=20)
    parser.add_argument("--backtest-workers", type=int, default=None)
    return parser


def _resolve_workflow_args(args: argparse.Namespace) -> dict[str, str]:
    preset = dict(PRESET_WORKFLOWS.get(args.preset, {}))
    return {
        "workflow": args.workflow or preset.get("workflow", "quant_trading"),
        "mode": args.mode or preset.get("mode", "plan"),
        "stage": args.stage or preset.get("stage", "readiness"),
        "task_type": args.task_type or preset.get("task_type", "simulation"),
        "preset": args.preset,
    }


def _cli_summary(result: dict[str, object], *, preset: str) -> dict[str, object]:
    return {
        "preset": preset,
        "status": result.get("status"),
        "workflow_name": result.get("workflow_name"),
        "mode": result.get("mode"),
        "task_type": result.get("task_type"),
        "workflow_summary": result.get("workflow_summary"),
        "workflow_report": result.get("workflow_report"),
        "latest_index": result.get("latest_index"),
        "artifacts": {
            key: result.get(key)
            for key in ("candidate_artifact", "backtest_artifact", "readiness_artifact", "candidate_prepare_report")
            if result.get(key)
        },
        "warnings": result.get("warnings"),
    }


def main() -> int:
    args = build_parser().parse_args()
    workflow_args = _resolve_workflow_args(args)
    service = QuantWorkflowService(REPO_ROOT)
    profile = {
        "capital": args.capital,
        "hours_per_week": args.hours_per_week,
        "max_drawdown_pct": args.max_drawdown_pct,
        "risk_profile": args.risk_profile,
        "preferred_market": args.preferred_markets[0] if args.preferred_markets else None,
    }
    result = service.run(
        workflow_name=workflow_args["workflow"],
        mode=workflow_args["mode"],
        profile=profile,
        preferred_markets=list(args.preferred_markets or []),
        max_candidates=max(int(args.max_candidates), 1),
        stage=workflow_args["stage"],
        task_type=workflow_args["task_type"],
        prepare_candidates=bool(args.prepare_candidates),
        prepare_include_market_data=bool(args.prepare_include_market_data),
        prepare_knot_runtime=str(args.prepare_knot_runtime or "auto"),
        auto_execute_backtests=bool(args.auto_execute_backtests),
        backtest_optimize_mode=str(args.backtest_optimize_mode or "ga"),
        backtest_start=args.backtest_start,
        backtest_end=args.backtest_end,
        backtest_rate=float(args.backtest_rate),
        backtest_slippage=float(args.backtest_slippage),
        backtest_size=int(args.backtest_size),
        backtest_pricetick=float(args.backtest_pricetick),
        backtest_top_n=int(args.backtest_top_n),
        backtest_workers=args.backtest_workers,
    )
    payload = _cli_summary(result, preset=workflow_args["preset"]) if args.summary_only else result
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
