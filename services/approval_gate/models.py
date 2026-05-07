from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ApprovalDecision:
    mode: str
    allowed: bool
    reason: str
    required_actions: List[str] = field(default_factory=list)
