from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SignalEvidence:
    source: str
    category: str
    summary: str
    score: float


@dataclass
class Candidate:
    symbol: str
    name: str
    market: str
    sector: Optional[str] = None
    rationale: str = ""
    risk: str = ""
    confidence: Optional[int] = None
    action: str = "observe"
    evidence: List[SignalEvidence] = field(default_factory=list)
