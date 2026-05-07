from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LiveOrderRequest:
    symbol: str
    market: str
    side: str
    qty: float
    order_type: str = 'LIMIT'
    price: Optional[float] = None
    tif: str = 'DAY'
    venue: Optional[str] = None
    reason: str = ''
    tags: List[str] = field(default_factory=list)
