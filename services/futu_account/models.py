from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FutuPosition:
    code: str
    name: str
    qty: float
    market_val: Optional[float] = None
    pl_ratio: Optional[float] = None


@dataclass
class FutuOrder:
    code: str
    side: str
    qty: float
    status: str


@dataclass
class FutuAccountSummary:
    status: str
    account_count: int = 0
    env: str = "SIMULATE"
    total_assets: Optional[float] = None
    cash: Optional[float] = None
    buying_power: Optional[float] = None
    positions: List[FutuPosition] = field(default_factory=list)
    orders: List[FutuOrder] = field(default_factory=list)
    message: str = "not_loaded"
