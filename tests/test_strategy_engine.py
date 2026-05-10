from __future__ import annotations

from services.strategy.engine import StrategyEngine


def test_strategy_engine_candidate_output_contains_standard_signal():
    engine = StrategyEngine()
    result = engine.evaluate_candidate(
        candidate={"symbol": "NVDA.US", "market": "us", "raw_score": 0.85, "signals": [{"score": 0.8}]},
        quote={"change_pct": 1.2, "turnover": 2_000_000_000},
        flow_divisor=5_000_000_000,
        has_event_catalyst=True,
    )

    assert result.signal.strategy_id == "raw_score_timing_v1"
    assert result.signal.symbol == "NVDA.US"
    assert 0.0 <= result.raw_score <= 1.0
    assert "candidate_scoring" in result.metadata
    assert result.metadata["candidate_scoring"]["mode"] == "dynamic"
    assert result.signal.metadata["candidate_scoring"]["model_id"] == "dynamic_hybrid_candidate_v2"
