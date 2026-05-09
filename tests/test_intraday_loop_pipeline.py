from __future__ import annotations

"""Unit tests for the classic_multifactor intraday loop plumbing.

These tests do NOT spin up MainEngine / FutuGateway. They verify:

1. ``validate_loop_mode`` correctly accepts / rejects mode mismatches.
2. ``ExecutionGuardPipeline`` wires the four gates together and appends
   structured events to ``events.jsonl``.
3. Each gate can block an order and leaves a blocked record.
4. Approved orders persist via ``OrderStateStore`` under the run state root.
5. The S3a CLI parser accepts the canonical NVDA_G09 config (schema check).
"""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classic_multifactor.config_schema import (
    LoopModeValidationError,
    resolve_loop_mode,
    validate_loop_mode,
)
from scripts.classic_multifactor.execution_pipeline import (
    ExecutionGuardPipeline,
    PipelineContext,
)
from scripts.classic_multifactor.minute_guard import (
    MinuteTradeGuard,
    MinuteTradeGuardConfig,
)
from services.execution_guard.idempotency import OrderIdempotencyGuard
from services.execution_guard.reconciliation import ReconciliationGuard
from services.risk_engine import LiveRiskGuard
from services.trade_state import OrderStateStore


class FakeBar:
    """Minimal BarData stand-in — only fields the pipeline touches."""

    def __init__(self, vt_symbol: str, dt: datetime, close_price: float):
        self.vt_symbol = vt_symbol
        self.datetime = dt
        self.close_price = close_price
        # fields referenced only in logging snapshots
        self.open_price = close_price
        self.high_price = close_price
        self.low_price = close_price


def _make_pipeline(tmp: Path, *, live_submit: bool = True, limits: dict | None = None):
    order_store = OrderStateStore(tmp / "orders")
    idempotency = OrderIdempotencyGuard(order_store)
    # Cold-start sentinel trick: when both recon file and sentinel are absent,
    # reconciliation returns allow-with-warn even in fail_closed mode.
    reconciliation = ReconciliationGuard(
        tmp / "recon.json",
        max_age_minutes=60,
        fail_closed=True,
        cold_start_sentinel=tmp / ".recon_sentinel",
    )
    minute_guard = MinuteTradeGuard(MinuteTradeGuardConfig.from_setting({
        "max_intraday_trades": 4,
        "entry_cooldown_minutes": 0,
        "min_hold_minutes": 0,
        "no_new_entry_after": "15:30",
    }))
    default_limits = {
        "max_single_position_pct": 0.50,
        "max_daily_new_position_pct": 0.60,
        "max_market_exposure_pct": 0.95,
        "max_signal_age_seconds": 900,
        "max_drawdown_pct": 0.08,
        "max_order_value": 1500.0,
    }
    if limits:
        default_limits.update(limits)
    live_risk = LiveRiskGuard(default_limits)

    events_log = tmp / "events.jsonl"

    def ctx_provider():
        return PipelineContext(
            capital=5000.0,
            equity=5000.0,
            cash=5000.0,
            symbol_market_value=0.0,
            market_existing_pct=0.0,
            daily_new_pct=0.0,
            current_drawdown_pct=0.0,
            signal_age_seconds=5,
            account_status="connected",
        )

    pipeline = ExecutionGuardPipeline(
        idempotency=idempotency,
        reconciliation=reconciliation,
        minute_guard=minute_guard,
        live_risk=live_risk,
        order_store=order_store,
        events_log_path=events_log,
        context_provider=ctx_provider,
        strategy_id="classic_multifactor_NVDA_US",
        market="US",
        loop_mode="intraday",
        live_submit=live_submit,
    )
    return pipeline, events_log, order_store


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 1. loop_mode validation
# ---------------------------------------------------------------------------
def test_resolve_loop_mode_default_intraday():
    assert resolve_loop_mode({}) == "intraday"
    assert resolve_loop_mode({"setting": {}}) == "intraday"
    assert resolve_loop_mode({"loop_mode": "daily"}) == "daily"


def test_validate_loop_mode_accepts_matching():
    assert validate_loop_mode({"loop_mode": "intraday", "setting": {"max_intraday_trades": 4}}, "intraday") == "intraday"
    assert validate_loop_mode({"loop_mode": "daily", "setting": {"rebalance_time": "16:00"}}, "daily") == "daily"


def test_validate_loop_mode_rejects_mismatch():
    try:
        validate_loop_mode({"loop_mode": "intraday"}, "daily")
    except LoopModeValidationError:
        return
    raise AssertionError("mismatch not rejected")


def test_validate_loop_mode_rejects_mixed_fields():
    # daily runner should reject intraday-only keys
    try:
        validate_loop_mode({"loop_mode": "daily", "setting": {"max_intraday_trades": 4}}, "daily")
    except LoopModeValidationError:
        pass
    else:
        raise AssertionError("intraday leak into daily config not rejected")
    # intraday runner should reject daily-only keys
    try:
        validate_loop_mode({"loop_mode": "intraday", "setting": {"rebalance_time": "16:00"}}, "intraday")
    except LoopModeValidationError:
        pass
    else:
        raise AssertionError("daily leak into intraday config not rejected")


