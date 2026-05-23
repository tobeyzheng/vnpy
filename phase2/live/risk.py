"""Order sizing + single-symbol risk check for phase2 live trading.

copied & adapted from scripts/classic_multifactor/risk.py.

Adaptations from the original (kept minimal so behaviour stays equivalent):

1. Removed the import of ``scripts.classic_multifactor.model.{ClassicMultiFactorConfig,
   FactorSnapshot}`` so this module has zero ``scripts.classic_multifactor.*``
   dependency. The two replacement dataclasses ``Phase2RiskConfig`` and
   ``RebalanceContext`` carry only the fields actually consumed here.
2. ``ClassicOrderRiskManager`` now creates a fresh ``LiveRiskGuard`` per
   ``size_and_check`` call. The shared instance in the original module
   accumulates ``_seen`` request_ids and would falsely reject re-evaluations
   inside the same process; phase2 idempotency is enforced at the dedicated
   ``IdempotencyGate`` (see ``phase2/live/guards.py``), not here.
3. Default ``account_status`` argument unchanged ("connected"); other
   call-site arguments unchanged.
4. Public class name kept (``ClassicOrderRiskManager``) so unit tests and
   guard wiring read identically to the source pipeline.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from execution.live_bridge.models import LiveOrderRequest
from services.risk_engine.live_guard import LiveRiskGuard

__all__ = [
    "Phase2RiskConfig",
    "RebalanceContext",
    "RiskDecision",
    "ClassicOrderRiskManager",
]


@dataclass(frozen=True)
class Phase2RiskConfig:
    """Minimal phase2 replacement for ``ClassicMultiFactorConfig``.

    Only the two fields actually consumed by ``ClassicOrderRiskManager`` are
    kept: ``max_position_pct`` and ``max_order_value``. Adding more fields here
    is allowed but should be justified by an actual call site.
    """

    max_position_pct: float
    max_order_value: float


@dataclass(frozen=True)
class RebalanceContext:
    """Minimal phase2 replacement for ``FactorSnapshot``.

    Only the two attributes referenced by the original ``risk.py`` are kept:
    ``datetime`` (for request_id seeding) and ``reason`` (forwarded into the
    ``LiveOrderRequest.reason`` field). Both are optional.
    """

    datetime: str = ""
    reason: str = "phase2_live"


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: list[str]
    qty: int
    notional: float
    target_position_pct: float
    request_id: str

    def to_dict(self) -> dict:
        return asdict(self)


class ClassicOrderRiskManager:
    """Order sizing and risk check using existing LiveRiskGuard semantics.

    Behavioural parity with ``scripts/classic_multifactor/risk.py`` is the
    primary goal; phase2-specific deviations are limited to what is documented
    in this module's top-level docstring.
    """

    def __init__(self, config: Phase2RiskConfig, *, market: str = "us"):
        self.config = config
        self.market = market
        self._guard_limits = {
            "max_single_position_pct": config.max_position_pct,
            "max_daily_new_position_pct": min(config.max_position_pct * 2, 1.0),
            "max_market_exposure_pct": 1.0,
            "max_drawdown_pct": 0.30,
            "max_order_value": config.max_order_value,
            "max_signal_age_seconds": 900,
        }

    # The classic implementation kept a single shared LiveRiskGuard instance
    # whose ``_seen`` set causes double-evaluations to be rejected as
    # "duplicate live request detected". phase2 isolates each call instead so
    # the gate pipeline owns idempotency end-to-end.
    @property
    def guard(self) -> LiveRiskGuard:  # pragma: no cover - trivial alias
        return LiveRiskGuard(dict(self._guard_limits))

    def size_and_check(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        cash: float,
        equity: float,
        current_qty: int,
        target_qty: int,
        factor: RebalanceContext | None,
        account_status: str = "connected",
    ) -> RiskDecision:
        qty = abs(int(target_qty) - int(current_qty))
        if side == "BUY":
            max_value = min(
                float(self.config.max_order_value),
                max(float(cash), 0.0),
                max(float(equity), 0.0) * float(self.config.max_position_pct),
            )
            qty = min(qty, int(max_value // price) if price > 0 else 0)
        else:
            qty = min(qty, max(int(current_qty), 0))
        notional = round(max(qty, 0) * max(price, 0.0), 4)
        target_position_pct = notional / equity if equity > 0 else 0.0
        request_id = self._request_id(symbol, side, factor)
        reasons: list[str] = []
        if qty <= 0:
            reasons.append("qty_zero")
        if price <= 0:
            reasons.append("invalid_price")
        if side not in {"BUY", "SELL"}:
            reasons.append("invalid_side")
        if not reasons:
            order = LiveOrderRequest(
                request_id=request_id,
                symbol=symbol,
                market=self.market,
                side=side,
                qty=float(qty),
                target_position_pct=target_position_pct,
                notional=notional,
                order_type="LIMIT",
                price=price,
                reason=factor.reason if factor else "phase2_live",
                source="phase2_live",
                mode="sim",
            )
            risk = self.guard.evaluate(
                order,
                market_existing_pct=0.0,
                daily_new_pct=0.0,
                current_drawdown_pct=0.0,
                signal_age_seconds=0,
                account_status=account_status,
            )
            reasons.extend(risk.reasons)
        return RiskDecision(
            not reasons, reasons, qty, notional, target_position_pct, request_id
        )

    def _request_id(
        self, symbol: str, side: str, factor: RebalanceContext | None
    ) -> str:
        seed = f"phase2_live|{symbol}|{side}|{factor.datetime if factor else ''}"
        return hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]
