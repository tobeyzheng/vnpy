from __future__ import annotations

from datetime import timedelta

from vnpy_llm.base import LlmSignal, utc_now
from vnpy_llm.risk import RiskPolicy, make_risk_decision
from vnpy_llm.signal_store import SignalStore


def test_backtest_decision_requires_visible_signal(tmp_path) -> None:
    decision_time = utc_now()
    payload = {
        "signal_id": "future-signal",
        "symbol": "SOXL.SMART",
        "as_of": decision_time.isoformat(),
        "valid_until": (decision_time + timedelta(hours=24)).isoformat(),
        "source_time": (decision_time + timedelta(minutes=1)).isoformat(),
        "retrieved_at": (decision_time + timedelta(minutes=1)).isoformat(),
        "scored_at": (decision_time + timedelta(minutes=1)).isoformat(),
        "sector_sentiment": 1.0,
        "sector_risk": 0.1,
        "macro_risk": 0.1,
        "fed_policy_bias": "dovish",
        "jpy_fx_risk": 0.1,
        "us_economy_score": 0.5,
        "event_score": 0.8,
        "confidence": 0.9,
        "trade_filter": "allow_long",
        "position_multiplier": 1.0,
        "reasons": [],
        "sources": [],
    }
    store = SignalStore(tmp_path)
    store.write(LlmSignal.from_dict(payload))

    assert store.latest("SOXL.SMART", decision_time) is None
    decision = make_risk_decision(store.latest("SOXL.SMART", decision_time), RiskPolicy(), decision_time)
    assert not decision.allow_new_long
