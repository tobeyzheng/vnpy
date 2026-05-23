"""Optimizer subagent tests: proposal generation across iterations."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from phase2.optimize.optimizer import (
    HistoryEntry,
    history_from_evaluations,
    propose,
)
from phase2.optimize.search_space import load_search_space, validate_proposal


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "optimize_search_space.yaml"
)


@pytest.fixture(scope="module")
def space():
    return load_search_space(CONFIG_PATH)


def test_iter0_first_trial_is_default(space) -> None:
    batch = propose(iter_idx=0, n_trials=4, space=space, rng=random.Random(0))
    assert batch[0].origin == "default"
    assert batch[0].changed_params == {}
    assert batch[0].full_params == space.defaults()


def test_iter0_remaining_mix_grid_random(space) -> None:
    batch = propose(iter_idx=0, n_trials=4, space=space, rng=random.Random(0))
    origins = {p.origin for p in batch[1:]}
    assert origins.issubset({"grid", "random"})
    assert len(origins) >= 1


def test_iter0_all_proposals_validate(space) -> None:
    batch = propose(iter_idx=0, n_trials=6, space=space, rng=random.Random(7))
    for p in batch:
        validate_proposal(p.changed_params, space)


def test_iterk_uses_local_perturb_and_one_explore(space) -> None:
    parent = HistoryEntry(
        trial_id="iter00_trial00",
        iter_idx=0,
        trial_idx=0,
        full_params=space.defaults(),
        score=0.6,
        rank=1,
    )
    batch = propose(iter_idx=1, n_trials=4, space=space, history=[parent],
                    rng=random.Random(42))
    origins = [p.origin for p in batch]
    assert origins.count("explore") == 1
    assert origins.count("local-perturb") == 3
    for p in batch:
        validate_proposal(p.changed_params, space)


def test_iterk_proposals_reference_parent(space) -> None:
    parent = HistoryEntry(
        trial_id="iter00_trial02",
        iter_idx=0,
        trial_idx=2,
        full_params=space.defaults(),
        score=0.5,
        rank=1,
    )
    batch = propose(iter_idx=2, n_trials=3, space=space, history=[parent],
                    rng=random.Random(11))
    perturb_parents = {p.parent_trial for p in batch if p.origin == "local-perturb"}
    assert perturb_parents == {parent.trial_id}


def test_llm_proposer_failure_degrades_to_local(space) -> None:
    def boom(**kwargs):
        raise RuntimeError("simulated llm timeout")

    batch = propose(iter_idx=0, n_trials=3, space=space, rng=random.Random(0),
                    llm_proposer=boom)
    assert len(batch) == 3
    assert batch[0].origin == "default"


def test_llm_proposer_partial_payload_is_supplemented(space) -> None:
    from phase2.optimize.io_schemas import Proposal

    def partial(**kwargs):
        return [
            Proposal(
                trial_id="custom_0",
                iter_idx=0,
                trial_idx=0,
                origin="llm",
                parent_trial=None,
                changed_params={"fast_window": 7},
                full_params={**space.defaults(), "fast_window": 7},
                rationale="single shot",
            )
        ]

    batch = propose(iter_idx=0, n_trials=3, space=space, rng=random.Random(1),
                    llm_proposer=partial)
    assert len(batch) == 3
    assert batch[0].origin == "llm"
    # supplemental slots are local random
    assert all(p.origin in ("llm", "random", "grid", "default", "local-perturb", "explore")
               for p in batch)


def test_history_from_evaluations_returns_top_k_only() -> None:
    payload = {
        "evaluations": [
            {"trial_id": "a", "iter_idx": 0, "trial_idx": 0, "score": 0.7,
             "rank": 1, "metrics": {}},
            {"trial_id": "b", "iter_idx": 0, "trial_idx": 1, "score": 0.5,
             "rank": 2, "metrics": {}},
            {"trial_id": "c", "iter_idx": 0, "trial_idx": 2, "score": 0.3,
             "rank": 3, "metrics": {}},
        ],
        "top_k": ["a", "b"],
    }
    rows = history_from_evaluations(payload)
    assert [r.trial_id for r in rows] == ["a", "b"]
