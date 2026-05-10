from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.quant_workflow import QuantWorkflowService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Beginner quant workflow entry. Default behaviour is planning/report-only; "
            "it does not auto-run simulation or live scripts."
        )
    )
    parser.add_argument("--workflow", default="beginner_quant")
    parser.add_argument("--mode", default="plan", choices=["plan", "research_only", "stage_only"])
    parser.add_argument("--stage", default="research", choices=["research", "backtest", "simulation", "live"])
    parser.add_argument("--preferred-market", action="append", dest="preferred_markets", default=[])
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--hours-per-week", type=float, default=None)
    parser.add_argument("--max-drawdown-pct", type=float, default=None)
    parser.add_argument("--risk-profile", default="conservative", choices=["conservative", "moderate"])
    parser.add_argument("--max-candidates", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    service = QuantWorkflowService(REPO_ROOT)
    profile = {
        "capital": args.capital,
        "hours_per_week": args.hours_per_week,
        "max_drawdown_pct": args.max_drawdown_pct,
        "risk_profile": args.risk_profile,
        "preferred_market": args.preferred_markets[0] if args.preferred_markets else None,
    }
    result = service.run(
        workflow_name=args.workflow,
        mode=args.mode,
        profile=profile,
        preferred_markets=list(args.preferred_markets or []),
        max_candidates=max(int(args.max_candidates), 1),
        stage=args.stage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
