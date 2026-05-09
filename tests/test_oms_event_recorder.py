from __future__ import annotations

"""Unit tests for OmsEventRecorder (Task 6 / S4).

Three scenarios required by the plan:

1. **push out-of-order**: EVENT_TRADE arrives before EVENT_ORDER for the
   same vt_orderid. The recorder must apply the trade (orphan replay)
   once ``register_request`` is called, and the order push must not
   regress the resulting ``filled`` status.
2. **push lost / duplicate**: redelivery of the same EVENT_TRADE
   (same ``vt_tradeid``) and the same EVENT_ORDER tuple
   ``(vt_orderid, status, traded)`` must be deduplicated.
3. **restart recovery**: a brand-new recorder bound to the existing
   ``OrderStateStore`` directory must rebuild its
   ``vt_orderid -> request_id`` index from the persisted
   ``broker_order_id`` field so a late EVENT_TRADE after a restart still
   updates the correct OrderState.

The tests do NOT instantiate ``MainEngine`` / ``FutuGateway`` — we drive
the recorder via a fake ``EventEngine`` stub and synthetic
``OrderData`` / ``TradeData`` payloads.
"""

import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common import OrderIntent
from services.trade_state import OmsEventRecorder, OrderStateStore
from services.trade_state.state_machine import OrderStateMachine


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeEventEngine:
    """Minimal stand-in for vnpy.event.EventEngine."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[[Any], None]]] = {}

    def register(self, event_type: str, handler: Callable[[Any], None]) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def unregister(self, event_type: str, handler: Callable[[Any], None]) -> None:
        if event_type in self.handlers and handler in self.handlers[event_type]:
            self.handlers[event_type].remove(handler)

    def fire(self, event_type: str, data: Any) -> None:
        evt = SimpleNamespace(type=event_type, data=data)
        for handler in list(self.handlers.get(event_type, [])):
            handler(evt)


@dataclass
class FakeOrderPush:
    vt_orderid: str
    orderid: str
    status: str  # already coerced to upper-case enum-name style ("ALLTRADED" etc.)
    volume: float
    traded: float = 0
    price: float | None = None
    datetime: Any = None


@dataclass
class FakeTradePush:
    vt_orderid: str
    vt_tradeid: str
    tradeid: str
    volume: float
    price: float | None
    datetime: Any = None


def _approved_state(store: OrderStateStore, request_id: str, qty: int = 100) -> None:
    """Create an ``approved`` OrderState on disk, mimicking what
    ``ExecutionGuardPipeline._approve`` would have written."""
    machine = OrderStateMachine()
    intent = OrderIntent(
        request_id=request_id,
        symbol="NVDA",
        market="US",
        strategy_id="classic_multifactor_NVDA_US",
        side="BUY",
        qty=qty,
        price=180.0,
        target_position_pct=0.1,
        reason="test",
    )
    state = machine.create(intent)
    state = machine.transition(state, "validated", note="t")
    state = machine.transition(state, "risk_checked", note="t")
    state = machine.transition(state, "approved", note="t")
    store.save(state)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class OmsEventRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.orders_dir = self.root / "orders"
        self.events_path = self.root / "events.jsonl"
        self.store = OrderStateStore(self.orders_dir)
        self.fake_engine = FakeEventEngine()
        self.main = SimpleNamespace(event_engine=self.fake_engine)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_recorder(self) -> OmsEventRecorder:
        recorder = OmsEventRecorder(self.store, events_log_path=self.events_path)
        recorder.attach(self.main)
        return recorder

    # ---- Scenario 1: out-of-order push -----------------------------------
    def test_trade_before_order_replays_on_register(self) -> None:
        """EVENT_TRADE for an unknown vt_orderid is buffered, then replayed
        when register_request() binds the request_id."""
        request_id = "req_001"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()

        # Fire EVENT_TRADE BEFORE register_request — should orphan.
        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X1", "FUTU.X1.T1", "T1", volume=100, price=181.0),
        )
        self.assertEqual(recorder.stats["orphan_trades"], 1)
        # No state mutation yet.
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "approved")

        # Now bind — recorder must replay buffered trade.
        recorder.register_request("FUTU.X1", request_id)
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "filled")
        self.assertEqual(state.filled_qty, 100)
        self.assertAlmostEqual(state.avg_fill_price or 0.0, 181.0, places=4)
        self.assertEqual(recorder.stats["applied_trades"], 1)

    def test_late_order_status_does_not_regress_filled_state(self) -> None:
        """After a fill, a stray ``SUBMITTING`` push for the same vt_orderid
        must not flip the OrderState back to submitted."""
        request_id = "req_002"
        _approved_state(self.store, request_id, qty=50)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X2", request_id)

        # Trade fills the order completely.
        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X2", "FUTU.X2.T1", "T1", volume=50, price=200.0),
        )
        self.assertEqual(self.store.load(request_id).status, "filled")  # type: ignore[union-attr]

        # Late SUBMITTING push (legitimate vn.py replay on reconnect).
        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.X2", "X2", "SUBMITTING", volume=50, traded=0),
        )
        self.assertEqual(self.store.load(request_id).status, "filled")  # type: ignore[union-attr]

    # ---- Scenario 2: dedup ------------------------------------------------
    def test_duplicate_trade_id_is_deduped(self) -> None:
        request_id = "req_003"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X3", request_id)

        push = FakeTradePush("FUTU.X3", "FUTU.X3.T1", "T1", volume=40, price=190.0)
        self.fake_engine.fire("eTrade.", push)
        self.fake_engine.fire("eTrade.", push)  # redelivery
        self.fake_engine.fire("eTrade.", push)  # redelivery again

        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.filled_qty, 40)
        self.assertEqual(recorder.stats["applied_trades"], 1)
        self.assertEqual(recorder.stats["trade_dedup_skips"], 2)

    def test_duplicate_order_status_is_deduped(self) -> None:
        request_id = "req_004"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X4", request_id)

        push = FakeOrderPush("FUTU.X4", "X4", "NOTTRADED", volume=100, traded=0)
        self.fake_engine.fire("eOrder.", push)
        self.fake_engine.fire("eOrder.", push)  # same (vt_orderid, status, traded)
        self.fake_engine.fire("eOrder.", push)

        self.assertEqual(recorder.stats["applied_orders"], 1)
        self.assertEqual(recorder.stats["order_dedup_skips"], 2)

    def test_partial_then_full_fill_aggregates_correctly(self) -> None:
        request_id = "req_005"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X5", request_id)

        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X5", "FUTU.X5.T1", "T1", volume=40, price=180.0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "partial_filled")
        self.assertEqual(state.filled_qty, 40)

        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X5", "FUTU.X5.T2", "T2", volume=60, price=182.0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "filled")
        self.assertEqual(state.filled_qty, 100)
        # Weighted average of (40 * 180 + 60 * 182) / 100 == 181.2
        self.assertAlmostEqual(state.avg_fill_price or 0.0, 181.2, places=2)

    # ---- Scenario 3: restart recovery ------------------------------------
    def test_restart_recovers_index_from_disk(self) -> None:
        request_id = "req_006"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X6", request_id)

        # First half-fill before "crash".
        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X6", "FUTU.X6.T1", "T1", volume=30, price=170.0),
        )
        recorder.detach()

        # Simulate restart: brand-new engine + recorder, re-attach.
        new_engine = FakeEventEngine()
        new_main = SimpleNamespace(event_engine=new_engine)
        new_store = OrderStateStore(self.orders_dir)
        new_recorder = OmsEventRecorder(new_store, events_log_path=self.events_path)
        new_recorder.attach(new_main)

        # The new recorder should have rebuilt the vt_orderid index from the
        # persisted broker_order_id, so a follow-up trade is NOT orphaned.
        new_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X6", "FUTU.X6.T2", "T2", volume=70, price=172.0),
        )
        self.assertEqual(new_recorder.stats["orphan_trades"], 0)
        self.assertEqual(new_recorder.stats["applied_trades"], 1)

        state = new_store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "filled")
        self.assertEqual(state.filled_qty, 100)

    # ---- Bonus: events.jsonl is appended ----------------------------------
    def test_events_jsonl_is_appended_on_apply(self) -> None:
        import json as _json

        request_id = "req_007"
        _approved_state(self.store, request_id, qty=10)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.X7", request_id)

        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.X7", "FUTU.X7.T1", "T1", volume=10, price=99.0),
        )
        self.assertTrue(self.events_path.exists())
        events = [
            _json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kinds = {e.get("event") for e in events}
        # Must include either order_status_update or order_fill
        self.assertTrue(
            "order_fill" in kinds or "order_status_update" in kinds,
            f"unexpected events: {events}",
        )

    # ---- Scenario 4: cancel / reject paths -------------------------------
    def test_approved_then_rejected_directly(self) -> None:
        """Broker rejects an order immediately after submission. The
        recorder must move the OrderState to ``rejected`` even though
        the project-side pipeline only reached ``approved`` (no bridge
        through ``submitted`` is required because approved->rejected is
        a legal direct edge)."""
        request_id = "req_rej_001"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.RJ1", request_id)

        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.RJ1", "RJ1", "REJECTED", volume=100, traded=0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "rejected")
        self.assertEqual(state.filled_qty, 0)
        self.assertEqual(recorder.stats["forced_rejected"], 1)

    def test_partial_fill_then_cancel_routes_to_cancelled(self) -> None:
        """User cancels a working order after a partial fill. Even though
        ``traded > 0`` would normally route to ``partial_filled``, the
        recorder must honour the cancel signal and end up in ``cancelled``
        with ``filled_qty`` preserved at the partial amount."""
        request_id = "req_cxl_001"
        _approved_state(self.store, request_id, qty=100)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.CXL1", request_id)

        # Step 1: a partial trade fills 30 of 100.
        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.CXL1", "FUTU.CXL1.T1", "T1", volume=30, price=200.0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "partial_filled")
        self.assertEqual(state.filled_qty, 30)

        # Step 2: broker reports cancellation with traded=30.
        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.CXL1", "CXL1", "CANCELLED", volume=100, traded=30),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "cancelled")
        # filled_qty must NOT regress.
        self.assertEqual(state.filled_qty, 30)
        self.assertEqual(recorder.stats["forced_cancelled"], 1)

    def test_chinese_cancel_label_is_classified(self) -> None:
        """Futu emits ``"已撤单"`` / ``"撤单"`` in Chinese locales. The
        recorder must classify these as cancelled even though the default
        state machine matcher is case-insensitive English-only."""
        request_id = "req_cxl_zh"
        _approved_state(self.store, request_id, qty=50)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.ZH1", request_id)

        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.ZH1", "ZH1", "已撤单", volume=50, traded=0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "cancelled")
        self.assertEqual(recorder.stats["forced_cancelled"], 1)

    def test_approved_then_cancelled_bridges_through_submitted(self) -> None:
        """``approved -> cancelled`` is NOT a legal direct edge in
        ALLOWED_TRANSITIONS. The recorder must bridge through ``submitted``
        first and then transition to ``cancelled`` so the audit trail is
        legal."""
        request_id = "req_cxl_pre"
        _approved_state(self.store, request_id, qty=20)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.CXL2", request_id)

        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.CXL2", "CXL2", "CANCELLED", volume=20, traded=0),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "cancelled")
        # The bridge note should be recorded in notes.
        bridge_notes = [n for n in state.notes if "bridge" in n]
        self.assertTrue(bridge_notes, f"expected bridge note, got notes={state.notes}")

    def test_late_cancel_after_filled_is_recorded_as_snapshot_only(self) -> None:
        """A stray CANCELLED push arriving after the order is fully filled
        must NOT regress the OrderState. It is recorded as a late_events
        snapshot for audit only."""
        request_id = "req_late_cxl"
        _approved_state(self.store, request_id, qty=10)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.LC1", request_id)

        self.fake_engine.fire(
            "eTrade.",
            FakeTradePush("FUTU.LC1", "FUTU.LC1.T1", "T1", volume=10, price=50.0),
        )
        self.assertEqual(self.store.load(request_id).status, "filled")  # type: ignore[union-attr]

        # Stray cancel push (e.g. broker replay).
        self.fake_engine.fire(
            "eOrder.",
            FakeOrderPush("FUTU.LC1", "LC1", "CANCELLED", volume=10, traded=10),
        )
        state = self.store.load(request_id)
        assert state is not None
        self.assertEqual(state.status, "filled")
        self.assertEqual(state.filled_qty, 10)
        # Snapshot is recorded.
        late = state.snapshots.get("late_events", [])
        self.assertTrue(
            any(e.get("label") == "order_status_late_push" for e in late),
            f"expected late_push snapshot, got {late}",
        )

    def test_duplicate_cancel_pushes_are_deduped(self) -> None:
        """Same vt_orderid + same broker status text + same traded must
        be deduped, preventing the stats counter from double-counting a
        replayed cancellation."""
        request_id = "req_cxl_dup"
        _approved_state(self.store, request_id, qty=80)
        recorder = self._make_recorder()
        recorder.register_request("FUTU.DUP1", request_id)

        push = FakeOrderPush("FUTU.DUP1", "DUP1", "CANCELLED", volume=80, traded=0)
        self.fake_engine.fire("eOrder.", push)
        self.fake_engine.fire("eOrder.", push)
        self.fake_engine.fire("eOrder.", push)

        self.assertEqual(self.store.load(request_id).status, "cancelled")  # type: ignore[union-attr]
        # Forced counter must only increment once because dedup happens
        # before _apply_order_payload is reached.
        self.assertEqual(recorder.stats["forced_cancelled"], 1)
        self.assertEqual(recorder.stats["order_dedup_skips"], 2)


if __name__ == "__main__":
    unittest.main()
