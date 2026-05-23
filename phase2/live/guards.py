"""Four-stage pre-trade gate pipeline for phase2 live trading.

Pipeline order (verbatim from requirements §5):

    OrderIntent  ──►  ① IdempotencyGate
                  ──►  ② ReconciliationGate
                  ──►  ③ RiskGate (single-symbol)
                  ──►  ④ PortfolioRiskGate
                  ──►  approved → broker.place_order

A rejection at any stage:
- prevents broker.place_order from being called.
- emits an ``order_blocked`` event into ``events.jsonl`` containing the
  triggering gate name and the structured reason payload.
- transitions the persisted order state ``created → rejected`` via
  ``OrderStateStoreExt.update_status``.

Minute-level rate-limit gate (``MinuteTradeGuard``) is intentionally **not**
wired here per the milestone scope (daily rebalance only).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from phase2.live.order_state import (
    OrderIntent,
    OrderStateStoreExt,
    OPEN_STATUSES,
)
from phase2.live.risk import (
    ClassicOrderRiskManager,
    RebalanceContext,
    RiskDecision,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GateResult",
    "Gate",
    "IdempotencyGate",
    "ReconciliationGate",
    "RiskGate",
    "PortfolioRiskGate",
    "GatePipeline",
    "PortfolioState",
    "GateContext",
    "EventLogger",
]


# ---------------------------------------------------------------------------
# Result + protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    allowed: bool
    gate: str
    reasons: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, gate: str, **payload: Any) -> "GateResult":
        return cls(True, gate, [], payload)

    @classmethod
    def block(cls, gate: str, *reasons: str, **payload: Any) -> "GateResult":
        return cls(False, gate, list(reasons), payload)


@dataclass
class PortfolioState:
    """Snapshot of the portfolio at the moment ``intent`` is being checked.

    Held by the runner and refreshed before each rebalance pass so the gates
    can take consistent decisions. Risk-related fields default to safe values
    so unit tests can pass partial dicts.
    """

    cash: float
    equity: float
    current_positions: dict[str, int] = field(default_factory=dict)
    target_positions: dict[str, int] = field(default_factory=dict)
    market_existing_pct: float = 0.0
    daily_new_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    last_close: dict[str, float] = field(default_factory=dict)


@dataclass
class GateContext:
    portfolio: PortfolioState
    rebalance_date: str = ""
    safe_mode_close_only: bool = False  # when reconciliation flagged a breach


@runtime_checkable
class Gate(Protocol):
    name: str

    def check(self, intent: OrderIntent, ctx: GateContext) -> GateResult: ...


# ---------------------------------------------------------------------------
# Event logger
# ---------------------------------------------------------------------------

class EventLogger:
    """Append JSON lines to ``events.jsonl`` under the run's products dir.

    Stays small on purpose: the runner constructs one and hands it to gates.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **fields: Any) -> None:
        line = {
            "ts": time.time(),
            "event_type": event_type,
            **fields,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# ① Idempotency
# ---------------------------------------------------------------------------

class IdempotencyGate:
    name = "idempotency"

    def __init__(self, store: OrderStateStoreExt):
        self.store = store

    def check(self, intent: OrderIntent, ctx: GateContext) -> GateResult:
        existing = self.store.load(intent.request_id)
        if existing is None:
            return GateResult.ok(self.name, request_id=intent.request_id)
        if existing.status in OPEN_STATUSES and existing.status != "created":
            return GateResult.block(
                self.name,
                "duplicate_request_in_flight",
                existing_status=existing.status,
                request_id=intent.request_id,
            )
        if existing.status == "filled":
            return GateResult.block(
                self.name,
                "duplicate_request_already_filled",
                existing_status=existing.status,
                request_id=intent.request_id,
            )
        # 'created' is the freshly-saved intent we just wrote ourselves; allow.
        # 'rejected' / 'cancelled' / 'failed' would indicate the strategy is
        # retrying after a known failure — we let the next stages decide.
        return GateResult.ok(
            self.name,
            existing_status=existing.status,
            request_id=intent.request_id,
        )


# ---------------------------------------------------------------------------
# ② Reconciliation
# ---------------------------------------------------------------------------

class ReconciliationGate:
    """Verify that a recent reconcile report exists and shows no breach.

    The runner writes ``state/runs/phase2_live/<env>/<run_id>/reconcile/<ts>.json``
    after each ``broker.query_positions()`` cross-check. This gate looks up
    the newest report under ``reconcile_dir`` and rejects orders if:

    - no report exists at all (cold start), AND ``require_report=True``
    - the newest report is older than ``max_age_seconds``
    - the newest report has ``breaches`` non-empty (drift over threshold)

    For dry_run we typically pass ``require_report=False`` so phase2 backtest
    smoke tests don't need a fake report on disk.
    """

    name = "reconciliation"

    def __init__(
        self,
        reconcile_dir: str | Path,
        *,
        max_age_seconds: float = 24 * 3600,
        require_report: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        self.reconcile_dir = Path(reconcile_dir)
        self.max_age_seconds = max_age_seconds
        self.require_report = require_report
        self._clock = clock

    def latest_report(self) -> dict | None:
        if not self.reconcile_dir.exists():
            return None
        files = sorted(self.reconcile_dir.glob("*.json"))
        if not files:
            return None
        try:
            return json.loads(files[-1].read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("failed to read reconcile report: %s", exc)
            return None

    def check(self, intent: OrderIntent, ctx: GateContext) -> GateResult:
        report = self.latest_report()
        if report is None:
            if self.require_report:
                return GateResult.block(
                    self.name, "no_reconcile_report",
                    request_id=intent.request_id,
                )
            return GateResult.ok(self.name, report_present=False)
        ts = float(report.get("ts", 0.0))
        age = self._clock() - ts
        if age > self.max_age_seconds:
            return GateResult.block(
                self.name, "reconcile_report_expired",
                age_seconds=age,
                request_id=intent.request_id,
            )
        breaches = report.get("breaches") or []
        if breaches:
            # Safe-mode: only allow SELL orders when a reconcile breach is
            # active so we can flatten without opening new exposure.
            if intent.side == "BUY":
                return GateResult.block(
                    self.name, "reconcile_breach_close_only",
                    breaches=breaches,
                    request_id=intent.request_id,
                )
            return GateResult.ok(
                self.name,
                note="buy_blocked_due_to_breach_but_sell_ok",
                breaches=breaches,
            )
        if ctx.safe_mode_close_only and intent.side == "BUY":
            return GateResult.block(
                self.name, "safe_mode_close_only",
                request_id=intent.request_id,
            )
        return GateResult.ok(self.name, age_seconds=age)


# ---------------------------------------------------------------------------
# ③ Single-symbol risk
# ---------------------------------------------------------------------------

class RiskGate:
    name = "risk"

    def __init__(self, risk_mgr: ClassicOrderRiskManager):
        self.risk_mgr = risk_mgr

    def check(self, intent: OrderIntent, ctx: GateContext) -> GateResult:
        ps = ctx.portfolio
        current_qty = int(ps.current_positions.get(intent.symbol, 0))
        if intent.side == "BUY":
            target_qty = current_qty + int(intent.qty)
        else:
            target_qty = max(current_qty - int(intent.qty), 0)
        price = float(intent.price) if intent.price is not None else 0.0
        decision: RiskDecision = self.risk_mgr.size_and_check(
            symbol=intent.symbol,
            side=intent.side,
            price=price,
            cash=ps.cash,
            equity=ps.equity,
            current_qty=current_qty,
            target_qty=target_qty,
            factor=RebalanceContext(datetime=ctx.rebalance_date, reason=intent.reason),
        )
        if not decision.allowed:
            return GateResult.block(
                self.name, *decision.reasons,
                qty=decision.qty,
                notional=decision.notional,
                request_id=intent.request_id,
            )
        return GateResult.ok(
            self.name,
            qty=decision.qty,
            notional=decision.notional,
            target_position_pct=decision.target_position_pct,
        )


# ---------------------------------------------------------------------------
# ④ Portfolio-wide risk
# ---------------------------------------------------------------------------

@dataclass
class PortfolioRiskLimits:
    max_single_position_pct: float
    max_daily_new_position_pct: float
    max_market_exposure_pct: float
    max_drawdown_pct: float


class PortfolioRiskGate:
    name = "portfolio_risk"

    def __init__(self, limits: PortfolioRiskLimits):
        self.limits = limits

    def check(self, intent: OrderIntent, ctx: GateContext) -> GateResult:
        ps = ctx.portfolio
        if ps.equity <= 0:
            return GateResult.block(self.name, "non_positive_equity")
        notional = abs(int(intent.qty)) * float(intent.price or 0.0)
        order_pct = notional / ps.equity
        # post-trade single-position pct: existing held + this BUY's add (only
        # check on BUY; SELL reduces exposure so isn't bound).
        if intent.side == "BUY":
            held = int(ps.current_positions.get(intent.symbol, 0))
            held_value = held * float(ps.last_close.get(intent.symbol, intent.price or 0.0))
            post_pct = (held_value + notional) / ps.equity
            if post_pct > self.limits.max_single_position_pct + 1e-9:
                return GateResult.block(
                    self.name, "single_position_pct_exceeded",
                    post_pct=post_pct,
                    cap=self.limits.max_single_position_pct,
                )
            if (ps.daily_new_pct + order_pct) > self.limits.max_daily_new_position_pct + 1e-9:
                return GateResult.block(
                    self.name, "daily_new_position_pct_exceeded",
                    requested=ps.daily_new_pct + order_pct,
                    cap=self.limits.max_daily_new_position_pct,
                )
            if (ps.market_existing_pct + order_pct) > self.limits.max_market_exposure_pct + 1e-9:
                return GateResult.block(
                    self.name, "market_exposure_pct_exceeded",
                    requested=ps.market_existing_pct + order_pct,
                    cap=self.limits.max_market_exposure_pct,
                )
        if ps.current_drawdown_pct > self.limits.max_drawdown_pct + 1e-9:
            return GateResult.block(
                self.name, "drawdown_breach",
                current=ps.current_drawdown_pct,
                cap=self.limits.max_drawdown_pct,
            )
        return GateResult.ok(self.name, order_pct=order_pct)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PipelineDecision:
    allowed: bool
    request_id: str
    blocked_by: str | None  # name of the gate that rejected, or None
    reasons: list[str] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)


class GatePipeline:
    """Run the four gates in order; persist + emit on every outcome."""

    def __init__(
        self,
        gates: Iterable[Gate],
        *,
        store: OrderStateStoreExt,
        events: EventLogger,
    ):
        self.gates = list(gates)
        self.store = store
        self.events = events

    def run(self, intent: OrderIntent, ctx: GateContext) -> PipelineDecision:
        # Persist the intent as a 'created' state so the idempotency gate sees
        # it on a subsequent retry within the same run.
        self.store.save_intent_as_state(intent)
        payloads: list[dict] = []
        for gate in self.gates:
            result = gate.check(intent, ctx)
            payloads.append(
                {"gate": result.gate, "allowed": result.allowed,
                 "reasons": list(result.reasons), "payload": dict(result.payload)}
            )
            if not result.allowed:
                self.events.emit(
                    "order_blocked",
                    request_id=intent.request_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    gate=result.gate,
                    reasons=list(result.reasons),
                    payload=dict(result.payload),
                )
                # Drive state machine: created → rejected.
                state = self.store.load(intent.request_id)
                if state is not None and state.status == "created":
                    self.store.update_status(intent.request_id, "rejected")
                return PipelineDecision(
                    allowed=False,
                    request_id=intent.request_id,
                    blocked_by=result.gate,
                    reasons=list(result.reasons),
                    payloads=payloads,
                )
        # All gates passed — record a single audit event and let the caller
        # advance the state machine via approved/submitting/submitted.
        self.events.emit(
            "order_approved",
            request_id=intent.request_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            payloads=payloads,
        )
        # Drive state machine: created → validated → risk_checked → approved
        # All in one shot since each transition is legal here.
        for tgt in ("validated", "risk_checked", "approved"):
            state = self.store.load(intent.request_id)
            if state is None or state.status not in OPEN_STATUSES:
                break
            try:
                self.store.update_status(intent.request_id, tgt)
            except ValueError:
                break
        return PipelineDecision(
            allowed=True,
            request_id=intent.request_id,
            blocked_by=None,
            reasons=[],
            payloads=payloads,
        )
