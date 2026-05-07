from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ApprovalRecord:
    request_id: str
    status: str  # pending/approved/rejected
    approver: Optional[str] = None
    reason: str = ''


@dataclass
class TradeStateRecord:
    request_id: str
    symbol: str
    market: str
    phase: str
    notes: List[str] = field(default_factory=list)
