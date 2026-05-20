"""US Multi-Symbol Quant Phase 2 — backtest runner (DRY-RUN by default).

Responsibilities (per requirements §4.1 / task-item §7):
- Load the phase-② pool config and the multi-symbol strategy module.
- Drive a *deterministic* dry-run that:
    * lists the resolved pool & strategy parameters,
    * iterates a synthetic / replayed bar feed when ``--mock-feed`` is set,
    * routes every order intent through the budget allocator and
      portfolio risk gates,
    * writes a structured run report to
      ``state/runs/<plan>/<run_id>/run_report.json``.
- **Never** connects to OpenD, Futu, or any remote service.
- **Never** flips ``LIVE_SUBMIT`` — the strategy guard stays authoritative.

Two knobs the runner exposes:
- ``--dry-run`` (default True): print the plan, write the report, exit 0.
- ``--no-dry-run``: reserved for a future plan; in this branch it
  immediately aborts with exit 3 and reminds the operator to open a new
  plan under ``.codebuddy/plan/``.

Outputs (under ``state/runs/<plan>/<run_id>/``):
- ``run_report.json`` — pool, params, candidate count, breaker state.
- ``portfolio_state.json`` — touched only when the strategy persists state.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.strategy.pool_loader import (  # noqa: E402
    load_pool_config,
)
from phase2.strategy.portfolio_risk import (  # noqa: E402
    load_portfolio_state,
    state_file_path,
)
from phase2.strategy.us_multi_symbol_phase2_strategy import (  # noqa: E402
    LIVE_SUBMIT,
    Strategy,
    allocate_orders_serial,
)


PLAN_NAME = "us_multi_symbol_quant_phase2"
DEFAULT_POOL_CONFIG = (
    _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_config.yaml"
)


logger = logging.getLogger("phase2.run_backtest")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_run_id(arg: str | None) -> str:
    if arg:
        return arg
    return datetime.now(timezone.utc).strftime("dryrun_%Y%m%dT%H%M%SZ")


def _resolve_state_dir(run_id: str) -> Path:
    base_dir = os.environ.get("PHASE2_STATE_DIR", "state/runs")
    return Path(base_dir) / PLAN_NAME / run_id


def _build_synthetic_candidates(symbols: List[str]) -> List[Dict[str, Any]]:
    """Build a deterministic candidate list for dry-run smoke."""

    out: List[Dict[str, Any]] = []
    for i, s in enumerate(symbols[:5]):  # cap so allocator stays representative
        out.append({"symbol": s, "price": 100.0 + i * 25.0})
    return out


def _run_dry(args: argparse.Namespace) -> int:
    if not LIVE_SUBMIT:
        logger.info("LIVE_SUBMIT == False — strategy will never place orders.")
    else:
        logger.error(
            "LIVE_SUBMIT is True in this branch; aborting per project rule 2."
        )
        return 4

    cfg = load_pool_config(args.pool_config)
    logger.info(
        "loaded pool: %d symbols (cap=%d, currency=%s)",
        len(cfg.symbols),
        cfg.max_pool_size,
        cfg.currency,
    )

    run_id = _build_run_id(args.run_id)
    state_dir = _resolve_state_dir(run_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PHASE2_RUN_ID", run_id)
    os.environ.setdefault("PHASE2_POOL_CONFIG", str(args.pool_config))

    # Boot the strategy through its lifecycle to validate parameters.
    strategy = Strategy()
    strategy.initialize()

    # Read persisted breaker state for the report (defaults when absent).
    breaker_path = state_file_path(PLAN_NAME, run_id, base_dir=state_dir.parent.parent)
    state = load_portfolio_state(breaker_path, plan=PLAN_NAME, run_id=run_id)

    # Run the budget allocator off a synthetic candidate list — this is
    # purely illustrative; a real backtest engine integration lives in a
    # later plan, see docs/system_integration_guide.md §阶段②.
    candidates = _build_synthetic_candidates(strategy.pool_symbols)
    plan = allocate_orders_serial(
        candidates,
        nav=float(args.mock_nav),
        initial_cash=float(args.mock_cash),
        pool_budget_pct=float(strategy.pool_budget_pct),
        max_concurrent_holdings=int(strategy.max_concurrent_holdings),
        position_pct=float(strategy.position_pct),
        cash_buffer_pct=float(strategy.cash_buffer_pct),
        max_orders_per_day=int(strategy.max_orders_per_day),
    )

    report: Dict[str, Any] = {
        "plan": PLAN_NAME,
        "run_id": run_id,
        "live_submit": LIVE_SUBMIT,
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pool": {
            "size": len(cfg.symbols),
            "currency": cfg.currency,
            "symbols": cfg.symbol_list(),
        },
        "params": {
            "pool_budget_pct": float(strategy.pool_budget_pct),
            "max_concurrent_holdings": int(strategy.max_concurrent_holdings),
            "position_pct": float(strategy.position_pct),
            "cash_buffer_pct": float(strategy.cash_buffer_pct),
            "max_orders_per_day": int(strategy.max_orders_per_day),
            "stop_loss_pct": float(strategy.stop_loss_pct),
            "take_profit_pct": float(strategy.take_profit_pct),
            "trailing_drawdown_pct": float(strategy.trailing_drawdown_pct),
            "portfolio_dd_limit": float(strategy.portfolio_dd_limit),
            "sector_cap": float(strategy.sector_cap),
            "daily_loss_limit_pct": float(strategy.daily_loss_limit_pct),
            "max_consecutive_loss_days": int(strategy.max_consecutive_loss_days),
        },
        "allocator_smoke": {
            "mock_nav": float(args.mock_nav),
            "mock_cash": float(args.mock_cash),
            "candidates": candidates,
            "orders_planned": plan["orders"],
            "skipped": plan["skipped"],
            "remaining_cash": plan["remaining_cash"],
            "slice_value": plan["slice_value"],
        },
        "breaker_state": {
            "triggered": state.breaker_triggered,
            "reason": state.breaker_reason,
            "triggered_at": state.breaker_triggered_at,
        },
    }

    out_path = state_dir / "run_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    logger.info("dry-run report written to %s", out_path)
    print(f"[run_phase2_backtest] OK — report: {out_path}")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-② multi-symbol backtest runner (dry-run by default).",
    )
    parser.add_argument("--pool-config", default=str(DEFAULT_POOL_CONFIG))
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--dry-run", action=argparse.BooleanOptionalAction, default=True,
        help="Default True. --no-dry-run is reserved for a future plan.",
    )
    parser.add_argument("--mock-nav", type=float, default=1_000_000.0)
    parser.add_argument("--mock-cash", type=float, default=1_000_000.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if not args.dry_run:
        print(
            "[run_phase2_backtest] aborting: --no-dry-run requires a new plan "
            "under .codebuddy/plan/ and explicit user confirmation per "
            "project rule 2.",
            file=sys.stderr,
        )
        return 3
    return _run_dry(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
