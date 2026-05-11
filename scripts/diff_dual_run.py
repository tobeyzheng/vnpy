from __future__ import annotations

"""Dual-run reconciliation tool for SIM A/B testing (Task 7 / S5).

Compares two SIM runs of classic_multifactor — typically:

* **Run A**: legacy ``run_loop.py`` (frozen on tag ``classic-pre-vnpy-rewrite-v1``)
* **Run B**: new ``run_intraday_loop.py`` (vnpy-native)

For each run, the script extracts a normalised metric vector from up to
three sources:

1. ``state/runs/<report-filename>``                             — aggregated counters
2. ``state/runs/<execution_env>/orders/*.json`` (or legacy ``orders/*.json``)
   ``OrderState``                                               — per-order ground truth
3. ``state/runs/<execution_env>/events.jsonl`` (or legacy root ``events.jsonl``)
                                                               — gate/approval timeline

It then computes a per-metric absolute & relative delta and emits a JSON
report. A non-zero exit code is returned when any "hard" metric diverges
beyond the configured tolerance, which makes the script directly usable
in CI / cron after each dual-run trading day.

Usage
-----
::

    python3 scripts/diff_dual_run.py \
        --run-a /path/to/legacy/state/runs \
        --run-b /path/to/new/state/runs \
        --report-filename-a classic_multifactor_NVDA_US_live_report.json \
        --report-filename-b classic_multifactor_intraday_report.json \
        --output state/runs/reports/dual_run_diff.json

Both ``--report-filename-*`` arguments are optional; when omitted the
report source is silently skipped (the orders + events sources are still
inspected). At least one source per run must yield non-empty data, else
the script exits with code 4 ("insufficient input").

Tolerance policy
----------------
A metric ``m`` is considered ``OK`` when::

    abs(b - a) <= max(abs_tol[m], rel_tol[m] * max(abs(a), abs(b)))

Defaults: ``abs_tol = 2``, ``rel_tol = 0.02``. Both can be overridden
per-metric via ``--abs-tol`` / ``--rel-tol`` (key=value form).

Strict business-key mode
------------------------
``--strict-rids`` activates a stricter ``request_ids`` comparison: instead
of relying on raw ``request_id`` strings (which embed a per-process uuid
and therefore *always* differ between A and B), the script groups
orders by the business 5-tuple
``(strategy_id, market, symbol, side, qty, price-bucket-2dp)``. Two
orders are considered equivalent if and only if all five fields match,
with ``price`` rounded to 2 decimal places. Pairs with a missing
counterpart in the other run are reported as ``only_a`` / ``only_b``
business intents (these *are* real divergences and should investigate).

Markdown summary
----------------
Pass ``--markdown <path.md>`` to additionally emit a Github-friendly
summary table for PR / changelog inclusion.

The script never writes to brokerage state and never connects to
OpenD — it is a pure file diff. Safe to run unattended.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_ORDER_ENV_DIRS = ("dry_run", "futu_sim", "futu_real")

# Hard metrics — divergence => exit code 1 (block CI / cron pipeline).
HARD_METRICS = {
    "submitted_count",
    "filled_qty_total",
    "unique_request_ids",
}

# Soft metrics — divergence => warning only (still emitted to JSON).
SOFT_METRICS = {
    "approved_count",
    "filled_notional_total",
    "events_total",
    "events_order_blocked",
    "events_order_approved",
    "orders_open_residual",
    "orders_failed_residual",
}


def _candidate_order_dirs(state_root: Path) -> list[Path]:
    """Return execution-env order directories plus legacy fallback when present."""

    candidates = [state_root / env / "orders" for env in _ORDER_ENV_DIRS]
    legacy = state_root / "orders"
    out: list[Path] = []
    seen: set[Path] = set()
    for path in [*candidates, legacy]:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            out.append(path)
    return out


def _candidate_event_paths(state_root: Path) -> list[Path]:
    """Return execution-env event logs plus legacy root-level fallback."""

    candidates = [state_root / env / "events.jsonl" for env in _ORDER_ENV_DIRS]
    legacy = state_root / "events.jsonl"
    out: list[Path] = []
    seen: set[Path] = set()
    for path in [*candidates, legacy]:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

@dataclass
class RunMetrics:
    run_name: str
    state_root: Path
    report_path: Path | None = None
    report_present: bool = False
    orders_present: bool = False
    events_present: bool = False
    metrics: dict[str, float] = field(default_factory=dict)
    blocked_by_gate: dict[str, int] = field(default_factory=dict)
    request_ids: set[str] = field(default_factory=set, repr=False)
    samples: dict[str, Any] = field(default_factory=dict)
    # Business-key tuples for --strict-rids mode. Each tuple is
    # (strategy_id, market, symbol, side, qty, price_bucket).
    business_keys: list[tuple[str, str, str, str, int, str]] = field(
        default_factory=list, repr=False
    )


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue
    except OSError:
        return


def _extract_report_metrics(payload: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    """Pull canonical counters out of either old or new report shapes.

    Old report (services/trading_pipeline/live_task.py): includes
    ``orders_submitted`` / ``orders_approved`` / ``orders_blocked`` /
    ``trades`` arrays at the top level.

    New report (run_intraday_loop.py / run_daily_rebalance.py):
    ``approved`` / ``blocked: {gate: count}`` / ``live_submit``.

    Anything missing is left out of the metrics dict so the diff can
    still proceed using orders/events as a fallback source.
    """

    metrics: dict[str, float] = {}
    blocked: dict[str, int] = {}

    # New shape
    if "approved" in payload and isinstance(payload["approved"], (int, float)):
        metrics["approved_count"] = float(payload["approved"])
    if "blocked" in payload and isinstance(payload["blocked"], dict):
        blocked = {str(k): int(v) for k, v in payload["blocked"].items() if isinstance(v, (int, float))}

    # Old shape — best-effort.
    for key, dst in [
        ("orders_approved", "approved_count"),
        ("orders_submitted", "submitted_count"),
        ("approved_count", "approved_count"),
        ("submitted_count", "submitted_count"),
    ]:
        if key in payload and dst not in metrics and isinstance(payload[key], (int, float)):
            metrics[dst] = float(payload[key])

    if "orders_blocked" in payload and isinstance(payload["orders_blocked"], dict) and not blocked:
        blocked = {str(k): int(v) for k, v in payload["orders_blocked"].items() if isinstance(v, (int, float))}

    return metrics, blocked


def _scan_orders(order_dirs: Iterable[Path]) -> dict[str, Any]:
    """Aggregate ``OrderState`` records from new and legacy order-state layouts.

    The preferred layout is ``state/runs/<execution_env>/orders/*.json``.
    For backward compatibility we still scan legacy ``state/runs/orders/*.json``
    when present.
    """

    out = {
        "orders_total": 0,
        "submitted_count": 0,
        "filled_qty_total": 0,
        "filled_notional_total": 0.0,
        "orders_open_residual": 0,
        "orders_failed_residual": 0,
        "request_ids": set(),
        "broker_orderids": set(),
        "business_keys": [],
        "scanned_dirs": [],
    }
    open_status = {
        "created", "validated", "risk_checked", "approval_required", "approved",
        "submitting", "submitted", "partial_filled", "cancel_requested",
    }
    failed_status = {"rejected", "expired", "failed"}
    for orders_dir in order_dirs:
        if not orders_dir.exists():
            continue
        out["scanned_dirs"].append(str(orders_dir))
        for path in sorted(orders_dir.glob("*.json")):
            data = _safe_load_json(path)
            if not isinstance(data, dict):
                continue
            out["orders_total"] += 1
            rid = str(data.get("request_id") or "").strip()
            if rid:
                out["request_ids"].add(rid)
            broker = str(data.get("broker_order_id") or "").strip()
            if broker:
                out["broker_orderids"].add(broker)
                out["submitted_count"] += 1
            filled = int(data.get("filled_qty") or 0)
            if filled > 0:
                out["filled_qty_total"] += filled
                avg_price = data.get("avg_fill_price")
                if isinstance(avg_price, (int, float)):
                    out["filled_notional_total"] += float(avg_price) * filled
            status = str(data.get("status") or "").strip()
            if status in open_status and status != "approved":
                # ``approved`` alone (without broker_order_id) is a dry-run trace;
                # only count truly open submitted orders as residual.
                if broker:
                    out["orders_open_residual"] += 1
            elif status in failed_status:
                out["orders_failed_residual"] += 1
            # Business 5-tuple for --strict-rids mode. Price is bucketed to
            # 2 decimal places to absorb the per-order limit-price jitter
            # that is identical across A/B in classic_multifactor.
            strategy_id = str(data.get("strategy_id") or "").strip()
            market = str(data.get("market") or "").strip()
            symbol = str(data.get("symbol") or "").strip()
            side = str(data.get("side") or "").strip()
            qty = int(data.get("qty") or 0)
            price = data.get("price")
            try:
                price_bucket = f"{float(price):.2f}" if price is not None else "-"
            except (TypeError, ValueError):
                price_bucket = "-"
            if strategy_id and symbol and side and qty:
                out["business_keys"].append(
                    (strategy_id, market, symbol, side, qty, price_bucket)
                )
    return out


def _scan_events(events_paths: Iterable[Path]) -> dict[str, Any]:
    out = {
        "events_total": 0,
        "events_order_approved": 0,
        "events_order_blocked": 0,
        "events_order_submitted": 0,
        "events_order_fill": 0,
        "events_order_status_update": 0,
        "blocked_by_gate": {},
        "scanned_paths": [],
    }
    for events_path in events_paths:
        if not events_path.exists():
            continue
        out["scanned_paths"].append(str(events_path))
        for evt in _iter_jsonl(events_path):
            out["events_total"] += 1
            kind = str(evt.get("event") or "")
            if kind == "order_approved":
                out["events_order_approved"] += 1
            elif kind == "order_blocked":
                out["events_order_blocked"] += 1
                gate = str(evt.get("gate") or "unknown")
                out["blocked_by_gate"][gate] = out["blocked_by_gate"].get(gate, 0) + 1
            elif kind == "order_submitted":
                out["events_order_submitted"] += 1
            elif kind == "order_fill":
                out["events_order_fill"] += 1
            elif kind == "order_status_update":
                out["events_order_status_update"] += 1
    return out


def collect_run_metrics(
    run_name: str,
    state_root: Path,
    report_filename: str | None,
) -> RunMetrics:
    state_root = state_root.expanduser().resolve()
    rm = RunMetrics(run_name=run_name, state_root=state_root)

    # --- Source 1: report.json
    if report_filename:
        rp = state_root / report_filename
        rm.report_path = rp
        report_data = _safe_load_json(rp) if rp.exists() else None
        if report_data is not None:
            rm.report_present = True
            metrics, blocked = _extract_report_metrics(report_data)
            for k, v in metrics.items():
                rm.metrics.setdefault(k, v)
            if blocked:
                rm.blocked_by_gate.update(blocked)
            rm.samples["report_keys"] = sorted(report_data.keys())[:20]

    # --- Source 2: execution-env orders/*.json (legacy root-level orders still supported)
    orders_summary = _scan_orders(_candidate_order_dirs(state_root))
    if orders_summary["orders_total"] > 0:
        rm.orders_present = True
    rm.metrics.setdefault("submitted_count", float(orders_summary["submitted_count"]))
    rm.metrics["filled_qty_total"] = float(orders_summary["filled_qty_total"])
    rm.metrics["filled_notional_total"] = float(orders_summary["filled_notional_total"])
    rm.metrics["orders_open_residual"] = float(orders_summary["orders_open_residual"])
    rm.metrics["orders_failed_residual"] = float(orders_summary["orders_failed_residual"])
    rm.request_ids.update(orders_summary["request_ids"])
    rm.metrics["unique_request_ids"] = float(len(rm.request_ids))
    rm.business_keys = list(orders_summary["business_keys"])
    rm.samples["orders_total"] = orders_summary["orders_total"]
    rm.samples["broker_orderids_count"] = len(orders_summary["broker_orderids"])
    rm.samples["orders_dirs"] = orders_summary["scanned_dirs"]

    # --- Source 3: execution-env events.jsonl (legacy root-level events still supported)
    events_summary = _scan_events(_candidate_event_paths(state_root))
    if events_summary["events_total"] > 0:
        rm.events_present = True
        for k in (
            "events_total",
            "events_order_approved",
            "events_order_blocked",
            "events_order_submitted",
            "events_order_fill",
            "events_order_status_update",
        ):
            rm.metrics[k] = float(events_summary[k])
        # If the report didn't already provide approved_count, fall back to events.
        rm.metrics.setdefault("approved_count", float(events_summary["events_order_approved"]))
        # Merge gate-level counters (events are authoritative if present).
        for gate, count in events_summary["blocked_by_gate"].items():
            rm.blocked_by_gate[gate] = rm.blocked_by_gate.get(gate, 0) + int(count)
        rm.samples["events_total"] = events_summary["events_total"]
        rm.samples["event_paths"] = events_summary["scanned_paths"]

    rm.metrics.setdefault("approved_count", 0.0)
    rm.metrics.setdefault("submitted_count", 0.0)
    rm.metrics.setdefault("events_total", 0.0)
    rm.metrics.setdefault("events_order_blocked", 0.0)
    rm.metrics.setdefault("events_order_approved", 0.0)
    return rm


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

@dataclass
class MetricDelta:
    metric: str
    a: float
    b: float
    delta: float
    rel_delta: float
    severity: str  # "hard" / "soft"
    status: str  # "ok" / "warn" / "fail"
    note: str = ""


def _diff_metrics(
    a: RunMetrics, b: RunMetrics, abs_tol: dict[str, float], rel_tol: dict[str, float]
) -> list[MetricDelta]:
    keys = set(a.metrics.keys()) | set(b.metrics.keys())
    deltas: list[MetricDelta] = []
    for key in sorted(keys):
        va = float(a.metrics.get(key, 0.0))
        vb = float(b.metrics.get(key, 0.0))
        diff = vb - va
        denom = max(abs(va), abs(vb), 1.0)
        rel = abs(diff) / denom
        atol = float(abs_tol.get(key, abs_tol.get("__default__", 2.0)))
        rtol = float(rel_tol.get(key, rel_tol.get("__default__", 0.02)))
        within = abs(diff) <= max(atol, rtol * max(abs(va), abs(vb)))
        severity = "hard" if key in HARD_METRICS else "soft"
        if within:
            status = "ok"
        elif severity == "soft":
            status = "warn"
        else:
            status = "fail"
        deltas.append(
            MetricDelta(
                metric=key, a=va, b=vb, delta=diff, rel_delta=rel,
                severity=severity, status=status,
            )
        )
    return deltas


def _diff_blocked_by_gate(a: RunMetrics, b: RunMetrics) -> dict[str, dict[str, int]]:
    gates = set(a.blocked_by_gate) | set(b.blocked_by_gate)
    return {
        gate: {
            "a": int(a.blocked_by_gate.get(gate, 0)),
            "b": int(b.blocked_by_gate.get(gate, 0)),
            "delta": int(b.blocked_by_gate.get(gate, 0)) - int(a.blocked_by_gate.get(gate, 0)),
        }
        for gate in sorted(gates)
    }


def _diff_request_ids(a: RunMetrics, b: RunMetrics) -> dict[str, Any]:
    only_a = sorted(a.request_ids - b.request_ids)
    only_b = sorted(b.request_ids - a.request_ids)
    common = sorted(a.request_ids & b.request_ids)
    return {
        "common_count": len(common),
        "only_a_count": len(only_a),
        "only_b_count": len(only_b),
        # Cap samples so the JSON stays manageable.
        "only_a_sample": only_a[:20],
        "only_b_sample": only_b[:20],
    }


def _diff_business_keys(a: RunMetrics, b: RunMetrics) -> dict[str, Any]:
    """Compare two runs by business 5-tuple multiset semantics.

    Each tuple is ``(strategy_id, market, symbol, side, qty, price_bucket)``.
    Multiplicities matter — if A submitted the same intent twice but B
    only once, the duplicated tuple shows up in ``only_a`` once. This
    catches both "missing intent in B" and "extra intent in B" cases
    that the raw uuid-based ``--request-ids`` diff cannot detect.
    """

    from collections import Counter

    counter_a = Counter(a.business_keys)
    counter_b = Counter(b.business_keys)
    only_a = counter_a - counter_b
    only_b = counter_b - counter_a
    common = counter_a & counter_b

    def _format(counter: Counter) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key, count in sorted(counter.items()):
            strategy_id, market, symbol, side, qty, price_bucket = key
            items.append(
                {
                    "strategy_id": strategy_id,
                    "market": market,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price_bucket": price_bucket,
                    "count": count,
                }
            )
        return items

    return {
        "common_unique_count": len(common),
        "common_total_count": int(sum(common.values())),
        "only_a_unique_count": len(only_a),
        "only_a_total_count": int(sum(only_a.values())),
        "only_b_unique_count": len(only_b),
        "only_b_total_count": int(sum(only_b.values())),
        # Cap samples so the JSON stays manageable.
        "only_a_sample": _format(only_a)[:20],
        "only_b_sample": _format(only_b)[:20],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_kv_floats(pairs: list[str] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for token in pairs or []:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        try:
            out[key.strip()] = float(value.strip())
        except ValueError:
            continue
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Diff two SIM runs of classic_multifactor (legacy vs vnpy-native)."
    )
    p.add_argument("--run-a", required=True, type=Path, help="Path to legacy state/runs root")
    p.add_argument("--run-b", required=True, type=Path, help="Path to new state/runs root")
    p.add_argument("--name-a", type=str, default="legacy")
    p.add_argument("--name-b", type=str, default="vnpy_native")
    p.add_argument("--report-filename-a", type=str, default="")
    p.add_argument("--report-filename-b", type=str, default="classic_multifactor_intraday_report.json")
    p.add_argument(
        "--output",
        type=Path,
        default=Path("state/runs/reports/dual_run_diff.json"),
        help="Where to write the JSON diff report",
    )
    p.add_argument(
        "--abs-tol",
        nargs="*",
        default=[],
        help="Per-metric absolute tolerance overrides (key=value). __default__=2",
    )
    p.add_argument(
        "--rel-tol",
        nargs="*",
        default=[],
        help="Per-metric relative tolerance overrides (key=value). __default__=0.02",
    )
    p.add_argument("--print-summary", action="store_true", default=True)
    p.add_argument("--no-print-summary", dest="print_summary", action="store_false")
    p.add_argument(
        "--strict-rids",
        action="store_true",
        default=False,
        help=(
            "Compare orders by business 5-tuple (strategy_id,market,"
            "symbol,side,qty,price_bucket) instead of uuid request_ids. "
            "Any only_a / only_b tuples become a HARD failure."
        ),
    )
    p.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Optional path to write a Github-friendly markdown summary.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    abs_tol = _parse_kv_floats(args.abs_tol)
    rel_tol = _parse_kv_floats(args.rel_tol)
    abs_tol.setdefault("__default__", 2.0)
    rel_tol.setdefault("__default__", 0.02)

    rm_a = collect_run_metrics(args.name_a, args.run_a, args.report_filename_a or None)
    rm_b = collect_run_metrics(args.name_b, args.run_b, args.report_filename_b or None)

    # Sanity: at least one source must be present per run.
    if not (rm_a.report_present or rm_a.orders_present or rm_a.events_present):
        print(f"insufficient input for run-a at {rm_a.state_root}", file=sys.stderr)
        return 4
    if not (rm_b.report_present or rm_b.orders_present or rm_b.events_present):
        print(f"insufficient input for run-b at {rm_b.state_root}", file=sys.stderr)
        return 4

    deltas = _diff_metrics(rm_a, rm_b, abs_tol, rel_tol)
    gate_diff = _diff_blocked_by_gate(rm_a, rm_b)
    rid_diff = _diff_request_ids(rm_a, rm_b)
    biz_diff: dict[str, Any] | None = None
    if args.strict_rids:
        biz_diff = _diff_business_keys(rm_a, rm_b)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_a": {
            "name": rm_a.run_name,
            "state_root": str(rm_a.state_root),
            "report_path": str(rm_a.report_path) if rm_a.report_path else None,
            "report_present": rm_a.report_present,
            "orders_present": rm_a.orders_present,
            "events_present": rm_a.events_present,
            "samples": rm_a.samples,
        },
        "run_b": {
            "name": rm_b.run_name,
            "state_root": str(rm_b.state_root),
            "report_path": str(rm_b.report_path) if rm_b.report_path else None,
            "report_present": rm_b.report_present,
            "orders_present": rm_b.orders_present,
            "events_present": rm_b.events_present,
            "samples": rm_b.samples,
        },
        "deltas": [asdict(d) for d in deltas],
        "blocked_by_gate": gate_diff,
        "request_ids": rid_diff,
    }
    if biz_diff is not None:
        summary["business_keys"] = biz_diff

    failures = [d for d in deltas if d.status == "fail"]
    warnings = [d for d in deltas if d.status == "warn"]
    summary["totals"] = {
        "metrics": len(deltas),
        "ok": sum(1 for d in deltas if d.status == "ok"),
        "warn": len(warnings),
        "fail": len(failures),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Optional markdown report.
    if args.markdown is not None:
        md = _render_markdown(summary, deltas)
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md, encoding="utf-8")

    if args.print_summary:
        _print_summary(summary, deltas)

    # Strict-rids divergence is treated as a HARD failure.
    biz_fail = bool(
        biz_diff
        and (biz_diff["only_a_total_count"] > 0 or biz_diff["only_b_total_count"] > 0)
    )
    if failures or biz_fail:
        return 1
    if warnings:
        return 0  # warnings don't fail the run by default
    return 0


def _print_summary(summary: dict[str, Any], deltas: list[MetricDelta]) -> None:
    a_root = summary["run_a"]["state_root"]
    b_root = summary["run_b"]["state_root"]
    print(f"=== diff_dual_run: {summary['run_a']['name']} vs {summary['run_b']['name']} ===")
    print(f" run_a: {a_root}")
    print(f" run_b: {b_root}")
    sources_a = []
    for label, key in [("report", "report_present"), ("orders", "orders_present"), ("events", "events_present")]:
        if summary["run_a"][key]:
            sources_a.append(label)
    sources_b = []
    for label, key in [("report", "report_present"), ("orders", "orders_present"), ("events", "events_present")]:
        if summary["run_b"][key]:
            sources_b.append(label)
    print(f" sources_a: {','.join(sources_a) or '(none)'}")
    print(f" sources_b: {','.join(sources_b) or '(none)'}")
    totals = summary["totals"]
    print(f" totals: ok={totals['ok']}  warn={totals['warn']}  fail={totals['fail']}")
    print("-- metric                       a            b           delta   rel    status")
    for d in deltas:
        marker = {"ok": "  ", "warn": "**", "fail": "!!"}.get(d.status, "  ")
        print(f"{marker} {d.metric:<28} {d.a:>10.2f}  {d.b:>10.2f}  {d.delta:>+8.2f}  {d.rel_delta:>5.1%}  {d.status}")
    if summary["blocked_by_gate"]:
        print("-- blocked_by_gate")
        for gate, info in summary["blocked_by_gate"].items():
            mark = "  " if info["delta"] == 0 else "**"
            print(f"{mark} {gate:<24} a={info['a']:<5} b={info['b']:<5} d={info['delta']:+d}")
    rid = summary["request_ids"]
    print(f"-- request_ids: common={rid['common_count']} only_a={rid['only_a_count']} only_b={rid['only_b_count']}")
    biz = summary.get("business_keys")
    if biz:
        print(
            "-- business_keys (strict-rids):"
            f" common_unique={biz['common_unique_count']}"
            f" only_a={biz['only_a_unique_count']}/{biz['only_a_total_count']}"
            f" only_b={biz['only_b_unique_count']}/{biz['only_b_total_count']}"
        )
        for sample in biz["only_a_sample"][:5]:
            print(f"   only_a: {sample}")
        for sample in biz["only_b_sample"][:5]:
            print(f"   only_b: {sample}")


def _render_markdown(summary: dict[str, Any], deltas: list[MetricDelta]) -> str:
    """Render the diff as a Github-flavoured markdown report.

    The output is intentionally compact — a single H2, a totals line, a
    metric table and (optionally) a strict-rids divergence table.
    """

    a_name = summary["run_a"]["name"]
    b_name = summary["run_b"]["name"]
    totals = summary["totals"]
    biz = summary.get("business_keys")

    overall = "✅ PASS"
    if totals["fail"] > 0:
        overall = "❌ FAIL"
    elif biz and (biz["only_a_total_count"] > 0 or biz["only_b_total_count"] > 0):
        overall = "❌ FAIL (strict-rids)"
    elif totals["warn"] > 0:
        overall = "⚠️ WARN"

    lines: list[str] = []
    lines.append(f"## Dual-run diff — {a_name} vs {b_name}")
    lines.append("")
    lines.append(f"- generated_at: `{summary['generated_at']}`")
    lines.append(f"- run_a state_root: `{summary['run_a']['state_root']}`")
    lines.append(f"- run_b state_root: `{summary['run_b']['state_root']}`")
    lines.append(
        f"- totals: **{overall}** · ok={totals['ok']} · warn={totals['warn']} · fail={totals['fail']}"
    )
    lines.append("")

    lines.append("### Metric deltas")
    lines.append("")
    lines.append("| metric | a | b | delta | rel | severity | status |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for d in deltas:
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(d.status, "❓")
        lines.append(
            f"| `{d.metric}` | {d.a:.2f} | {d.b:.2f} | {d.delta:+.2f} | "
            f"{d.rel_delta:.1%} | {d.severity} | {icon} {d.status} |"
        )
    lines.append("")

    if summary.get("blocked_by_gate"):
        lines.append("### Blocked by gate")
        lines.append("")
        lines.append("| gate | a | b | delta |")
        lines.append("|---|---:|---:|---:|")
        for gate, info in summary["blocked_by_gate"].items():
            lines.append(
                f"| `{gate}` | {info['a']} | {info['b']} | {info['delta']:+d} |"
            )
        lines.append("")

    rid = summary["request_ids"]
    lines.append("### Request IDs (uuid-level)")
    lines.append("")
    lines.append(
        f"- common: **{rid['common_count']}** · only_a: {rid['only_a_count']} · only_b: {rid['only_b_count']}"
    )
    lines.append("")
    lines.append(
        "> Note: a non-zero only_a/only_b at the uuid level is **expected** "
        "because each process generates its own request_id. Use `--strict-rids` "
        "to compare by business 5-tuple instead."
    )
    lines.append("")

    if biz:
        lines.append("### Business keys (strict-rids 5-tuple)")
        lines.append("")
        lines.append(
            f"- common (unique/total): **{biz['common_unique_count']} / {biz['common_total_count']}**"
        )
        lines.append(
            f"- only_a (unique/total): **{biz['only_a_unique_count']} / {biz['only_a_total_count']}**"
        )
        lines.append(
            f"- only_b (unique/total): **{biz['only_b_unique_count']} / {biz['only_b_total_count']}**"
        )
        if biz["only_a_sample"] or biz["only_b_sample"]:
            lines.append("")
            lines.append("| run | strategy_id | market | symbol | side | qty | price | n |")
            lines.append("|---|---|---|---|---|---:|---:|---:|")
            for s in biz["only_a_sample"]:
                lines.append(
                    f"| only_a | `{s['strategy_id']}` | {s['market']} | {s['symbol']} | "
                    f"{s['side']} | {s['qty']} | {s['price_bucket']} | {s['count']} |"
                )
            for s in biz["only_b_sample"]:
                lines.append(
                    f"| only_b | `{s['strategy_id']}` | {s['market']} | {s['symbol']} | "
                    f"{s['side']} | {s['qty']} | {s['price_bucket']} | {s['count']} |"
                )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"



if __name__ == "__main__":
    sys.exit(main())
