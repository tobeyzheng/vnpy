"""Coordinator: drive the optimize -> backtest -> evaluate loop.

Responsibility split (requirement 6.x):
- Build the :class:`SessionPaths`, ensure dirs, refuse collisions.
- Loop iter_idx in [0, max_iters):
    1. Ask Optimizer for ``trials_per_iter`` proposals; persist
       ``proposals.json``.
    2. For each proposal, call ``trial_runner.run_trial`` (serial); the
       runner writes per-trial artefacts.
    3. Build :class:`IterContext` and call ``evaluator.evaluate_iteration``;
       persist ``evaluations.json`` + ``leaderboard.csv``.
    4. Append a progress line; check decision; break if ``stop``.
- Always write ``session_summary.json`` on exit (success / abort).

The reporter (task 8) is invoked at the end of a successful loop;
this module does not import it directly so a missing reporter does
not break the loop.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from . import assert_no_live_imports
from .evaluator import (
    DEFAULT_SCORE_FORMULA,
    IterContext,
    StopRules,
    evaluate_iteration,
    write_iter_outputs,
)
from .io_schemas import (
    IterEvaluations,
    Proposal,
    SessionSummary,
    TrialResult,
    write_json,
)
from .optimizer import HistoryEntry, propose
from .search_space import SearchSpace, load_search_space
from .session import SessionPaths, find_latest_iter
from .trial_runner import TrialRunConfig, run_trial

logger = logging.getLogger(__name__)


_DISK_FLOOR_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB (requirement 8.5)


# ---------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------


@dataclass
class CoordinatorConfig:
    max_iters: int = 10
    trials_per_iter: int = 4
    top_k: int = 2
    stop_rules: StopRules = field(default_factory=StopRules)
    score_formula: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SCORE_FORMULA))
    dry_run: bool = False
    resume: bool = False
    pool_config_path: Path | None = None
    search_space_path: Path | None = None


@dataclass
class CoordinatorResult:
    session: SessionPaths
    summary: SessionSummary
    iterations: list[IterEvaluations]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_progress(session: SessionPaths, line: str) -> None:
    session.progress_log.parent.mkdir(parents=True, exist_ok=True)
    with session.progress_log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    print(line, flush=True)


def _write_proposals(session: SessionPaths, iter_idx: int, batch: Sequence[Proposal]) -> None:
    payload = {
        "iter_idx": iter_idx,
        "trials": [p.to_dict() for p in batch],
    }
    write_json(session.proposals_json(iter_idx), payload)


def _load_pool_symbols(pool_config_path: Path) -> tuple[list[str], dict[str, Any]]:
    """Load the pool config. Always delegates to ``phase2.strategy.pool_loader``
    so the loader's invariants (max_pool_size, defaults, locked flag) apply
    uniformly. Returns (symbols, raw_dict).
    """
    raw = yaml.safe_load(pool_config_path.read_text(encoding="utf-8")) or {}
    from phase2.strategy.pool_loader import load_pool_config  # type: ignore
    cfg = load_pool_config(str(pool_config_path))
    symbols = list(cfg.symbol_list())
    return symbols, raw if isinstance(raw, Mapping) else {}


def _history_for(iter_idx: int, session: SessionPaths, top_k: int) -> list[HistoryEntry]:
    if iter_idx == 0:
        return []
    prev_path = session.evaluations_json(iter_idx - 1)
    if not prev_path.exists():
        return []
    payload = json.loads(prev_path.read_text(encoding="utf-8"))
    top_ids = list(payload.get("top_k") or [])[:top_k]
    by_id = {e["trial_id"]: e for e in payload.get("evaluations", [])}
    out: list[HistoryEntry] = []
    for tid in top_ids:
        if tid not in by_id:
            continue
        ev = by_id[tid]
        # Recover full_params from the trial's applied_params.json
        artefact = ev.get("metrics", {})
        # We didn't store applied_params inside metrics; instead read it back
        applied_path = session.trial_dir(ev["iter_idx"], ev["trial_idx"]) / "applied_params.json"
        full_params = {}
        if applied_path.exists():
            try:
                full_params = json.loads(applied_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                full_params = {}
        out.append(
            HistoryEntry(
                trial_id=tid,
                iter_idx=int(ev["iter_idx"]),
                trial_idx=int(ev["trial_idx"]),
                full_params=full_params,
                score=ev.get("score"),
                rank=ev.get("rank"),
            )
        )
    return out


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------


def run_session(
    *,
    session: SessionPaths,
    space: SearchSpace,
    trial_cfg: TrialRunConfig,
    coord_cfg: CoordinatorConfig,
    cli_args: Mapping[str, Any] | None = None,
    llm_proposer: Callable[..., list[Proposal]] | None = None,
    llm_reviewer: Callable[..., dict[str, Any]] | None = None,
) -> CoordinatorResult:
    """Run the optimize / backtest / evaluate loop end-to-end."""

    assert_no_live_imports()
    session.ensure_dirs()
    started_at_iso = _now_iso()
    started_at_mono = time.monotonic()

    iterations: list[IterEvaluations] = []
    prev_top_k_ids: list[str] = []
    prev_best_score: float | None = None
    consecutive_no_change = 0
    total_trials = 0
    stop_reason: str | None = None
    best_score: float | None = None
    best_trial_path: str | None = None

    # Resume support: skip iterations that already have evaluations.json
    start_iter = 0
    if coord_cfg.resume:
        latest = find_latest_iter(session)
        if latest >= 0:
            start_iter = latest + 1
            _write_progress(
                session,
                f"[resume] continuing from iter {start_iter} (latest existing iter={latest})",
            )

    _write_progress(
        session,
        f"[start] session_id={session.session_id} max_iters={coord_cfg.max_iters} "
        f"trials_per_iter={coord_cfg.trials_per_iter} dry_run={coord_cfg.dry_run}",
    )

    try:
        for iter_idx in range(start_iter, coord_cfg.max_iters):
            iter_started = time.monotonic()

            # Disk floor (requirement 8.5)
            if session.disk_free_bytes() < _DISK_FLOOR_BYTES:
                stop_reason = "disk_low"
                _write_progress(session, f"[stop] disk_low: {session.disk_free_bytes()} B free")
                break

            # 1. propose
            history = _history_for(iter_idx, session, coord_cfg.top_k)
            try:
                batch = propose(
                    iter_idx=iter_idx,
                    n_trials=coord_cfg.trials_per_iter,
                    space=space,
                    history=history,
                    llm_proposer=llm_proposer,
                )
            except Exception as exc:  # noqa: BLE001
                stop_reason = "optimizer_dry"
                _write_progress(session, f"[stop] optimizer_dry: {exc}")
                break

            if not batch:
                stop_reason = "optimizer_dry"
                _write_progress(session, "[stop] optimizer_dry: empty proposal batch")
                break

            session.ensure_iter_dirs(iter_idx, len(batch))
            _write_proposals(session, iter_idx, batch)

            # 2. run trials (serial)
            trial_results: list[TrialResult] = []
            trial_dirs: dict[str, Path] = {}
            for proposal in batch:
                trial_dir = session.trial_dir(proposal.iter_idx, proposal.trial_idx)
                trial_dirs[proposal.trial_id] = trial_dir
                # also persist proposal.json next to trial dir
                write_json(trial_dir / "proposal.json", proposal.to_dict())
                try:
                    res = run_trial(
                        proposal=proposal,
                        space=space,
                        cfg=trial_cfg,
                        trial_dir=trial_dir,
                        dry_run=coord_cfg.dry_run,
                    )
                except Exception as exc:  # noqa: BLE001 - defensive double-catch
                    (trial_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
                    res = TrialResult(
                        trial_id=proposal.trial_id,
                        iter_idx=proposal.iter_idx,
                        trial_idx=proposal.trial_idx,
                        trial_status="failed",
                        summary=None,
                        error=str(exc),
                        artefact_dir=str(trial_dir),
                    )
                    write_json(trial_dir / "trial_result.json", res.to_dict())
                trial_results.append(res)
                total_trials += 1

            # 3. evaluate
            cum_runtime = time.monotonic() - started_at_mono
            ctx = IterContext(
                iter_idx=iter_idx,
                started_at_monotonic=iter_started,
                cumulative_runtime_sec=cum_runtime,
                prev_best_score=prev_best_score,
                prev_top_k_ids=list(prev_top_k_ids),
                consecutive_no_change=consecutive_no_change,
                baseline_score=None,
            )
            iter_eval = evaluate_iteration(
                trial_results=trial_results,
                trial_dirs=trial_dirs,
                iter_idx=iter_idx,
                score_formula=coord_cfg.score_formula,
                top_k=coord_cfg.top_k,
                ctx=ctx,
                rules=coord_cfg.stop_rules,
            )
            # Optional LLM review hook (degraded-safe).
            if llm_reviewer is not None:
                try:
                    notes = llm_reviewer(iter_eval=iter_eval, history=history)
                    iter_eval.llm_notes = notes if isinstance(notes, dict) else {"raw": notes}
                except Exception as exc:  # noqa: BLE001
                    logger.warning("llm_reviewer failed (%s); skipping notes", exc)

            write_iter_outputs(
                iter_eval=iter_eval,
                evaluations_json_path=session.evaluations_json(iter_idx),
                leaderboard_csv_path=session.leaderboard_csv(iter_idx),
            )
            iterations.append(iter_eval)

            # 4. progress + decision
            iter_runtime = time.monotonic() - iter_started
            _write_progress(
                session,
                f"[iter {iter_idx:02d}] best_score={iter_eval.best_score} "
                f"top_k={iter_eval.top_k} delta_prev={iter_eval.delta_vs_prev} "
                f"trials={len(trial_results)} runtime={iter_runtime:.1f}s "
                f"decision={iter_eval.decision} reason={iter_eval.stop_reason}",
            )

            # update best
            if iter_eval.best_score is not None:
                if best_score is None or iter_eval.best_score > best_score:
                    best_score = iter_eval.best_score
                    if iter_eval.top_k:
                        bt_id = iter_eval.top_k[0]
                        bt_idx = next(
                            (e.trial_idx for e in iter_eval.evaluations if e.trial_id == bt_id),
                            None,
                        )
                        if bt_idx is not None:
                            best_trial_path = str(session.trial_dir(iter_idx, bt_idx))

            # update streak counters
            if (
                prev_top_k_ids
                and iter_eval.top_k
                and set(iter_eval.top_k) == set(prev_top_k_ids)
            ):
                consecutive_no_change += 1
            else:
                consecutive_no_change = 0
            prev_top_k_ids = list(iter_eval.top_k)
            prev_best_score = iter_eval.best_score

            if iter_eval.decision == "stop":
                stop_reason = iter_eval.stop_reason or "evaluator_stop"
                break
        else:
            stop_reason = stop_reason or "max_iters"
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        _write_progress(session, "[stop] interrupted by user")
    except Exception as exc:  # noqa: BLE001
        stop_reason = "exception"
        _write_progress(session, f"[stop] exception: {exc}")
        traceback.print_exc()

    finished_at_iso = _now_iso()
    runtime_sec = time.monotonic() - started_at_mono

    summary = SessionSummary(
        session_id=session.session_id,
        namespace=session.namespace,
        started_at=started_at_iso,
        finished_at=finished_at_iso,
        iterations=len(iterations),
        total_trials=total_trials,
        best_trial_path=best_trial_path,
        best_score=best_score,
        stop_reason=stop_reason or "unknown",
        cli_args=dict(cli_args or {}),
        pool_config_path=str(coord_cfg.pool_config_path) if coord_cfg.pool_config_path else None,
        search_space_path=str(coord_cfg.search_space_path) if coord_cfg.search_space_path else None,
        score_formula=dict(coord_cfg.score_formula),
        runtime_sec=round(runtime_sec, 2),
    )
    write_json(session.session_summary_json, summary.to_dict())
    _write_progress(
        session,
        f"[done] iterations={summary.iterations} total_trials={summary.total_trials} "
        f"best_score={summary.best_score} stop_reason={summary.stop_reason} "
        f"runtime={runtime_sec:.1f}s",
    )

    return CoordinatorResult(session=session, summary=summary, iterations=iterations)


__all__ = [
    "CoordinatorConfig",
    "CoordinatorResult",
    "_load_pool_symbols",
    "run_session",
]
