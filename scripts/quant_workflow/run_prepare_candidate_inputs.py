from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.strategy.candidate_preparation import (
    CandidateInputPreparationService,
    DEFAULT_KNOT_TARGET_COUNT,
    DEFAULT_TOP_N,
    SUPPORTED_PREPARE_MARKETS,
    SUPPORTED_PREPARE_STRATEGIES,
)
from services.strategy.universe import DEFAULT_UNIVERSE_LIMIT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh the dynamic candidate pool for the quant workflow. "
            "By default the script runs the market-scoped flow: it asks the remote Knot "
            "agent for fresh candidates per market (knot_first), falling back to the "
            "Futu universe + multifactor scoring (score_first) when Knot is unreachable. "
            "Existing rows for markets outside --market are preserved untouched."
        )
    )
    parser.add_argument(
        "--market",
        choices=("all", *SUPPORTED_PREPARE_MARKETS),
        default="all",
        help="Target market to refresh; 'all' iterates over hong_kong and us.",
    )
    parser.add_argument(
        "--strategy",
        choices=SUPPORTED_PREPARE_STRATEGIES,
        default="knot_first",
        help="Candidate generation strategy. knot_first auto-falls back to score_first "
        "when the remote Knot agent is unavailable.",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--knot-target-count", type=int, default=DEFAULT_KNOT_TARGET_COUNT)
    parser.add_argument("--universe-limit", type=int, default=DEFAULT_UNIVERSE_LIMIT)
    parser.add_argument(
        "--knot-runtime",
        choices=["off", "local", "remote", "auto"],
        default="auto",
    )
    parser.add_argument(
        "--include-market-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Toggle Futu snapshot enrichment during scoring (default: enabled).",
    )
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but do not write candidate_inputs.dynamic.json.",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Run the legacy prepare() flow that re-processes existing dynamic + static "
        "sources without market scoping. Static pool refresh stays gated behind this flag.",
    )
    parser.add_argument("--dynamic-source", action="append", dest="dynamic_sources", default=[])
    parser.add_argument("--static-source", action="append", dest="static_sources", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    service = CandidateInputPreparationService(REPO_ROOT)
    if args.legacy:
        report = service.prepare(
            dynamic_sources=list(args.dynamic_sources or []) or None,
            static_sources=list(args.static_sources or []) or None,
            as_of_date=args.as_of_date,
            include_market_data=bool(args.include_market_data),
            knot_runtime=str(args.knot_runtime or "auto"),
        )
    else:
        report = service.prepare_market(
            market=str(args.market or "all"),
            strategy=str(args.strategy or "knot_first"),
            top_n=int(args.top_n or DEFAULT_TOP_N),
            knot_target_count=int(args.knot_target_count or DEFAULT_KNOT_TARGET_COUNT),
            knot_runtime=str(args.knot_runtime or "auto"),
            include_market_data=bool(args.include_market_data),
            universe_limit=int(args.universe_limit or DEFAULT_UNIVERSE_LIMIT),
            dry_run=bool(args.dry_run),
            as_of_date=args.as_of_date,
        )
    print(report["report_path"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
