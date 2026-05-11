from __future__ import annotations

import json

from services.strategy.candidate_provider import UnifiedCandidateProvider


def test_load_market_returns_only_target_market_rows(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.hong_kong.json").write_text(
        json.dumps({"items": [{"symbol": "0700.HK", "market": "hong_kong", "name": "Tencent"}]}),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.static.us.json").write_text(
        json.dumps([{"symbol": "NVDA.US", "market": "us", "name": "NVIDIA"}]),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.static.hong_kong.json").write_text(
        json.dumps([{"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba"}]),
        encoding="utf-8",
    )

    rows = UnifiedCandidateProvider(tmp_path).load("us")

    assert [row["symbol"] for row in rows] == ["NVDA.US"]


def test_load_all_merges_per_market_dynamic_and_static_by_symbol(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.hong_kong.json").write_text(
        json.dumps({"items": [{"symbol": "0700.HK", "market": "hong_kong", "name": "Tencent"}]}),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.static.us.json").write_text(
        json.dumps([{"symbol": "NVDA.US", "market": "us", "name": "NVIDIA"}]),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.static.hong_kong.json").write_text(
        json.dumps([{"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba"}]),
        encoding="utf-8",
    )

    rows = UnifiedCandidateProvider(tmp_path).load()

    symbols = {row["symbol"] for row in rows}
    assert symbols == {"NVDA.US", "09988.HK", "00700.HK"}


def test_dynamic_row_overrides_static_row_for_same_symbol(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / "candidate_inputs.dynamic.hong_kong.json").write_text(
        json.dumps({
            "items": [
                {"symbol": "700.HK", "market": "hong_kong", "name": "Tencent Dynamic", "raw_score": 0.91}
            ]
        }),
        encoding="utf-8",
    )
    (runs / "candidate_inputs.static.hong_kong.json").write_text(
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


def test_load_falls_back_to_legacy_combined_files_when_per_market_missing(tmp_path):
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    # Only legacy combined files exist – simulating the pre-split state.
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
    symbols = {row["symbol"] for row in rows}
    assert symbols == {"NVDA.US", "09988.HK", "00700.HK"}


def test_per_market_files_are_isolated(tmp_path):
    """Writing one market's dynamic file must not affect another market's view."""

    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    hk_path = runs / "candidate_inputs.dynamic.hong_kong.json"
    us_path = runs / "candidate_inputs.dynamic.us.json"
    hk_path.write_text(
        json.dumps({"items": [{"symbol": "00700.HK", "market": "hong_kong", "name": "HK1"}]}),
        encoding="utf-8",
    )
    us_path.write_text(
        json.dumps({"items": [{"symbol": "NVDA.US", "market": "us", "name": "US1"}]}),
        encoding="utf-8",
    )

    provider = UnifiedCandidateProvider(tmp_path)

    # Rewrite only HK – US file must remain untouched.
    hk_path.write_text(
        json.dumps({"items": [{"symbol": "09988.HK", "market": "hong_kong", "name": "HK2"}]}),
        encoding="utf-8",
    )
    rows_us = provider.load("us")
    rows_hk = provider.load("hong_kong")
    assert [row["symbol"] for row in rows_us] == ["NVDA.US"]
    assert [row["symbol"] for row in rows_hk] == ["09988.HK"]
