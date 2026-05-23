"""Evaluator subagent tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from phase2.optimize.evaluator import (
    DEFAULT_SCORE_FORMULA,
    IterContext,
    StopRules,
    compute_scores,
    derive_metrics,
    evaluate_iteration,
)
from phase2.optimize.io_schemas import TrialResult


def _make_summary(**kw):
    base = {
        "annual_trading_days": 252,
        "trading_days": 100,
        "init_cash": 100000.0,
        "annualised_return_pct": 10.0,
        "max_drawdown_pct": 5.0,
        "total_return_pct": 8.0,
        "trade_count": 30,
        "win_count": 18,
        "loss_count": 12,
    }
    base.update(kw)
    return base


def test_derive_metrics_basic() -> None:
    summary = _make_summary()
    rows = [{"nav": 100000.0, "position_value": 50000.0}] * 10
    m = derive_metrics(summary, rows, [])
    assert m["trade_count"] == 30
    assert m["win_rate"] == round(18 / 30, 4)
    assert m["calmar_proxy"] == 2.0
    # 10 non-zero-position days / 100 trading_days
    assert m["exposure_days"] == 0.1


def test_derive_metrics_zero_drawdown() -> None:
    summary = _make_summary(max_drawdown_pct=0.0)
    m = derive_metrics(summary, [], [])
    assert m["calmar_proxy"] is None


def test_compute_scores_normalises_min_max() -> None:
    rows = [
        {"annualised_return_pct": 30.0, "calmar_proxy": 3.0, "max_drawdown_pct": 5.0},
        {"annualised_return_pct": 10.0, "calmar_proxy": 1.0, "max_drawdown_pct": 10.0},
        {"annualised_return_pct": 20.0, "calmar_proxy": 2.0, "max_drawdown_pct": 7.0},
    ]
    scores, meta = compute_scores(rows, DEFAULT_SCORE_FORMULA)
    assert scores[0] is not None
    # First row has highest annual return + highest calmar + lowest drawdown -> top score.
    assert scores[0] >= scores[1]
    assert scores[0] >= scores[2]


def test_compute_scores_handles_none_rows() -> None:
    rows = [
        {"annualised_return_pct": 10.0, "calmar_proxy": 1.0, "max_drawdown_pct": 5.0},
        None,
    ]
    scores, _ = compute_scores(rows)
    assert scores[1] is None


def test_evaluate_iteration_orders_by_score(tmp_path: Path) -> None:
    # Build two ok trials + one failed trial.
    ok1 = TrialResult(
        trial_id="t1", iter_idx=0, trial_idx=0, trial_status="ok",
        summary=_make_summary(annualised_return_pct=20.0, max_drawdown_pct=4.0),
        artefact_dir=str(tmp_path / "t1"),
    )
    ok2 = TrialResult(
        trial_id="t2", iter_idx=0, trial_idx=1, trial_status="ok",
        summary=_make_summary(annualised_return_pct=8.0, max_drawdown_pct=12.0),
        artefact_dir=str(tmp_path / "t2"),
    )
    fail = TrialResult(
        trial_id="t3", iter_idx=0, trial_idx=2, trial_status="failed",
        summary=None, error="boom",
        artefact_dir=str(tmp_path / "t3"),
    )
    for d in ("t1", "t2", "t3"):
        (tmp_path / d).mkdir()

    ctx = IterContext(
        iter_idx=0,
        started_at_monotonic=time.monotonic(),
        cumulative_runtime_sec=10.0,
        prev_best_score=None,
        prev_top_k_ids=[],
        consecutive_no_change=0,
    )
    res = evaluate_iteration(
        trial_results=[ok1, ok2, fail],
        trial_dirs={t.trial_id: Path(t.artefact_dir) for t in [ok1, ok2, fail]},
        iter_idx=0,
        top_k=2,
        ctx=ctx,
        rules=StopRules(max_iters=5),
    )
    assert res.top_k[0] == "t1"
    fail_eval = next(e for e in res.evaluations if e.trial_id == "t3")
    assert fail_eval.score is None
    assert fail_eval.rank is None
    assert res.decision == "continue"


def test_evaluate_iteration_stops_when_no_valid_trials(tmp_path: Path) -> None:
    fail = TrialResult(
        trial_id="t1", iter_idx=0, trial_idx=0, trial_status="failed",
        summary=None, artefact_dir=str(tmp_path),
    )
    ctx = IterContext(
        iter_idx=0,
        started_at_monotonic=time.monotonic(),
        cumulative_runtime_sec=1.0,
        prev_best_score=None,
        prev_top_k_ids=[],
        consecutive_no_change=0,
    )
    res = evaluate_iteration(
        trial_results=[fail],
        trial_dirs={"t1": tmp_path},
        iter_idx=0,
        ctx=ctx,
    )
    assert res.decision == "stop"
    assert res.stop_reason == "no_valid_trials"


def test_evaluate_iteration_diagnoses_thin_trades(tmp_path: Path) -> None:
    thin = TrialResult(
        trial_id="thin", iter_idx=0, trial_idx=0, trial_status="ok",
        summary=_make_summary(trade_count=3, win_count=2, loss_count=1),
        artefact_dir=str(tmp_path),
    )
    res = evaluate_iteration(
        trial_results=[thin],
        trial_dirs={"thin": tmp_path},
        iter_idx=0,
    )
    diag = res.evaluations[0].diagnostics
    assert diag.get("suspicious_thin_trades") is True


def test_evaluate_iteration_stops_on_max_iters(tmp_path: Path) -> None:
    ok = TrialResult(
        trial_id="ok", iter_idx=2, trial_idx=0, trial_status="ok",
        summary=_make_summary(),
        artefact_dir=str(tmp_path),
    )
    ctx = IterContext(
        iter_idx=2,
        started_at_monotonic=time.monotonic(),
        cumulative_runtime_sec=1.0,
        prev_best_score=0.5,
        prev_top_k_ids=["ok"],
        consecutive_no_change=0,
    )
    res = evaluate_iteration(
        trial_results=[ok],
        trial_dirs={"ok": tmp_path},
        iter_idx=2,
        ctx=ctx,
        rules=StopRules(max_iters=3),
    )
    assert res.decision == "stop"
    assert res.stop_reason == "max_iters"
