from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LiveOrderRequest:
    request_id: str
    symbol: str
    market: str
    side: str
    qty: float
    target_position_pct: float | None = None
    notional: float | None = None
    order_type: str = 'LIMIT'
    price: Optional[float] = None
    tif: str = 'DAY'
    venue: Optional[str] = None
    reason: str = ''
    source: str = 'paper_intent'
    mode: str = 'live_prep'
    tags: List[str] = field(default_factory=list)
