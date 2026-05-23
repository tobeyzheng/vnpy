"""Evaluator subagent: score every trial in a single iteration.

Pure stdlib. Reads ``summary.json`` + ``equity_curve.csv`` + (optionally)
``trade_ledger.csv`` from each trial dir, derives risk-adjusted
metrics, applies a configurable score formula and produces a stable
ranking + ``continue/stop`` decision per requirement 5.x.

The evaluator never imports the engine or the strategy; it only reads
artefacts produced by ``trial_runner.run_trial``.
"""

from __future__ import annotations

import csv
import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io_schemas import (
    Evaluation,
    IterEvaluations,
    LeaderboardRow,
    TrialResult,
    write_json,
    write_leaderboard_csv,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------


DEFAULT_SCORE_FORMULA: dict[str, Any] = {
    "weights": {
        "annualised_return_pct": 0.5,
        "calmar_proxy": 0.3,
        "max_drawdown_pct": -0.2,  # higher dd -> worse
    },
    "min_trades_for_validity": 5,
}


@dataclass
class StopRules:
    """Termination thresholds (requirement 5.4)."""
    min_delta: float = 0.5      # ε for "best score not improving"
    patience: int = 3            # iters of no top-k change
    max_iters: int = 10
    max_runtime_min: float = 90.0


@dataclass
class IterContext:
    """Cross-iteration state the evaluator needs to make decisions."""
    iter_idx: int
    started_at_monotonic: float
    cumulative_runtime_sec: float
    prev_best_score: float | None
    prev_top_k_ids: list[str]
    consecutive_no_change: int
    baseline_score: float | None = None  # score of fixed-pool baseline


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------


def _read_equity_curve(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_trade_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _compute_returns(navs: Sequence[float]) -> list[float]:
    rets: list[float] = []
    for i in range(1, len(navs)):
        prev = navs[i - 1]
        if prev > 0:
            rets.append(navs[i] / prev - 1.0)
    return rets


def _annualised_vol_pct(returns: Sequence[float], annual_trading_days: int) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = statistics.pstdev(returns)
    return float(sigma * math.sqrt(annual_trading_days) * 100.0)


def derive_metrics(
    summary: Mapping[str, Any],
    equity_rows: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute the requirement-5.1 metric set from raw artefacts."""

    annual_trading_days = int(summary.get("annual_trading_days", 252))
    trading_days = int(summary.get("trading_days", len(equity_rows)))
    init_cash = float(summary.get("init_cash", 0.0) or 0.0)

    navs = []
    nonzero_position_days = 0
    for row in equity_rows:
        try:
            nav = float(row.get("nav", 0.0))
        except (TypeError, ValueError):
            continue
        navs.append(nav)
        try:
            pv = float(row.get("position_value", 0.0))
        except (TypeError, ValueError):
            pv = 0.0
        if pv > 0:
            nonzero_position_days += 1

    rets = _compute_returns(navs)
    ann_vol_pct = _annualised_vol_pct(rets, annual_trading_days)

    annualised_return_pct = float(summary.get("annualised_return_pct", 0.0) or 0.0)
    max_drawdown_pct = float(summary.get("max_drawdown_pct", 0.0) or 0.0)
    total_return_pct = float(summary.get("total_return_pct", 0.0) or 0.0)
    trade_count = int(summary.get("trade_count", 0) or 0)
    win_count = int(summary.get("win_count", 0) or 0)
    loss_count = int(summary.get("loss_count", 0) or 0)

    sharpe_proxy: float | None
    if ann_vol_pct > 0:
        sharpe_proxy = round(annualised_return_pct / ann_vol_pct, 4)
    else:
        sharpe_proxy = None

    calmar_proxy: float | None
    if max_drawdown_pct > 0:
        calmar_proxy = round(annualised_return_pct / max_drawdown_pct, 4)
    else:
        calmar_proxy = None

    win_rate: float | str
    if (win_count + loss_count) > 0:
        win_rate = round(win_count / (win_count + loss_count), 4)
    else:
        win_rate = "unknown"

    turnover_proxy = round(trade_count / trading_days, 4) if trading_days > 0 else 0.0
    exposure_days = (
        round(nonzero_position_days / trading_days, 4)
        if trading_days > 0
        else 0.0
    )

    return {
        "total_return_pct": total_return_pct,
        "annualised_return_pct": annualised_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "annualised_vol_pct": round(ann_vol_pct, 4),
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "sharpe_proxy": sharpe_proxy,
        "calmar_proxy": calmar_proxy,
        "turnover_proxy": turnover_proxy,
        "exposure_days": exposure_days,
        "trading_days": trading_days,
        "init_cash": init_cash,
        "trade_ledger_rows": len(trades),
    }


# ---------------------------------------------------------------------
# Score / ranking
# ---------------------------------------------------------------------


def _normalise(values: Sequence[float | None]) -> list[float | None]:
    """Min-max scale into [0, 1]; ``None`` is preserved."""
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return [None] * len(values)
    lo, hi = min(finite), max(finite)
    span = hi - lo
    if span == 0:
        return [0.5 if v is not None else None for v in values]
    return [None if v is None else (v - lo) / span for v in values]


def compute_scores(
    metric_rows: Sequence[Mapping[str, Any]],
    formula: Mapping[str, Any] | None = None,
) -> tuple[list[float | None], dict[str, Any]]:
    """Return (scores, normalisation_meta).

    ``metric_rows`` is one entry per trial (``derive_metrics`` output).
    ``None`` entries (missing / failed trials) get ``score=None`` so they
    do not pollute ranking.
    """
    f = dict(formula or DEFAULT_SCORE_FORMULA)
    weights: Mapping[str, float] = f.get("weights", {})
    keys = list(weights.keys())

    columns: dict[str, list[float | None]] = {}
    for k in keys:
        col: list[float | None] = []
        for row in metric_rows:
            if row is None:
                col.append(None)
            else:
                v = row.get(k)
                col.append(float(v) if v is not None and isinstance(v, (int, float)) else None)
        columns[k] = col

    normalised: dict[str, list[float | None]] = {
        k: _normalise(col) for k, col in columns.items()
    }

    scores: list[float | None] = []
    for i in range(len(metric_rows)):
        if metric_rows[i] is None:
            scores.append(None)
            continue
        s = 0.0
        any_value = False
        for k, w in weights.items():
            v = normalised[k][i]
            if v is None:
                continue
            s += float(w) * v
            any_value = True
        scores.append(round(s, 4) if any_value else None)

    meta = {
        "formula": dict(f),
        "raw_columns": {k: list(v) for k, v in columns.items()},
        "normalised_columns": {k: list(v) for k, v in normalised.items()},
    }
    return scores, meta


# ---------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------


def _diagnose(metrics: Mapping[str, Any], score_pop: Sequence[float]) -> dict[str, Any]:
    """Heuristic flags per requirement 5.3."""
    diag: dict[str, Any] = {}
    trade_count = int(metrics.get("trade_count", 0) or 0)
    diag["suspicious_invalid"] = trade_count == 0
    diag["suspicious_thin_trades"] = trade_count > 0 and trade_count < 20
    exposure = metrics.get("exposure_days") or 0.0
    diag["suspicious_overconcentrated"] = float(exposure) > 0.95
    calmar = metrics.get("calmar_proxy")
    if calmar is not None and len(score_pop) > 1:
        mu = statistics.mean(score_pop)
        sd = statistics.pstdev(score_pop)
        if sd > 0 and (calmar - mu) / sd > 3.0:
            diag["suspicious_overfit"] = True
        else:
            diag["suspicious_overfit"] = False
    else:
        diag["suspicious_overfit"] = False
    return diag


# ---------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------


def evaluate_iteration(
    *,
    trial_results: Sequence[TrialResult],
    trial_dirs: Mapping[str, Path],
    iter_idx: int,
    score_formula: Mapping[str, Any] | None = None,
    top_k: int = 2,
    ctx: IterContext | None = None,
    rules: StopRules | None = None,
) -> IterEvaluations:
    """Score one iteration's worth of trials."""

    formula = dict(score_formula or DEFAULT_SCORE_FORMULA)
    rules = rules or StopRules()

    # 1. Materialise per-trial metrics rows (None for missing summaries).
    metric_rows: list[dict[str, Any] | None] = []
    raw_metric_rows: list[dict[str, Any]] = []
    statuses: list[str] = []
    for tr in trial_results:
        if tr.trial_status != "ok" or tr.summary is None:
            metric_rows.append(None)
            raw_metric_rows.append({})
            statuses.append(tr.trial_status if tr.trial_status != "ok" else "missing_summary")
            continue
        artefact_dir = Path(tr.artefact_dir or trial_dirs.get(tr.trial_id, ""))
        equity_csv = artefact_dir / "equity_curve.csv"
        ledger_csv = artefact_dir / "trade_ledger.csv"
        rows = _read_equity_curve(equity_csv)
        trades = _read_trade_ledger(ledger_csv)
        metrics = derive_metrics(tr.summary, rows, trades)
        metric_rows.append(metrics)
        raw_metric_rows.append(metrics)
        statuses.append("ok")

    # 2. Compute scores + diagnostics.
    scores, meta = compute_scores(metric_rows, formula=formula)
    score_pop = [s for s in scores if s is not None]

    # 3. Build Evaluation list (ordered by trial_idx).
    evaluations: list[Evaluation] = []
    for i, tr in enumerate(trial_results):
        score = scores[i]
        metrics = raw_metric_rows[i] or {}
        diag = _diagnose(metrics, score_pop) if metrics else {}
        evaluations.append(
            Evaluation(
                trial_id=tr.trial_id,
                iter_idx=tr.iter_idx,
                trial_idx=tr.trial_idx,
                status=statuses[i],
                score=score,
                metrics=metrics,
                diagnostics=diag,
            )
        )

    # 4. Rank descending by score (None last).
    ranked = sorted(
        evaluations,
        key=lambda e: (e.score is None, -(e.score or 0.0)),
    )
    for r, ev in enumerate(ranked, start=1):
        if ev.score is None:
            ev.rank = None
        else:
            ev.rank = r

    top_ids = [e.trial_id for e in ranked if e.score is not None][:top_k]
    best_score = ranked[0].score if ranked and ranked[0].score is not None else None

    # 5. continue/stop decision.
    decision, stop_reason, prev_best, no_change = "continue", None, None, 0
    if ctx is not None:
        prev_best = ctx.prev_best_score
        no_change = ctx.consecutive_no_change

    delta_vs_prev = None
    if best_score is not None and prev_best is not None:
        delta_vs_prev = round(best_score - prev_best, 4)

    delta_vs_baseline = None
    if best_score is not None and ctx is not None and ctx.baseline_score is not None:
        delta_vs_baseline = round(best_score - ctx.baseline_score, 4)

    if best_score is None:
        decision = "stop"
        stop_reason = "no_valid_trials"
    elif iter_idx + 1 >= rules.max_iters:
        decision = "stop"
        stop_reason = "max_iters"
    elif (
        ctx is not None
        and ctx.cumulative_runtime_sec / 60.0 >= rules.max_runtime_min
    ):
        decision = "stop"
        stop_reason = "time_budget"
    elif (
        ctx is not None
        and ctx.prev_top_k_ids
        and top_ids
        and set(ctx.prev_top_k_ids) == set(top_ids)
        and (no_change + 1) >= rules.patience
    ):
        decision = "stop"
        stop_reason = "no_top_k_change"
    elif (
        delta_vs_prev is not None
        and delta_vs_prev < rules.min_delta
        and (no_change + 1) >= rules.patience
    ):
        decision = "stop"
        stop_reason = "no_score_improvement"

    return IterEvaluations(
        iter_idx=iter_idx,
        score_formula=formula,
        evaluations=evaluations,
        top_k=top_ids,
        best_score=best_score,
        delta_vs_prev=delta_vs_prev,
        delta_vs_baseline=delta_vs_baseline,
        decision=decision,
        stop_reason=stop_reason,
    )


def write_iter_outputs(
    *,
    iter_eval: IterEvaluations,
    evaluations_json_path: Path,
    leaderboard_csv_path: Path,
) -> None:
    write_json(evaluations_json_path, iter_eval.to_dict())
    rows: list[LeaderboardRow] = []
    for ev in iter_eval.evaluations:
        m = ev.metrics or {}
        rows.append(
            LeaderboardRow(
                iter_idx=ev.iter_idx,
                trial_idx=ev.trial_idx,
                trial_id=ev.trial_id,
                origin=m.get("origin", "unknown"),
                status=ev.status,
                score=ev.score,
                total_return_pct=m.get("total_return_pct"),
                annualised_return_pct=m.get("annualised_return_pct"),
                max_drawdown_pct=m.get("max_drawdown_pct"),
                trade_count=m.get("trade_count"),
                sharpe_proxy=m.get("sharpe_proxy"),
                calmar_proxy=m.get("calmar_proxy"),
            )
        )
    write_leaderboard_csv(leaderboard_csv_path, rows)


__all__ = [
    "DEFAULT_SCORE_FORMULA",
    "IterContext",
    "StopRules",
    "compute_scores",
    "derive_metrics",
    "evaluate_iteration",
    "write_iter_outputs",
]
