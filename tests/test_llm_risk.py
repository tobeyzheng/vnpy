from __future__ import annotations

from datetime import timedelta

from vnpy_llm.base import LlmSignal, utc_now
from vnpy_llm.risk import RiskPolicy, make_risk_decision


def signal(**overrides) -> LlmSignal:
    now = utc_now()
    payload = {
        "signal_id": "sig",
        "symbol": "SOXL.SMART",
        "as_of": now.isoformat(),
        "valid_until": (now + timedelta(hours=24)).isoformat(),
        "source_time": now.isoformat(),
        "retrieved_at": now.isoformat(),
        "scored_at": now.isoformat(),
        "sector_sentiment": 0.4,
        "sector_risk": 0.3,
        "macro_risk": 0.3,
        "fed_policy_bias": "neutral",
        "jpy_fx_risk": 0.2,
        "us_economy_score": 0.2,
        "event_score": 0.3,
        "confidence": 0.8,
        "trade_filter": "allow_long",
        "position_multiplier": 0.8,
        "reasons": [],
        "sources": [],
    }
    payload.update(overrides)
    return LlmSignal.from_dict(payload)


def test_missing_signal_fails_closed() -> None:
    decision = make_risk_decision(None, RiskPolicy(fail_closed=True))
    assert not decision.allow_new_long
    assert decision.reduce_only


def test_allow_signal_passes() -> None:
    item = signal()
    decision = make_risk_decision(item, RiskPolicy(), item.scored_at)
    assert decision.allow_new_long
    assert decision.position_multiplier == 0.8


def test_low_confidence_blocks() -> None:
    item = signal(confidence=0.2)
    decision = make_risk_decision(item, RiskPolicy(), item.scored_at)
    assert not decision.allow_new_long
    assert decision.reason == "confidence_too_low"


def test_high_macro_risk_blocks() -> None:
    item = signal(macro_risk=0.95)
    decision = make_risk_decision(item, RiskPolicy(), item.scored_at)
    assert not decision.allow_new_long
    assert decision.reason == "risk_above_block_threshold"
