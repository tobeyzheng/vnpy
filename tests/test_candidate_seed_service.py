from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from services.strategy.candidate_seed import KnotCandidateSeedService


class _StubLlmClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete_json(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.payload


def test_knot_seed_service_returns_normalized_rows_for_us_market():
    payload = {
        "items": [
            {
                "symbol": "NVDA.US",
                "market": "us",
                "name": "NVIDIA Corp",
                "raw_score": 0.82,
                "rationale": "AI compute leader",
                "risk": "valuation hot",
                "action_hint": "wait pullback",
                "theme_bucket": "ai_compute",
                "risk_level": "medium",
            },
            {"symbol": "00700.HK", "market": "hong_kong", "name": "Tencent", "raw_score": 0.71},
            {"symbol": "TSLA.US", "market": "us", "name": "Tesla", "raw_score": 0.65},
        ]
    }
    service = KnotCandidateSeedService(llm_client_factory=lambda: _StubLlmClient(payload))
    result = service.generate_candidates(market="us", target_count=10)
    assert result.available is True
    symbols = {row["symbol"] for row in result.rows}
    assert symbols == {"NVDA.US", "TSLA.US"}
    nvda = next(row for row in result.rows if row["symbol"] == "NVDA.US")
    assert nvda["confidence_source"] == "knot_agent_dynamic"
    assert nvda["raw_score"] == pytest.approx(0.82)
    assert nvda["theme_bucket"] == "ai_compute"
    assert nvda["risk_level"] == "medium"
    assert nvda["signals"] and nvda["signals"][0]["source"] == "knot_agent_dynamic"


def test_knot_seed_service_filters_invalid_and_caps_target_count():
    payload = {
        "items": [
            {"symbol": "BAD", "market": "us", "name": "Missing dot"},
            {"symbol": "AAA.US", "market": "us", "name": "Alpha", "raw_score": 0.5},
            {"symbol": "AAA.US", "market": "us", "name": "Duplicate"},
            {"symbol": "BBB.US", "market": "us", "name": "Beta"},
            {"symbol": "CCC.US", "market": "us", "name": "Gamma"},
        ]
    }
    service = KnotCandidateSeedService(llm_client_factory=lambda: _StubLlmClient(payload))
    result = service.generate_candidates(market="us", target_count=2)
    assert [row["symbol"] for row in result.rows] == ["AAA.US", "BBB.US"]


def test_knot_seed_service_marks_unavailable_when_no_client():
    service = KnotCandidateSeedService(llm_client_factory=lambda: None)
    result = service.generate_candidates(market="us", target_count=5)
    assert result.is_empty()
    assert result.available is False
    assert "knot_client_unconfigured" in result.fallback_reason


def test_knot_seed_service_handles_call_failure_and_falls_back():
    class _BoomClient:
        def complete_json(self, *_args, **_kwargs):
            raise OSError("network down")

    service = KnotCandidateSeedService(llm_client_factory=lambda: _BoomClient())
    result = service.generate_candidates(market="hong_kong", target_count=5)
    assert result.is_empty()
    assert result.fallback_reason.startswith("knot_call_failed:")


def test_knot_seed_service_off_runtime_skips_call():
    class _ShouldNotBeCalled:
        def complete_json(self, *_args, **_kwargs):
            raise AssertionError("client must not be invoked when runtime is off")

    service = KnotCandidateSeedService(llm_client_factory=lambda: _ShouldNotBeCalled())
    result = service.generate_candidates(market="us", target_count=3, runtime_mode="off")
    assert result.is_empty()
    assert result.fallback_reason == "knot_runtime_off"


def test_knot_seed_service_local_runtime_cannot_generate():
    service = KnotCandidateSeedService(llm_client_factory=lambda: _StubLlmClient({"items": []}))
    result = service.generate_candidates(market="us", target_count=3, runtime_mode="local")
    assert result.is_empty()
    assert result.fallback_reason == "local_runtime_cannot_generate"


def test_knot_seed_service_rejects_unknown_market():
    service = KnotCandidateSeedService(llm_client_factory=lambda: _StubLlmClient({"items": []}))
    result = service.generate_candidates(market="cn", target_count=3)
    assert result.is_empty()
    assert result.fallback_reason.startswith("unsupported_market:")


def test_knot_seed_service_parses_string_response_payload():
    raw_str = json.dumps(
        {
            "items": [
                {
                    "symbol": "AAPL.US",
                    "market": "us",
                    "name": "Apple",
                    "raw_score": 0.72,
                    "rationale": "ai pc cycle",
                }
            ]
        }
    )
    service = KnotCandidateSeedService(llm_client_factory=lambda: _StubLlmClient(raw_str))
    result = service.generate_candidates(market="us", target_count=3)
    assert [row["symbol"] for row in result.rows] == ["AAPL.US"]
