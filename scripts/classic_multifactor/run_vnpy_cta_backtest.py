from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.cta_backtest import ClassicCtaBacktestRunner
from scripts.classic_multifactor.data import VnpyBarRepository, parse_us_symbol


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classic multifactor official vn.py CTA backtest")
    parser.add_argument("--symbol", default="NVDA.US")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--capital", type=float, default=20000.0)
    parser.add_argument("--interval", choices=["1d", "1m"], default="1d")
    parser.add_argument("--rate", type=float, default=0.0003)

    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--size", type=int, default=1)
    parser.add_argument("--pricetick", type=float, default=0.01)
    parser.add_argument("--fast-window", type=int, default=10)
    parser.add_argument("--slow-window", type=int, default=60)
    parser.add_argument("--momentum-window", type=int, default=20)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--entry-score", type=float, default=0.62)

    parser.add_argument("--exit-score", type=float, default=0.46)
    parser.add_argument("--max-order-value", type=float, default=5000.0)

    parser.add_argument("--signal-interval-minutes", type=int, default=1)
    parser.add_argument("--confirm-bars", type=int, default=1)
    parser.add_argument("--min-volume-ratio", type=float, default=0.0)
    parser.add_argument("--min-atr-pct", type=float, default=0.0)
    parser.add_argument("--min-trend-score", type=float, default=0.60)

    parser.add_argument("--stop-atr", type=float, default=0.0)
    parser.add_argument("--take-profit-atr", type=float, default=0.0)
    parser.add_argument("--trailing-atr", type=float, default=0.0)
    parser.add_argument("--max-intraday-trades", type=int, default=0)
    parser.add_argument("--entry-cooldown-minutes", type=int, default=0)
    parser.add_argument("--min-hold-minutes", type=int, default=0)
    parser.add_argument("--no-new-entry-after", default="")

    parser.add_argument("--output", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _symbol, vt_symbol, _futu_code = parse_us_symbol(args.symbol)
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    VnpyBarRepository(fetch_futu_history=True).load_us_bars(args.symbol, start, end, args.interval)

    setting = {
        "fast_window": args.fast_window,
        "slow_window": args.slow_window,
        "momentum_window": args.momentum_window,
        "atr_window": args.atr_window,
        "entry_score": args.entry_score,

        "exit_score": args.exit_score,
        "max_order_value": args.max_order_value,

        "capital": args.capital,
        "data_interval": args.interval,
        "signal_interval_minutes": args.signal_interval_minutes,
        "confirm_bars": args.confirm_bars,
        "min_volume_ratio": args.min_volume_ratio,
        "min_atr_pct": args.min_atr_pct,
        "min_trend_score": args.min_trend_score,

        "stop_atr": args.stop_atr,
        "take_profit_atr": args.take_profit_atr,
        "trailing_atr": args.trailing_atr,
        "max_intraday_trades": args.max_intraday_trades,
        "entry_cooldown_minutes": args.entry_cooldown_minutes,
        "min_hold_minutes": args.min_hold_minutes,
        "no_new_entry_after": args.no_new_entry_after,
    }

    stats, _engine = ClassicCtaBacktestRunner().run(
        vt_symbol=vt_symbol,
        interval=args.interval,
        start=start,

        end=end,
        capital=args.capital,
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        setting=setting,
    )
    report = {"strategy": "classic_multifactor_no_llm_vnpy_cta", "symbol": args.symbol, "vt_symbol": vt_symbol, "interval": args.interval, "setting": setting, "stats": stats}

    out = Path(args.output) if args.output else REPO_ROOT / "state" / "runs" / "classic_multifactor" / "vnpy_cta_backtest_report.json"
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(out)
    print(json.dumps(report["stats"], ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
