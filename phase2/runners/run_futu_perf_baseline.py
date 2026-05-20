#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 — Futu platform performance baseline runner (skeleton).

Goal
----
Probe Futu platform's `handle_data` per-call latency, total backtest engine
runtime and serial order placement delay across 1 / 10 / 30 trigger symbols
on a short 30-trading-day window. Produces structured rows that downstream
report script can paste into
``docs/research/us_multi_symbol_quant/04_platform_performance_baseline.md``.

Safety
------
* Default mode is ``--dry-run`` (no Futu connection, no backtest run, no
  files written under ``state/runs/`` or ``tmp/data/``). Real execution is
  gated behind ``--execute`` AND requires explicit user confirmation per
  project rule 2.
* Script does NOT submit any SIM/REAL order, does NOT mutate
  ``pool_config.yaml`` and does NOT modify any file under ``tmp/``.
* When ``--execute`` is set without prior interactive confirmation, the
  script aborts with exit code 3.

Exit codes
----------
0  dry-run completed (or real run completed successfully)
2  argument error
3  execution attempted without confirmation gate
4  threshold breach: any tier's per-call ``handle_data`` >= ``--p95-threshold``
   (default 5.0 seconds) — caller should trigger pool downgrade to <=10.

Usage
-----
Dry run (default, safe)::

    python3 phase2/runners/run_futu_perf_baseline.py

Real run (must be preceded by user confirmation in chat)::

    python3 phase2/runners/run_futu_perf_baseline.py --execute --confirm

Notes
-----
The actual Futu-side measurement code is left as a TODO block: it will be
filled in only after the user confirms execution and the matching plan item
is unblocked. Until then the dry-run path is the only reachable code path,
and it merely prints what *would* be measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Default symbol tiers used to probe the platform. The 30-symbol tier is the
# one that determines whether phase 2 pool ceiling stays at 20 or downgrades
# to 10 (per requirement 5.3 in this plan).
DEFAULT_TIERS: List[int] = [1, 10, 30]
DEFAULT_WINDOW_DAYS: int = 30
DEFAULT_P95_THRESHOLD_SEC: float = 5.0


@dataclass(frozen=True)
class TierResult:
    """Structured per-tier measurement record."""

    n_symbols: int
    window_days: int
    handle_data_avg_ms: Optional[float]
    handle_data_p95_ms: Optional[float]
    engine_total_sec: Optional[float]
    serial_order_delay_ms: Optional[float]
    notes: str


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_futu_perf_baseline",
        description=(
            "Phase 2 Futu performance baseline probe — dry-run by default."
        ),
    )
    p.add_argument(
        "--tiers",
        type=str,
        default=",".join(str(t) for t in DEFAULT_TIERS),
        help="Comma-separated symbol-count tiers, default '1,10,30'.",
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="Backtest window length in trading days (default 30).",
    )
    p.add_argument(
        "--p95-threshold",
        type=float,
        default=DEFAULT_P95_THRESHOLD_SEC,
        help=(
            "If any tier's per-call handle_data p95 latency exceeds this "
            "threshold (seconds), exit with code 4 to trigger pool downgrade."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry-run only (default). No Futu connection, no backtest run.",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "Actually run the probe on Futu platform. Requires --confirm "
            "and explicit user confirmation in chat per project rule 2."
        ),
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Acknowledge that user confirmation has been recorded in chat.",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Optional JSON path for the structured baseline rows. Default "
            "is stdout only; if provided, parent dir must already exist."
        ),
    )
    return p


def _parse_tiers(s: str) -> List[int]:
    out: List[int] = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        n = int(tok)
        if n <= 0:
            raise ValueError(f"tier must be positive int, got {n}")
        if n > 50:
            # Per docs/research/us_multi_symbol_quant/02 — Futu single-strategy
            # ceiling is 50 trigger symbols.
            raise ValueError(
                f"tier {n} exceeds Futu single-strategy ceiling 50"
            )
        out.append(n)
    if not out:
        raise ValueError("at least one tier required")
    return out


