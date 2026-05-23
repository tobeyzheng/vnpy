"""Trial-runner tests (no engine execution; dry-run + error paths)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from phase2.optimize.io_schemas import Proposal
from phase2.optimize.search_space import load_search_space
from phase2.optimize.session import SessionPaths
from phase2.optimize.trial_runner import TrialRunConfig, run_trial


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "optimize_search_space.yaml"
)


@pytest.fixture(scope="module")
def space():
    return load_search_space(CONFIG_PATH)


@pytest.fixture()
def session(tmp_path: Path) -> SessionPaths:
    sp = SessionPaths.create(repo_root=tmp_path)
    sp.ensure_dirs()
    return sp


def _cfg() -> TrialRunConfig:
    return TrialRunConfig(
        pool_symbols=("NVDA", "MSFT"),
        start=date(2025, 1, 2),
        end=date(2025, 1, 10),
        init_cash=100_000.0,
    )


def _proposal(iter_idx: int, trial_idx: int, changed=None) -> Proposal:
    return Proposal(
        trial_id=f"iter{iter_idx:02d}_trial{trial_idx:02d}",
        iter_idx=iter_idx,
        trial_idx=trial_idx,
        origin="default",
        parent_trial=None,
        changed_params=changed or {},
        full_params={},
        rationale="ut",
    )


def test_dry_run_creates_skeleton(space, session: SessionPaths) -> None:
    trial_dir = session.trial_dir(0, 0)
    res = run_trial(
        proposal=_proposal(0, 0, {"fast_window": 6}),
        space=space,
        cfg=_cfg(),
        trial_dir=trial_dir,
        dry_run=True,
    )
    assert res.trial_status == "dry"
    assert (trial_dir / "params.yaml").exists()
    assert (trial_dir / "applied_params.json").exists()
    applied = json.loads((trial_dir / "applied_params.json").read_text())
    assert applied["fast_window"] == 6
    # full param set is materialised, not just the diff
    assert "stop_loss_pct" in applied


def test_respect_live_submit_is_rejected(space, session: SessionPaths) -> None:
    with pytest.raises(ValueError, match="respect_live_submit"):
        run_trial(
            proposal=_proposal(0, 0),
            space=space,
            cfg=_cfg(),
            trial_dir=session.trial_dir(0, 0),
            respect_live_submit=True,
        )


def test_oob_proposal_yields_injection_mismatch(space, session: SessionPaths) -> None:
    res = run_trial(
        proposal=_proposal(0, 1, {"fast_window": 9999}),
        space=space,
        cfg=_cfg(),
        trial_dir=session.trial_dir(0, 1),
        dry_run=True,
    )
    assert res.trial_status == "injection_mismatch"
    assert res.error and "outside" in res.error


def test_frozen_param_proposal_rejected(space, session: SessionPaths) -> None:
    res = run_trial(
        proposal=_proposal(0, 2, {"LIVE_SUBMIT": True}),
        space=space,
        cfg=_cfg(),
        trial_dir=session.trial_dir(0, 2),
        dry_run=True,
    )
    assert res.trial_status == "injection_mismatch"
    assert res.error and "frozen" in res.error


def test_session_paths_collision_protection(tmp_path: Path) -> None:
    # Pre-create a sibling phase2_multi_backtest run with the SAME id.
    runs = tmp_path / "state" / "runs"
    sibling = runs / "phase2_multi_backtest" / "opt_20260102T000000Z"
    sibling.mkdir(parents=True)
    sp = SessionPaths.create(session_id="opt_20260102T000000Z", repo_root=tmp_path)
    with pytest.raises(RuntimeError, match="collision"):
        sp.ensure_dirs()
