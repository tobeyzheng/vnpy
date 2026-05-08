from __future__ import annotations

import json

from services.execution_guard.reconciliation import ReconciliationGuard


def test_reconciliation_guard_blocks_qty_mismatch(tmp_path):
    path = tmp_path / "reconcile.json"
    path.write_text(json.dumps({
        "success": True,
        "message": "ok",
        "diffs": [{"symbol": "00700.HK", "qty_match": False, "sellable_match": True}],
    }), encoding="utf-8")

    decision = ReconciliationGuard(path, fail_closed=True).evaluate(side="BUY", symbol="00700.HK")

    assert not decision.allowed
    assert decision.blocking_level == "block"
    assert decision.diff_symbols == ["00700.HK"]


def test_reconciliation_guard_warns_when_missing_and_not_fail_closed(tmp_path):
    decision = ReconciliationGuard(tmp_path / "missing.json", fail_closed=False).evaluate()

    assert decision.allowed
    assert decision.blocking_level == "warn"


def test_reconciliation_guard_cold_start_exempts_when_sentinel_absent(tmp_path):
    sentinel = tmp_path / ".reconciliation_ever_generated"
    decision = ReconciliationGuard(
        tmp_path / "missing.json",
        fail_closed=True,
        cold_start_sentinel=sentinel,
    ).evaluate()

    assert decision.allowed
    assert decision.blocking_level == "warn"
    assert "cold start: no reconciliation yet" in decision.reasons
    assert not sentinel.exists()


def test_reconciliation_guard_blocks_when_sentinel_present_but_file_missing(tmp_path):
    sentinel = tmp_path / ".reconciliation_ever_generated"
    sentinel.write_text("", encoding="utf-8")
    decision = ReconciliationGuard(
        tmp_path / "missing.json",
        fail_closed=True,
        cold_start_sentinel=sentinel,
    ).evaluate()

    assert not decision.allowed
    assert decision.blocking_level == "block"
    assert "reconciliation file missing" in decision.reasons


def test_reconciliation_guard_writes_sentinel_on_successful_read(tmp_path):
    path = tmp_path / "reconcile.json"
    path.write_text(json.dumps({"success": True, "message": "ok", "diffs": []}), encoding="utf-8")
    sentinel = tmp_path / ".reconciliation_ever_generated"

    decision = ReconciliationGuard(
        path,
        fail_closed=True,
        cold_start_sentinel=sentinel,
    ).evaluate()

    assert decision.allowed
    assert sentinel.exists()
