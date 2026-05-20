# -*- coding: utf-8 -*-
"""Phase-② multi-symbol *real* backtest runner (uses local vnpy database).

Differs from ``run_phase2_backtest.py`` (a synthetic dry-run smoke):
this runner replays real daily bars from the local vnpy database
through the per-symbol bucketed adapter and the futumd-compatible
strategy, with next-bar-open settlement and a portfolio-level cash
account.

Boundaries:
- NEVER connects to OpenD or any remote service.
- NEVER flips ``LIVE_SUBMIT``.
- Reads pool_config.yaml only; never writes back.
- Output goes under ``state/runs/phase2_multi_backtest/<run_id>/``.

Usage::

    python3 phase2/runners/run_phase2_multi_backtest.py \\
        --start 2025-05-21 --end 2026-05-20 \\
        --init-cash 1000000 --run-id smoke_2025_2026
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vnpy.trader.constant import Exchange, Interval  # noqa: E402

from phase2.backtest.portfolio_backtest_engine import (  # noqa: E402
    PortfolioBacktestEngine,
)
from phase2.strategy.pool_loader import load_pool_config  # noqa: E402


PLAN_NAME = "phase2_multi_backtest"
DEFAULT_POOL_CONFIG = (
    _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_config.yaml"
)
DEFAULT_STRATEGY_PATH = (
    _REPO_ROOT / "phase2" / "strategy"
    / "us_multi_symbol_phase2_strategy_futumd.py"
)
DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "state" / "runs" / PLAN_NAME

logger = logging.getLogger("phase2.run_multi_backtest")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_run_id(arg: Optional[str]) -> str:
    if arg:
        return arg
    return datetime.now(timezone.utc).strftime("multi_%Y%m%dT%H%M%SZ")


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase-② multi-symbol real backtest runner "
                    "(local vnpy database; never connects to OpenD).",
    )
    p.add_argument("--pool-config", default=str(DEFAULT_POOL_CONFIG))
    p.add_argument("--strategy-path", default=str(DEFAULT_STRATEGY_PATH))
    p.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--init-cash", type=float, default=1_000_000.0)
    p.add_argument("--rate", type=float, default=0.0003,
                   help="Commission rate per notional")
    p.add_argument("--slippage", type=float, default=0.0,
                   help="Slippage as a fraction of next-bar open")
    p.add_argument("--exchange", default="SMART",
                   help="vnpy Exchange enum name (default: SMART)")
    p.add_argument("--interval", default="d",
                   choices=["d", "1h", "1m"],
                   help="vnpy Interval value (default: d)")
    p.add_argument("--annual-trading-days", type=int, default=252)
    p.add_argument("--run-id", default=None)
    p.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _resolve_interval(value: str) -> Interval:
    for iv in Interval:
        if iv.value == value:
            return iv
    raise ValueError(f"unknown interval value: {value!r}")


def _resolve_exchange(name: str) -> Exchange:
    try:
        return Exchange[name.upper()]
    except KeyError as exc:
        raise ValueError(f"unknown exchange: {name!r}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    cfg = load_pool_config(args.pool_config)
    pool_symbols = cfg.symbol_list()
    logger.info(
        "loaded pool: %d symbols, currency=%s",
        len(pool_symbols), cfg.currency,
    )

    start_dt = _parse_date(args.start).date()
    end_dt = _parse_date(args.end).date()

    run_id = _build_run_id(args.run_id)
    engine = PortfolioBacktestEngine(
        pool_symbols=pool_symbols,
        start=start_dt,
        end=end_dt,
        init_cash=float(args.init_cash),
        exchange=_resolve_exchange(args.exchange),
        interval=_resolve_interval(args.interval),
        fee_rate=float(args.rate),
        slippage=float(args.slippage),
        annual_trading_days=int(args.annual_trading_days),
    )

    logger.info(
        "running multi-symbol backtest: pool=%d, %s -> %s, init_cash=%.0f, "
        "fee_rate=%.4f, slippage=%.4f, run_id=%s",
        len(pool_symbols), start_dt, end_dt, args.init_cash,
        args.rate, args.slippage, run_id,
    )

    result = engine.run(
        strategy_path=args.strategy_path,
        run_id=run_id,
        output_root=args.output_root,
    )

    print("=" * 72)
    print(f"[run_phase2_multi_backtest] OK")
    print(f"  run_id            : {result.run_id}")
    print(f"  output_dir        : {result.output_dir}")
    print(f"  pool              : {len(result.pool)} symbols")
    print(f"  init_cash         : {result.init_cash:,.2f}")
    print(f"  final_nav         : {result.final_nav:,.2f}")
    print(f"  total_return_pct  : {result.total_return_pct:.4f}")
    print(f"  annualised_pct    : {result.annualised_return_pct:.4f}")
    print(f"  max_drawdown_pct  : {result.max_drawdown_pct:.4f}")
    print(f"  trade_count       : {result.trade_count}")
    print(f"  win / loss        : {result.win_count} / {result.loss_count}")
    print("=" * 72)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
