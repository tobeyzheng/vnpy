"""CLI for pulling Futu OpenD K-lines into the local vnpy SQLite database.

Examples
--------
Pull daily bars (default) for HK Tencent + US Apple over a date range. The
manager automatically reuses already-cached bars and only requests gaps.

    python tmp/run_futu_data_pull.py \
        --symbols HK.00700,US.AAPL,SH.600519 \
        --start 2024-01-01 --end 2024-12-31

Pull 5-minute bars (intraday)::

    python tmp/run_futu_data_pull.py \
        --symbols HK.00700 --interval 5m \
        --start 2024-12-01 --end 2024-12-31

Show cache index status::

    python tmp/run_futu_data_pull.py --status

Force re-pull a symbol (ignore cache)::

    python tmp/run_futu_data_pull.py --symbols HK.00700 \
        --start 2024-01-01 --end 2024-12-31 --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running this script directly without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vnpy_futu_data import FutuDataManager  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull historical K-lines from Futu OpenD into vnpy SQLite database.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated futu codes, e.g. 'HK.00700,US.AAPL,SH.600519'",
    )
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--interval",
        type=str,
        default="1d",
        help="Interval alias: 1m / 5m / 15m / 30m / 60m / 1d (default: 1d)",
    )
    parser.add_argument(
        "--autype",
        type=str,
        default="qfq",
        choices=["qfq", "hfq", "none"],
        help="Adjustment type (default: qfq)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache and re-pull the entire requested range.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the cache index and exit (no pulling).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    mgr = FutuDataManager()
    try:
        if args.status:
            import json

            print(json.dumps(mgr.status(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        if not args.symbols or not args.start or not args.end:
            print("error: --symbols, --start, --end are required (or use --status)", file=sys.stderr)
            return 2

        codes = [s.strip() for s in args.symbols.split(",") if s.strip()]
        results = mgr.pull(
            futu_codes=codes,
            start=args.start,
            end=args.end,
            interval_alias=args.interval,
            autype=args.autype,
            force=args.force,
        )

        # Compact summary
        print()
        print(f"{'symbol':<14}{'kl':<8}{'cache_hit':<11}{'fetched':<10}{'final_range'}")
        print("-" * 70)
        for r in results:
            rng = f"{r.final_start} ~ {r.final_end}"
            print(f"{r.futu_code:<14}{r.kl_type:<8}{str(r.cache_hit):<11}{r.fetched_bars:<10}{rng}")
        return 0
    finally:
        mgr.close()


if __name__ == "__main__":
    raise SystemExit(main())
