from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.common import OrderIntent, OrderState, OrderStatus


ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    "created": {"validated", "rejected", "failed"},
    "validated": {"risk_checked", "rejected", "expired", "failed"},
    "risk_checked": {"approval_required", "approved", "rejected", "failed"},
    "approval_required": {"approved", "rejected", "expired", "failed"},
    "approved": {"submitting", "submitted", "expired", "failed"},
    "submitting": {"submitted", "rejected", "failed"},
    "submitted": {"partial_filled", "filled", "cancel_requested", "cancelled", "rejected", "failed"},
    "partial_filled": {"filled", "cancel_requested", "cancelled", "failed"},
    "filled": {"reconciled"},
    "cancel_requested": {"cancelled", "partial_filled", "filled", "failed"},
    "cancelled": {"reconciled"},
    "rejected": {"reconciled"},
    "expired": {"reconciled"},
    "failed": {"reconciled"},
    "reconciled": set(),
}


class InvalidOrderTransition(ValueError):
    pass


class OrderStateMachine:
    def create(self, intent: OrderIntent) -> OrderState:
        return OrderState(
            request_id=intent.request_id,
            symbol=intent.symbol,
            market=intent.market,
            side=intent.side,
            qty=int(intent.qty),
            price=intent.price,
            strategy_id=intent.strategy_id,
            reason=intent.reason,
            snapshots={"signal": intent.signal_snapshot, "risk": intent.risk_snapshot},
        )

    def transition(self, state: OrderState, status: OrderStatus, note: str = "", snapshot: dict[str, Any] | None = None) -> OrderState:
        allowed = ALLOWED_TRANSITIONS.get(state.status, set())
        if status != state.status and status not in allowed:
            raise InvalidOrderTransition(f"invalid transition: {state.status} -> {status}")
        notes = list(state.notes)
        if note:
            notes.append(note)
        snapshots = dict(state.snapshots)
        if snapshot:
            snapshots.setdefault("events", []).append(snapshot)
        return replace(state, status=status, notes=notes, snapshots=snapshots)

    def apply_broker_order(
        self,
        state: OrderState,
        *,
        broker_order_id: str | None,
        broker_status: str,
        filled_qty: int = 0,
        avg_fill_price: float | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> OrderState:
        status = self._map_broker_status(broker_status, filled_qty, state.qty)
        next_state = self.transition(state, status, note=f"broker_order:{broker_status}", snapshot=snapshot)
        return replace(
            next_state,
            broker_order_id=broker_order_id or next_state.broker_order_id,
            filled_qty=max(next_state.filled_qty, int(filled_qty or 0)),
            avg_fill_price=avg_fill_price if avg_fill_price is not None else next_state.avg_fill_price,
        )

    def _map_broker_status(self, broker_status: str, filled_qty: int, qty: int) -> OrderStatus:
        text = str(broker_status).upper()
        if "FILLED_ALL" in text or "ALLTRADED" in text or (qty > 0 and filled_qty >= qty):
            return "filled"
        if "FILLED_PART" in text or "PARTTRADED" in text or filled_qty > 0:
            return "partial_filled"
        if "CANCEL" in text:
            return "cancelled"
        if "REJECT" in text or "FAILED" in text:
            return "rejected"
        if "SUBMIT" in text or "WAITING" in text:
            return "submitted"
        return "submitted"
