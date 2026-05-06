from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .base import LlmSignal, RiskDecision, clamp, parse_datetime


@dataclass(frozen=True)
class RiskPolicy:
    min_confidence: float = 0.55
    stale_after_hours: int = 30
    high_risk_threshold: float = 0.75
    block_risk_threshold: float = 0.85
    default_position_multiplier: float = 0.0
    fail_closed: bool = True


def conservative_decision(reason: str, policy: RiskPolicy) -> RiskDecision:
    multiplier = 0.0 if policy.fail_closed else clamp(policy.default_position_multiplier)
    return RiskDecision(
        allow_new_long=not policy.fail_closed and multiplier > 0,
        reduce_only=policy.fail_closed,
        position_multiplier=multiplier,
        reason=reason,
        signal_id=None,
    )


def make_risk_decision(
    signal: LlmSignal | None,
    policy: RiskPolicy | None = None,
    as_of: datetime | None = None,
) -> RiskDecision:
    policy = policy or RiskPolicy()
    decision_time = parse_datetime(as_of) if as_of else parse_datetime(datetime.now())

    if signal is None:
        return conservative_decision("missing_llm_signal", policy)

    if not signal.is_visible_at(decision_time):
        return RiskDecision(False, True, 0.0, "signal_not_visible_at_decision_time", signal.signal_id)

    if decision_time > signal.valid_until:
        return RiskDecision(False, True, 0.0, "signal_expired", signal.signal_id)

    if decision_time - signal.scored_at > timedelta(hours=policy.stale_after_hours):
        return RiskDecision(False, True, 0.0, "signal_stale", signal.signal_id)

    if signal.confidence < policy.min_confidence:
        return RiskDecision(False, True, 0.0, "confidence_too_low", signal.signal_id)

    if signal.trade_filter == "block_long":
        return RiskDecision(False, True, 0.0, "llm_block_long", signal.signal_id)

    if max(signal.sector_risk, signal.macro_risk, signal.jpy_fx_risk) >= policy.block_risk_threshold:
        return RiskDecision(False, True, 0.0, "risk_above_block_threshold", signal.signal_id)

    multiplier = clamp(signal.position_multiplier)
    reduce_only = signal.trade_filter == "reduce_only"

    if max(signal.sector_risk, signal.macro_risk, signal.jpy_fx_risk) >= policy.high_risk_threshold:
        multiplier = min(multiplier, 0.3)
        reduce_only = True

    allow = signal.trade_filter == "allow_long" and multiplier > 0 and not reduce_only
    reason = "allow_long" if allow else "reduce_only_or_zero_multiplier"
    return RiskDecision(allow, reduce_only, multiplier, reason, signal.signal_id)
