"""Holdings -> Knot directional review.

Reads the current Futu account positions (read-only via FutuAccountProvider),
runs each through the local CandidateScoringService, then asks the remote
Knot agent for a directional action (hold / add / trim / exit) per name.

Sensitive numeric fields (cash, market value, quantity, total assets,
buying power) are masked before the prompt is built and before any output
is printed or persisted. We only expose:
  - the masked symbol / name / market
  - a relative weight bucket (small / medium / large) computed locally
  - a P/L direction tag (up / flat / down)

Side-effects: read-only OpenD position query (no orders) + outbound LLM
call. Output is mirrored to ``log/YYYYMMDDHH/holdings_knot_review.json``.
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

from services.futu_account import FutuAccountProvider  # noqa: E402
from services.strategy.knot_pick_helpers import (  # noqa: E402
    HoldingsReviewResult,
    KnotPickError,
    build_holdings_rows,
    call_knot_holdings_advice,
    format_holdings_review,
    mask_account_summary,
    merge_holdings_advice,
    resolve_output_path,
    score_candidate_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Futu holdings, score them locally and ask Knot for "
                    "a directional action per name. All amounts/quantities are masked."
    )
    parser.add_argument("--output", default=None,
                        help="Optional output path. Defaults to log/YYYYMMDDHH/holdings_knot_review.json under the repo root.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print compact text only; do not write the JSON file.")
    parser.add_argument("--skip-knot", action="store_true",
                        help="Skip the Knot call (useful for offline previewing). "
                             "Still scores locally and prints positions with action='review'.")
    parser.add_argument("--trd-env", default="REAL", choices=("REAL", "SIMULATE"),
                        help="Expected Futu trading environment for the read-only account query. Defaults to REAL.")
    parser.add_argument("--live-strict", action="store_true",
                        help="Require strict account selection when querying the requested trading environment.")
    return parser


def _read_account_summary(*, trd_env: str = "REAL", live_strict: bool = False):
    provider = FutuAccountProvider(
        live_strict=bool(live_strict),
        expect_trd_env=str(trd_env or "REAL").upper(),
    )
    return provider.get_summary()


def main() -> int:
    args = build_parser().parse_args()

    try:
        summary = _read_account_summary(
            trd_env=str(args.trd_env or "REAL").upper(),
            live_strict=bool(args.live_strict),
        )
    except Exception as exc:  # pragma: no cover - depends on OpenD availability
        print(f"[holdings_knot_review] failed to read account: {exc}", file=sys.stderr)
        return 2

    masked = mask_account_summary(summary)
    masked_positions = list(masked.get("positions") or [])
    rows = build_holdings_rows(masked_positions)
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched_rows = score_candidate_rows(rows, mode="dynamic", generated_at=generated_at)

    advice_by_symbol: dict[str, dict] = {}
    if not args.skip_knot and masked_positions:
        try:
            advice_by_symbol = call_knot_holdings_advice(masked_positions)
        except KnotPickError as exc:
            print(f"[holdings_knot_review] knot error: {exc}", file=sys.stderr)
            return 2

    merged = merge_holdings_advice(masked_positions, enriched_rows, advice_by_symbol)
    result = HoldingsReviewResult(
        generated_at=generated_at,
        env=str(masked.get("env") or "?"),
        position_count=int(masked.get("position_count") or 0),
        rows=merged,
    )

    print(format_holdings_review(result))

    if not args.dry_run:
        out_path = resolve_output_path(args.output, default_filename="holdings_knot_review.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": generated_at,
            "env": result.env,
            "position_count": result.position_count,
            "rows": result.rows,
            "knot_skipped": bool(args.skip_knot),
            "wrote_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[holdings_knot_review] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
