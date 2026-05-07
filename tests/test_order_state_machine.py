from __future__ import annotations

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
    ))

    state = machine.transition(state, "validated")
    state = machine.transition(state, "risk_checked")
    state = machine.transition(state, "approved")
    state = machine.transition(state, "submitted")
    state = machine.apply_broker_order(state, broker_order_id="b1", broker_status="FILLED_ALL", filled_qty=1, avg_fill_price=100.0)

    assert state.status == "filled"
    assert state.broker_order_id == "b1"


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
