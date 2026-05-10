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


def test_load_all_merges_dynamic_with_static_by_symbol(tmp_path):
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

    assert [row["symbol"] for row in rows] == ["NVDA.US", "09988.HK", "00700.HK"]


def test_dynamic_row_overrides_static_row_for_same_symbol(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.json").write_text(
        json.dumps({
            "items": [
                {"symbol": "700.HK", "market": "hong_kong", "name": "Tencent Dynamic", "raw_score": 0.91}
            ]
        }),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.json").write_text(
        json.dumps([
            {"symbol": "00700.HK", "market": "hong_kong", "name": "Tencent Static", "raw_score": 0.72}
        ]),
        encoding="utf-8",
    )

    rows = UnifiedCandidateProvider(tmp_path).load("hong_kong")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "00700.HK"
    assert rows[0]["name"] == "Tencent Dynamic"
    assert rows[0]["candidate_source"] == "dynamic_override"
    assert rows[0]["merged_from_sources"] == ["dynamic", "static"]
