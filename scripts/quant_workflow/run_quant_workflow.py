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
    "beginner_full": {"workflow": "beginner_quant", "mode": "plan", "stage": "research"},
    "research_snapshot": {"workflow": "beginner_quant_research_snapshot", "mode": "research_only", "stage": "research"},
    "simulation_gate": {"workflow": "beginner_quant_simulation_gate", "mode": "stage_only", "stage": "simulation"},
    "live_gate": {"workflow": "beginner_quant_live_gate", "mode": "stage_only", "stage": "live"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Beginner quant workflow entry. Default behaviour is planning/report-only; "
            "it does not auto-run simulation or live scripts."
        )
    )
    parser.add_argument("--workflow", default=None)
    parser.add_argument("--preset", choices=sorted(PRESET_WORKFLOWS.keys()), default="beginner_full")
    parser.add_argument("--mode", default=None, choices=["plan", "research_only", "stage_only"])
    parser.add_argument("--stage", default=None, choices=["research", "backtest", "simulation", "live"])
    parser.add_argument("--preferred-market", action="append", dest="preferred_markets", default=[])
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--hours-per-week", type=float, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)
    parser.add_argument("--risk-profile", default="conservative", choices=["conservative", "moderate"])
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--prepare-candidates", action="store_true")
    parser.add_argument("--prepare-include-market-data", action="store_true")
    parser.add_argument("--prepare-knot-runtime", choices=["off", "local", "remote", "auto"], default="auto")
    return parser


def _resolve_workflow_args(args: argparse.Namespace) -> dict[str, str]:
    preset = dict(PRESET_WORKFLOWS.get(args.preset, {}))
    return {
        "workflow": args.workflow or preset.get("workflow", "beginner_quant"),
        "mode": args.mode or preset.get("mode", "plan"),
        "stage": args.stage or preset.get("stage", "research"),
        "preset": args.preset,
    }


def _cli_summary(result: dict[str, object], *, preset: str) -> dict[str, object]:
    return {
        "preset": preset,
        "status": result.get("status"),
        "workflow_name": result.get("workflow_name"),
        "mode": result.get("mode"),
        "workflow_summary": result.get("workflow_summary"),
        "workflow_report": result.get("workflow_report"),
        "latest_index": result.get("latest_index"),
        "artifacts": {
            key: result.get(key)
            for key in ("research_artifact", "candidate_artifact", "plan_artifact", "candidate_prepare_report")
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
        prepare_candidates=bool(args.prepare_candidates),
        prepare_include_market_data=bool(args.prepare_include_market_data),
        prepare_knot_runtime=str(args.prepare_knot_runtime or "auto"),
    )
    payload = _cli_summary(result, preset=workflow_args["preset"]) if args.summary_only else result
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
