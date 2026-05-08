from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Set

from execution.live_bridge.models import LiveOrderRequest


@dataclass
class LiveRiskResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class LiveRiskGuard:
    def __init__(self, limits: dict):
        self.limits = limits
        self._seen: Set[str] = set()

    def evaluate(
        self,
        order: LiveOrderRequest,
        market_existing_pct: float = 0.0,
        daily_new_pct: float = 0.0,
        current_drawdown_pct: float = 0.0,
        signal_age_seconds: int = 0,
        account_status: str = 'connected',
        market_existing_value: float = 0.0,
        budget_per_trade: float = 0.0,
    ) -> LiveRiskResult:
        reasons: List[str] = []
        single_limit = float(self.limits.get('max_single_position_pct', 0.1))
        daily_limit = float(self.limits.get('max_daily_new_position_pct', 0.2))
        market_limit = float(self.limits.get('max_market_exposure_pct', 0.35))
        signal_age_limit = int(self.limits.get('max_signal_age_seconds', 900))
        drawdown_limit = float(self.limits.get('max_drawdown_pct', 0.08))

        order_exposure_pct = float(order.target_position_pct if order.target_position_pct is not None else order.qty)
        max_order_value = self.limits.get('max_order_value')

        # Market-exposure check uses notional amounts against budget_per_trade when provided;
        # falls back to the pct-based check otherwise.
        order_notional = float(order.notional or 0.0)
        budget = float(budget_per_trade or 0.0)
        if budget > 0:
            market_projected_pct = (float(market_existing_value or 0.0) + max(order_notional, 0.0)) / budget
            market_over = market_projected_pct > market_limit
        else:
            market_projected_pct = market_existing_pct + order_exposure_pct
            market_over = market_projected_pct > market_limit

        if order.request_id in self._seen:
            reasons.append('duplicate live request detected')
        if order_exposure_pct <= 0 or not math.isfinite(order_exposure_pct):
            reasons.append('order exposure must be positive and finite')
        if order_exposure_pct > single_limit:
            reasons.append(f'single position exceeds limit {single_limit}')
        if daily_new_pct + order_exposure_pct > daily_limit:
            reasons.append(f'daily new exposure exceeds limit {daily_limit}')
        if market_over:
            reasons.append(f'market exposure exceeds limit {market_limit}')
        if max_order_value is not None and order.notional is not None and order.notional > float(max_order_value):
            reasons.append(f'order value exceeds limit {float(max_order_value)}')
        if signal_age_seconds > signal_age_limit:
            reasons.append('signal too old for live trading')
        if current_drawdown_pct >= drawdown_limit:
            reasons.append(f'drawdown stop triggered at {current_drawdown_pct}')
        if account_status != 'connected':
            reasons.append(f'account status not ready: {account_status}')

        self._seen.add(order.request_id)
        return LiveRiskResult(allowed=not reasons, reasons=reasons)
