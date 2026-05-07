from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioSummary:
    total_cash: float
    total_nav: float
    market_count: int
    position_count: int


def build_portfolio_summary(accounts: list[dict]) -> PortfolioSummary:
    total_cash = sum(float(a.get('cash', 0) or 0) for a in accounts)
    total_nav = sum(float(a.get('nav', 0) or 0) for a in accounts)
    market_count = len(accounts)
    position_count = sum(len(a.get('positions', []) or []) for a in accounts)
    return PortfolioSummary(total_cash=round(total_cash, 4), total_nav=round(total_nav, 4), market_count=market_count, position_count=position_count)
