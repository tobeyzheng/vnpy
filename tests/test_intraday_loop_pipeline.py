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
import argparse
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
from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy
from scripts.classic_multifactor.run_intraday_loop import build_parser as build_intraday_parser
from services.strategy.market_rules import (
    is_market_open,
    market_session_end,
    market_timezone,
)


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
        execution_env="futu_real" if live_submit else "dry_run",
    )
    return pipeline, events_log, order_store


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _make_args_for_runner(overrides: dict) -> argparse.Namespace:
    parser = build_intraday_parser()
    base = ["--config", overrides.pop("config", "")]
    for k, v in overrides.items():
        flag = "--" + k.replace("_", "-")
        base.extend([flag, str(v)])
    return parser.parse_args(base)


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
        approved = next(e for e in events if e["event"] == "order_approved")
        assert approved["execution_env"] == "dry_run"
        stored = list(store.root.glob("*.json"))
        assert len(stored) == 1, "approved order must persist to order store"
        state = store.load(stored[0].stem)
        assert state is not None
        assert state.execution_env == "dry_run"
        assert state.execution_channel == "futu"
        assert state.source_phase == "live_session"
        assert state.submitted_to_broker is False


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
# 5. Intraday log filtering
# ---------------------------------------------------------------------------
def _make_runner_for_log_filter(*, live_submit: bool = False):
    from scripts.classic_multifactor.run_intraday_loop import IntradayLoopRunner

    runner = IntradayLoopRunner.__new__(IntradayLoopRunner)
    runner.live_submit = live_submit
    runner._pipeline = SimpleNamespace(approved_count=0, blocked_by_gate={})
    runner._strategy_instance = None
    runner._intraday_bars_seen = 0
    runner._last_bar_monotonic_at = None
    runner._last_bar_exchange_time = ""
    runner._runtime_debug_heartbeat_seconds = 60.0
    runner._next_runtime_debug_heartbeat_at = 60.0
    return runner


def _make_strategy_for_log_filter(
    *,
    last_signal: str,
    bars_count: int,
    signal_interval_minutes: int = 15,
    pos: int = 0,
    raw_score: float = 0.0,
    active_orderids: set[str] | None = None,
):
    return SimpleNamespace(
        strategy_name="classic_multifactor_01810_HK",
        last_signal=last_signal,
        pos=pos,
        raw_score=raw_score,
        active_orderids=active_orderids or set(),
        bars=[object()] * bars_count,
        model=SimpleNamespace(config=SimpleNamespace(signal_interval_minutes=signal_interval_minutes)),
    )


def _make_bar_for_log_filter(*, minute: int) -> FakeBar:
    return FakeBar(
        "01810.SEHK",
        datetime(2026, 5, 11, 13, minute, tzinfo=timezone.utc),
        34.5,
    )


def test_intraday_log_filter_skips_warmup_and_non_decision_bar():
    runner = _make_runner_for_log_filter(live_submit=False)
    warmup_strategy = _make_strategy_for_log_filter(last_signal="warmup", bars_count=480)
    non_decision_strategy = _make_strategy_for_log_filter(last_signal="classic_multifactor_hold", bars_count=16)

    assert runner._should_log_intraday_bar_result(
        warmup_strategy,
        bar=_make_bar_for_log_filter(minute=15),
        approved_delta=0,
        blocked_total_delta=0,
        active_before=0,
        active_after=0,
        error_text="",
    ) is False
    assert runner._should_log_intraday_bar_result(
        non_decision_strategy,
        bar=_make_bar_for_log_filter(minute=16),
        approved_delta=0,
        blocked_total_delta=0,
        active_before=0,
        active_after=0,
        error_text="",
    ) is False


def test_intraday_log_filter_keeps_decision_bar_and_meaningful_transitions():
    runner = _make_runner_for_log_filter(live_submit=False)
    decision_strategy = _make_strategy_for_log_filter(last_signal="classic_multifactor_hold", bars_count=15)
    transition_strategy = _make_strategy_for_log_filter(last_signal="waiting_order", bars_count=16)

    assert runner._should_log_intraday_bar_result(
        decision_strategy,
        bar=_make_bar_for_log_filter(minute=15),
        approved_delta=0,
        blocked_total_delta=0,
        active_before=0,
        active_after=0,
        error_text="",
    ) is True
    assert runner._should_log_intraday_bar_result(
        transition_strategy,
        bar=_make_bar_for_log_filter(minute=16),
        approved_delta=0,
        blocked_total_delta=0,
        active_before=0,
        active_after=1,
        error_text="",
    ) is True
    assert runner._should_log_intraday_bar_result(
        transition_strategy,
        bar=_make_bar_for_log_filter(minute=16),
        approved_delta=1,
        blocked_total_delta=0,
        active_before=0,
        active_after=0,
        error_text="",
    ) is True
    assert runner._should_log_intraday_bar_result(
        transition_strategy,
        bar=_make_bar_for_log_filter(minute=16),
        approved_delta=0,
        blocked_total_delta=1,
        active_before=0,
        active_after=0,
        error_text="",
    ) is True
    assert runner._should_log_intraday_bar_result(
        transition_strategy,
        bar=_make_bar_for_log_filter(minute=16),
        approved_delta=0,
        blocked_total_delta=0,
        active_before=0,
        active_after=0,
        error_text="boom",
    ) is True