def _dry_run_tier(n: int, window_days: int) -> TierResult:
    """Pretend to measure a tier — pure local stand-in for dry-run."""
    # Use a tiny CPU-only pause so the structure mirrors a real run without
    # producing fake numbers. We deliberately leave latency fields as None
    # so downstream reports cannot mistake placeholders for measurements.
    time.sleep(0.001)
    return TierResult(
        n_symbols=n,
        window_days=window_days,
        handle_data_avg_ms=None,
        handle_data_p95_ms=None,
        engine_total_sec=None,
        serial_order_delay_ms=None,
        notes="dry-run placeholder; real values pending user-confirmed run",
    )


def _real_run_tier(n: int, window_days: int) -> TierResult:
    """Real Futu-side measurement — intentionally unimplemented.

    This block is reached only after the operator confirms execution. The
    Futu platform call sites (declare_trig_symbol, BacktestingEngine, place
    APIs) live inside Futu's runtime and are not available in local Python.
    Filling this in requires a separate, user-confirmed deployment plan.
    """
    raise NotImplementedError(
        "Real Futu-side measurement is gated behind a separate plan; "
        "the dry-run path is the only intended local execution today."
    )


def _format_table(rows: List[TierResult]) -> str:
    header = (
        "| n_symbols | window_days | handle_data_avg_ms | "
        "handle_data_p95_ms | engine_total_sec | serial_order_delay_ms | "
        "notes |"
    )
    sep = (
        "|-----------|-------------|--------------------|"
        "--------------------|------------------|------------------------|"
        "-------|"
    )
    lines = [header, sep]
    for r in rows:
        def _fmt(x: Optional[float]) -> str:
            return "—" if x is None else f"{x:.2f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(r.n_symbols),
                    str(r.window_days),
                    _fmt(r.handle_data_avg_ms),
                    _fmt(r.handle_data_p95_ms),
                    _fmt(r.engine_total_sec),
                    _fmt(r.serial_order_delay_ms),
                    r.notes,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _check_threshold(rows: List[TierResult], threshold_sec: float) -> bool:
    """Return True iff any tier's p95 exceeds the threshold."""
    for r in rows:
        if r.handle_data_p95_ms is None:
            continue
        if r.handle_data_p95_ms / 1000.0 >= threshold_sec:
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_argparser()
    ns = parser.parse_args(argv)

    try:
        tiers = _parse_tiers(ns.tiers)
    except ValueError as e:
        print(f"❌ invalid --tiers: {e}", file=sys.stderr)
        return 2

    is_real = bool(ns.execute)
    if is_real and not ns.confirm:
        print(
            "❌ --execute requires --confirm AND explicit user confirmation "
            "in chat per project rule 2; aborting.",
            file=sys.stderr,
        )
        return 3

    started_at = datetime.now().isoformat(timespec="seconds")
    print(
        f"# Futu Phase 2 perf baseline — "
        f"mode={'real' if is_real else 'dry-run'} "
        f"tiers={tiers} window_days={ns.window_days} started_at={started_at}"
    )

    rows: List[TierResult] = []
    for n in tiers:
        if is_real:
            r = _real_run_tier(n, ns.window_days)
        else:
            r = _dry_run_tier(n, ns.window_days)
        rows.append(r)

    print()
    print(_format_table(rows))
    print()

    if ns.output:
        out_path = Path(ns.output)
        if not out_path.is_absolute():
            out_path = _PROJECT_ROOT / out_path
        if not out_path.parent.exists():
            print(
                f"❌ output dir does not exist: {out_path.parent}",
                file=sys.stderr,
            )
            return 2
        payload = {
            "started_at": started_at,
            "mode": "real" if is_real else "dry-run",
            "window_days": ns.window_days,
            "p95_threshold_sec": ns.p95_threshold,
            "rows": [asdict(r) for r in rows],
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"💾 baseline rows written: {out_path}")

    if _check_threshold(rows, ns.p95_threshold):
        print(
            "⚠️ p95 latency threshold breached — "
            "downstream should downgrade pool ceiling from 20 to 10 "
            "per requirement 5.3.",
            file=sys.stderr,
        )
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
