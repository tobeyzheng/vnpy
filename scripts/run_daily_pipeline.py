from __future__ import annotations

import argparse
from pathlib import Path

from run_market_recap import main as recap_main
from run_midday_report import main as midday_main
from run_premarket_report import run_market


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["premarket", "midday", "recap", "healthcheck", "brief"], required=True)
    parser.add_argument("--market", choices=["us", "hong_kong", "a_share"])
    args = parser.parse_args()

    if args.mode == "premarket":
        if not args.market:
            raise SystemExit("--market is required for premarket mode")
        path = run_market(args.market)
        print(path)
        return

    if args.mode == "midday":
        midday_main()
        return

    if args.mode == "recap":
        recap_main()
        return

    raise SystemExit("unsupported mode")


if __name__ == "__main__":
    main()
