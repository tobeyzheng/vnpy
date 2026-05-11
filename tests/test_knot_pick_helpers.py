"""Unit tests for the Knot four-dimension pick / holdings-review helpers.

These tests stub the ``OpenAICompatibleClient`` so no real Knot endpoint
is contacted, and verify that:

* The four-dim parser only keeps symbols that match the expected market.
* ``CandidateScoringService.enrich_row`` runs end-to-end on the parsed rows.
* The compact text output respects the dimension layout.
* ``mask_account_summary`` strips every numeric account field.
* ``call_knot_holdings_advice`` only accepts the four allowed actions.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.futu_account.models import FutuAccountSummary, FutuPosition  # noqa: E402
from services.strategy import knot_pick_helpers as helpers  # noqa: E402


class _StubClient:
    """Mimics OpenAICompatibleClient.complete_json with a canned payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def complete_json(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._payload


def _stub_factory(payload):
    def factory():
        return _StubClient(payload)
    return factory


# ---------------------------------------------------------------------------
# 4-dim picks
# ---------------------------------------------------------------------------
def test_call_knot_4dim_picks_parses_dimensions_and_filters_market():
    payload = {
        "technical": [
            {"symbol": "00700.HK", "market": "hong_kong", "name": "Tencent",
             "raw_score": 0.71, "rationale": "trend up", "risk": "vol",
             "action_hint": "buy dip"},
        ],
        "fundamental": [
            {"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba",
             "raw_score": 0.66, "rationale": "earnings beat", "risk": "macro",
             "action_hint": "watch"},
            # Wrong market should be filtered out.
            {"symbol": "NVDA.US", "market": "us", "name": "NVIDIA",
             "raw_score": 0.9, "rationale": "irrelevant", "risk": "n/a",
             "action_hint": "n/a"},
        ],
        "capital_flow": [
            {"symbol": "01810.HK", "market": "hong_kong", "name": "Xiaomi",
             "raw_score": 0.6, "rationale": "inflow", "risk": "demand",
             "action_hint": "watch"},
        ],
        "event_driven": [
            {"symbol": "03690.HK", "market": "hong_kong", "name": "Meituan",
             "raw_score": 0.55, "rationale": "regulator news", "risk": "policy",
             "action_hint": "wait"},
        ],
    }
    result = helpers.call_knot_4dim_picks(
        market="hong_kong",
        per_dim=3,
        client_factory=_stub_factory(payload),
    )
    assert result.market == "hong_kong"
    assert result.total_picks() == 4
    fundamental = result.dimensions["fundamental"]
    # The wrong-market NVDA.US row must be dropped.
    assert all(row["symbol"].endswith(".HK") for row in fundamental)
    assert fundamental[0]["symbol"] == "09988.HK"


def test_call_knot_4dim_picks_raises_when_client_unconfigured():
    try:
        helpers.call_knot_4dim_picks(
            market="us",
            client_factory=lambda: None,
        )
    except helpers.KnotPickError as exc:
        assert "Knot client is not configured" in str(exc)
    else:
        raise AssertionError("Expected KnotPickError when factory returns None")


def test_format_four_dim_picks_renders_all_four_sections():
    payload = {
        "technical": [
            {"symbol": "NVDA.US", "market": "us", "name": "NVIDIA",
             "raw_score": 0.8, "rationale": "trend", "risk": "vol",
             "action_hint": "buy dip"},
        ],
        "fundamental": [
            {"symbol": "AAPL.US", "market": "us", "name": "Apple",
             "raw_score": 0.7, "rationale": "guidance", "risk": "macro",
             "action_hint": "watch"},
        ],
        "capital_flow": [],
        "event_driven": [
            {"symbol": "TSLA.US", "market": "us", "name": "Tesla",
             "raw_score": 0.6, "rationale": "delivery beat", "risk": "exec",
             "action_hint": "watch"},
        ],
    }
    picks = helpers.call_knot_4dim_picks(
        market="us",
        per_dim=2,
        client_factory=_stub_factory(payload),
    )
    enriched = {
        dim: helpers.score_candidate_rows(rows, mode="dynamic")
        for dim, rows in picks.dimensions.items()
    }
    text = helpers.format_four_dim_picks(picks, enriched=enriched)
    assert "[US 4-Dim Picks @" in text
    assert "Technical" in text and "Fundamental" in text
    assert "Capital Flow" in text and "Event Driven" in text
    assert "NVDA.US" in text and "AAPL.US" in text and "TSLA.US" in text
    # Capital flow has no picks -> placeholder line must appear.
    assert "(no usable picks)" in text


# ---------------------------------------------------------------------------
# Holdings masking & advice
# ---------------------------------------------------------------------------
def test_mask_account_summary_strips_numeric_fields():
    summary = FutuAccountSummary(
        status="ok",
        account_count=1,
        env="SIMULATE",
        total_assets=1_000_000.0,
        cash=200_000.0,
        buying_power=400_000.0,
        positions=[
            FutuPosition(code="HK.00700", name="Tencent", qty=100.0,
                         market_val=40_000.0, pl_ratio=0.08),
            FutuPosition(code="US.NVDA", name="NVIDIA", qty=10.0,
                         market_val=200_000.0, pl_ratio=-0.02),
            FutuPosition(code="HK.09988", name="Alibaba", qty=200.0,
                         market_val=20_000.0, pl_ratio=0.001),
        ],
    )
    masked = helpers.mask_account_summary(summary)

    assert masked["env"] == "SIMULATE"
    assert masked["position_count"] == 3

    serialized = repr(masked)
    for forbidden in ("1000000", "200000", "400000", "40000", "100", "10", "20000"):
        assert forbidden not in serialized, f"sensitive token leaked: {forbidden}"

    weights = {p["symbol"]: p["weight_bucket"] for p in masked["positions"]}
    assert weights["00700.HK"] == "small"      # 4% of NAV
    assert weights["NVDA.US"] == "large"       # 20% of NAV
    assert weights["09988.HK"] == "small"      # 2% of NAV

    pl_dirs = {p["symbol"]: p["pl_direction"] for p in masked["positions"]}
    assert pl_dirs["00700.HK"] == "up"
    assert pl_dirs["NVDA.US"] == "down"
    assert pl_dirs["09988.HK"] == "flat"


def test_call_knot_holdings_advice_filters_invalid_actions():
    payload = {
        "items": [
            {"symbol": "NVDA.US", "action": "trim", "confidence": 0.7,
             "reason": "short-term overbought", "risk_flags": ["high_volatility"]},
            {"symbol": "AAPL.US", "action": "stay", "confidence": 0.5,
             "reason": "neutral"},  # invalid action
            {"symbol": "TSLA.US", "action": "exit", "confidence": 0.4,
             "reason": "trend break"},
        ]
    }
    masked_positions = [
        {"symbol": "NVDA.US", "name": "NVIDIA", "market": "us",
         "weight_bucket": "medium", "pl_direction": "down"},
        {"symbol": "AAPL.US", "name": "Apple", "market": "us",
         "weight_bucket": "small", "pl_direction": "flat"},
        {"symbol": "TSLA.US", "name": "Tesla", "market": "us",
         "weight_bucket": "small", "pl_direction": "down"},
    ]
    advice = helpers.call_knot_holdings_advice(
        masked_positions,
        client_factory=_stub_factory(payload),
    )
    assert set(advice.keys()) == {"NVDA.US", "TSLA.US"}
    assert advice["NVDA.US"]["action"] == "trim"
    assert advice["TSLA.US"]["action"] == "exit"


def test_merge_holdings_advice_combines_inputs():
    masked_positions = [
        {"symbol": "NVDA.US", "name": "NVIDIA", "market": "us",
         "weight_bucket": "medium", "pl_direction": "down"},
    ]
    enriched_rows = [
        {"symbol": "NVDA.US", "raw_score": 0.6, "trend_score": 0.55,
         "flow_score": 0.5, "event_score": 0.4, "quality_score": 0.7,
         "risk_level": "medium"},
    ]
    advice = {
        "NVDA.US": {"action": "trim", "confidence": 0.65,
                    "reason": "overbought", "risk_flags": ["high_volatility"]},
    }
    merged = helpers.merge_holdings_advice(masked_positions, enriched_rows, advice)
    assert len(merged) == 1
    row = merged[0]
    assert row["symbol"] == "NVDA.US"
    assert row["weight_bucket"] == "medium"
    assert row["knot_action"] == "trim"
    assert row["knot_confidence"] == 0.65
    assert row["knot_risk_flags"] == ["high_volatility"]
    assert row["raw_score"] == 0.6


def test_classify_weight_buckets():
    assert helpers.classify_weight(40_000, 1_000_000) == "small"   # 4%
    assert helpers.classify_weight(80_000, 1_000_000) == "medium"  # 8%
    assert helpers.classify_weight(200_000, 1_000_000) == "large"  # 20%
    assert helpers.classify_weight(0, 1_000_000) == "unknown"
    assert helpers.classify_weight(100, 0) == "unknown"
