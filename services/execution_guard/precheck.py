from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from execution.live_bridge.models import LiveOrderRequest


@dataclass
class SubmitPrecheckResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class SubmitPrecheck:
    def evaluate(self, order: LiveOrderRequest, mode: str = 'paper') -> SubmitPrecheckResult:
        reasons: List[str] = []
        if mode != 'live':
            reasons.append('mode is not live; submit blocked')
        if order.qty <= 0:
            reasons.append('qty must be positive')
        if order.order_type == 'LIMIT' and order.price in (None, 0, 0.0):
            reasons.append('limit order requires price')
        if order.side not in {'BUY', 'SELL'}:
            reasons.append('invalid side')
        return SubmitPrecheckResult(allowed=not reasons, reasons=reasons)
