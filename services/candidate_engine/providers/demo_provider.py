from __future__ import annotations

from typing import List

from services.candidate_engine.models import Candidate
from services.reporting.demo_data import build_demo_candidates

from .base import CandidateProvider


class DemoCandidateProvider(CandidateProvider):
    def get_candidates(self, market: str) -> List[Candidate]:
        return build_demo_candidates(market)
