from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from execution.live_bridge.models import LiveOrderRequest


@dataclass
class SubmitPrecheckResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class SubmitPrecheck:
    def __init__(self):
        self._seen: Set[str] = set()

    def evaluate(self, order: LiveOrderRequest, mode: str = 'paper', signal_age_seconds: int | None = None) -> SubmitPrecheckResult:
        reasons: List[str] = []
        if mode != 'live':
            reasons.append('mode is not live; submit blocked')
        if order.qty <= 0:
            reasons.append('qty must be positive')
        if order.order_type == 'LIMIT' and order.price in (None, 0, 0.0):
            reasons.append('limit order requires price')
        if order.side not in {'BUY', 'SELL'}:
            reasons.append('invalid side')
        if order.request_id in self._seen:
            reasons.append('duplicate request detected')
        if signal_age_seconds is not None and signal_age_seconds > 900:
            reasons.append('stale signal detected')
        if order.market not in {'us', 'hong_kong', 'a_share'}:
            reasons.append('unsupported market')
        self._seen.add(order.request_id)
        return SubmitPrecheckResult(allowed=not reasons, reasons=reasons)