def test_validate_nvda_g09_real_config():
    cfg = json.loads((REPO_ROOT / "configs" / "classic_multifactor" / "nvda_g09.json").read_text(encoding="utf-8"))
    assert validate_loop_mode(cfg, "intraday") == "intraday"


# ---------------------------------------------------------------------------
# 2. Pipeline happy path (dry-run)
# ---------------------------------------------------------------------------
def test_pipeline_dry_run_blocks_submission_but_records_approved():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pipeline, events_log, store = _make_pipeline(tmp, live_submit=False)
        bar = FakeBar("NVDA.SMART", datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), 120.0)
        allowed, reason = pipeline.approve_buy(bar, qty=1, price=120.0)
        assert allowed is False, "dry-run must block actual submission"
        assert reason == "dry_run"
        assert pipeline.approved_count == 1
        events = _read_events(events_log)
        kinds = [e["event"] for e in events]
        assert "order_approved" in kinds
        stored = list(store.root.glob("*.json"))
        assert len(stored) == 1, "approved order must persist to order store"


# ---------------------------------------------------------------------------
# 3. Each gate can block
# ---------------------------------------------------------------------------
def test_pipeline_live_risk_blocks_oversize_order():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # max_order_value 50 < notional 120 → reject
        pipeline, events_log, _ = _make_pipeline(
            tmp, live_submit=True, limits={"max_order_value": 50.0}
        )
        bar = FakeBar("NVDA.SMART", datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), 120.0)
        allowed, reason = pipeline.approve_buy(bar, qty=1, price=120.0)
        assert allowed is False
        assert reason.startswith("live_risk:")
        assert pipeline.blocked_by_gate.get("live_risk", 0) == 1
        events = _read_events(events_log)
        assert any(e["event"] == "order_blocked" and e["gate"] == "live_risk" for e in events)


def test_pipeline_minute_guard_blocks_after_max_trades():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pipeline, _events, _store = _make_pipeline(tmp, live_submit=True)
        bar = FakeBar("NVDA.SMART", datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), 120.0)
        # Seed trade_times to saturate the minute guard.
        saturated = [bar.datetime for _ in range(10)]
        pipeline.trade_times_provider = lambda: saturated
        allowed, reason = pipeline.approve_buy(bar, qty=1, price=120.0)
        assert allowed is False
        assert reason.startswith("minute_guard:")
        assert pipeline.blocked_by_gate.get("minute_guard", 0) == 1


def test_pipeline_reconciliation_blocks_when_file_exists_with_mismatch():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Pre-create a reconciliation file with a qty mismatch to force block.
        recon_file = tmp / "recon.json"
        sentinel = tmp / ".recon_sentinel"
        sentinel.write_text("", encoding="utf-8")  # sentinel exists → no cold-start grace
        recon_file.write_text(json.dumps({
            "success": True,
            "diffs": [{"symbol": "NVDA", "qty_match": False, "sellable_match": False}],
        }), encoding="utf-8")
        pipeline, _events, _store = _make_pipeline(tmp, live_submit=True)
        bar = FakeBar("NVDA.SMART", datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc), 120.0)
        allowed, reason = pipeline.approve_buy(bar, qty=1, price=120.0)
        assert allowed is False
        assert reason.startswith("reconciliation:")
        assert pipeline.blocked_by_gate.get("reconciliation", 0) == 1


# ---------------------------------------------------------------------------
# 4. Hard-switch gating (unit-level: no MainEngine)
# ---------------------------------------------------------------------------
def test_live_submit_gating_requires_all_env_vars(monkeypatch=None):
    # Manual monkeypatch to avoid pytest fixture dependency.
    import os

    from scripts.classic_multifactor.run_intraday_loop import IntradayLoopRunner

    saved = {k: os.environ.get(k) for k in ("VNPY_LIVE_CONFIG", "VNPY_LIVE_SUBMIT", "VNPY_LIVE_APPROVED")}
    try:
        # 1) no env vars → dry-run
        for k in saved:
            os.environ.pop(k, None)
        assert IntradayLoopRunner._resolve_live_submit(True) is False

        # 2) two of three → still dry-run
        os.environ["VNPY_LIVE_CONFIG"] = "YES"
        os.environ["VNPY_LIVE_SUBMIT"] = "YES"
        assert IntradayLoopRunner._resolve_live_submit(True) is False

        # 3) all three → live
        os.environ["VNPY_LIVE_APPROVED"] = "YES"
        assert IntradayLoopRunner._resolve_live_submit(True) is True

        # 4) CLI flag off → always dry-run regardless
        assert IntradayLoopRunner._resolve_live_submit(False) is False
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
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
