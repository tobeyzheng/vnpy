#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry — phase2 strategy self-optimization closed loop.

Boundaries
----------
- This runner is a *pure local backtest* loop. It MUST NOT connect to
  OpenD or any remote broker. ``phase2.optimize`` enforces that no
  ``futu`` / ``phase2.live.*`` modules are imported on the path here.
- All artefacts land under
  ``state/runs/phase2_strategy_self_optimize/<session_id>/``.
- ``--respect-live-submit`` is intentionally NOT exposed: trials are
  hard-wired to ``force_live_submit=True`` so the strategy actually
  emits BUY/SELL intents into the in-memory adapter.

Usage::

    python3 phase2/runners/run_phase2_strategy_self_optimize.py \\
        --max-iters 10 --trials-per-iter 4 \\
        --start 2021-05-23 --end 2026-05-22

    # Print cumulative leaderboard for an existing session:
    python3 phase2/runners/run_phase2_strategy_self_optimize.py \\
        --print-leaderboard <session_id>

    # Dry-run: skip backtests, only generate proposals + skeletons:
    python3 phase2/runners/run_phase2_strategy_self_optimize.py \\
        --dry-run --max-iters 2 --trials-per-iter 2 \\
        --start 2025-01-02 --end 2025-01-10
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phase2.optimize import assert_no_live_imports  # noqa: E402
from phase2.optimize.coordinator import (  # noqa: E402
    CoordinatorConfig,
    _load_pool_symbols,
    run_session,
)
from phase2.optimize.evaluator import StopRules  # noqa: E402
from phase2.optimize.search_space import load_search_space  # noqa: E402
from phase2.optimize.session import SessionPaths  # noqa: E402
from phase2.optimize.trial_runner import TrialRunConfig  # noqa: E402

import yaml  # noqa: E402


PLAN_NAME = "phase2_strategy_self_optimize"
DEFAULT_POOL_CONFIG = (
    _REPO_ROOT / "phase2" / "strategy" / "config" / "pool_config_fixed.yaml"
)
DEFAULT_SEARCH_SPACE = (
    _REPO_ROOT / "phase2" / "strategy" / "config" / "optimize_search_space.yaml"
)
DEFAULT_STRATEGY_PATH = (
    _REPO_ROOT / "phase2" / "strategy" / "us_multi_symbol_phase2_strategy_futumd.py"
)


logger = logging.getLogger("phase2.run_strategy_self_optimize")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_date(s: str) -> datetime.date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="phase2 strategy self-optimization (Optimizer + "
                    "Evaluator double-subagent closed loop). Pure local "
                    "backtest path; never connects to OpenD.",
    )
    p.add_argument("--max-iters", type=int, default=10)
    p.add_argument("--trials-per-iter", type=int, default=4)
    p.add_argument("--top-k", type=int, default=2)

    p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--pool-config", default=str(DEFAULT_POOL_CONFIG))
    p.add_argument("--search-space", default=str(DEFAULT_SEARCH_SPACE))
    p.add_argument("--strategy-path", default=str(DEFAULT_STRATEGY_PATH))
    p.add_argument("--rate", type=float, default=0.0003)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--init-cash", type=float, default=100_000.0)
    p.add_argument("--annual-trading-days", type=int, default=252)
    p.add_argument("--exchange", default="SMART")
    p.add_argument("--interval", default="d", choices=["d", "1h", "1m"])

    p.add_argument("--max-runtime-min", type=float, default=90.0)
    p.add_argument("--min-delta", type=float, default=0.5)
    p.add_argument("--patience", type=int, default=3)

    p.add_argument("--score-formula", default=None,
                   help="optional path to a YAML overriding the default "
                        "evaluator score formula")
    p.add_argument("--llm-optimizer", action="store_true",
                   help="enable optional LLM proposer (degrades to local "
                        "rules on any failure)")
    p.add_argument("--llm-evaluator", action="store_true",
                   help="enable optional LLM reviewer (notes only; never "
                        "alters ranking)")

    p.add_argument("--dry-run", action="store_true",
                   help="produce proposals and trial skeletons, but skip "
                        "backtest execution.")
    p.add_argument("--resume", default=None,
                   help="continue an existing session_id; never overwrite "
                        "previous iter dirs")
    p.add_argument("--session-id", default=None,
                   help="override the auto-generated session_id "
                        "(advanced; mainly for tests)")
    p.add_argument("--print-leaderboard", default=None,
                   help="print the cumulative leaderboard of <session_id> "
                        "and exit; does not run any backtest or LLM call")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _print_leaderboard(session_id: str) -> int:
    sess = SessionPaths.create(session_id=session_id)
    if not sess.session_dir.exists():
        print(f"session not found: {sess.session_dir}", file=sys.stderr)
        return 2
    rows: list[dict[str, Any]] = []
    for child in sorted(sess.session_dir.iterdir()):
        if not (child.is_dir() and child.name.startswith("iter_")):
            continue
        lb = child / "leaderboard.csv"
        if not lb.exists():
            continue
        with lb.open("r", encoding="utf-8", newline="") as fh:
            rows.extend(csv.DictReader(fh))
    if not rows:
        print(f"(no leaderboard rows under {sess.session_dir})")
        return 0
    fields = list(rows[0].keys())
    print("\t".join(fields))
    rows.sort(
        key=lambda r: (
            -float(r["score"]) if r.get("score") not in (None, "", "None") else 0.0,
        ),
    )
    for r in rows:
        print("\t".join(str(r.get(k, "")) for k in fields))
    return 0


