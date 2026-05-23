"""OrderIntent / OrderState data model for phase2 live trading.

This module is a thin phase2-namespaced facade over the production-grade
``services.common.trading_models`` and ``services.trade_state.storage``.
It adds three pieces of value on top:

1. ``build_request_id`` — deterministic, idempotent request id from
   ``(strategy_id, symbol, side, rebalance_date, seq)``. Same inputs always
   produce the same id, which lets the idempotency gate dedupe restarts.
2. ``LEGAL_TRANSITIONS`` + ``transition`` — explicit state machine guard so
   phase2 live code cannot silently drive an order from e.g. ``rejected`` to
   ``filled``. Mirrors the (limited) transition semantics already implied by
   ``services.trade_state.state_machine`` but stays a small, dependency-free
   helper for unit testing.
3. ``OrderStateStoreExt`` — wraps ``services.trade_state.storage.OrderStateStore``
   and adds ``list_open_request_ids`` plus ``save_intent_as_state`` so the
   pre-trade pipeline can persist a freshly-created intent in a single call.

Design constraints:
- Must not import from ``scripts.classic_multifactor.*`` (project rule).
- Must not modify ``phase2.backtest.*`` or ``phase2.strategy.*``.
- All persistence must respect the per-execution-env directory layout
  ``state/runs/phase2_live/<env>/<run_id>/orders/`` set by the runner.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterable

from services.common.trading_models import (
    ExecutionChannel,
    ExecutionEnvironment,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderStatus,
    SourcePhase,
)
from services.trade_state.storage import OrderStateStore

__all__ = [
    "OrderIntent",
    "OrderState",
    "OrderStatus",
    "OrderSide",
    "ExecutionEnvironment",
    "ExecutionChannel",
    "SourcePhase",
    "OrderStateStore",
    "OrderStateStoreExt",
    "build_request_id",
    "transition",
    "LEGAL_TRANSITIONS",
    "OPEN_STATUSES",
    "TERMINAL_STATUSES",
]


# ---------------------------------------------------------------------------
# Deterministic request_id
# ---------------------------------------------------------------------------

def build_request_id(
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    rebalance_date: str,
    seq: int = 0,
) -> str:
    """Build a deterministic request_id from the rebalance context.

    The id is stable across process restarts so the idempotency gate can
    safely reject duplicates if the runner is re-launched on the same trading
    day. ``seq`` lets a single (strategy, symbol, side, date) tuple carry
    multiple distinct intents within the same rebalance (rare for daily
    rebalance, but supported for safety).
    """
    if not strategy_id:
        raise ValueError("strategy_id must be a non-empty string")
    if not symbol:
        raise ValueError("symbol must be a non-empty string")
    side_norm = (side or "").upper()
    if side_norm not in {"BUY", "SELL"}:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if not rebalance_date:
        raise ValueError("rebalance_date must be a non-empty string")
    if seq < 0:
        raise ValueError("seq must be non-negative")
    payload = f"{strategy_id}|{symbol}|{side_norm}|{rebalance_date}|{seq}".encode(
        "utf-8"
    )
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"p2live-{digest}"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

OPEN_STATUSES: frozenset[str] = frozenset(
    {
        "created",
        "validated",
        "risk_checked",
        "approval_required",
        "approved",
        "submitting",
        "submitted",
        "partial_filled",
        "cancel_requested",
    }
)

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "cancelled", "rejected", "expired", "failed", "reconciled"}
)

# Legal transitions for phase2 daily rebalance. Strictly subset of the
# OrderStatus union; out-of-graph attempts must raise.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"validated", "rejected", "failed"}),
    "validated": frozenset({"risk_checked", "rejected", "failed"}),
    "risk_checked": frozenset({"approved", "approval_required", "rejected", "failed"}),
    "approval_required": frozenset({"approved", "rejected", "failed"}),
    "approved": frozenset({"submitting", "rejected", "failed"}),
    "submitting": frozenset({"submitted", "rejected", "failed"}),
    "submitted": frozenset(
        {
            "partial_filled",
            "filled",
            "cancel_requested",
            "cancelled",
            "rejected",
            "expired",
            "failed",
        }
    ),
    "partial_filled": frozenset(
        {"filled", "cancel_requested", "cancelled", "expired", "failed"}
    ),
    "cancel_requested": frozenset({"cancelled", "filled", "partial_filled", "failed"}),
    # Terminal states cannot transition further.
    "filled": frozenset({"reconciled"}),
    "cancelled": frozenset({"reconciled"}),
    "rejected": frozenset(),
    "expired": frozenset(),
    "failed": frozenset(),
    "reconciled": frozenset(),
}


def transition(state: OrderState, target: str) -> OrderState:
    """Return a new ``OrderState`` whose status is ``target``.

    Raises ``ValueError`` if the transition is not in ``LEGAL_TRANSITIONS``.
    The original ``state`` instance is not mutated.
    """
    current = state.status
    if current not in LEGAL_TRANSITIONS:
        raise ValueError(f"unknown current status {current!r}")
    if target not in LEGAL_TRANSITIONS:
        raise ValueError(f"unknown target status {target!r}")
    if target not in LEGAL_TRANSITIONS[current]:
        raise ValueError(
            f"illegal transition {current!r} -> {target!r}; "
            f"allowed: {sorted(LEGAL_TRANSITIONS[current])}"
        )
    return replace(state, status=target)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Storage extension
# ---------------------------------------------------------------------------

class OrderStateStoreExt:
    """Thin wrapper around ``services.trade_state.storage.OrderStateStore``.

    Adds two phase2-flavoured helpers:

    - ``save_intent_as_state(intent)`` persists a freshly-built ``OrderIntent``
      as a ``created`` ``OrderState`` so the idempotency gate sees it.
    - ``list_open_request_ids()`` returns the set of request_ids that are still
      in ``OPEN_STATUSES`` — used by the runner to recover pending orders on
      restart.

    The wrapper intentionally exposes ``inner`` so callers that already accept
    a vanilla ``OrderStateStore`` keep working without changes.
    """

    def __init__(self, root: str | Path):
        self.inner = OrderStateStore(root)
        self.root = self.inner.root

    # -- convenience proxies -------------------------------------------------
    def save(self, state: OrderState) -> Path:
        return self.inner.save(state)

    def load(self, request_id: str) -> OrderState | None:
        return self.inner.load(request_id)

    def list(self) -> list[OrderState]:
        return self.inner.list()

    def has_open_request(self, request_id: str) -> bool:
        return self.inner.has_open_request(request_id)

    def find_by_broker_order_id(self, broker_order_id: str) -> OrderState | None:
        return self.inner.find_by_broker_order_id(broker_order_id)

    def summary(self) -> dict[str, int]:
        return self.inner.summary()

    # -- phase2-specific extras ---------------------------------------------
    def save_intent_as_state(self, intent: OrderIntent) -> OrderState:
        """Materialise an OrderIntent as a ``created`` OrderState and persist."""
        existing = self.inner.load(intent.request_id)
        if existing is not None:
            return existing
        state = OrderState(
            request_id=intent.request_id,
            symbol=intent.symbol,
            market=intent.market,
            side=intent.side,
            qty=intent.qty,
            status="created",
            price=intent.price,
            strategy_id=intent.strategy_id,
            reason=intent.reason,
            execution_channel=intent.execution_channel,
            execution_env=intent.execution_env,
            source_phase=intent.source_phase,
        )
        self.inner.save(state)
        return state

    def list_open_request_ids(self) -> list[str]:
        return [s.request_id for s in self.inner.list() if s.status in OPEN_STATUSES]

    def update_status(self, request_id: str, target: str) -> OrderState:
        """Load → transition → save in one call, with full guard-rail."""
        state = self.inner.load(request_id)
        if state is None:
            raise KeyError(f"no order state for request_id={request_id!r}")
        next_state = transition(state, target)
        self.inner.save(next_state)
        return next_state


# ---------------------------------------------------------------------------
# Helper factories used by live_adapter / runner
# ---------------------------------------------------------------------------

def make_intent(
    *,
    strategy_id: str,
    symbol: str,
    market: str,
    side: str,
    qty: int,
    price: float | None,
    rebalance_date: str,
    seq: int = 0,
    execution_env: str = "dry_run",
    execution_channel: str = "futu",
    source_phase: str = "live_session",
    target_position_pct: float = 0.0,
    reason: str = "",
    signal_snapshot: dict | None = None,
    risk_snapshot: dict | None = None,
) -> OrderIntent:
    """Build a fully-populated ``OrderIntent`` with deterministic request_id."""
    request_id = build_request_id(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        rebalance_date=rebalance_date,
        seq=seq,
    )
    return OrderIntent(
        request_id=request_id,
        symbol=symbol,
        market=market,
        strategy_id=strategy_id,
        side=side.upper(),  # type: ignore[arg-type]
        qty=int(qty),
        price=price,
        target_position_pct=float(target_position_pct),
        reason=reason,
        signal_snapshot=signal_snapshot or {},
        risk_snapshot=risk_snapshot or {},
        execution_channel=execution_channel,  # type: ignore[arg-type]
        execution_env=execution_env,  # type: ignore[arg-type]
        source_phase=source_phase,  # type: ignore[arg-type]
        submitted_to_broker=False,
    )


def to_dict(obj: OrderIntent | OrderState) -> dict:
    """Convert an OrderIntent / OrderState into a plain dict for JSON dump."""
    return asdict(obj)


def collect_open_states(
    states: Iterable[OrderState],
) -> list[OrderState]:  # pragma: no cover - trivial helper
    return [s for s in states if s.status in OPEN_STATUSES]
