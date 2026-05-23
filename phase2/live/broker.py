"""LiveBroker protocol for phase2 live trading.

Defines the 9 methods required by the runner so the runner can drive any
broker implementation behind a single typed interface. The Futu OpenD
implementation lives in ``phase2.live.futu_broker``; tests substitute a
``InMemoryBroker`` (see ``phase2/live/tests/test_broker.py``).

All DTOs are plain dataclasses so unit tests can construct them without
``futu`` installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, runtime_checkable

from phase2.live.order_state import OrderIntent

__all__ = [
    "BrokerAccount",
    "BrokerPosition",
    "BrokerOrderAck",
    "BrokerOrderUpdate",
    "OrderHandlerCallback",
    "LiveBroker",
]


@dataclass(frozen=True)
class BrokerAccount:
    cash: float
    market_value: float
    total_assets: float
    currency: str = "USD"
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int
    avg_cost: float = 0.0
    market_value: float = 0.0
    currency: str = "USD"
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerOrderAck:
    """Result of a successful place_order call."""

    request_id: str
    broker_order_id: str
    accepted_qty: int
    status: str  # "submitted" | "accepted" | "rejected"
    message: str = ""
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerOrderUpdate:
    """Asynchronous status update from broker callback."""

    broker_order_id: str
    request_id: str | None
    symbol: str
    status: str  # phase2 OrderStatus literal
    filled_qty: int
    avg_fill_price: float
    raw: dict = field(default_factory=dict)


OrderHandlerCallback = Callable[[BrokerOrderUpdate], None]


@runtime_checkable
class LiveBroker(Protocol):
    """Minimal contract every phase2 live broker MUST implement.

    Methods are kept synchronous; futu's async callbacks are surfaced via
    ``register_order_handler`` (the broker invokes the registered callback
    from its own internal thread).
    """

    execution_env: str  # "futu_sim" | "futu_real"

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def unlock_trade(self) -> bool:
        """REAL: unlock trading. SIM/dry: must return True without side effects."""
        ...

    def query_account(self) -> BrokerAccount: ...

    def query_positions(self) -> list[BrokerPosition]: ...

    def place_order(self, intent: OrderIntent) -> BrokerOrderAck: ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def subscribe_quote(self, symbols: Iterable[str]) -> None: ...

    def register_order_handler(self, callback: OrderHandlerCallback) -> None: ...
