from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PaperTradeIntent:
    symbol: str
    market: str
    side: str
    reason: str
    confidence: Optional[int] = None
    target_position_pct: Optional[float] = None
    tags: List[str] = field(default_factory=list)
