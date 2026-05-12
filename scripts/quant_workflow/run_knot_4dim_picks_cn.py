"""China A-share 4-dimension Knot picks entry.

Asks the remote Knot agent for three China A-share candidates per dimension
(technical / fundamental / capital flow / event driven), runs each through
the local CandidateScoringService and prints a compact text summary.

Side-effects: outbound LLM call only; no OpenD, no orders, no trading
state files are touched. Structured JSON is mirrored to
``log/YYYYMMDDHH/knot_4dim_cn.json`` for traceability.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.strategy.knot_pick_helpers import (  # noqa: E402
    DEFAULT_PER_DIM,
    KnotPickError,
    call_knot_4dim_picks,
    format_four_dim_picks,
    resolve_output_path,
    score_candidate_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knot 4-dim picks for the China A-share market.")
    parser.add_argument("--per-dim", type=int, default=DEFAULT_PER_DIM,
                        help="Number of picks per dimension (default 3).")
    parser.add_argument("--output", default=None,
                        help="Optional output path. Defaults to log/YYYYMMDDHH/knot_4dim_cn.json under the repo root.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print compact text only; do not write the JSON file.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        picks = call_knot_4dim_picks(market="china", per_dim=int(args.per_dim))
    except KnotPickError as exc:
        print(f"[knot_4dim_cn] error: {exc}", file=sys.stderr)
        return 2

    enriched: dict[str, list] = {}
    for dim_key, rows in picks.dimensions.items():
        enriched[dim_key] = score_candidate_rows(rows, mode="dynamic", generated_at=picks.generated_at)

    text = format_four_dim_picks(picks, enriched=enriched)
    print(text)

    if not args.dry_run:
        out_path = resolve_output_path(args.output, default_filename="knot_4dim_cn.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "market": "china",
            "generated_at": picks.generated_at,
            "per_dim": int(args.per_dim),
            "dimensions": enriched,
            "knot_raw_response": picks.raw_response,
            "wrote_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[knot_4dim_cn] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
