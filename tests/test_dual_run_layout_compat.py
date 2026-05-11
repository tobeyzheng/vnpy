from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.diff_dual_run import collect_run_metrics
from scripts.dual_run_preflight import probe_worktree


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_run_metrics_prefers_execution_env_layout_and_keeps_legacy_compat():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_json(
            root / "dry_run" / "orders" / "dry_order.json",
            {
                "request_id": "dry-1",
                "strategy_id": "classic_multifactor_09992_HK",
                "market": "HK",
                "symbol": "09992.SEHK",
                "side": "BUY",
                "qty": 100,
                "price": 48.2,
                "status": "approved",
                "broker_order_id": "",
                "filled_qty": 0,
            },
        )
        _write_json(
            root / "futu_sim" / "orders" / "sim_order.json",
            {
                "request_id": "sim-1",
                "strategy_id": "classic_multifactor_09992_HK",
                "market": "HK",
                "symbol": "09992.SEHK",
                "side": "BUY",
                "qty": 100,
                "price": 48.2,
                "status": "submitted",
                "broker_order_id": "FUTU.SIM.1",
                "filled_qty": 0,
            },
        )
        _write_json(
            root / "orders" / "legacy_order.json",
            {
                "request_id": "legacy-1",
                "strategy_id": "classic_multifactor_NVDA_US",
                "market": "US",
                "symbol": "NVDA.SMART",
                "side": "SELL",
                "qty": 2,
                "price": 900.0,
                "status": "filled",
                "broker_order_id": "LEGACY.1",
                "filled_qty": 2,
                "avg_fill_price": 901.0,
            },
        )

        (root / "dry_run" / "events.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (root / "dry_run" / "events.jsonl").write_text(
            json.dumps({"event": "order_approved"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (root / "futu_sim" / "events.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (root / "futu_sim" / "events.jsonl").write_text(
            json.dumps({"event": "order_submitted"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (root / "events.jsonl").write_text(
            json.dumps({"event": "order_fill"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        metrics = collect_run_metrics("compat", root, None)

        assert metrics.orders_present is True
        assert metrics.events_present is True
        assert metrics.metrics["submitted_count"] == 2.0
        assert metrics.metrics["filled_qty_total"] == 2.0
        assert metrics.metrics["events_total"] == 3.0
        assert metrics.metrics["events_order_approved"] == 1.0
        assert metrics.metrics["events_order_submitted"] == 1.0
        assert metrics.metrics["events_order_fill"] == 1.0
        assert metrics.samples["orders_total"] == 3
        assert len(metrics.samples["orders_dirs"]) == 3
        assert len(metrics.samples["event_paths"]) == 3


def test_probe_worktree_detects_residual_orders_in_new_and_legacy_layouts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".git").mkdir()
        _write_json(
            root / "state" / "runs" / "futu_real" / "orders" / "live_open.json",
            {
                "status": "submitted",
                "broker_order_id": "FUTU.REAL.1",
            },
        )
        _write_json(
            root / "state" / "runs" / "orders" / "legacy_open.json",
            {
                "status": "partial_filled",
                "broker_order_id": "LEGACY.2",
            },
        )
        _write_json(
            root / "state" / "runs" / "dry_run" / "orders" / "dry_approved.json",
            {
                "status": "approved",
                "broker_order_id": "",
            },
        )

        report = probe_worktree("compat", root, None)

        assert report.exists is True
        assert report.is_git is True
        assert report.orders_residual == 2
        assert any(item.endswith("futu_real/orders/live_open.json") for item in report.orders_residual_sample)
        assert any(item.endswith("orders/legacy_open.json") for item in report.orders_residual_sample)
        assert any(path.endswith("state/runs/futu_real/orders") for path in report.orders_checked_dirs)
        assert any(path.endswith("state/runs/orders") for path in report.orders_checked_dirs)


def _run_all():
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed: list[tuple[str, str]] = []
    for fn in funcs:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((fn.__name__, repr(exc)))
        else:
            passed += 1
            print(f"PASS {fn.__name__}")
    print(f"\n{passed}/{len(funcs)} tests passed")
    for name, err in failed:
        print(f"FAIL {name}: {err}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
