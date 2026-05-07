from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from services.candidate_engine.models import Candidate


class CandidateProvider(ABC):
    @abstractmethod
    def get_candidates(self, market: str) -> List[Candidate]:
        raise NotImplementedError