def test_intraday_debug_checkpoint_tracks_bar_progress():
    runner = _make_runner_for_log_filter(live_submit=False)
    strategy = _make_strategy_for_log_filter(last_signal="classic_multifactor_hold", bars_count=16)
    runner._strategy_instance = strategy
    bar = _make_bar_for_log_filter(minute=17)

    runner._log_intraday_bar_checkpoint("on_bar_enter", strategy, bar)

    assert runner._intraday_bars_seen == 1
    assert runner._last_bar_exchange_time == bar.datetime.isoformat()
    snapshot = runner._intraday_runtime_debug_snapshot(
        now_monotonic=runner._last_bar_monotonic_at,
    )
    assert snapshot["bars_seen"] == 1
    assert snapshot["last_bar_time"] == bar.datetime.isoformat()
    assert snapshot["seconds_since_last_bar"] == 0.0


def test_intraday_runtime_debug_snapshot_summarises_bar_gap_and_pipeline_totals():
    runner = _make_runner_for_log_filter(live_submit=False)
    strategy = _make_strategy_for_log_filter(last_signal="hook_blocked:minute_guard", bars_count=25)
    runner._strategy_instance = strategy
    runner._pipeline = SimpleNamespace(approved_count=2, blocked_by_gate={"minute_guard": 3, "live_risk": 1})
    runner._intraday_bars_seen = 8
    runner._last_bar_monotonic_at = 100.0
    runner._last_bar_exchange_time = "2026-05-11T14:30:00+00:00"

    snapshot = runner._intraday_runtime_debug_snapshot(now_monotonic=112.5)

    assert snapshot["bars_seen"] == 8
    assert snapshot["last_bar_time"] == "2026-05-11T14:30:00+00:00"
    assert snapshot["seconds_since_last_bar"] == 12.5
    assert snapshot["approved_total"] == 2
    assert snapshot["blocked_total"] == 4
    assert snapshot["last_signal"] == "hook_blocked:minute_guard"


# ---------------------------------------------------------------------------
# 6. Warmup load conversion
# ---------------------------------------------------------------------------
def test_warmup_load_days_converts_minute_bar_requirement_to_natural_days():
    assert ClassicMultiFactorCtaStrategy.warmup_load_days(480, "1m") == 4
    assert ClassicMultiFactorCtaStrategy.resolve_warmup_load_interval("1m").value == "1m"


def test_warmup_load_days_keeps_daily_mode_as_day_count():
    assert ClassicMultiFactorCtaStrategy.warmup_load_days(62, "1d") == 62
    assert ClassicMultiFactorCtaStrategy.resolve_warmup_load_interval("1d").value == "d"


# ---------------------------------------------------------------------------
# 7. Market session helpers
# ---------------------------------------------------------------------------
def test_market_session_helpers_cover_us_and_hk_defaults():
    assert market_timezone("us") == "America/New_York"
    assert market_timezone("hong_kong") == "Asia/Hong_Kong"
    assert market_session_end("us") == "16:00"
    assert market_session_end("hong_kong") == "16:00"

    assert is_market_open("us", datetime.fromisoformat("2026-05-12T10:00:00-04:00")) is True
    assert is_market_open("us", datetime.fromisoformat("2026-05-12T16:00:00-04:00")) is False
    assert is_market_open("hong_kong", datetime.fromisoformat("2026-05-12T10:00:00+08:00")) is True
    assert is_market_open("hong_kong", datetime.fromisoformat("2026-05-12T12:15:00+08:00")) is False


# ---------------------------------------------------------------------------
# 8. Session timezone derivation
# ---------------------------------------------------------------------------
def test_intraday_runner_derives_us_session_timezone_from_config():
    from scripts.classic_multifactor.run_intraday_loop import IntradayLoopRunner

    cfg = REPO_ROOT / "configs" / "classic_multifactor" / "nvda_g09.json"
    with tempfile.TemporaryDirectory() as td:
        args = _make_args_for_runner({"config": str(cfg), "state_root": td})
        runner = IntradayLoopRunner(args)
        assert runner.session_tz == "America/New_York"
        assert runner.session_end_local.strftime("%H:%M") == "16:00"
        assert runner.session_start_local.strftime("%H:%M") == "09:30"


def test_intraday_runner_derives_hk_session_timezone_from_config():
    from scripts.classic_multifactor.run_intraday_loop import IntradayLoopRunner

    cfg = REPO_ROOT / "configs" / "classic_multifactor" / "xiaomi_hk_g01.json"
    with tempfile.TemporaryDirectory() as td:
        args = _make_args_for_runner({"config": str(cfg), "state_root": td})
        runner = IntradayLoopRunner(args)
        assert runner.session_tz == "Asia/Hong_Kong"
        assert runner.session_end_local.strftime("%H:%M") == "16:00"
        assert runner.session_start_local.strftime("%H:%M") == "09:30"


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
