"""Coordinator dry-run smoke (no engine execution, no LLM call)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from phase2.optimize.coordinator import (
    CoordinatorConfig,
    run_session,
)
from phase2.optimize.evaluator import StopRules
from phase2.optimize.search_space import load_search_space
from phase2.optimize.session import SessionPaths
from phase2.optimize.trial_runner import TrialRunConfig


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "optimize_search_space.yaml"
)


@pytest.fixture(scope="module")
def space():
    return load_search_space(CONFIG_PATH)


def _trial_cfg() -> TrialRunConfig:
    return TrialRunConfig(
        pool_symbols=("NVDA", "MSFT", "AVGO"),
        start=date(2025, 1, 2),
        end=date(2025, 1, 10),
        init_cash=100_000.0,
    )


def test_dry_run_session_writes_all_artefacts(space, tmp_path: Path) -> None:
    sp = SessionPaths.create(repo_root=tmp_path)
    cfg = CoordinatorConfig(
        max_iters=2,
        trials_per_iter=2,
        top_k=1,
        dry_run=True,
        stop_rules=StopRules(max_iters=2, patience=2),
    )
    res = run_session(
        session=sp,
        space=space,
        trial_cfg=_trial_cfg(),
        coord_cfg=cfg,
        cli_args={"dry_run": True},
    )
    # Session-level artefacts
    assert sp.session_summary_json.exists()
    assert sp.progress_log.exists()
    summary = json.loads(sp.session_summary_json.read_text())
    assert summary["session_id"] == sp.session_id
    assert summary["iterations"] >= 1
    # Per-trial artefacts in iter_00
    iter0 = sp.iter_dir(0)
    assert (iter0 / "proposals.json").exists()
    assert (iter0 / "evaluations.json").exists()
    assert (iter0 / "leaderboard.csv").exists()
    for slot in range(2):
        td = sp.trial_dir(0, slot)
        assert (td / "params.yaml").exists()
        assert (td / "applied_params.json").exists()
        assert (td / "trial_result.json").exists()


def test_dry_run_session_stops_with_no_valid_trials(space, tmp_path: Path) -> None:
    """All trials are 'dry' -> no valid scores -> evaluator must stop."""
    sp = SessionPaths.create(repo_root=tmp_path)
    cfg = CoordinatorConfig(
        max_iters=5,
        trials_per_iter=2,
        top_k=1,
        dry_run=True,
    )
    res = run_session(
        session=sp,
        space=space,
        trial_cfg=_trial_cfg(),
        coord_cfg=cfg,
        cli_args={},
    )
    assert res.summary.iterations == 1
    assert res.summary.stop_reason == "no_valid_trials"
    # leaderboard CSV is well-formed
    lb = sp.leaderboard_csv(0)
    assert lb.exists()
    text = lb.read_text(encoding="utf-8")
    assert "iter_idx,trial_idx,trial_id" in text


def test_dry_run_session_first_trial_is_default(space, tmp_path: Path) -> None:
    sp = SessionPaths.create(repo_root=tmp_path)
    cfg = CoordinatorConfig(
        max_iters=1,
        trials_per_iter=3,
        top_k=1,
        dry_run=True,
    )
    run_session(
        session=sp,
        space=space,
        trial_cfg=_trial_cfg(),
        coord_cfg=cfg,
        cli_args={},
    )
    proposals = json.loads(sp.proposals_json(0).read_text())
    assert proposals["trials"][0]["origin"] == "default"
    assert proposals["trials"][0]["changed_params"] == {}
