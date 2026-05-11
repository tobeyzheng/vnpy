from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from services.strategy.candidate_preparation import CandidateInputPreparationService
from services.strategy.candidate_seed import KnotCandidateSeedResult, KnotCandidateSeedService


class _StubSeedService(KnotCandidateSeedService):
    def __init__(self, results_by_market):
        super().__init__(llm_client_factory=lambda: None)
        self._results_by_market = results_by_market
        self.calls = []

    def generate_candidates(self, *, market, target_count, runtime_mode="auto"):
        self.calls.append((market, target_count, runtime_mode))
        result = self._results_by_market.get(market)
        if result is None:
            return KnotCandidateSeedResult(available=False, fallback_reason="no_stub")
        return result


class _StubUniverseProvider:
    def __init__(self, symbols_by_market):
        self._symbols_by_market = symbols_by_market
        self.calls = []

    def list_symbols(self, market):
        self.calls.append(market)
        return list(self._symbols_by_market.get(market, []))


def _seed_row(symbol, market, name, raw_score, rationale):
    return {
        "symbol": symbol,
        "market": market,
        "candidate_type": "dynamic",
        "name": name,
        "raw_score": raw_score,
        "rationale": rationale,
        "risk": "controlled",
        "action_hint": "watch",
        "theme_bucket": "ai_compute",
        "risk_level": "medium",
        "confidence_source": "knot_agent_dynamic",
        "signals": [
            {
                "symbol": symbol,
                "market": market,
                "source": "knot_agent_dynamic",
                "category": "research",
                "score": raw_score,
                "summary": rationale,
            }
        ],
    }


