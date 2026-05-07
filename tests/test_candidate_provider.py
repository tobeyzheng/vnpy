from __future__ import annotations

import json

from services.strategy.candidate_provider import UnifiedCandidateProvider


def test_load_market_falls_back_to_static_when_dynamic_missing_market(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.json").write_text(
        json.dumps({"items": [{"symbol": "0700.HK", "market": "hong_kong", "name": "Tencent"}]}),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.json").write_text(
        json.dumps([
            {"symbol": "NVDA.US", "market": "us", "name": "NVIDIA"},
            {"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba"},
        ]),
        encoding="utf-8",
    )

    rows = UnifiedCandidateProvider(tmp_path).load("us")

    assert [row["symbol"] for row in rows] == ["NVDA.US"]


def test_load_all_merges_dynamic_with_static_for_uncovered_markets(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.json").write_text(
        json.dumps({"items": [{"symbol": "0700.HK", "market": "hong_kong", "name": "Tencent"}]}),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.json").write_text(
        json.dumps([
            {"symbol": "NVDA.US", "market": "us", "name": "NVIDIA"},
            {"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba"},
        ]),
        encoding="utf-8",
    )

    rows = UnifiedCandidateProvider(tmp_path).load()

    assert [row["symbol"] for row in rows] == ["00700.HK", "NVDA.US"]
