"""US Multi-Symbol Quant Phase 2 — reconciliation runner (DRY-RUN by default).

Responsibilities (per requirements §4.2 / task-item §7):
- Read a backtest report (``run_report.json``) and a reference report.
- Compare 5 mandatory metrics (返回率、最大回撤、Sharpe、年化波动、胜率).
- Exit code 5 when more than ``--max-fail-pct`` (default 20%) of the
  metrics fall outside the configured tolerance.
- Always emit a placeholder robustness chart at
  ``state/runs/<plan>/<run_id>/robustness.png`` (empty file is fine; the
  real chart is rendered by a follow-up plan).
- ``--dry-run`` is the default; ``--no-dry-run`` is reserved for a future
  plan and aborts with exit code 3.

Inputs/outputs are JSON files only — no network, no Futu, no SIM/REAL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PLAN_NAME = "us_multi_symbol_quant_phase2"

logger = logging.getLogger("phase2.run_reconcile")

# Five mandatory metrics. ``rel`` means relative tolerance, ``abs`` means
# absolute tolerance — both forms are supported per metric.
DEFAULT_METRIC_TOLERANCE: Dict[str, Dict[str, float]] = {
    "total_return": {"abs": 0.005},     # ±0.5pp
    "max_drawdown": {"abs": 0.005},     # ±0.5pp
    "sharpe": {"abs": 0.10},
    "annual_volatility": {"abs": 0.005},
    "win_rate": {"abs": 0.01},          # ±1pp
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _load_metrics(path: Path, key: str) -> Dict[str, float]:
    """Pull the 5 metrics out of a report. Missing keys default to NaN."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    block = raw.get(key) or raw.get("metrics") or {}
    out: Dict[str, float] = {}
    for m in DEFAULT_METRIC_TOLERANCE:
        try:
            out[m] = float(block.get(m, float("nan")))
        except (TypeError, ValueError):
            out[m] = float("nan")
    return out


def _within_tolerance(
    actual: float, expected: float, tol: Dict[str, float]
) -> bool:
    if any(v != v for v in (actual, expected)):  # NaN guard
        return False
    if "abs" in tol and abs(actual - expected) <= tol["abs"]:
        return True
    if "rel" in tol and expected != 0 and abs(actual - expected) / abs(expected) <= tol["rel"]:
        return True
    return False


def _compare(
    actual: Dict[str, float],
    expected: Dict[str, float],
    tolerances: Dict[str, Dict[str, float]],
) -> Tuple[List[Dict[str, Any]], int, int]:
    rows: List[Dict[str, Any]] = []
    failed = 0
    total = len(tolerances)
    for m, tol in tolerances.items():
        a = actual.get(m, float("nan"))
        e = expected.get(m, float("nan"))
        ok = _within_tolerance(a, e, tol)
        rows.append({
            "metric": m,
            "actual": a,
            "expected": e,
            "tolerance": tol,
            "passed": ok,
        })
        if not ok:
            failed += 1
    return rows, failed, total


def _resolve_state_dir(run_id: str) -> Path:
    base_dir = os.environ.get("PHASE2_STATE_DIR", "state/runs")
    return Path(base_dir) / PLAN_NAME / run_id


def _write_placeholder_chart(path: Path) -> None:
    """Write a 1-byte placeholder PNG.

    Real plotting belongs to a follow-up plan; here we simply ensure the
    file exists so downstream pipelines that grep for it can proceed.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header marker


def _run_dry(args: argparse.Namespace) -> int:
    actual_path = Path(args.actual)
    expected_path = Path(args.expected)
    if not actual_path.is_file():
        logger.error("actual report not found: %s", actual_path)
        return 2
    if not expected_path.is_file():
        logger.error("expected report not found: %s", expected_path)
        return 2

    actual = _load_metrics(actual_path, "metrics")
    expected = _load_metrics(expected_path, "metrics")
    rows, failed, total = _compare(actual, expected, DEFAULT_METRIC_TOLERANCE)

    fail_pct = failed / total if total else 0.0
    threshold = float(args.max_fail_pct)
    over_threshold = fail_pct > threshold

    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "reconcile_%Y%m%dT%H%M%SZ"
    )
    state_dir = _resolve_state_dir(run_id)
    state_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "plan": PLAN_NAME,
        "run_id": run_id,
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actual_report": str(actual_path),
        "expected_report": str(expected_path),
        "rows": rows,
        "failed": failed,
        "total": total,
        "fail_pct": fail_pct,
        "max_fail_pct": threshold,
        "verdict": "FAIL" if over_threshold else "PASS",
    }
    out_path = state_dir / "reconcile_report.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    _write_placeholder_chart(state_dir / "robustness.png")

    logger.info(
        "reconcile %s: failed=%d/%d (%.1f%% > %.1f%%? %s)",
        summary["verdict"], failed, total, fail_pct * 100,
        threshold * 100, over_threshold,
    )
    print(
        f"[run_phase2_reconcile] {summary['verdict']} — report: {out_path}"
    )
    return 5 if over_threshold else 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-② backtest reconciliation runner (dry-run by default).",
    )
    parser.add_argument("--actual", required=True, help="path to actual run report (JSON)")
    parser.add_argument("--expected", required=True, help="path to reference report (JSON)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--max-fail-pct", type=float, default=0.20,
                        help="exit non-zero when failed/total > this ratio")
    parser.add_argument(
        "--dry-run", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if not args.dry_run:
        print(
            "[run_phase2_reconcile] aborting: --no-dry-run requires a new plan "
            "under .codebuddy/plan/ and explicit user confirmation per "
            "project rule 2.",
            file=sys.stderr,
        )
        return 3
    return _run_dry(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