def _seed_existing_per_market_pools(repo_root: Path) -> tuple[Path, Path]:
    """Seed per-market dynamic pools so we can verify isolation after a refresh."""

    runs_root = repo_root / "state" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    us_path = runs_root / "candidate_inputs.dynamic.us.json"
    hk_path = runs_root / "candidate_inputs.dynamic.hong_kong.json"
    us_path.write_text(
        json.dumps(
            {
                "schema_version": "candidate_inputs_v3",
                "items": [
                    {
                        "symbol": "OLD.US",
                        "market": "us",
                        "name": "Legacy US",
                        "candidate_type": "dynamic",
                        "raw_score": 0.4,
                        "rationale": "stale us row",
                        "risk": "stale",
                        "action_hint": "n/a",
                        "signals": [],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    hk_path.write_text(
        json.dumps(
            {
                "schema_version": "candidate_inputs_v3",
                "items": [
                    {
                        "symbol": "00700.HK",
                        "market": "hong_kong",
                        "name": "Tencent (legacy)",
                        "candidate_type": "dynamic",
                        "raw_score": 0.3,
                        "rationale": "stale hk row",
                        "risk": "stale",
                        "action_hint": "n/a",
                        "signals": [],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return us_path, hk_path


@pytest.fixture
def repo_root(tmp_path):
    (tmp_path / "state" / "runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_prepare_market_knot_first_us_only_does_not_touch_hk_file(repo_root):
    us_path, hk_path = _seed_existing_per_market_pools(repo_root)
    hk_original = hk_path.read_text()
    seed_rows = [
        _seed_row("NVDA.US", "us", "NVIDIA", 0.86, "AI tailwind"),
        _seed_row("AAPL.US", "us", "Apple", 0.78, "AI PC cycle"),
    ]
    seed_service = _StubSeedService({"us": KnotCandidateSeedResult(rows=seed_rows, available=True)})
    universe = _StubUniverseProvider({})
    service = CandidateInputPreparationService(
        repo_root,
        seed_service=seed_service,
        universe_provider=universe,
    )

    report = service.prepare_market(
        market="us",
        strategy="knot_first",
        top_n=2,
        knot_target_count=5,
        knot_runtime="auto",
        include_market_data=False,
    )

    assert report["written"] is True
    assert report["target_markets"] == ["us"]
    market_run = report["market_runs"][0]
    assert market_run["strategy_used"] == "knot_first"
    assert market_run["knot_status"] == "ok"
    assert market_run["kept_count"] == 2

    us_payload = json.loads(us_path.read_text(encoding="utf-8"))
    us_symbols = {row["symbol"] for row in us_payload["items"]}
    assert us_symbols == {"NVDA.US", "AAPL.US"}

    # HK file must remain untouched – this is the whole point of file-level isolation.
    assert hk_path.read_text() == hk_original
    assert universe.calls == []


def test_prepare_market_falls_back_to_score_first_when_knot_unavailable(repo_root):
    us_path, _hk_path = _seed_existing_per_market_pools(repo_root)
    seed_service = _StubSeedService({
        "us": KnotCandidateSeedResult(available=False, fallback_reason="knot_call_failed:OSError"),
    })
    universe = _StubUniverseProvider(
        {
            "us": [
                {"symbol": "BBB.US", "market": "us", "name": "Beta"},
                {"symbol": "CCC.US", "market": "us", "name": "Charlie"},
                {"symbol": "DDD.US", "market": "us", "name": "Delta"},
            ]
        }
    )
    service = CandidateInputPreparationService(
        repo_root,
        seed_service=seed_service,
        universe_provider=universe,
    )

    report = service.prepare_market(
        market="us",
        strategy="knot_first",
        top_n=2,
        knot_target_count=5,
        knot_runtime="off",
        include_market_data=False,
    )

    market_run = report["market_runs"][0]
    assert market_run["strategy_used"] == "score_first"
    assert market_run["knot_status"].startswith("unavailable_fallback_to_score_first")
    assert market_run["universe_size"] == 3
    assert market_run["kept_count"] == 2
    assert universe.calls == ["us"]


def test_prepare_market_dry_run_does_not_write_pool(repo_root):
    us_path, _hk_path = _seed_existing_per_market_pools(repo_root)
    seed_service = _StubSeedService({
        "us": KnotCandidateSeedResult(
            rows=[_seed_row("NVDA.US", "us", "NVIDIA", 0.9, "AI surge")],
            available=True,
        )
    })
    universe = _StubUniverseProvider({})
    service = CandidateInputPreparationService(
        repo_root,
        seed_service=seed_service,
        universe_provider=universe,
    )
    original_content = us_path.read_text()

    report = service.prepare_market(
        market="us",
        strategy="knot_first",
        top_n=1,
        knot_target_count=3,
        knot_runtime="off",
        include_market_data=False,
        dry_run=True,
    )

    assert report["written"] is False
    assert us_path.read_text() == original_content


def test_prepare_market_all_iterates_each_market_independently(repo_root):
    us_path, hk_path = _seed_existing_per_market_pools(repo_root)
    seed_service = _StubSeedService(
        {
            "us": KnotCandidateSeedResult(
                rows=[_seed_row("NVDA.US", "us", "NVIDIA", 0.9, "AI surge")],
                available=True,
            ),
            "hong_kong": KnotCandidateSeedResult(
                rows=[_seed_row("00700.HK", "hong_kong", "Tencent", 0.8, "platform recovery")],
                available=True,
            ),
        }
    )
    universe = _StubUniverseProvider({})
    service = CandidateInputPreparationService(
        repo_root,
        seed_service=seed_service,
        universe_provider=universe,
    )

    report = service.prepare_market(
        market="all",
        strategy="knot_first",
        top_n=1,
        knot_target_count=3,
        knot_runtime="off",
        include_market_data=False,
    )

    assert report["target_markets"] == ["hong_kong", "us"]
    assert {(call[0]) for call in seed_service.calls} == {"hong_kong", "us"}

    us_payload = json.loads(us_path.read_text(encoding="utf-8"))
    hk_payload = json.loads(hk_path.read_text(encoding="utf-8"))
    assert {row["symbol"] for row in us_payload["items"]} == {"NVDA.US"}
    assert {row["symbol"] for row in hk_payload["items"]} == {"00700.HK"}


def test_prepare_market_score_first_explicit_uses_universe_only(repo_root):
    _us_path, hk_path = _seed_existing_per_market_pools(repo_root)
    seed_service = _StubSeedService({})  # should not be called for explicit score_first
    universe = _StubUniverseProvider(
        {
            "hong_kong": [
                {"symbol": "00700.HK", "market": "hong_kong", "name": "Tencent"},
                {"symbol": "09988.HK", "market": "hong_kong", "name": "Alibaba"},
            ]
        }
    )
    service = CandidateInputPreparationService(
        repo_root,
        seed_service=seed_service,
        universe_provider=universe,
    )

    report = service.prepare_market(
        market="hong_kong",
        strategy="score_first",
        top_n=2,
        knot_target_count=5,
        knot_runtime="off",
        include_market_data=False,
    )

    market_run = report["market_runs"][0]
    assert seed_service.calls == []
    assert market_run["strategy_used"] == "score_first"
    assert market_run["universe_size"] == 2
    assert market_run["kept_count"] == 2
    hk_payload = json.loads(hk_path.read_text(encoding="utf-8"))
    assert {row["symbol"] for row in hk_payload["items"]} == {"00700.HK", "09988.HK"}
