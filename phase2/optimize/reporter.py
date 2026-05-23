"""Markdown report generator for a finished optimization session.

Renders ``<session_dir>/REPORT.md`` from on-disk artefacts (no LLM,
no extra backtests). Layout:
1. Session summary (id, time window, runtime, stop_reason)
2. Search-space summary
3. Per-iter leaderboards (compact)
4. Best trial deep-dive: parameter diff vs default + raw summary
5. Comparison table vs the fixed-pool baseline (if available)
6. Suggested next step

Stdlib-only.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io_schemas import SessionSummary, read_json
from .search_space import SearchSpace, load_search_space
from .session import SessionPaths

logger = logging.getLogger(__name__)


_BASELINE_RUN_ID = "fixed_pool_5y_a1"  # current canonical multi-backtest baseline


@dataclass
class _CoordResult:
    """Loose duck type matching ``coordinator.CoordinatorResult``."""
    session: SessionPaths
    summary: SessionSummary
    iterations: list[Any]


def _md_escape(text: Any) -> str:
    return str(text).replace("|", r"\|")


def _format_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def _read_iter_evaluations(session: SessionPaths, iter_idx: int) -> dict[str, Any] | None:
    p = session.evaluations_json(iter_idx)
    if not p.exists():
        return None
    try:
        return read_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def _iter_indices(session: SessionPaths) -> list[int]:
    if not session.session_dir.exists():
        return []
    indices: list[int] = []
    for child in session.session_dir.iterdir():
        if child.is_dir() and child.name.startswith("iter_"):
            try:
                indices.append(int(child.name.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
    return sorted(indices)


def _baseline_summary(session: SessionPaths) -> dict[str, Any] | None:
    bp = session.sibling_dir / _BASELINE_RUN_ID / "summary.json"
    if not bp.exists():
        return None
    try:
        return json.loads(bp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _best_trial_payload(session: SessionPaths, summary: SessionSummary) -> dict[str, Any] | None:
    if not summary.best_trial_path:
        return None
    p = Path(summary.best_trial_path)
    summary_path = p / "summary.json"
    applied_path = p / "applied_params.json"
    proposal_path = p / "proposal.json"
    payload: dict[str, Any] = {"path": str(p)}
    for tag, fp in (("summary", summary_path),
                    ("applied", applied_path),
                    ("proposal", proposal_path)):
        if fp.exists():
            try:
                payload[tag] = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload[tag] = None
    return payload


# ---------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------


def _render_header(summary: SessionSummary) -> list[str]:
    lines = [
        f"# phase2 strategy self-optimization — `{summary.session_id}`",
        "",
        f"- namespace: `{summary.namespace}`",
        f"- started_at (UTC): `{summary.started_at}`",
        f"- finished_at (UTC): `{summary.finished_at}`",
        f"- runtime_sec: `{summary.runtime_sec}`",
        f"- iterations: `{summary.iterations}`",
        f"- total_trials: `{summary.total_trials}`",
        f"- best_score: `{summary.best_score}`",
        f"- best_trial_path: `{summary.best_trial_path}`",
        f"- stop_reason: **`{summary.stop_reason}`**",
        f"- pool_config: `{summary.pool_config_path}`",
        f"- search_space: `{summary.search_space_path}`",
        "",
    ]
    return lines


def _render_search_space(space: SearchSpace | None) -> list[str]:
    if space is None:
        return ["## Search space\n", "_(unavailable)_\n"]
    lines = ["## Search space", "",
             f"- frozen: `{sorted(space.frozen)}`",
             "",
             "| param | type | default | min | max | step |",
             "|---|---|---|---|---|---|"]
    for name, p in space.params.items():
        lines.append(
            f"| `{name}` | {p.type} | {_md_escape(p.default)} | "
            f"{_md_escape(p.min)} | {_md_escape(p.max)} | "
            f"{_md_escape(p.step)} |"
        )
    lines.append("")
    return lines


def _render_iter_leaderboards(session: SessionPaths) -> list[str]:
    lines = ["## Per-iteration leaderboards", ""]
    for idx in _iter_indices(session):
        ev = _read_iter_evaluations(session, idx)
        if not ev:
            continue
        lines.append(f"### iter `{idx:02d}` — best=`{ev.get('best_score')}`  "
                     f"decision=`{ev.get('decision')}`  "
                     f"stop_reason=`{ev.get('stop_reason')}`")
        lines.append("")
        lines.append("| rank | trial | status | score | total% | ann% | mdd% | trades | sharpe | calmar |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for e in sorted(ev.get("evaluations") or [],
                        key=lambda x: (x.get("rank") is None, x.get("rank") or 99)):
            m = e.get("metrics") or {}
            lines.append(
                f"| {e.get('rank') or '—'} | `{e.get('trial_id')}` | "
                f"{e.get('status')} | {_format_pct(e.get('score'))} | "
                f"{_format_pct(m.get('total_return_pct'))} | "
                f"{_format_pct(m.get('annualised_return_pct'))} | "
                f"{_format_pct(m.get('max_drawdown_pct'))} | "
                f"{m.get('trade_count') or 0} | "
                f"{_format_pct(m.get('sharpe_proxy'))} | "
                f"{_format_pct(m.get('calmar_proxy'))} |"
            )
        lines.append("")
    return lines


def _render_best_trial(
    space: SearchSpace | None,
    payload: Mapping[str, Any] | None,
) -> list[str]:
    lines = ["## Best trial", ""]
    if not payload:
        lines.append("_(no best trial recorded)_\n")
        return lines

    lines.append(f"- path: `{payload.get('path')}`")
    summary = payload.get("summary") or {}
    applied = payload.get("applied") or {}
    proposal = payload.get("proposal") or {}
    if proposal:
        lines.append(f"- origin: `{proposal.get('origin')}`")
        lines.append(f"- parent_trial: `{proposal.get('parent_trial')}`")
        lines.append(f"- rationale: {proposal.get('rationale')}")
    lines.append("")

    if applied:
        lines.append("### Parameter diff vs strategy default")
        lines.append("")
        defaults = space.defaults() if space else {}
        lines.append("| param | default | applied | Δ |")
        lines.append("|---|---|---|---|")
        for k in sorted(applied.keys()):
            d_val = defaults.get(k, "—")
            a_val = applied.get(k)
            changed = "**yes**" if d_val != a_val else "—"
            lines.append(f"| `{k}` | {_md_escape(d_val)} | {_md_escape(a_val)} | {changed} |")
        lines.append("")

    if summary:
        lines.append("### summary.json (raw)")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return lines


def _render_baseline_compare(
    best: Mapping[str, Any] | None,
    baseline: Mapping[str, Any] | None,
) -> list[str]:
    if not best or not baseline:
        return []
    bs = best.get("summary") or {}
    if not bs:
        return []
    lines = ["## vs fixed-pool baseline", "",
             f"- baseline: `state/runs/phase2_multi_backtest/{_BASELINE_RUN_ID}/summary.json`",
             "",
             "| metric | best | baseline | Δ |",
             "|---|---|---|---|"]
    for key in (
        "total_return_pct", "annualised_return_pct", "max_drawdown_pct",
        "trade_count", "win_count", "loss_count", "trading_days",
    ):
        b_v = bs.get(key)
        base_v = baseline.get(key)
        delta = None
        if isinstance(b_v, (int, float)) and isinstance(base_v, (int, float)):
            delta = round(b_v - base_v, 4)
        lines.append(
            f"| `{key}` | {_md_escape(b_v)} | {_md_escape(base_v)} | "
            f"{_md_escape(delta)} |"
        )
    lines.append("")
    return lines


def _render_next_step(summary: SessionSummary, best: Mapping[str, Any] | None) -> list[str]:
    lines = ["## Suggested next step", ""]
    if summary.stop_reason == "no_top_k_change":
        lines.append("- top-k did not move for several iterations; consider widening "
                     "search space (e.g. relax `step` or extend `max`/`min` for "
                     "the most-locked param).")
    elif summary.stop_reason == "no_score_improvement":
        lines.append("- score plateau reached; re-run with a smaller `--min-delta` "
                     "or hand-craft a fresh seed parameter set.")
    elif summary.stop_reason == "max_iters":
        lines.append("- iteration budget exhausted; rerun with `--resume <session_id>` "
                     "or larger `--max-iters` if you still see improvement.")
    elif summary.stop_reason == "no_valid_trials":
        lines.append("- every trial was invalid; check `error.log` per trial and "
                     "the search-space YAML.")
    else:
        lines.append(f"- session ended with `stop_reason={summary.stop_reason}`.")

    if best and best.get("path"):
        lines.append(
            f"- Re-run the canonical multi-backtest with the best trial's "
            f"`applied_params.json` to confirm reproducibility before "
            f"adopting any change."
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------


def render_session_report(result: _CoordResult) -> Path:
    """Build ``<session_dir>/REPORT.md``. Returns the report path."""

    session: SessionPaths = result.session
    summary: SessionSummary = result.summary

    space: SearchSpace | None
    try:
        space = (
            load_search_space(summary.search_space_path)
            if summary.search_space_path
            else None
        )
    except Exception:  # noqa: BLE001
        space = None

    best_payload = _best_trial_payload(session, summary)
    baseline = _baseline_summary(session)

    lines: list[str] = []
    lines += _render_header(summary)
    lines += _render_search_space(space)
    lines += _render_iter_leaderboards(session)
    lines += _render_best_trial(space, best_payload)
    lines += _render_baseline_compare(best_payload, baseline)
    lines += _render_next_step(summary, best_payload)

    report_path = session.report_md
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("REPORT.md rendered: %s", report_path)
    return report_path


def print_cumulative_leaderboard(session: SessionPaths) -> int:
    """Print the cumulative leaderboard of an existing session.

    The same logic is also exposed by the runner's ``--print-leaderboard``
    flag; this entry exists so other code paths (and the test suite)
    can reuse it.
    """
    rows: list[dict[str, Any]] = []
    for idx in _iter_indices(session):
        lb = session.leaderboard_csv(idx)
        if not lb.exists():
            continue
        with lb.open("r", encoding="utf-8", newline="") as fh:
            rows.extend(csv.DictReader(fh))
    if not rows:
        print("(no leaderboard rows)")
        return 0
    fields = list(rows[0].keys())
    print("\t".join(fields))
    rows.sort(
        key=lambda r: (
            -float(r["score"]) if r.get("score") not in (None, "", "None") else 0.0,
        ),
    )
    for r in rows:
        print("\t".join(str(r.get(k, "")) for k in fields))
    return 0


__all__ = [
    "print_cumulative_leaderboard",
    "render_session_report",
]
