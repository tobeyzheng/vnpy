"""CLI entry for phase2 multi-symbol US daily live trading.

Three execution modes (selected by ``--futu-env`` + ``--live-submit``):

- ``dry_run``  : no OpenD connection, no real orders. Bars come from the local
                 vnpy database (same source the multi-backtest CLI uses).
- ``futu_sim`` : OpenD-connected SIM account; orders go to ``TrdEnv.SIMULATE``.
- ``futu_real``: OpenD-connected REAL account; requires the full 6-switch set
                 (see ``phase2.live.safety``).

Products land under
``state/runs/phase2_live/{dry_run|futu_sim|futu_real}/<run_id>/`` per the
requirements. ``--run-id`` defaults to ``<execution_env>_<UTC timestamp>``.

This entry deliberately uses lazy imports for vnpy.trader.database and the
``futu`` package so ``--help`` returns instantly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

# Allow ``python3 phase2/runners/run_phase2_live_daily.py`` from repo root
# without setting PYTHONPATH (matches phase2/runners/run_phase2_backtest.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_phase2_live_daily",
        description="phase2 multi-symbol US daily live trading runner",
    )
    p.add_argument("--futu-env", choices=["模拟", "真实"], default=None,
                   help="Connect to OpenD SIM (模拟) or REAL (真实) account; "
                        "omit + skip --live-submit for a pure dry run.")
    p.add_argument("--futu-market", default="US",
                   help="Trading market (only US is supported in this milestone).")
    p.add_argument("--rebalance-time", default="15:55",
                   help="Local time (HH:MM) at which to trigger the single rebalance.")
    p.add_argument("--session-tz", default="America/New_York",
                   help="IANA tz name for --rebalance-time.")
    p.add_argument("--live-submit", action="store_true", default=False,
                   help="When set, approved orders are forwarded to the broker. "
                        "MUST be present together with --futu-env to leave dry mode.")
    p.add_argument("--respect-live-submit", action="store_true", default=False,
                   help="If set, the strategy file's own LIVE_SUBMIT flag is "
                        "honoured (default: phase2 live always sets it to True "
                        "before handle_data()).")
    p.add_argument("--pool-config",
                   default="/projects/vnpy/phase2/strategy/config/pool_config.yaml",
                   help="Path to the phase2 pool config YAML.")
    p.add_argument("--strategy-path",
                   default="/projects/vnpy/phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py",
                   help="Path to the futumd strategy file (must remain unmodified).")
    p.add_argument("--init-cash", type=float, default=100_000.0,
                   help="Initial USD cash used for sizing (no real account funds).")
    p.add_argument("--max-single-position-pct", type=float, default=0.20)
    p.add_argument("--max-daily-new-position-pct", type=float, default=0.30)
    p.add_argument("--max-market-exposure-pct", type=float, default=0.95)
    p.add_argument("--max-drawdown-pct", type=float, default=0.20)
    p.add_argument("--max-order-value", type=float, default=20_000.0)
    p.add_argument("--auto-cancel-on-eod", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Cancel all still-open orders at end-of-day "
                        "(REAL mode forces this on regardless).")
    p.add_argument("--post-rebalance-wait-seconds", type=float, default=120.0)
    p.add_argument("--max-runtime-seconds", type=float, default=1800.0)
    p.add_argument("--bar-warmup", type=int, default=60)
    p.add_argument("--run-id", default=None,
                   help="Optional run id; defaults to <execution_env>_<UTC ts>.")
    p.add_argument("--rebalance-now", action="store_true", default=False,
                   help="Skip the rebalance-time wait loop (useful for dry-run smoke).")
    return p


# ---------------------------------------------------------------------------
# Bars providers
# ---------------------------------------------------------------------------

def _vnpy_bars_provider(symbols: Iterable[str], *, n_bars: int):
    """Provider for dry_run: read the latest ``n_bars`` daily K from vnpy DB."""
    # Lazy import to keep --help fast.
    from vnpy.trader.constant import Exchange, Interval
    from vnpy.trader.database import get_database
    from datetime import datetime

    db = get_database()
    out: dict[str, list] = {}
    for sym in symbols:
        # symbols look like "US.AAPL" → strip the prefix.
        ticker = sym.split(".", 1)[-1]
        bars = db.load_bar_data(
            symbol=ticker,
            exchange=Exchange.NASDAQ,  # phase2 currently uses NASDAQ tier
            interval=Interval.DAILY,
            start=datetime(1970, 1, 1),
            end=datetime(2099, 1, 1),
        )
        if not bars:
            out[sym] = []
            continue
        bars = bars[-n_bars:]
        out[sym] = [(b.datetime.strftime("%Y-%m-%d"), float(b.open_price),
                     float(b.high_price), float(b.low_price),
                     float(b.close_price), float(b.volume)) for b in bars]
    return out


def _futu_bars_provider(broker, *, n_bars: int):
    """Provider that uses the broker's quote_ctx via get_cur_kline."""
    def _provider(symbols):
        # Lazy import.
        import futu as ft
        out: dict[str, list] = {}
        if broker._quote_ctx is None:
            return {s: [] for s in symbols}
        for sym in symbols:
            ret, data = broker._quote_ctx.get_cur_kline(
                code=sym, num=n_bars,
                ktype=ft.KLType.K_DAY, autype=ft.AuType.QFQ,
            )
            if ret != ft.RET_OK or not hasattr(data, "to_dict"):
                out[sym] = []
                continue
            rows = data.to_dict("records")
            out[sym] = [
                (str(r.get("time_key", ""))[:10],
                 float(r.get("open", 0)), float(r.get("high", 0)),
                 float(r.get("low", 0)), float(r.get("close", 0)),
                 float(r.get("volume", 0)))
                for r in rows
            ]
        return out
    return _provider


