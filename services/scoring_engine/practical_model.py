from __future__ import annotations

from dataclasses import dataclass

from services.evaluation_hub import EvaluationBundle


@dataclass
class PracticalScoreResult:
    total_score: float
    confidence: float
    summary: str


class PracticalScoringModel:
    def __init__(self, weights: dict[str, float]):
        self.weights = weights

    def score_bundle(self, bundle: EvaluationBundle) -> PracticalScoreResult:
        dim_scores: dict[str, list[float]] = {}
        for signal in bundle.signals:
            dim_scores.setdefault(signal.dimension, []).append(signal.score * signal.confidence)

        total = 0.0
        parts = []
        for dim, weight in self.weights.items():
            values = dim_scores.get(dim, [])
            avg = sum(values) / len(values) if values else 0.0
            total += avg * weight
            if values:
                parts.append(f"{dim}:{avg:.2f}")

        confidence = max(0.0, min(100.0, total * 100))
        summary = " | ".join(parts) if parts else "no evaluation signals"
        return PracticalScoreResult(total_score=total, confidence=confidence, summary=summary)
