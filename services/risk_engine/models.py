from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class RiskAlert:
    level: str
    message: str


@dataclass
class RiskEvaluation:
    allowed: bool
    alerts: List[RiskAlert] = field(default_factory=list)
