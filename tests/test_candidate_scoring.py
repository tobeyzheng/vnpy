from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from services.futu_account.quote_client import FutuQuoteClient
from services.strategy.candidate_enrichment import CandidateKnotEnrichmentService, CandidateMarketDataService
from services.strategy.candidate_generation import HybridCandidateGenerationService
from services.strategy.candidate_scoring import CandidateScoringService
from vnpy_llm.base import beijing_now_isoformat


class StubQuoteClient:
    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def get_snapshot(self, codes: list[str]) -> list[dict[str, object]]:
        assert codes == ["US.NVDA"]
        return [
            {
                "code": "US.NVDA",
                "last_price": 911.5,
                "change_pct": 4.2,
                "turnover": 8_500_000_000,
                "turnover_rate": 1.4,
                "total_market_val": 2_800_000_000_000,
            }
        ]


class StubKnotRuntime:
    def evaluate_symbol(self, *, symbol: str, market: str, payload: dict[str, object], task_type: str = "evaluation") -> dict[str, object]:
        assert symbol == "NVDA.US"
        assert market == "us"
        return {
            "symbol": symbol,
            "market": market,
            "task_type": task_type,
            "parsed": {
                "strategy": "trend_following",
                "confidence": 0.83,
                "reason": "Knot confirms strong trend with liquidity support.",
                "risk_flags": ["watch_gap_risk"],
            },
            "runtime": "stub-knot-local",
            "fallback_used": False,
            "schema_validated": True,
        }


def test_candidate_scoring_service_scores_dynamic_candidate_with_consensus_fields():
    service = CandidateScoringService()

    row = service.enrich_row(
        {
            "symbol": "NVDA.US",
            "market": "us",
            "name": "NVIDIA",
            "rationale": "AI demand remains strong.",
            "risk": "valuation sensitivity",
            "action_hint": "buy on pullback",
            "signals": [
                {"source": "classic_trend", "category": "technical", "score": 0.91, "summary": "strong uptrend"},
                {"source": "knot_agent", "category": "research", "score": 0.76, "summary": "AI capex narrative still intact"},
            ],
            "has_event_catalyst": True,
            "avg_daily_turnover": 8_000_000_000,
            "market_cap": 2_500_000_000_000,
            "event_freshness_days": 2,
        },
        mode="dynamic",
        generated_at="2026-05-10T10:00:00+00:00",
        as_of_date="2026-05-10",
    )

    assert row["candidate_type"] == "dynamic"
    assert row["selection_policy"] == "dynamic_hybrid_market_complete_v2"
    assert row["raw_score"] > 0.7
    assert row["consensus_score"] > 0.6
    assert "strategy_tags" in row and row["strategy_tags"]
    assert "source_breakdown" in row and "classic" in row["source_breakdown"]
    assert row["signals"][-1]["category"] == "candidate_score"
    assert row["scoring"]["model_id"] == "dynamic_hybrid_candidate_v2"


def test_candidate_market_data_service_attaches_snapshot_aliases_and_signal():
    service = CandidateMarketDataService(quote_client=StubQuoteClient())
    rows = [{"symbol": "NVDA.US", "market": "us", "name": "NVIDIA", "signals": []}]

    meta = service.enrich_rows(rows)

    assert meta["status"] == "ok"
    assert meta["matched_rows"] == 1
    assert rows[0]["quote"]["change_pct"] == 4.2
    assert rows[0]["change_pct"] == 4.2
    assert rows[0]["market_cap"] == 2_800_000_000_000
    assert rows[0]["signals"][-1]["category"] == "market_data"


def test_futu_quote_client_normalize_row_replaces_non_finite_values_with_none():
    row = FutuQuoteClient._normalize_row(
        {
            "code": "HK.00700",
            "last_price": 110.0,
            "prev_close_price": 100.0,
            "change_rate": math.nan,
            "stock_owner": math.nan,
            "future_position": math.inf,
            "future_main_contract": -math.inf,
            "turnover": 1_000.0,
        }
    )

    assert row["change_pct"] == 10.0
    assert row["stock_owner"] is None
    assert row["future_position"] is None
    assert row["future_main_contract"] is None
    assert row["turnover"] == 1_000.0


