from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FutuOrderDraft:
    code: str
    trd_env: str
    side: str
    reason: str
    qty: Optional[int] = None
    target_position_pct: Optional[float] = None
    confidence: Optional[int] = None
    notes: List[str] = field(default_factory=list)
