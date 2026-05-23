"""Optional LLM channel for the phase2 self-optimization loop.

This module is **completely optional**. It is loaded only when the
runner is started with ``--llm-optimizer`` or ``--llm-evaluator``;
any failure (missing config, network error, schema mismatch, timeout)
**must** degrade gracefully into the local rule path — the loop is
never allowed to abort because of an LLM hiccup.

Both functions return JSON-validated payloads:
- ``llm_propose`` → ``list[Proposal]``
- ``llm_review``  → ``dict`` (free-form notes; never alters ranking)

We rely on env vars to keep the runner argparse surface small:
    PHASE2_OPT_LLM_BASE_URL   (e.g. https://api.openai.com/v1)
    PHASE2_OPT_LLM_API_KEY
    PHASE2_OPT_LLM_MODEL      (e.g. gpt-4o-mini)
    PHASE2_OPT_LLM_API_TYPE   (optional: "openai" | "knot_agui")
    PHASE2_OPT_LLM_API_USER   (optional, knot_agui only)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence

from .io_schemas import Proposal
from .optimizer import HistoryEntry
from .search_space import SearchSpace, validate_proposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Client construction (lazy)
# ---------------------------------------------------------------------


def _build_client():
    """Return a configured ``OpenAICompatibleClient`` or ``None``.

    Importing ``vnpy_llm`` is deferred so that running the optimization
    loop without the LLM extras never imports it.
    """
    base_url = os.environ.get("PHASE2_OPT_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("PHASE2_OPT_LLM_API_KEY", "").strip()
    model = os.environ.get("PHASE2_OPT_LLM_MODEL", "").strip()
    api_type = os.environ.get("PHASE2_OPT_LLM_API_TYPE", "openai").strip() or "openai"
    api_user = os.environ.get("PHASE2_OPT_LLM_API_USER", "").strip()

    if not (base_url and api_key):
        logger.warning(
            "phase2 LLM bridge: PHASE2_OPT_LLM_BASE_URL/API_KEY not set; "
            "LLM channel disabled this run."
        )
        return None

    try:
        from vnpy_llm.llm_client import OpenAICompatibleClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning("phase2 LLM bridge: vnpy_llm import failed (%s)", exc)
        return None

    client = OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_type=api_type,
        api_user=api_user,
        timeout_seconds=int(os.environ.get("PHASE2_OPT_LLM_TIMEOUT", "60")),
        temperature=float(os.environ.get("PHASE2_OPT_LLM_TEMPERATURE", "0.2")),
    )
    if not client.is_configured():
        logger.warning("phase2 LLM bridge: client.is_configured() returned False")
        return None
    return client


# ---------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------


_SYSTEM_PROMPT_PROPOSER = (
    "You are an optimization sub-agent for a quantitative trading "
    "strategy. Your job is to propose a JSON batch of N candidate "
    "parameter sets that stay strictly within the provided search "
    "space. NEVER touch any frozen parameter. Always return a single "
    "JSON object with key 'trials' which is a list. Each list item "
    "MUST have keys: trial_id, origin (one of grid/random/local-perturb"
    "/explore/llm), parent_trial (string or null), changed_params "
    "(object), rationale (one sentence in Chinese)."
)


_SYSTEM_PROMPT_REVIEWER = (
    "You are an evaluator sub-agent. Read the trial evaluations and "
    "produce a JSON object with keys: 'observations' (string list), "
    "'risks' (string list), 'next_iter_hint' (string). Never alter "
    "ranking, scores, or stop_reason. Reply with a single JSON object."
)


def _format_history(history: Sequence[HistoryEntry]) -> list[dict[str, Any]]:
    return [
        {
            "trial_id": h.trial_id,
            "iter_idx": h.iter_idx,
            "trial_idx": h.trial_idx,
            "score": h.score,
            "rank": h.rank,
            "full_params": dict(h.full_params),
        }
        for h in history
    ]


# ---------------------------------------------------------------------
# Public hooks
# ---------------------------------------------------------------------


def llm_propose(
    *,
    iter_idx: int,
    n_trials: int,
    space: SearchSpace,
    history: Sequence[HistoryEntry],
) -> list[Proposal]:
    """Ask the remote LLM for ``n_trials`` proposals.

    Returns ``[]`` on any failure; the optimizer treats an empty batch
    as "fall back to the local rule path".
    """
    client = _build_client()
    if client is None:
        return []

    user_prompt = {
        "iter_idx": iter_idx,
        "n_trials": n_trials,
        "search_space": space.to_summary(),
        "history": _format_history(history),
        "instructions": (
            "Propose exactly N trials. Stay inside the search space. "
            "Diversify across grid / random / local-perturb / explore "
            "origins. Keep changed_params small (1-4 keys per trial)."
        ),
    }
    import json
    try:
        payload = client.complete_json(
            system_prompt=_SYSTEM_PROMPT_PROPOSER,
            user_prompt=json.dumps(user_prompt, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_propose: client.complete_json failed (%s)", exc)
        return []

    trials = payload.get("trials") if isinstance(payload, Mapping) else None
    if not isinstance(trials, list) or not trials:
        logger.warning("llm_propose: malformed payload (no trials list)")
        return []

    proposals: list[Proposal] = []
    for slot, raw in enumerate(trials[:n_trials]):
        if not isinstance(raw, Mapping):
            continue
        try:
            changed = dict(raw.get("changed_params") or {})
            validated = validate_proposal(changed, space)
        except ValueError as exc:
            logger.warning("llm_propose: trial %d invalid (%s); dropping", slot, exc)
            continue
        full = space.defaults()
        full.update(validated)
        proposals.append(
            Proposal(
                trial_id=str(raw.get("trial_id") or f"iter{iter_idx:02d}_llm{slot:02d}"),
                iter_idx=iter_idx,
                trial_idx=slot,
                origin="llm",
                parent_trial=raw.get("parent_trial") or None,
                changed_params=validated,
                full_params=full,
                rationale=str(raw.get("rationale") or "llm proposal"),
                predicted_metric_direction=str(
                    raw.get("predicted_metric_direction", "unknown")
                ),
            )
        )
    return proposals


def llm_review(
    *,
    iter_eval: Any,  # IterEvaluations; loose typed to avoid circular import
    history: Sequence[HistoryEntry],
) -> dict[str, Any]:
    """Ask the remote LLM for free-form notes about the iteration.

    Always returns a dict. On failure returns an empty dict so the
    caller writes ``llm_notes: {}`` and proceeds.
    """
    client = _build_client()
    if client is None:
        return {}
    import json
    try:
        user_prompt = {
            "iter_idx": iter_eval.iter_idx,
            "best_score": iter_eval.best_score,
            "top_k": list(iter_eval.top_k),
            "decision": iter_eval.decision,
            "evaluations": [e.to_dict() for e in iter_eval.evaluations],
            "history": _format_history(history),
        }
        payload = client.complete_json(
            system_prompt=_SYSTEM_PROMPT_REVIEWER,
            user_prompt=json.dumps(user_prompt, ensure_ascii=False, default=str),
        )
        if not isinstance(payload, dict):
            return {}
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_review: client.complete_json failed (%s)", exc)
        return {}


__all__ = [
    "llm_propose",
    "llm_review",
]
