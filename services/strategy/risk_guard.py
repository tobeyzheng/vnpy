from __future__ import annotations

from dataclasses import dataclass

from services.sim_account.models import SimAccount


@dataclass
class RiskGuardResult:
    allowed: bool
    reason: str


class RiskGuard:
    def __init__(self, max_single_position_pct: float = 0.25, max_positions: int = 5):
        self.max_single_position_pct = max_single_position_pct
        self.max_positions = max_positions

    def can_open(self, account: SimAccount, *, symbol: str, est_cost: float) -> RiskGuardResult:
        if len(account.positions) >= self.max_positions and all(p.symbol != symbol for p in account.positions):
            return RiskGuardResult(False, 'max_positions_exceeded')
        nav = account.nav if account.nav > 0 else account.cash
        if est_cost > nav * self.max_single_position_pct:
            return RiskGuardResult(False, 'single_position_limit_exceeded')
        return RiskGuardResult(True, 'ok')
