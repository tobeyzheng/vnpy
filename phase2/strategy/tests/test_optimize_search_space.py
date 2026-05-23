"""Search-space loader & frozen-param white-list tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from phase2.optimize.search_space import (
    DEFAULT_FROZEN_PARAMS,
    HARD_CEILINGS,
    load_search_space,
    merge_with_defaults,
    validate_proposal,
)


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "optimize_search_space.yaml"
)


@pytest.fixture(scope="module")
def space():
    return load_search_space(CONFIG_PATH)


def test_loads_real_yaml(space) -> None:
    assert space.version == 1
    assert len(space.params) >= 10
    # frozen list must always cover the default safety set
    assert DEFAULT_FROZEN_PARAMS.issubset(space.frozen)


def test_defaults_within_range(space) -> None:
    for name, spec in space.params.items():
        assert spec.is_in_range(spec.default), name


def test_hard_ceilings_enforced(space) -> None:
    sl = space.params["stop_loss_pct"]
    assert sl.max <= HARD_CEILINGS["stop_loss_pct"]["max"]
    assert sl.min >= HARD_CEILINGS["stop_loss_pct"]["min"]
    assert space.params["pool_budget_pct"].max <= HARD_CEILINGS["pool_budget_pct"]["max"]
    assert space.params["max_concurrent_holdings"].max <= HARD_CEILINGS["max_concurrent_holdings"]["max"]


def test_validate_rejects_frozen(space) -> None:
    with pytest.raises(ValueError, match="frozen"):
        validate_proposal({"LIVE_SUBMIT": True}, space)


def test_validate_rejects_unknown(space) -> None:
    with pytest.raises(ValueError, match="unknown"):
        validate_proposal({"definitely_not_a_real_param": 1.0}, space)


def test_validate_rejects_out_of_range(space) -> None:
    with pytest.raises(ValueError, match="outside the declared range"):
        validate_proposal({"fast_window": 999}, space)


def test_validate_rejects_hard_ceiling(space) -> None:
    # stop_loss_pct=0.5 violates the 0.10 hard ceiling.
    with pytest.raises(ValueError):
        validate_proposal({"stop_loss_pct": 0.5}, space)


def test_validate_casts_types(space) -> None:
    out = validate_proposal({"fast_window": 7, "stop_loss_pct": "0.06"}, space)
    assert out["fast_window"] == 7
    assert isinstance(out["fast_window"], int)
    assert isinstance(out["stop_loss_pct"], float)


def test_merge_with_defaults_fills_remaining(space) -> None:
    merged = merge_with_defaults({"fast_window": 9}, space)
    assert merged["fast_window"] == 9
    # Untouched params come from defaults.
    assert merged["slow_window"] == space.params["slow_window"].default


def test_loader_rejects_collision_between_frozen_and_params(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "frozen": ["stop_loss_pct"],
                "params": {
                    "stop_loss_pct": {
                        "type": "float",
                        "default": 0.05,
                        "min": 0.02,
                        "max": 0.10,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frozen"):
        load_search_space(bad)


def test_loader_rejects_widening_hard_ceiling(tmp_path: Path) -> None:
    bad = tmp_path / "wide.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "params": {
                    "stop_loss_pct": {
                        "type": "float",
                        "default": 0.05,
                        "min": 0.02,
                        "max": 0.50,  # exceeds hard ceiling 0.10
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hard ceiling"):
        load_search_space(bad)
