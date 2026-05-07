from __future__ import annotations

from dataclasses import dataclass

from services.trade_state import OrderStateStore

OPEN_STATUSES = {
    "created",
    "validated",
    "risk_checked",
    "approval_required",
    "approved",
    "submitting",
    "submitted",
    "partial_filled",
    "cancel_requested",
}


@dataclass(frozen=True)
class IdempotencyDecision:
    allowed: bool
    reason: str = "ok"


class OrderIdempotencyGuard:
    def __init__(self, store: OrderStateStore):
        self.store = store

    def evaluate(self, request_id: str) -> IdempotencyDecision:
        state = self.store.load(request_id)
        if not state:
            return IdempotencyDecision(True)
        if state.status in OPEN_STATUSES:
            return IdempotencyDecision(False, f"open_order_exists:{state.status}")
        if state.status in {"filled", "reconciled"}:
            return IdempotencyDecision(False, f"already_executed:{state.status}")
        return IdempotencyDecision(True, f"previous_terminal_state:{state.status}")
