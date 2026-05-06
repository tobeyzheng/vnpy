from __future__ import annotations

from datetime import timedelta

from vnpy_llm.base import LlmSignal, utc_now
from vnpy_llm.signal_store import SignalStore


def make_payload() -> dict:
    now = utc_now()
    return {
        "signal_id": "SOXL.SMART:test",
        "symbol": "SOXL.SMART",
        "as_of": now.isoformat(),
        "valid_until": (now + timedelta(hours=24)).isoformat(),
        "source_time": now.isoformat(),
        "retrieved_at": now.isoformat(),
        "scored_at": now.isoformat(),
        "sector_sentiment": 0.2,
        "sector_risk": 0.3,
        "macro_risk": 0.4,
        "fed_policy_bias": "neutral",
        "jpy_fx_risk": 0.2,
        "us_economy_score": 0.1,
        "event_score": 0.2,
        "confidence": 0.8,
        "trade_filter": "allow_long",
        "position_multiplier": 0.7,
        "reasons": ["test"],
        "sources": ["news-1"],
    }


def test_signal_roundtrip() -> None:
    signal = LlmSignal.from_dict(make_payload())
    restored = LlmSignal.from_dict(signal.to_dict())
    assert restored.signal_id == signal.signal_id
    assert restored.symbol == "SOXL.SMART"
    assert restored.position_multiplier == 0.7
    assert restored.is_valid_at(signal.as_of)


def test_signal_store_ignores_future_source(tmp_path) -> None:
    now = utc_now()
    payload = make_payload()
    payload["source_time"] = (now + timedelta(hours=1)).isoformat()
    payload["retrieved_at"] = (now + timedelta(hours=1)).isoformat()
    payload["scored_at"] = (now + timedelta(hours=1)).isoformat()
    payload["as_of"] = now.isoformat()
    payload["valid_until"] = (now + timedelta(hours=24)).isoformat()

    store = SignalStore(tmp_path)
    store.write(LlmSignal.from_dict(payload))

    assert store.latest("SOXL.SMART", now) is None