def test_candidate_knot_enrichment_service_attaches_strategy_selection_and_scores():
    service = CandidateKnotEnrichmentService(Path("/projects/vnpy"), local_runtime=StubKnotRuntime())
    rows = [
        {
            "symbol": "NVDA.US",
            "market": "us",
            "name": "NVIDIA",
            "raw_score": 0.79,
            "rationale": "AI demand remains strong.",
            "risk": "valuation sensitivity",
            "action_hint": "buy on pullback",
            "signals": [{"source": "classic_trend", "category": "technical", "score": 0.9, "summary": "trend intact"}],
        }
    ]

    meta = service.enrich_rows(rows, mode="dynamic", runtime_mode="local")

    assert meta["status"] == "ok"
    assert meta["evaluated_rows"] == 1
    assert rows[0]["strategy_selection"]["strategy_id"] == "trend_following"
    assert rows[0]["knot_overlay_score"] > 0.7
    assert rows[0]["confidence_source"] == "stub-knot-local"
    assert "watch_gap_risk" in rows[0]["risk_flags"]
    assert rows[0]["signals"][-1]["source"] == "stub-knot-local"


def test_hybrid_candidate_generation_service_evaluates_single_candidate_with_optional_enrichment():
    service = HybridCandidateGenerationService(
        Path("/projects/vnpy"),
        market_data_service=CandidateMarketDataService(quote_client=StubQuoteClient()),
        knot_service=CandidateKnotEnrichmentService(Path("/projects/vnpy"), local_runtime=StubKnotRuntime()),
    )

    row = service.evaluate_single_candidate(
        {
            "symbol": "NVDA.US",
            "market": "us",
            "name": "NVIDIA",
            "signals": [{"source": "classic_quality", "category": "quality", "score": 0.84, "summary": "cash flow leader"}],
            "rationale": "AI demand remains strong.",
            "risk": "valuation sensitivity",
            "action_hint": "buy on pullback",
        },
        mode="dynamic",
        generated_at="2026-05-10T10:00:00+00:00",
        as_of_date="2026-05-10",
        include_market_data=True,
        knot_runtime="local",
    )

    assert row["candidate_type"] == "dynamic"
    assert row["quote"]["change_pct"] == 4.2
    assert row["strategy_selection"]["strategy_id"] == "trend_following"
    assert row["knot_evaluation"]["runtime"] == "stub-knot-local"
    assert row["raw_score"] > 0.6
    assert row["data_completeness"] >= 0.85


def test_hybrid_candidate_generation_service_evaluates_single_static_candidate():
    service = HybridCandidateGenerationService(Path("/projects/vnpy"))

    row = service.evaluate_single_candidate(
        {
            "symbol": "0700.HK",
            "market": "hong_kong",
            "name": "Tencent",
            "signals": [{"source": "classic_quality", "category": "quality", "score": 0.84, "summary": "cash flow leader"}],
            "market_cap": 3_000_000_000_000,
            "avg_daily_turnover": 4_000_000_000,
            "research_note": "Platform cash flow and buyback support remain intact.",
        },
        mode="static",
        generated_at="2026-05-10T10:00:00+00:00",
        as_of_date="2026-05-10",
    )

    assert row["candidate_type"] == "static"
    assert row["selection_policy"] == "stable_baseline_hybrid_v2"
    assert row["raw_score"] > 0.55
    assert row["risk_level"] in {"low", "medium", "high"}
    assert row["explanation_ready"] is True
    assert row["scoring"]["mode"] == "static"


def test_candidate_knot_enrichment_service_defaults_to_auto_runtime():
    service = CandidateKnotEnrichmentService(Path("/projects/vnpy"), local_runtime=StubKnotRuntime(), remote_runtime=StubKnotRuntime())
    rows = [
        {
            "symbol": "NVDA.US",
            "market": "us",
            "name": "NVIDIA",
            "raw_score": 0.79,
            "rationale": "AI demand remains strong.",
            "risk": "valuation sensitivity",
            "action_hint": "buy on pullback",
            "signals": [{"source": "classic_trend", "category": "technical", "score": 0.9, "summary": "trend intact"}],
        }
    ]

    meta = service.enrich_rows(rows, mode="dynamic")

    assert meta["runtime_mode"] == "auto"
    assert meta["status"] == "ok"
    assert rows[0]["strategy_selection"]["strategy_id"] == "trend_following"


def test_beijing_now_isoformat_returns_shanghai_offset():
    ts = beijing_now_isoformat()

    assert ts.endswith("+08:00")
    assert datetime.fromisoformat(ts).utcoffset().total_seconds() == 8 * 3600
