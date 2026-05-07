from __future__ import annotations

from services.candidate_engine.models import Candidate, SignalEvidence
from services.signals import CandidateInput


def candidate_from_input(item: CandidateInput) -> Candidate:
    return Candidate(
        symbol=item.symbol,
        name=item.name,
        market=item.market,
        rationale=item.rationale,
        risk=item.risk,
        action=item.action_hint,
        evidence=[
            SignalEvidence(
                source=s.source,
                category=s.category,
                summary=s.summary,
                score=s.score,
            )
            for s in item.signals
        ],
    )
