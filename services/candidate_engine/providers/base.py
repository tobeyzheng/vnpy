from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from services.signals import CandidateInput


class CandidateProvider(ABC):
    @abstractmethod
    def get_candidate_inputs(self, market: str) -> List[CandidateInput]:
        raise NotImplementedError
