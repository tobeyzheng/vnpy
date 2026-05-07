from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from execution.live_bridge.models import LiveOrderRequest


@dataclass
class LiveRiskResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class LiveRiskGuard:
    def __init__(self, limits: dict):
        self.limits = limits

    def evaluate(
        self,
        order: LiveOrderRequest,
        market_existing_pct: float = 0.0,
        daily_new_pct: float = 0.0,
        current_drawdown_pct: float = 0.0,
        signal_age_seconds: int = 0,
    ) -> LiveRiskResult:
        reasons: List[str] = []
        single_limit = float(self.limits.get('max_single_position_pct', 0.1))
        daily_limit = float(self.limits.get('max_daily_new_position_pct', 0.2))
        market_limit = float(self.limits.get('max_market_exposure_pct', 0.35))
        signal_age_limit = int(self.limits.get('max_signal_age_seconds', 900))
        drawdown_limit = float(self.limits.get('max_drawdown_pct', 0.08))

        if order.qty > single_limit:
            reasons.append(f'single position exceeds limit {single_limit}')
        if daily_new_pct + order.qty > daily_limit:
            reasons.append(f'daily new exposure exceeds limit {daily_limit}')
        if market_existing_pct + order.qty > market_limit:
            reasons.append(f'market exposure exceeds limit {market_limit}')
        if signal_age_seconds > signal_age_limit:
            reasons.append('signal too old for live trading')
        if current_drawdown_pct >= drawdown_limit:
            reasons.append(f'drawdown stop triggered at {current_drawdown_pct}')
        return LiveRiskResult(allowed=not reasons, reasons=reasons)
