from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawSignal:
    symbol: str
    market: str
    source: str
    category: str
    score: float
    summary: str
    timestamp: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class CandidateInput:
    symbol: str
    market: str
    name: str
    rationale: str
    risk: str
    raw_score: float
    confidence_source: str
    action_hint: str
    signals: List[RawSignal] = field(default_factory=list)
