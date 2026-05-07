from __future__ import annotations

from typing import List

from services.reporting.demo_data import build_demo_candidates
from services.signals import CandidateInput, RawSignal

from .base import CandidateProvider


class DemoCandidateProvider(CandidateProvider):
    def get_candidate_inputs(self, market: str) -> List[CandidateInput]:
        result: List[CandidateInput] = []
        for candidate in build_demo_candidates(market):
            signals = [
                RawSignal(
                    symbol=candidate.symbol,
                    market=candidate.market,
                    source=e.source,
                    category=e.category,
                    score=e.score,
                    summary=e.summary,
                )
                for e in candidate.evidence
            ]
            result.append(
                CandidateInput(
                    symbol=candidate.symbol,
                    market=candidate.market,
                    name=candidate.name,
                    rationale=candidate.rationale,
                    risk=candidate.risk,
                    raw_score=max((e.score for e in signals), default=0.0),
                    confidence_source="demo_provider",
                    action_hint=candidate.action,
                    signals=signals,
                )
            )
        return result