def _load_score_formula(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path).resolve()
    return yaml.safe_load(p.read_text(encoding="utf-8")) or None


def _build_llm_hooks(args: argparse.Namespace):
    proposer = None
    reviewer = None
    if args.llm_optimizer or args.llm_evaluator:
        try:
            from phase2.optimize import llm_bridge  # type: ignore

            if args.llm_optimizer:
                proposer = llm_bridge.llm_propose
            if args.llm_evaluator:
                reviewer = llm_bridge.llm_review
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "llm_bridge unavailable (%s); ignoring --llm-* flags this run",
                exc,
            )
    return proposer, reviewer


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.verbose)
    assert_no_live_imports()

    if args.print_leaderboard:
        return _print_leaderboard(args.print_leaderboard)

    if not args.start or not args.end:
        print("error: --start and --end are required (unless --print-leaderboard)",
              file=sys.stderr)
        return 2

    pool_config_path = Path(args.pool_config).resolve()
    search_space_path = Path(args.search_space).resolve()
    pool_symbols, _ = _load_pool_symbols(pool_config_path)
    if not pool_symbols:
        print(f"error: empty pool from {pool_config_path}", file=sys.stderr)
        return 2

    space = load_search_space(search_space_path)

    score_formula = _load_score_formula(args.score_formula)
    coord_cfg = CoordinatorConfig(
        max_iters=int(args.max_iters),
        trials_per_iter=int(args.trials_per_iter),
        top_k=int(args.top_k),
        stop_rules=StopRules(
            min_delta=float(args.min_delta),
            patience=int(args.patience),
            max_iters=int(args.max_iters),
            max_runtime_min=float(args.max_runtime_min),
        ),
        score_formula=score_formula or {},
        dry_run=bool(args.dry_run),
        resume=bool(args.resume),
        pool_config_path=pool_config_path,
        search_space_path=search_space_path,
    )
    if not coord_cfg.score_formula:
        # fall back to evaluator default inside coordinator/evaluator paths
        from phase2.optimize.evaluator import DEFAULT_SCORE_FORMULA
        coord_cfg.score_formula = dict(DEFAULT_SCORE_FORMULA)

    trial_cfg = TrialRunConfig(
        pool_symbols=tuple(pool_symbols),
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        init_cash=float(args.init_cash),
        fee_rate=float(args.rate),
        slippage=float(args.slippage),
        annual_trading_days=int(args.annual_trading_days),
        exchange_name=str(args.exchange),
        interval_value=str(args.interval),
        strategy_path=Path(args.strategy_path).resolve(),
    )

    session_id = args.resume or args.session_id
    session = SessionPaths.create(session_id=session_id)

    proposer, reviewer = _build_llm_hooks(args)

    cli_args = {
        k: (str(v) if isinstance(v, Path) else v)
        for k, v in vars(args).items()
    }

    result = run_session(
        session=session,
        space=space,
        trial_cfg=trial_cfg,
        coord_cfg=coord_cfg,
        cli_args=cli_args,
        llm_proposer=proposer,
        llm_reviewer=reviewer,
    )

    # Try to render the report; absent reporter is non-fatal.
    try:
        from phase2.optimize import reporter  # type: ignore
        reporter.render_session_report(result)
    except Exception as exc:  # noqa: BLE001
        logger.info("reporter not invoked (%s)", exc)

    print("=" * 72)
    print("[run_phase2_strategy_self_optimize] DONE")
    print(f"  session_id        : {session.session_id}")
    print(f"  session_dir       : {session.session_dir}")
    print(f"  iterations        : {result.summary.iterations}")
    print(f"  total_trials      : {result.summary.total_trials}")
    print(f"  best_score        : {result.summary.best_score}")
    print(f"  best_trial_path   : {result.summary.best_trial_path}")
    print(f"  stop_reason       : {result.summary.stop_reason}")
    print(f"  runtime_sec       : {result.summary.runtime_sec}")
    print("=" * 72)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
