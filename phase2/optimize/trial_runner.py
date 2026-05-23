"""Trial runner: drives a single backtest trial under a session/iter.

Responsibilities (requirement 3.x):
- Materialise ``params.yaml`` + ``applied_params.json`` inside the
  trial dir for full reproducibility.
- Build a :class:`PortfolioBacktestEngine` with ``force_live_submit=True``
  and ``param_overrides`` so the optimizer's proposed values are
  setattr'd onto the Strategy instance immediately after
  ``initialize()`` — never edits the strategy source file.
- Catch any uncaught exception, write ``error.log`` and a
  ``trial_result.json`` with ``trial_status='failed'``; never abort
  the iteration.
- Refuse the unsupported ``--respect-live-submit`` mode (requirement 3.5):
  the optimization loop is hard-wired to the pure-backtest path.

The runner is import-safe: it does NOT import ``futu`` or
``phase2.live.*``. The integration test in task 9 enforces that.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from . import assert_no_live_imports
from .io_schemas import Proposal, TrialResult, write_json
from .search_space import SearchSpace, merge_with_defaults

logger = logging.getLogger(__name__)


_DEFAULT_STRATEGY_PATH = (
    Path(__file__).resolve().parents[1]
    / "strategy"
    / "us_multi_symbol_phase2_strategy_futumd.py"
)


# ---------------------------------------------------------------------
# Configuration container
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class TrialRunConfig:
    """Static (per-session) parameters shared by every trial."""

    pool_symbols: tuple[str, ...]
    start: date
    end: date
    init_cash: float = 100_000.0
    fee_rate: float = 0.0003
    slippage: float = 0.0
    annual_trading_days: int = 252
    exchange_name: str = "SMART"
    interval_value: str = "d"
    strategy_path: Path = _DEFAULT_STRATEGY_PATH
    output_root: Optional[Path] = None  # session.namespace_dir; trial_dir resolves below

    def __post_init__(self) -> None:
        if not self.pool_symbols:
            raise ValueError("pool_symbols must be non-empty")
        if self.start > self.end:
            raise ValueError(f"start {self.start} must be <= end {self.end}")
        if self.init_cash <= 0:
            raise ValueError(f"init_cash must be > 0, got {self.init_cash}")


# ---------------------------------------------------------------------
# Param-file IO
# ---------------------------------------------------------------------


def _write_params_yaml(path: Path, params: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            dict(params),
            fh,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )


def _write_applied_params(path: Path, applied: Mapping[str, Any]) -> None:
    write_json(path, dict(applied))


# ---------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------


def _import_engine():
    """Lazy import to keep ``phase2.optimize`` import time minimal and
    to make sure the engine module is loaded only on the trial path
    (test_no_live_imports verifies this).
    """
    from phase2.backtest.portfolio_backtest_engine import (
        PortfolioBacktestEngine,
    )
    from vnpy.trader.constant import Exchange, Interval

    return PortfolioBacktestEngine, Exchange, Interval


def _resolve_exchange(name: str):
    _PortfolioBacktestEngine, Exchange, _Interval = _import_engine()
    try:
        return Exchange[name.upper()]
    except KeyError as exc:
        raise ValueError(f"unknown exchange: {name!r}") from exc


def _resolve_interval(value: str):
    _PortfolioBacktestEngine, _Exchange, Interval = _import_engine()
    for iv in Interval:
        if iv.value == value:
            return iv
    raise ValueError(f"unknown interval value: {value!r}")


def run_trial(
    *,
    proposal: Proposal,
    space: SearchSpace,
    cfg: TrialRunConfig,
    trial_dir: Path,
    respect_live_submit: bool = False,
    dry_run: bool = False,
) -> TrialResult:
    """Run a single trial end-to-end.

    Always returns a :class:`TrialResult`; never raises into the caller
    (per requirement 3.4).  Caller (Coordinator) is responsible for
    persisting the result via ``write_json``.
    """

    assert_no_live_imports()

    if respect_live_submit:
        raise ValueError(
            "respect_live_submit=True is rejected in the optimization loop "
            "(requirement 3.5): trials must run on the force_live_submit=True "
            "pure-backtest branch."
        )

    trial_dir.mkdir(parents=True, exist_ok=True)
    params_yaml = trial_dir / "params.yaml"
    applied_json = trial_dir / "applied_params.json"
    error_log = trial_dir / "error.log"
    result_json = trial_dir / "trial_result.json"

    # 1. Resolve full param dict (defaults <- proposal.changed_params).
    try:
        full_params = merge_with_defaults(proposal.changed_params, space)
    except ValueError as exc:
        result = TrialResult(
            trial_id=proposal.trial_id,
            iter_idx=proposal.iter_idx,
            trial_idx=proposal.trial_idx,
            trial_status="injection_mismatch",
            summary=None,
            error=f"proposal validation failed: {exc}",
            artefact_dir=str(trial_dir),
        )
        error_log.write_text(str(exc) + "\n", encoding="utf-8")
        write_json(result_json, result.to_dict())
        return result

    _write_params_yaml(params_yaml, full_params)

    # 2. Dry-run short-circuit (requirement 6.5): produce skeleton only.
    if dry_run:
        result = TrialResult(
            trial_id=proposal.trial_id,
            iter_idx=proposal.iter_idx,
            trial_idx=proposal.trial_idx,
            trial_status="dry",
            summary=None,
            artefact_dir=str(trial_dir),
        )
        _write_applied_params(applied_json, full_params)
        write_json(result_json, result.to_dict())
        return result

    # 3. Build engine + run.
    started = time.monotonic()
    try:
        PortfolioBacktestEngine, _Ex, _Iv = _import_engine()
        engine = PortfolioBacktestEngine(
            pool_symbols=list(cfg.pool_symbols),
            start=cfg.start,
            end=cfg.end,
            init_cash=float(cfg.init_cash),
            exchange=_resolve_exchange(cfg.exchange_name),
            interval=_resolve_interval(cfg.interval_value),
            fee_rate=float(cfg.fee_rate),
            slippage=float(cfg.slippage),
            annual_trading_days=int(cfg.annual_trading_days),
            force_live_submit=True,
            param_overrides=dict(full_params),
        )
        engine_run_id = f"{proposal.trial_id}"
        engine_output_root = trial_dir.parent  # iter_<k>/  -> engine writes iter_<k>/<trial_id>/
        # We instead want the engine to write directly into trial_dir.
        # The engine appends ``/<run_id>`` to output_root, so we use
        # trial_dir.parent and pass run_id=trial_dir.name.
        engine_run_id = trial_dir.name
        result_obj = engine.run(
            strategy_path=cfg.strategy_path,
            run_id=engine_run_id,
            output_root=engine_output_root,
        )
    except RuntimeError as exc:
        # The engine raises RuntimeError on param injection mismatch.
        elapsed = time.monotonic() - started
        msg = "".join(traceback.format_exception_only(type(exc), exc))
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        result = TrialResult(
            trial_id=proposal.trial_id,
            iter_idx=proposal.iter_idx,
            trial_idx=proposal.trial_idx,
            trial_status="injection_mismatch"
            if "injection mismatch" in str(exc)
            else "failed",
            summary=None,
            error=msg.strip(),
            duration_sec=round(elapsed, 3),
            artefact_dir=str(trial_dir),
        )
        _write_applied_params(applied_json, full_params)
        write_json(result_json, result.to_dict())
        return result
    except Exception as exc:  # noqa: BLE001 - any other failure is a soft trial failure
        elapsed = time.monotonic() - started
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        result = TrialResult(
            trial_id=proposal.trial_id,
            iter_idx=proposal.iter_idx,
            trial_idx=proposal.trial_idx,
            trial_status="failed",
            summary=None,
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
            duration_sec=round(elapsed, 3),
            artefact_dir=str(trial_dir),
        )
        _write_applied_params(applied_json, full_params)
        write_json(result_json, result.to_dict())
        return result

    elapsed = time.monotonic() - started

    # 4. Read summary.json the engine just wrote, and store applied params.
    summary_path = result_obj.output_dir / "summary.json"
    summary_payload: dict[str, Any] | None = None
    status = "ok"
    error_msg: str | None = None
    if summary_path.exists():
        try:
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            status = "missing_summary"
            error_msg = f"summary.json unreadable: {exc}"
    else:
        status = "missing_summary"
        error_msg = f"summary.json missing at {summary_path}"

    _write_applied_params(applied_json, full_params)

    result = TrialResult(
        trial_id=proposal.trial_id,
        iter_idx=proposal.iter_idx,
        trial_idx=proposal.trial_idx,
        trial_status=status,
        summary=summary_payload,
        error=error_msg,
        duration_sec=round(elapsed, 3),
        artefact_dir=str(result_obj.output_dir),
    )
    write_json(result_json, result.to_dict())
    return result


__all__ = [
    "TrialRunConfig",
    "run_trial",
]
