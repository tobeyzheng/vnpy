from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PortfolioRiskResult:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ExposureBreakdown:
    total_nav_base: float
    cash_base: float
    market_exposure_pct: dict[str, float] = field(default_factory=dict)
    sector_exposure_pct: dict[str, float] = field(default_factory=dict)
    strategy_exposure_pct: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RebalanceSuggestion:
    symbol: str
    action: str
    reason: str
    target_value_base: float
    current_value_base: float


class CurrencyConverter:
    DEFAULT_RATES = {"USD": 1.0, "HKD": 0.128, "CNY": 0.138}

    def __init__(self, rates: dict[str, float] | None = None, base_currency: str = "USD"):
        self.rates = {**self.DEFAULT_RATES, **(rates or {})}
        self.base_currency = base_currency.upper()

    def to_base(self, amount: float, currency: str) -> float:
        src = currency.upper()
        if src == self.base_currency:
            return float(amount or 0.0)
        return float(amount or 0.0) * float(self.rates.get(src, 1.0)) / float(self.rates.get(self.base_currency, 1.0))


class PortfolioRiskGuard:
    def __init__(self, max_market_exposure_pct: float = 0.85, max_total_positions: int = 8, max_sector_exposure_pct: float = 0.45, max_strategy_exposure_pct: float = 0.50):
        self.max_market_exposure_pct = max_market_exposure_pct
        self.max_total_positions = max_total_positions
        self.max_sector_exposure_pct = max_sector_exposure_pct
        self.max_strategy_exposure_pct = max_strategy_exposure_pct

    def check(self, *, total_nav: float, market_nav: float, total_positions: int) -> PortfolioRiskResult:
        if total_positions >= self.max_total_positions:
            return PortfolioRiskResult(False, 'portfolio_max_positions_exceeded')
        if total_nav > 0 and market_nav / total_nav > self.max_market_exposure_pct:
            return PortfolioRiskResult(False, 'market_exposure_limit_exceeded')
        return PortfolioRiskResult(True, 'ok')

    def check_breakdown(self, breakdown: ExposureBreakdown, *, total_positions: int) -> PortfolioRiskResult:
        if total_positions >= self.max_total_positions:
            return PortfolioRiskResult(False, "portfolio_max_positions_exceeded")
        if any(v > self.max_market_exposure_pct for v in breakdown.market_exposure_pct.values()):
            return PortfolioRiskResult(False, "market_exposure_limit_exceeded")
        if any(v > self.max_sector_exposure_pct for v in breakdown.sector_exposure_pct.values()):
            return PortfolioRiskResult(False, "sector_exposure_limit_exceeded")
        if any(v > self.max_strategy_exposure_pct for v in breakdown.strategy_exposure_pct.values()):
            return PortfolioRiskResult(False, "strategy_exposure_limit_exceeded")
        return PortfolioRiskResult(True, "ok")


class PortfolioExposureHelper:
    def check_market_addition(self, *, total_nav: float, market_nav: float, add_cost: float, max_market_exposure_pct: float = 0.85) -> PortfolioRiskResult:
        next_total = total_nav + add_cost
        next_market = market_nav + add_cost
        if next_total > 0 and next_market / next_total > max_market_exposure_pct:
            return PortfolioRiskResult(False, "market_exposure_limit_after_trade")
        return PortfolioRiskResult(True, "ok")


class PortfolioExposureAnalyzer:
    def __init__(self, converter: CurrencyConverter | None = None):
        self.converter = converter or CurrencyConverter()

    def summarize(self, *, cash: float, cash_currency: str, positions: list[dict[str, Any]]) -> ExposureBreakdown:
        cash_base = self.converter.to_base(cash, cash_currency)
        market_values: dict[str, float] = {}
        sector_values: dict[str, float] = {}
        strategy_values: dict[str, float] = {}
        total_positions = 0.0
        for row in positions:
            value = self.converter.to_base(float(row.get("market_value", row.get("market_val", 0.0)) or 0.0), str(row.get("currency", cash_currency)))
            total_positions += value
            market_values[str(row.get("market", "unknown"))] = market_values.get(str(row.get("market", "unknown")), 0.0) + value
            sector_values[str(row.get("sector", "unknown"))] = sector_values.get(str(row.get("sector", "unknown")), 0.0) + value
            strategy_values[str(row.get("strategy_id", "unknown"))] = strategy_values.get(str(row.get("strategy_id", "unknown")), 0.0) + value
        total_nav = cash_base + total_positions
        return ExposureBreakdown(
            total_nav_base=round(total_nav, 4),
            cash_base=round(cash_base, 4),
            market_exposure_pct=self._pct_map(market_values, total_nav),
            sector_exposure_pct=self._pct_map(sector_values, total_nav),
            strategy_exposure_pct=self._pct_map(strategy_values, total_nav),
        )

    def _pct_map(self, values: dict[str, float], total: float) -> dict[str, float]:
        return {k: round(v / total, 6) for k, v in values.items() if total > 0}


class RebalancePlanner:
    def suggest(self, *, positions: list[dict[str, Any]], target_weights: dict[str, float], total_nav: float, tolerance_pct: float = 0.03) -> list[RebalanceSuggestion]:
        suggestions: list[RebalanceSuggestion] = []
        current = {str(p.get("symbol")): float(p.get("market_value", p.get("market_val", 0.0)) or 0.0) for p in positions}
        for symbol, target_weight in target_weights.items():
            target_value = max(float(target_weight), 0.0) * max(float(total_nav), 0.0)
            current_value = current.get(symbol, 0.0)
            drift = abs(target_value - current_value) / total_nav if total_nav > 0 else 0.0
            if drift <= tolerance_pct:
                continue
            action = "increase" if target_value > current_value else "reduce"
            suggestions.append(RebalanceSuggestion(symbol, action, "target_weight_drift", round(target_value, 4), round(current_value, 4)))
        return suggestions
