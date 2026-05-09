from __future__ import annotations

"""Dual-run reconciliation tool for SIM A/B testing (Task 7 / S5).

Compares two SIM runs of classic_multifactor — typically:

* **Run A**: legacy ``run_loop.py`` (frozen on tag ``classic-pre-vnpy-rewrite-v1``)
* **Run B**: new ``run_intraday_loop.py`` (vnpy-native)

For each run, the script extracts a normalised metric vector from up to
three sources:

1. ``state/runs/<report-filename>``                    — aggregated counters
2. ``state/runs/orders/*.json`` ``OrderState``          — per-order ground truth
3. ``state/runs/events.jsonl``                          — gate/approval timeline

It then computes a per-metric absolute & relative delta and emits a JSON
report. A non-zero exit code is returned when any "hard" metric diverges
beyond the configured tolerance, which makes the script directly usable
in CI / cron after each dual-run trading day.

Usage
-----
::

    python3 scripts/diff_dual_run.py \\
        --run-a /path/to/legacy/state/runs \\
        --run-b /path/to/new/state/runs \\
        --report-filename-a classic_multifactor_NVDA_US_live_report.json \\
        --report-filename-b classic_multifactor_intraday_report.json \\
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


def _scan_orders(orders_dir: Path) -> dict[str, Any]:
    """Aggregate ``state/runs/orders/*.json`` (OrderState records).

    The same ``OrderStateStore`` schema is used by both the legacy and
    new branches, which makes this the most reliable reconciliation
    source.
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
    }
    if not orders_dir.exists():
        return out
    open_status = {
        "created", "validated", "risk_checked", "approval_required", "approved",
        "submitting", "submitted", "partial_filled", "cancel_requested",
    }
    failed_status = {"rejected", "expired", "failed"}
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
    return out


def _scan_events(events_path: Path) -> dict[str, Any]:
    out = {
        "events_total": 0,
        "events_order_approved": 0,
        "events_order_blocked": 0,
        "events_order_submitted": 0,
        "events_order_fill": 0,
        "events_order_status_update": 0,
        "blocked_by_gate": {},
    }
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

    # --- Source 2: orders/*.json (always tried)
    orders_summary = _scan_orders(state_root / "orders")
    if orders_summary["orders_total"] > 0:
        rm.orders_present = True
    rm.metrics.setdefault("submitted_count", float(orders_summary["submitted_count"]))
    rm.metrics["filled_qty_total"] = float(orders_summary["filled_qty_total"])
    rm.metrics["filled_notional_total"] = float(orders_summary["filled_notional_total"])
    rm.metrics["orders_open_residual"] = float(orders_summary["orders_open_residual"])
    rm.metrics["orders_failed_residual"] = float(orders_summary["orders_failed_residual"])
    rm.request_ids.update(orders_summary["request_ids"])
    rm.metrics["unique_request_ids"] = float(len(rm.request_ids))
    rm.samples["orders_total"] = orders_summary["orders_total"]
    rm.samples["broker_orderids_count"] = len(orders_summary["broker_orderids"])

    # --- Source 3: events.jsonl
    events_path = state_root / "events.jsonl"
    if events_path.exists():
        rm.events_present = True
        ev = _scan_events(events_path)
        for k in ("events_total", "events_order_approved", "events_order_blocked",
                  "events_order_submitted", "events_order_fill",
                  "events_order_status_update"):
            rm.metrics[k] = float(ev[k])
        # If the report didn't already provide approved_count, fall back to events.
        rm.metrics.setdefault("approved_count", float(ev["events_order_approved"]))
        # Merge gate-level counters (events are authoritative if present).
        for gate, count in ev["blocked_by_gate"].items():
            rm.blocked_by_gate[gate] = rm.blocked_by_gate.get(gate, 0) + int(count)
        rm.samples["events_total"] = ev["events_total"]

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

    if args.print_summary:
        _print_summary(summary, deltas)

    if failures:
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


if __name__ == "__main__":
    sys.exit(main())
