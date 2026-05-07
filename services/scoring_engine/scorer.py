from __future__ import annotations

from typing import Dict


class MarketScorer:
    """Weighted score calculator for market-specific strategy aggregation."""

    def __init__(self, weights: Dict[str, float]):
        self.weights = weights

    def score(self, factors: Dict[str, float]) -> float:
        total = 0.0
        for key, weight in self.weights.items():
            total += float(factors.get(key, 0.0)) * weight
        return round(total, 4)

    @staticmethod
    def to_confidence(score: float) -> int:
        value = max(0, min(100, int(round(score * 100))))
        return value
