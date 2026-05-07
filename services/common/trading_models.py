from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Direction = Literal["long", "short", "flat"]
OrderSide = Literal["BUY", "SELL"]
OrderStatus = Literal[
    "created",
    "validated",
    "risk_checked",
    "approval_required",
    "approved",
    "submitting",
    "submitted",
    "partial_filled",
    "filled",
    "cancel_requested",
    "cancelled",
    "rejected",
    "expired",
    "reconciled",
    "failed",
]


@dataclass
class StrategySignal:
    strategy_id: str
    symbol: str
    market: str
    direction: Direction
    score: float
    confidence: float
    allow_trade: bool
    target_position_pct: float
    reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderIntent:
    request_id: str
    symbol: str
    market: str
    strategy_id: str
    side: OrderSide
    qty: int
    price: float | None
    target_position_pct: float
    reason: str = ""
    signal_snapshot: dict[str, Any] = field(default_factory=dict)
    risk_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderState:
    request_id: str
    symbol: str
    market: str
    side: OrderSide
    qty: int
    status: OrderStatus = "created"
    price: float | None = None
    filled_qty: int = 0
    avg_fill_price: float | None = None
    broker_order_id: str | None = None
    strategy_id: str = ""
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    snapshots: dict[str, Any] = field(default_factory=dict)
