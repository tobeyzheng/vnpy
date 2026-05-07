from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from execution.live_bridge.models import LiveOrderRequest
from services.execution_guard.precheck import SubmitPrecheck
from services.risk_engine import LiveRiskGuard


@dataclass
class LiveGateResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class LiveExecutionGate:
    def __init__(self, precheck: SubmitPrecheck, risk_guard: LiveRiskGuard):
        self.precheck = precheck
        self.risk_guard = risk_guard

    def evaluate(
        self,
        order: LiveOrderRequest,
        mode: str,
        signal_age_seconds: int,
        market_existing_pct: float,
        daily_new_pct: float,
        current_drawdown_pct: float,
        account_status: str,
    ) -> LiveGateResult:
        pre = self.precheck.evaluate(order, mode=mode, signal_age_seconds=signal_age_seconds)
        risk = self.risk_guard.evaluate(
            order,
            market_existing_pct=market_existing_pct,
            daily_new_pct=daily_new_pct,
            current_drawdown_pct=current_drawdown_pct,
            signal_age_seconds=signal_age_seconds,
            account_status=account_status,
        )
        reasons = [*pre.reasons, *risk.reasons]
        return LiveGateResult(allowed=not reasons, reasons=reasons)