# ---------------------------------------------------------------------------
# Pool loading (cheap; no DB)
# ---------------------------------------------------------------------------

def _load_pool_symbols(pool_config: str) -> list[str]:
    # Lazy import: pool_loader is light.
    from phase2.strategy.pool_loader import load_pool_config
    cfg = load_pool_config(pool_config)
    return [s.symbol for s in cfg.symbols]


# ---------------------------------------------------------------------------
# Run id + products dir
# ---------------------------------------------------------------------------

def _resolve_run_id(execution_env: str, override: str | None) -> str:
    if override:
        return override
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{execution_env}_{ts}"


def _resolve_products_dir(execution_env: str, run_id: str) -> Path:
    base = Path("/projects/vnpy/state/runs/phase2_live") / execution_env / run_id
    return base


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --- 6-switch safety check (per requirements §6) ----------------------
    from phase2.live.safety import validate_safety
    decision = validate_safety(
        futu_env_cli=args.futu_env,
        live_submit=args.live_submit,
        env=os.environ,
    )
    sys.stderr.write(decision.explain() + "\n")
    if not decision.allowed:
        return 2
    execution_env = decision.execution_env  # "dry_run" | "futu_sim" | "futu_real"

    if args.futu_market.upper() != "US":
        sys.stderr.write(f"phase2 live milestone only supports US, got {args.futu_market}\n")
        return 2

    # --- Resolve run paths ------------------------------------------------
    run_id = _resolve_run_id(execution_env, args.run_id)
    products_dir = _resolve_products_dir(execution_env, run_id)
    products_dir.mkdir(parents=True, exist_ok=True)
    orders_dir = products_dir / "orders"
    events_path = products_dir / "events.jsonl"
    reconcile_dir = products_dir / "reconcile"
    sys.stderr.write(f"phase2 live products → {products_dir}\n")

    # --- Pool ------------------------------------------------------------
    try:
        symbols = _load_pool_symbols(args.pool_config)
    except Exception as exc:
        sys.stderr.write(f"failed to load pool: {exc}\n")
        return 3
    if not symbols:
        sys.stderr.write("pool is empty; nothing to trade\n")
        return 4

    # --- Wire components --------------------------------------------------
    from phase2.live.guards import (
        EventLogger, GatePipeline, IdempotencyGate, PortfolioRiskGate,
        PortfolioRiskLimits, ReconciliationGate, RiskGate,
    )
    from phase2.live.order_state import OrderStateStoreExt
    from phase2.live.risk import ClassicOrderRiskManager, Phase2RiskConfig
    from phase2.live.runner import DailyLiveRebalanceRunner, RunnerConfig

    store = OrderStateStoreExt(orders_dir)
    events = EventLogger(events_path)

    risk_cfg = Phase2RiskConfig(
        max_position_pct=args.max_single_position_pct,
        max_order_value=args.max_order_value,
    )
    gates = [
        IdempotencyGate(store),
        ReconciliationGate(
            reconcile_dir,
            require_report=False,  # cold start tolerated; runner writes one mid-cycle
        ),
        RiskGate(ClassicOrderRiskManager(risk_cfg)),
        PortfolioRiskGate(PortfolioRiskLimits(
            max_single_position_pct=args.max_single_position_pct,
            max_daily_new_position_pct=args.max_daily_new_position_pct,
            max_market_exposure_pct=args.max_market_exposure_pct,
            max_drawdown_pct=args.max_drawdown_pct,
        )),
    ]
    pipeline = GatePipeline(gates, store=store, events=events)

    # --- Broker ----------------------------------------------------------
    broker: object
    if execution_env == "dry_run":
        # In dry mode we still need a LiveBroker-shaped object so the runner
        # can connect/place/cancel without errors; we use the test stub from
        # phase2.live.tests.test_runner only when phase2.live.tests is on the
        # path. To stay self-contained, define a minimal NoopBroker here.
        broker = _NoopBroker()
        bars_provider = lambda syms: _vnpy_bars_provider(syms, n_bars=args.bar_warmup)
    else:
        from phase2.live.futu_broker import FutuBroker, FutuBrokerConfig
        broker = FutuBroker(FutuBrokerConfig(
            host=os.environ.get("FUTU_HOST", "127.0.0.1"),
            port=int(os.environ.get("FUTU_PORT", "11111")),
            market="US",
            execution_env=execution_env,
            trd_password=os.environ.get("FUTU_TRADE_PASSWORD"),
        ))
        bars_provider = _futu_bars_provider(broker, n_bars=args.bar_warmup)

    cfg = RunnerConfig(
        strategy_path=Path(args.strategy_path),
        symbols=symbols,
        init_cash=float(args.init_cash),
        rebalance_date=time.strftime("%Y-%m-%d"),
        execution_env=execution_env,
        products_dir=products_dir,
        live_submit=args.live_submit,
        auto_cancel_on_eod=args.auto_cancel_on_eod,
        bar_warmup=args.bar_warmup,
        post_rebalance_wait_seconds=args.post_rebalance_wait_seconds,
        max_runtime_seconds=args.max_runtime_seconds,
        rebalance_time_epoch=None if args.rebalance_now
                              else _resolve_rebalance_time(args),
        # In dry_run the broker is a NoopBroker, so we flip the strategy's
        # LIVE_SUBMIT flag to True (unless --respect-live-submit) to walk the
        # full intent→gate chain. ``live_submit`` itself stays False so the
        # NoopBroker is never invoked.
        strategy_live_submit_override=(
            None if args.respect_live_submit
            else (True if execution_env == "dry_run" else None)
        ),
    )

    runner = DailyLiveRebalanceRunner(
        cfg, broker=broker, store=store, pipeline=pipeline, events=events,
        bars_provider=bars_provider,
    )
    report = runner.run()
    sys.stderr.write(
        f"phase2 live finished: env={execution_env} pool={len(symbols)} "
        f"intents={report.intents_emitted} approved={report.intents_approved} "
        f"fills={report.fill_count} cancels={report.cancel_count}\n"
    )
    return 0


