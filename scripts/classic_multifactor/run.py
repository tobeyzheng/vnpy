from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified classic multifactor runner")
    parser.add_argument("mode", choices=["cta-backtest", "alpha-backtest", "legacy-backtest", "sim-once"])
    parser.add_argument("--symbol", default="NVDA.US")
    parser.add_argument("--symbols", nargs="+", default=[])
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--capital", type=float, default=20000.0)
    parser.add_argument("--interval", choices=["1d", "1m"], default="1d")
    parser.add_argument("--max-order-value", type=float, default=5000.0)
    parser.add_argument("--minute-profile", action="store_true", help="使用保守分钟级降频参数")
    parser.add_argument("--submit-sim", action="store_true")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    python = sys.executable
    minute_args = []
    if args.minute_profile:
        minute_args = [
            "--fast-window", "6",
            "--slow-window", "24",
            "--momentum-window", "12",
            "--atr-window", "14",
            "--signal-interval-minutes", "5",
            "--confirm-bars", "1",
            "--entry-score", "0.66",

            "--min-volume-ratio", "0.8",
            "--min-atr-pct", "0.0012",
            "--min-trend-score", "0.55",

            "--max-intraday-trades", "4",
            "--entry-cooldown-minutes", "30",
            "--min-hold-minutes", "20",
            "--no-new-entry-after", "15:30",
            "--stop-atr", "1.5",
            "--take-profit-atr", "2.5",
            "--trailing-atr", "2.0",
        ]
    if args.mode == "cta-backtest":
        script = REPO_ROOT / "scripts" / "classic_multifactor" / "run_vnpy_cta_backtest.py"
        cmd = [python, str(script), "--symbol", args.symbol, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital), "--max-order-value", str(args.max_order_value), *minute_args]

    elif args.mode == "alpha-backtest":

        script = REPO_ROOT / "scripts" / "classic_multifactor" / "run_alpha_backtest.py"
        symbols = args.symbols or [args.symbol]
        cmd = [python, str(script), "--symbols", *symbols, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital)]

    else:
        script = REPO_ROOT / "scripts" / "classic_multifactor" / "us_single_symbol_multifactor.py"
        mode = "backtest" if args.mode == "legacy-backtest" else "sim-once"
        cmd = [python, str(script), "--mode", mode, "--symbol", args.symbol, "--interval", args.interval, "--start", args.start, "--end", args.end, "--capital", str(args.capital), "--max-order-value", str(args.max_order_value), *minute_args]


        if args.submit_sim:
            cmd.append("--submit-sim")
    raise SystemExit(subprocess.call(cmd, cwd=str(REPO_ROOT)))


if __name__ == "__main__":
    main()
