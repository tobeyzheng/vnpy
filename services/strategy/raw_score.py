from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawScoreFeatures:
    trend_score: float = 0.5
    momentum_score: float = 0.5
    flow_score: float = 0.5
    quality_score: float = 0.5
    event_score: float = 0.5
    risk_penalty: float = 0.5
    legacy_score: float | None = None


class RawScoreEngine:
    def __init__(self, weights: dict[str, float] | None = None, legacy_weight: float = 0.35):
        self.weights = weights or {
            'trend_score': 0.24,
            'momentum_score': 0.18,
            'flow_score': 0.16,
            'quality_score': 0.16,
            'event_score': 0.14,
            'risk_penalty': 0.12,
        }
        self.legacy_weight = legacy_weight

    def score(self, f: RawScoreFeatures) -> float:
        base = (
            self.weights['trend_score'] * f.trend_score
            + self.weights['momentum_score'] * f.momentum_score
            + self.weights['flow_score'] * f.flow_score
            + self.weights['quality_score'] * f.quality_score
            + self.weights['event_score'] * f.event_score
            - self.weights['risk_penalty'] * f.risk_penalty
        )
        if f.legacy_score is not None:
            base = (1 - self.legacy_weight) * base + self.legacy_weight * f.legacy_score
        return max(0.0, min(1.0, round(base, 4)))
