from __future__ import annotations

from typing import Iterable, List

from .models import Candidate


class CandidateRanker:
    """Simple candidate ranking scaffold using confidence-first ordering."""

    def top_n(self, candidates: Iterable[Candidate], n: int = 5) -> List[Candidate]:
        return sorted(
            candidates,
            key=lambda x: ((x.confidence or 0), len(x.evidence)),
            reverse=True,
        )[:n]
