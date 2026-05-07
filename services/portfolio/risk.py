from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioRiskResult:
    allowed: bool
    reason: str


class PortfolioRiskGuard:
    def __init__(self, max_market_exposure_pct: float = 0.85, max_total_positions: int = 8):
        self.max_market_exposure_pct = max_market_exposure_pct
        self.max_total_positions = max_total_positions

    def check(self, *, total_nav: float, market_nav: float, total_positions: int) -> PortfolioRiskResult:
        if total_positions >= self.max_total_positions:
            return PortfolioRiskResult(False, 'portfolio_max_positions_exceeded')
        if total_nav > 0 and market_nav / total_nav > self.max_market_exposure_pct:
            return PortfolioRiskResult(False, 'market_exposure_limit_exceeded')
        return PortfolioRiskResult(True, 'ok')


class PortfolioExposureHelper:
    def check_market_addition(self, *, total_nav: float, market_nav: float, add_cost: float, max_market_exposure_pct: float = 0.85) -> PortfolioRiskResult:
        next_total = total_nav + add_cost
        next_market = market_nav + add_cost
        if next_total > 0 and next_market / next_total > max_market_exposure_pct:
            return PortfolioRiskResult(False, "market_exposure_limit_after_trade")
        return PortfolioRiskResult(True, "ok")
