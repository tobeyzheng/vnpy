from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from services.candidate_engine.models import Candidate


@dataclass
class DecisionSignal:
    symbol: str
    name: str
    market: str
    action: str
    reason: str
    confidence: Optional[int] = None
    source_candidate: Optional[Candidate] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class MarketDecision:
    market: str
    regime: str
    signals: List[DecisionSignal] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)
