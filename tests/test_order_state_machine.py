from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common import OrderIntent
from services.trade_state.state_machine import InvalidOrderTransition, OrderStateMachine


def test_order_state_machine_valid_flow():
    machine = OrderStateMachine()
    state = machine.create(OrderIntent(
        request_id="r1",
        symbol="NVDA.US",
        market="us",
        strategy_id="test",
        side="BUY",
        qty=1,
        price=100.0,
        target_position_pct=0.1,
        execution_channel="futu",
        execution_env="dry_run",
        source_phase="live_session",
        submitted_to_broker=False,
    ))

    state = machine.transition(state, "validated")
    state = machine.transition(state, "risk_checked")
    state = machine.transition(state, "approved")
    state = machine.transition(state, "submitted")
    state = machine.apply_broker_order(state, broker_order_id="b1", broker_status="FILLED_ALL", filled_qty=1, avg_fill_price=100.0)

    assert state.status == "filled"
    assert state.broker_order_id == "b1"
    assert state.execution_channel == "futu"
    assert state.execution_env == "dry_run"
    assert state.source_phase == "live_session"
    assert state.submitted_to_broker is True


def test_order_state_machine_rejects_invalid_transition():
    machine = OrderStateMachine()
    state = machine.create(OrderIntent(
        request_id="r1",
        symbol="NVDA.US",
        market="us",
        strategy_id="test",
        side="BUY",
        qty=1,
        price=100.0,
        target_position_pct=0.1,
    ))

    try:
        machine.transition(state, "filled")
    except InvalidOrderTransition:
        return
    raise AssertionError("invalid transition should fail")
