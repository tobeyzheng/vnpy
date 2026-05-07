from __future__ import annotations

from typing import Iterable, List

from services.candidate_engine.models import Candidate

from .base import CandidateProvider


class CompositeCandidateProvider(CandidateProvider):
    def __init__(self, providers: Iterable[CandidateProvider]):
        self.providers = list(providers)

    def get_candidates(self, market: str) -> List[Candidate]:
        merged: dict[str, Candidate] = {}
        for provider in self.providers:
            for candidate in provider.get_candidates(market):
                if candidate.symbol not in merged:
                    merged[candidate.symbol] = candidate
                    continue
                existing = merged[candidate.symbol]
                existing.evidence.extend(candidate.evidence)
                if (candidate.confidence or 0) > (existing.confidence or 0):
                    existing.confidence = candidate.confidence
                    existing.rationale = candidate.rationale or existing.rationale
                    existing.risk = candidate.risk or existing.risk
                    existing.action = candidate.action or existing.action
        return list(merged.values())
