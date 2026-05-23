"""Optimizer subagent: produce N proposals per iteration.

Strategies (requirement 4.x):
- iter 0: ``trial_0`` is always the strategy default; the remaining
  N-1 are split between ``grid`` (deterministic neighbourhood walk)
  and ``random`` (uniform sample inside the search space).
- iter k>=1: read ``evaluations.json`` of iter k-1, pick its ``top_k``
  parents, generate ``local-perturb`` children around each parent,
  plus exactly one ``explore`` trial sampled uniformly across the
  full space (anti-stagnation seed).

The optional LLM channel hook is delegated to
``phase2.optimize.llm_bridge`` (task 7); failure of the LLM call must
*degrade* into the random sampler — never abort the iteration
(requirement 4.4 / 4.5).

Stdlib only.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .io_schemas import Proposal
from .search_space import ParamSpec, SearchSpace, validate_proposal

logger = logging.getLogger(__name__)


VALID_MODES = ("grid", "random", "bayesian", "local-perturb", "explore", "llm")


# ---------------------------------------------------------------------
# Helpers: per-spec sampling primitives
# ---------------------------------------------------------------------


def _grid_neighbours(spec: ParamSpec, value: Any) -> list[Any]:
    """Return the +/- one-step neighbours of ``value`` clipped to range."""
    if spec.choices is not None:
        return list(spec.choices)
    step = spec.step
    if step is None:
        return [value]
    out: set[Any] = set()
    candidates = [value - step, value + step, value - 2 * step, value + 2 * step]
    for c in candidates:
        if spec.type == "int":
            c = int(round(c))
        else:
            c = float(c)
        if spec.is_in_range(c):
            out.add(c)
    return sorted(out)


def _random_value(spec: ParamSpec, rng: random.Random) -> Any:
    if spec.choices is not None:
        return rng.choice(list(spec.choices))
    if spec.type == "bool":
        return rng.choice([True, False])
    if spec.type == "int":
        return rng.randint(int(spec.min), int(spec.max))
    if spec.type == "float":
        # Snap to the smallest of {step, 1e-4} to keep diffs readable.
        step = float(spec.step) if spec.step is not None else 0.0
        v = rng.uniform(float(spec.min), float(spec.max))
        if step > 0:
            n = round((v - float(spec.min)) / step)
            v = float(spec.min) + n * step
            v = max(float(spec.min), min(float(spec.max), v))
        return round(v, 6)
    raise ValueError(f"unsupported type {spec.type!r}")


def _perturb_value(spec: ParamSpec, parent_value: Any, rng: random.Random) -> Any:
    """Small step around ``parent_value`` (~ +/- 1 step), clamped to range."""
    if spec.choices is not None:
        return rng.choice(list(spec.choices))
    if spec.type == "bool":
        return not bool(parent_value)
    step = spec.step
    if step is None:
        return parent_value
    direction = rng.choice([-1, 1])
    nudge = direction * step
    candidate = parent_value + nudge
    if spec.type == "int":
        candidate = int(round(candidate))
    else:
        candidate = round(float(candidate), 6)
    if not spec.is_in_range(candidate):
        # Mirror: try the opposite direction.
        candidate = parent_value - nudge
        if spec.type == "int":
            candidate = int(round(candidate))
        else:
            candidate = round(float(candidate), 6)
    if not spec.is_in_range(candidate):
        candidate = parent_value
    return candidate


# ---------------------------------------------------------------------
# History entry — kept stdlib-friendly so it can be deserialised straight
# from evaluations.json without dataclass dependency
# ---------------------------------------------------------------------


@dataclass
class HistoryEntry:
    trial_id: str
    iter_idx: int
    trial_idx: int
    full_params: dict[str, Any]
    score: float | None
    rank: int | None


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def _trial_id(iter_idx: int, trial_idx: int) -> str:
    return f"iter{iter_idx:02d}_trial{trial_idx:02d}"


def _full_params(changed: Mapping[str, Any], space: SearchSpace) -> dict[str, Any]:
    out = space.defaults()
    out.update(dict(changed))
    return out


def _make_default_proposal(iter_idx: int, trial_idx: int, space: SearchSpace) -> Proposal:
    return Proposal(
        trial_id=_trial_id(iter_idx, trial_idx),
        iter_idx=iter_idx,
        trial_idx=trial_idx,
        origin="default",
        parent_trial=None,
        changed_params={},
        full_params=space.defaults(),
        rationale="strategy defaults baseline",
        predicted_metric_direction="unknown",
    )


def _make_random_proposal(
    iter_idx: int,
    trial_idx: int,
    space: SearchSpace,
    rng: random.Random,
    *,
    changed_keys: int = 3,
    origin: str = "random",
    rationale: str | None = None,
    parent_trial: str | None = None,
) -> Proposal:
    """Sample ``changed_keys`` distinct params and randomise them."""
    keys = list(space.params.keys())
    rng.shuffle(keys)
    pick = keys[: max(1, min(changed_keys, len(keys)))]
    changed: dict[str, Any] = {}
    for k in pick:
        changed[k] = _random_value(space.params[k], rng)
    full = _full_params(changed, space)
    return Proposal(
        trial_id=_trial_id(iter_idx, trial_idx),
        iter_idx=iter_idx,
        trial_idx=trial_idx,
        origin=origin,
        parent_trial=parent_trial,
        changed_params=changed,
        full_params=full,
        rationale=rationale or f"uniform sample on {len(pick)} keys: {pick}",
        predicted_metric_direction="unknown",
    )


def _make_grid_proposal(
    iter_idx: int,
    trial_idx: int,
    space: SearchSpace,
    rng: random.Random,
) -> Proposal:
    """Pick one tunable param and shift it by +/- one step from default."""
    keys = [k for k in space.params if space.params[k].step is not None]
    if not keys:
        return _make_random_proposal(iter_idx, trial_idx, space, rng, origin="random")
    key = rng.choice(keys)
    spec = space.params[key]
    neighbours = _grid_neighbours(spec, spec.default)
    if not neighbours:
        return _make_random_proposal(iter_idx, trial_idx, space, rng, origin="random")
    new_value = rng.choice(neighbours)
    changed = {key: new_value}
    return Proposal(
        trial_id=_trial_id(iter_idx, trial_idx),
        iter_idx=iter_idx,
        trial_idx=trial_idx,
        origin="grid",
        parent_trial=None,
        changed_params=changed,
        full_params=_full_params(changed, space),
        rationale=f"grid neighbour: {key} {spec.default} -> {new_value}",
        predicted_metric_direction="unknown",
    )


def _make_perturb_proposal(
    iter_idx: int,
    trial_idx: int,
    space: SearchSpace,
    rng: random.Random,
    parent: HistoryEntry,
    *,
    perturb_keys: int = 2,
) -> Proposal:
    """Local search: nudge ``perturb_keys`` parameters around the parent."""
    base = dict(parent.full_params)
    keys = list(space.params.keys())
    rng.shuffle(keys)
    pick = keys[: max(1, min(perturb_keys, len(keys)))]
    changed: dict[str, Any] = {}
    for k in pick:
        spec = space.params[k]
        parent_value = base.get(k, spec.default)
        new_value = _perturb_value(spec, parent_value, rng)
        if new_value != parent_value:
            changed[k] = new_value
    if not changed:
        # Force at least one change so we don't duplicate the parent.
        k = pick[0]
        spec = space.params[k]
        changed[k] = _random_value(spec, rng)
    return Proposal(
        trial_id=_trial_id(iter_idx, trial_idx),
        iter_idx=iter_idx,
        trial_idx=trial_idx,
        origin="local-perturb",
        parent_trial=parent.trial_id,
        changed_params=changed,
        full_params=_full_params({**{k: base[k] for k in base}, **changed}, space),
        rationale=(
            f"local perturb around parent={parent.trial_id} "
            f"(score={parent.score}); changed {sorted(changed.keys())}"
        ),
        predicted_metric_direction="up",
    )


def _make_explore_proposal(
    iter_idx: int,
    trial_idx: int,
    space: SearchSpace,
    rng: random.Random,
) -> Proposal:
    p = _make_random_proposal(
        iter_idx,
        trial_idx,
        space,
        rng,
        changed_keys=max(3, len(space.params) // 4),
        origin="explore",
        rationale="anti-stagnation explore: wide uniform sample",
    )
    return p


# ---------------------------------------------------------------------
# Top-level proposer
# ---------------------------------------------------------------------


def propose(
    *,
    iter_idx: int,
    n_trials: int,
    space: SearchSpace,
    history: Sequence[HistoryEntry] | None = None,
    rng: random.Random | None = None,
    llm_proposer: Callable[..., list[Proposal]] | None = None,
) -> list[Proposal]:
    """Build a fresh batch of ``n_trials`` proposals for iteration ``iter_idx``.

    Parameters
    ----------
    iter_idx, n_trials
        Self-explanatory.
    space
        The loaded :class:`SearchSpace`.
    history
        Top-k parents from the previous iteration (sorted by score
        descending). Pass ``None`` or an empty list for iter 0.
    rng
        Optional :class:`random.Random` for determinism in tests.
    llm_proposer
        Optional callable with signature ``(iter_idx, n_trials, space,
        history) -> list[Proposal]``. If it raises or returns an
        unusable batch, we fall back to the local rule set.
    """

    if n_trials <= 0:
        raise ValueError(f"n_trials must be > 0, got {n_trials}")
    rng = rng or random.Random(0xC0FFEE + iter_idx)

    # Optional LLM channel — try, then degrade.
    if llm_proposer is not None:
        try:
            llm_batch = llm_proposer(iter_idx=iter_idx, n_trials=n_trials,
                                     space=space, history=list(history or []))
            if llm_batch:
                cleaned = [p for p in llm_batch if _is_valid(p, space)]
                if len(cleaned) == n_trials:
                    return cleaned
                logger.warning(
                    "llm_proposer returned %d/%d valid proposals; "
                    "supplementing with local random samples",
                    len(cleaned),
                    n_trials,
                )
                # Fill the gap with random.
                while len(cleaned) < n_trials:
                    cleaned.append(
                        _make_random_proposal(iter_idx, len(cleaned), space, rng)
                    )
                return cleaned[:n_trials]
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_proposer failed (%s); degrading to local rules", exc)

    proposals: list[Proposal] = []
    if iter_idx == 0 or not history:
        proposals.append(_make_default_proposal(0, 0, space))
        # Mix grid + random for the remaining slots: alternate.
        for i in range(1, n_trials):
            if i % 2 == 1:
                proposals.append(_make_grid_proposal(iter_idx, i, space, rng))
            else:
                proposals.append(_make_random_proposal(iter_idx, i, space, rng))
        return proposals

    # iter k>=1: use top-k parents.
    parents = list(history)
    n_explore = 1 if n_trials >= 2 else 0
    n_local = n_trials - n_explore
    next_idx = 0
    for slot in range(n_local):
        parent = parents[slot % len(parents)]
        proposals.append(
            _make_perturb_proposal(iter_idx, next_idx, space, rng, parent)
        )
        next_idx += 1
    for _ in range(n_explore):
        proposals.append(_make_explore_proposal(iter_idx, next_idx, space, rng))
        next_idx += 1
    return proposals


def _is_valid(proposal: Proposal, space: SearchSpace) -> bool:
    try:
        validate_proposal(proposal.changed_params, space)
        return True
    except ValueError as exc:
        logger.warning(
            "rejecting invalid proposal %s: %s", proposal.trial_id, exc
        )
        return False


def history_from_evaluations(payload: Mapping[str, Any], top_k_only: bool = True) -> list[HistoryEntry]:
    """Convert a parsed ``evaluations.json`` into HistoryEntry list.

    ``top_k`` field of the payload is preferred when ``top_k_only=True``;
    otherwise all evaluations with a non-null score are included
    (sorted desc).
    """
    evaluations = payload.get("evaluations") or []
    by_id: dict[str, dict[str, Any]] = {e["trial_id"]: e for e in evaluations}

    if top_k_only:
        ids = list(payload.get("top_k") or [])
        rows = [by_id[i] for i in ids if i in by_id]
    else:
        rows = sorted(
            (e for e in evaluations if e.get("score") is not None),
            key=lambda e: e["score"],
            reverse=True,
        )

    out: list[HistoryEntry] = []
    for row in rows:
        out.append(
            HistoryEntry(
                trial_id=row["trial_id"],
                iter_idx=int(row["iter_idx"]),
                trial_idx=int(row["trial_idx"]),
                full_params=dict(row.get("metrics", {}).get("applied_params", {}) or {}),
                score=row.get("score"),
                rank=row.get("rank"),
            )
        )
    return out


__all__ = [
    "HistoryEntry",
    "VALID_MODES",
    "history_from_evaluations",
    "propose",
]
