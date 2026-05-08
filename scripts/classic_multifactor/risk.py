from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from execution.live_bridge.models import LiveOrderRequest
from services.risk_engine.live_guard import LiveRiskGuard

from scripts.classic_multifactor.model import ClassicMultiFactorConfig, FactorSnapshot


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
    """Order sizing and risk check using existing LiveRiskGuard semantics."""

    def __init__(self, config: ClassicMultiFactorConfig, *, market: str = "us"):
        self.config = config
        self.market = market
        self.guard = LiveRiskGuard(
            {
                "max_single_position_pct": config.max_position_pct,
                "max_daily_new_position_pct": min(config.max_position_pct * 2, 1.0),
                "max_market_exposure_pct": 1.0,
                "max_drawdown_pct": 0.30,
                "max_order_value": config.max_order_value,
                "max_signal_age_seconds": 900,
            }
        )

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
        factor: FactorSnapshot | None,
        account_status: str = "connected",
    ) -> RiskDecision:
        qty = abs(int(target_qty) - int(current_qty))
        if side == "BUY":
            max_value = min(float(self.config.max_order_value), max(float(cash), 0.0), max(float(equity), 0.0) * float(self.config.max_position_pct))
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
                reason=factor.reason if factor else "classic_multifactor",
                source="classic_multifactor",
                mode="sim",
            )
            risk = self.guard.evaluate(order, market_existing_pct=0.0, daily_new_pct=0.0, current_drawdown_pct=0.0, signal_age_seconds=0, account_status=account_status)
            reasons.extend(risk.reasons)
        return RiskDecision(not reasons, reasons, qty, notional, target_position_pct, request_id)

    def _request_id(self, symbol: str, side: str, factor: FactorSnapshot | None) -> str:
        seed = f"classic_multifactor|{symbol}|{side}|{factor.datetime if factor else ''}"
        return hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]
