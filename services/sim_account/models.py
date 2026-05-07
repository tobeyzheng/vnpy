from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SimPosition:
    symbol: str
    qty: int
    avg_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class SimOrder:
    symbol: str
    side: str
    qty: int
    price: float
    status: str
    reason: str


@dataclass
class SimAccount:
    base_currency: str = 'HKD'
    initial_cash: float = 10000.0
    cash: float = 10000.0
    nav: float = 10000.0
    realized_pnl: float = 0.0
    max_drawdown_limit_pct: float = 0.20
    positions: List[SimPosition] = field(default_factory=list)
    orders: List[SimOrder] = field(default_factory=list)
