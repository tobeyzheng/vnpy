from __future__ import annotations

from typing import Iterable, List

from services.signals import CandidateInput

from .base import CandidateProvider


class CompositeCandidateProvider(CandidateProvider):
    def __init__(self, providers: Iterable[CandidateProvider]):
        self.providers = list(providers)

    def get_candidate_inputs(self, market: str) -> List[CandidateInput]:
        merged: dict[str, CandidateInput] = {}
        for provider in self.providers:
            for item in provider.get_candidate_inputs(market):
                if item.symbol not in merged:
                    merged[item.symbol] = item
                    continue
                existing = merged[item.symbol]
                existing.signals.extend(item.signals)
                if item.raw_score > existing.raw_score:
                    existing.raw_score = item.raw_score
                    existing.rationale = item.rationale or existing.rationale
                    existing.risk = item.risk or existing.risk
                    existing.action_hint = item.action_hint or existing.action_hint
                    existing.confidence_source = item.confidence_source or existing.confidence_source
        return list(merged.values())