def _resolve_rebalance_time(args) -> float:
    """Convert (rebalance_time, session_tz) to a UTC epoch timestamp.

    On systems without zoneinfo (very old Pythons) we fall back to local
    time interpretation; phase2 runs on Python 3.11 so zoneinfo is always
    present.
    """
    from datetime import datetime, time as dtime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(args.session_tz)
    except Exception:
        tz = None
    hh, mm = args.rebalance_time.split(":")
    today = datetime.now(tz).date() if tz is not None else datetime.now().date()
    target = datetime.combine(today, dtime(int(hh), int(mm)), tzinfo=tz)
    return target.timestamp()


# ---------------------------------------------------------------------------
# Minimal in-process broker for dry_run mode
# ---------------------------------------------------------------------------

class _NoopBroker:
    """LiveBroker stub for dry_run: never contacts OpenD."""

    execution_env = "dry_run"

    def __init__(self):
        self._handler = None

    def connect(self): pass
    def disconnect(self): pass
    def unlock_trade(self): return True

    def query_account(self):
        from phase2.live.broker import BrokerAccount
        return BrokerAccount(cash=0.0, market_value=0.0, total_assets=0.0)

    def query_positions(self): return []

    def place_order(self, intent):  # pragma: no cover - never reached in dry mode
        from phase2.live.broker import BrokerOrderAck
        return BrokerOrderAck(intent.request_id, "", 0, "rejected", "dry_run_noop")

    def cancel_order(self, broker_order_id):  # pragma: no cover - dry mode
        return True

    def subscribe_quote(self, symbols): pass

    def register_order_handler(self, callback):
        self._handler = callback


if __name__ == "__main__":  # pragma: no cover - thin CLI entry
    sys.exit(main())
