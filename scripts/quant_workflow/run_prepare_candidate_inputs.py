from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.strategy.candidate_preparation import CandidateInputPreparationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare normalized candidate input artifacts for the quant workflow. "
            "This command rewrites state/runs/candidate_inputs.dynamic.json, "
            "state/runs/candidate_inputs.json, and emits a preparation report."
        )
    )
    parser.add_argument("--dynamic-source", action="append", dest="dynamic_sources", default=[])
    parser.add_argument("--static-source", action="append", dest="static_sources", default=[])
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--include-market-data", action="store_true")
    parser.add_argument("--knot-runtime", choices=["off", "local", "remote", "auto"], default="auto")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    service = CandidateInputPreparationService(REPO_ROOT)
    report = service.prepare(
        dynamic_sources=list(args.dynamic_sources or []) or None,
        static_sources=list(args.static_sources or []) or None,
        as_of_date=args.as_of_date,
        include_market_data=bool(args.include_market_data),
        knot_runtime=str(args.knot_runtime or "auto"),
    )
    print(report["report_path"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
