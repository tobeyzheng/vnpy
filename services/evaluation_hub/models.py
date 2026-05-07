from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class EvaluationSignal:
    symbol: str
    market: str
    source: str
    dimension: str
    score: float
    confidence: float
    summary: str
    risks: List[str] = field(default_factory=list)
    action_bias: str = "neutral"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationBundle:
    symbol: str
    market: str
    signals: List[EvaluationSignal] = field(default_factory=list)
