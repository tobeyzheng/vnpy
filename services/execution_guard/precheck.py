from __future__ import annotations

import math
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
        if order.qty <= 0 or not math.isfinite(float(order.qty)):
            reasons.append('qty must be positive and finite')
        if order.order_type == 'LIMIT':
            price = float(order.price or 0.0)
            if price <= 0 or not math.isfinite(price):
                reasons.append('limit order requires positive finite price')
        if order.notional is not None and (order.notional <= 0 or not math.isfinite(float(order.notional))):
            reasons.append('order notional must be positive and finite')
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
