from __future__ import annotations

from vnpy_llm.scoring import _normalize_payload


def test_normalize_payload_accepts_label_scores() -> None:
    payload = _normalize_payload(
        "SOXL.SMART",
        [],
        {
            "sector_sentiment": "positive",
            "sector_risk": "medium",
            "macro_risk": "low",
            "jpy_fx_risk": "high",
            "us_economy_score": "neutral",
            "event_score": "bullish",
            "confidence": "high",
            "trade_filter": "allow_long",
            "position_multiplier": "80%",
        },
    )
    assert payload["sector_sentiment"] == 0.5
    assert payload["sector_risk"] == 0.5
    assert payload["macro_risk"] == 0.25
    assert payload["jpy_fx_risk"] == 0.8
    assert payload["event_score"] == 0.7
    assert payload["confidence"] == 0.8
    assert payload["position_multiplier"] == 0.8
