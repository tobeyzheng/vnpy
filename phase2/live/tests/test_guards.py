"""Unit tests for phase2.live.guards.

Covers each gate's allow/block semantics, the pipeline ordering, the
events.jsonl emission contract, and the OrderStateStore transition driven by
the pipeline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from phase2.live.guards import (
    EventLogger,
    GateContext,
    GatePipeline,
    GateResult,
    IdempotencyGate,
    PortfolioRiskGate,
    PortfolioRiskLimits,
    PortfolioState,
    ReconciliationGate,
    RiskGate,
)
from phase2.live.order_state import OrderStateStoreExt, make_intent
from phase2.live.risk import (
    ClassicOrderRiskManager,
    Phase2RiskConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(symbol="US.AAPL", side="BUY", qty=10, price=100.0,
            seq=0, exec_env="futu_sim"):
    return make_intent(
        strategy_id="phase2", symbol=symbol, market="US", side=side,
        qty=qty, price=price, rebalance_date="2026-05-20", seq=seq,
        execution_env=exec_env,
    )


def _ctx(**overrides):
    portfolio = PortfolioState(
        cash=100_000.0, equity=200_000.0,
        current_positions={"US.AAPL": 5},
        last_close={"US.AAPL": 100.0},
        market_existing_pct=0.10,
        daily_new_pct=0.02,
        current_drawdown_pct=0.05,
    )
    portfolio_overrides = overrides.pop("portfolio", {})
    for k, v in portfolio_overrides.items():
        setattr(portfolio, k, v)
    return GateContext(
        portfolio=portfolio,
        rebalance_date=overrides.pop("rebalance_date", "2026-05-20"),
        safe_mode_close_only=overrides.pop("safe_mode_close_only", False),
    )


# ---------------------------------------------------------------------------
# IdempotencyGate
# ---------------------------------------------------------------------------

class TestIdempotencyGate:
    def test_first_seen_allowed(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        gate = IdempotencyGate(store)
        result = gate.check(_intent(), _ctx())
        assert result.allowed is True

    def test_open_inflight_blocks(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        i = _intent()
        store.save_intent_as_state(i)
        store.update_status(i.request_id, "validated")
        store.update_status(i.request_id, "risk_checked")
        store.update_status(i.request_id, "approved")
        store.update_status(i.request_id, "submitting")
        store.update_status(i.request_id, "submitted")
        gate = IdempotencyGate(store)
        result = gate.check(i, _ctx())
        assert result.allowed is False
        assert "duplicate_request_in_flight" in result.reasons

    def test_already_filled_blocks(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        i = _intent()
        store.save_intent_as_state(i)
        for tgt in ("validated", "risk_checked", "approved", "submitting", "submitted", "filled"):
            store.update_status(i.request_id, tgt)
        gate = IdempotencyGate(store)
        result = gate.check(i, _ctx())
        assert result.allowed is False
        assert "duplicate_request_already_filled" in result.reasons

    def test_created_status_passes(self, tmp_path):
        store = OrderStateStoreExt(tmp_path / "orders")
        i = _intent()
        store.save_intent_as_state(i)  # status = 'created'
        gate = IdempotencyGate(store)
        result = gate.check(i, _ctx())
        assert result.allowed is True


# ---------------------------------------------------------------------------
# ReconciliationGate
# ---------------------------------------------------------------------------

class TestReconciliationGate:
    def _write_report(self, dirpath: Path, *, ts: float, breaches=None):
        dirpath.mkdir(parents=True, exist_ok=True)
        # Filename order matches sorted(); include ts for deterministic order.
        fname = f"{int(ts):020d}.json"
        (dirpath / fname).write_text(
            json.dumps({"ts": ts, "breaches": breaches or []}),
            encoding="utf-8",
        )

    def test_no_report_blocks_when_required(self, tmp_path):
        gate = ReconciliationGate(tmp_path / "rec", require_report=True)
        result = gate.check(_intent(), _ctx())
        assert result.allowed is False
        assert "no_reconcile_report" in result.reasons

    def test_no_report_allowed_when_not_required(self, tmp_path):
        gate = ReconciliationGate(tmp_path / "rec", require_report=False)
        result = gate.check(_intent(), _ctx())
        assert result.allowed is True

    def test_fresh_report_allowed(self, tmp_path):
        rec = tmp_path / "rec"
        now = time.time()
        self._write_report(rec, ts=now - 60)
        gate = ReconciliationGate(rec, max_age_seconds=600)
        result = gate.check(_intent(), _ctx())
        assert result.allowed is True

    def test_expired_report_blocks(self, tmp_path):
        rec = tmp_path / "rec"
        now = time.time()
        self._write_report(rec, ts=now - 10_000)
        gate = ReconciliationGate(rec, max_age_seconds=600)
        result = gate.check(_intent(), _ctx())
        assert result.allowed is False
        assert "reconcile_report_expired" in result.reasons

    def test_breach_blocks_buy_allows_sell(self, tmp_path):
        rec = tmp_path / "rec"
        self._write_report(rec, ts=time.time(),
                           breaches=[{"symbol": "US.AAPL", "diff": 100}])
        gate = ReconciliationGate(rec, max_age_seconds=10_000)
        buy = gate.check(_intent(side="BUY"), _ctx())
        sell = gate.check(_intent(side="SELL"), _ctx())
        assert buy.allowed is False and "reconcile_breach_close_only" in buy.reasons
        assert sell.allowed is True

    def test_safe_mode_close_only_blocks_buy(self, tmp_path):
        rec = tmp_path / "rec"
        self._write_report(rec, ts=time.time())
        gate = ReconciliationGate(rec)
        result = gate.check(_intent(side="BUY"), _ctx(safe_mode_close_only=True))
        assert result.allowed is False


# ---------------------------------------------------------------------------
# RiskGate
# ---------------------------------------------------------------------------

class TestRiskGate:
    def _gate(self):
        cfg = Phase2RiskConfig(max_position_pct=0.20, max_order_value=20_000.0)
        return RiskGate(ClassicOrderRiskManager(cfg))

    def test_allowed(self):
        result = self._gate().check(_intent(qty=10, price=100.0), _ctx())
        assert result.allowed is True
        assert "qty" in result.payload

    def test_invalid_price_rejected(self):
        # price=0 is rejected by make_intent's underlying RiskGate evaluation.
        intent = _intent(qty=10, price=0.01)
        intent_zero = _intent(qty=10, price=100.0)
        # Force price to 0 after construction: dataclass is frozen → use replace.
        from dataclasses import replace
        bad = replace(intent_zero, price=0.0)
        result = self._gate().check(bad, _ctx())
        assert result.allowed is False
        assert "invalid_price" in result.reasons


# ---------------------------------------------------------------------------
# PortfolioRiskGate
# ---------------------------------------------------------------------------

class TestPortfolioRiskGate:
    def _gate(self, **kw):
        defaults = dict(
            max_single_position_pct=0.20,
            max_daily_new_position_pct=0.30,
            max_market_exposure_pct=0.95,
            max_drawdown_pct=0.20,
        )
        defaults.update(kw)
        return PortfolioRiskGate(PortfolioRiskLimits(**defaults))

    def test_allowed(self):
        result = self._gate().check(_intent(qty=10, price=100.0), _ctx())
        assert result.allowed is True

    def test_single_position_pct_exceeded(self):
        # equity=200000, cap=20%, held 5*100 = 500 already; new BUY 500*100 = 50_000
        # post = (500 + 50000)/200000 = 25.25% > 20%
        result = self._gate().check(_intent(qty=500, price=100.0), _ctx())
        assert result.allowed is False
        assert "single_position_pct_exceeded" in result.reasons

    def test_daily_new_position_pct_exceeded(self):
        # daily_new_pct already 0.02, cap=0.05; order pct = 50000/200000 = 0.25
        gate = self._gate(max_daily_new_position_pct=0.05)
        result = gate.check(_intent(qty=500, price=100.0), _ctx())
        assert result.allowed is False
        # We may hit single_position cap first; just assert daily_new fires
        # with a low single_position cap removed:
        gate2 = self._gate(max_single_position_pct=0.99,
                           max_daily_new_position_pct=0.05)
        result2 = gate2.check(_intent(qty=500, price=100.0), _ctx())
        assert "daily_new_position_pct_exceeded" in result2.reasons

    def test_market_exposure_pct_exceeded(self):
        gate = self._gate(
            max_single_position_pct=0.99,
            max_daily_new_position_pct=0.99,
            max_market_exposure_pct=0.15,
        )
        # market_existing_pct=0.10, order=0.25 ⇒ 0.35 > 0.15
        result = gate.check(_intent(qty=500, price=100.0), _ctx())
        assert "market_exposure_pct_exceeded" in result.reasons

    def test_drawdown_breach(self):
        gate = self._gate(max_drawdown_pct=0.04)
        result = gate.check(_intent(qty=10, price=100.0), _ctx())
        assert result.allowed is False
        assert "drawdown_breach" in result.reasons

    def test_sell_does_not_check_single_position(self):
        # SELL should bypass post-position cap (it reduces exposure).
        result = self._gate().check(_intent(side="SELL", qty=500, price=100.0), _ctx())
        # Drawdown ok, equity ok, side=SELL ⇒ allowed.
        assert result.allowed is True


# ---------------------------------------------------------------------------
# GatePipeline
# ---------------------------------------------------------------------------

class TestGatePipeline:
    def _setup(self, tmp_path, *, gates=None, require_report=False):
        store = OrderStateStoreExt(tmp_path / "orders")
        events = EventLogger(tmp_path / "events.jsonl")
        if gates is None:
            cfg = Phase2RiskConfig(max_position_pct=0.20, max_order_value=20_000.0)
            gates = [
                IdempotencyGate(store),
                ReconciliationGate(tmp_path / "rec", require_report=require_report),
                RiskGate(ClassicOrderRiskManager(cfg)),
                PortfolioRiskGate(PortfolioRiskLimits(
                    max_single_position_pct=0.99,
                    max_daily_new_position_pct=0.99,
                    max_market_exposure_pct=0.99,
                    max_drawdown_pct=0.99,
                )),
            ]
        pipe = GatePipeline(gates, store=store, events=events)
        return pipe, store, events, tmp_path / "events.jsonl"

    def test_happy_path_advances_state(self, tmp_path):
        pipe, store, _events, events_path = self._setup(tmp_path)
        intent = _intent(qty=10, price=100.0)
        decision = pipe.run(intent, _ctx())
        assert decision.allowed is True
        state = store.load(intent.request_id)
        assert state.status == "approved"
        # An order_approved event must be on disk.
        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        assert any('"event_type": "order_approved"' in l for l in lines)

    def test_idempotency_short_circuits(self, tmp_path):
        pipe, store, _events, events_path = self._setup(tmp_path)
        intent = _intent(qty=10, price=100.0)
        # Pre-seed: this intent already submitted earlier.
        store.save_intent_as_state(intent)
        for tgt in ("validated", "risk_checked", "approved", "submitting", "submitted"):
            store.update_status(intent.request_id, tgt)
        decision = pipe.run(intent, _ctx())
        assert decision.allowed is False
        assert decision.blocked_by == "idempotency"
        # State should remain 'submitted', not flip to rejected (only 'created' rejects).
        assert store.load(intent.request_id).status == "submitted"

    def test_risk_block_drives_state_to_rejected(self, tmp_path):
        pipe, store, _events, events_path = self._setup(tmp_path)
        # Force RiskGate rejection: SELL on a symbol the portfolio doesn't
        # hold ⇒ qty caps to 0 ⇒ qty_zero. We use US.NVDA which is absent
        # from the default ctx's current_positions.
        intent = _intent(symbol="US.NVDA", side="SELL", qty=10, price=100.0)
        decision = pipe.run(intent, _ctx())
        assert decision.allowed is False
        assert decision.blocked_by == "risk"
        assert store.load(intent.request_id).status == "rejected"
        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        blocked = [l for l in lines if '"event_type": "order_blocked"' in l]
        assert len(blocked) == 1
        body = json.loads(blocked[0])
        assert body["gate"] == "risk"
        assert body["request_id"] == intent.request_id

    def test_portfolio_risk_block_drives_state_to_rejected(self, tmp_path):
        cfg = Phase2RiskConfig(max_position_pct=0.99, max_order_value=10_000_000.0)
        store = OrderStateStoreExt(tmp_path / "orders")
        events = EventLogger(tmp_path / "events.jsonl")
        gates = [
            IdempotencyGate(store),
            ReconciliationGate(tmp_path / "rec", require_report=False),
            RiskGate(ClassicOrderRiskManager(cfg)),
            PortfolioRiskGate(PortfolioRiskLimits(
                max_single_position_pct=0.05,  # tight
                max_daily_new_position_pct=0.99,
                max_market_exposure_pct=0.99,
                max_drawdown_pct=0.99,
            )),
        ]
        pipe = GatePipeline(gates, store=store, events=events)
        intent = _intent(qty=500, price=100.0)
        decision = pipe.run(intent, _ctx())
        assert decision.allowed is False
        assert decision.blocked_by == "portfolio_risk"
        assert "single_position_pct_exceeded" in decision.reasons
        assert store.load(intent.request_id).status == "rejected"

    def test_pipeline_emits_one_blocked_event_only(self, tmp_path):
        pipe, _, _, events_path = self._setup(tmp_path, require_report=True)
        intent = _intent(qty=10, price=100.0)
        decision = pipe.run(intent, _ctx())
        assert decision.allowed is False
        assert decision.blocked_by == "reconciliation"
        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        # exactly one blocked event, no approved event.
        blocked = [l for l in lines if '"event_type": "order_blocked"' in l]
        approved = [l for l in lines if '"event_type": "order_approved"' in l]
        assert len(blocked) == 1
        assert len(approved) == 0
