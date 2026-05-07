from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VnpyOrderDraft:
    symbol: str
    market: str
    direction: str
    reason: str
    confidence: Optional[int] = None
    target_position_pct: Optional[float] = None
    gateway_name: Optional[str] = None
    notes: List[str] = field(default_factory=list)
