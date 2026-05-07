from __future__ import annotations

import argparse
from pathlib import Path

from services.candidate_engine import CandidateRanker, CandidateStateStore
from services.reporting import ActionLine, MarketEnvironment, PremarketReport, TextReportRenderer
from services.reporting.demo_data import build_demo_candidates
from services.scoring_engine import MarketScorer
from services.watchlist_engine import WatchlistItem, WatchlistManager, WatchlistStateStore
from services.watchlist_engine.configs import load_fixed_watchlist
from services.common.config_loader import load_yaml

MARKET_TO_CONFIG = {
    "us": "us.yaml",
    "hong_kong": "hk.yaml",
    "a_share": "a_share.yaml",
}

MARKET_TO_LABEL = {
    "us": "美股",
    "hong_kong": "港股",
    "a_share": "A股",
}

MARKET_ENV = {
    "us": [
        "纳指主线仍偏AI与半导体，但高位波动会放大",
        "10Y美债与美元若同步走强，将压制高beta追涨",
        "盘前更适合挑龙头和二次确认，不适合无脑扩仓",
    ],
    "hong_kong": [
        "恒科与南下资金方向决定今天港股弹性",
        "若人民币预期走稳，互联网与科技权重更容易占优",
        "盘前更适合做强主线回踩，不适合追情绪化脉冲",
    ],
    "a_share": [
        "A股更看主线板块强弱与情绪结构是否延续",
        "若量能不足，追高容易演化成冲高回落",
        "盘前优先盯龙头与ETF共振，不急于全面开仓",
    ],
}


def build_factor_map(market: str, symbol: str, evidence_sources: list[str]) -> dict[str, float]:
    if market == "us":
        return {
            "trend": 0.85 if "trend" in evidence_sources else 0.6,
            "fundamentals": 0.8 if symbol in {"AVGO.US", "MSFT.US"} else 0.72,
            "macro_regime": 0.7,
            "event_catalyst": 0.82 if symbol == "NVDA.US" else 0.68,
            "risk_indicators": 0.66,
        }
    if market == "hong_kong":
        return {
            "trend": 0.8 if "trend" in evidence_sources else 0.68,
            "fundamentals": 0.76,
            "southbound_flow": 0.72,
            "market_style": 0.7,
            "event_risk": 0.64,
        }
    return {
        "trend": 0.82 if "trend" in evidence_sources else 0.66,
        "fundamentals": 0.74,
        "sector_strength": 0.8,
        "capital_flow": 0.71,
        "event_risk": 0.63,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["us", "hong_kong", "a_share"], required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    market = args.market

    scoring_cfg = load_yaml(repo_root / "configs/scoring/score_model.yaml")
    weights = scoring_cfg["markets"][market]["weights"]
    scorer = MarketScorer(weights)

    candidates = build_demo_candidates(market)
    for candidate in candidates:
        factor_map = build_factor_map(market, candidate.symbol, [e.source for e in candidate.evidence])
        candidate.confidence = scorer.to_confidence(scorer.score(factor_map))

    ranker = CandidateRanker()
    top_candidates = ranker.top_n(candidates, n=5)

    fixed_watchlist = load_fixed_watchlist(repo_root / "configs/watchlists" / MARKET_TO_CONFIG[market])
    previous_store = WatchlistStateStore(repo_root / "state/watchlists")
    candidate_store = CandidateStateStore(repo_root / "state/candidates")
    previous_watchlist = previous_store.load(market)

    current_watch_items = {item.symbol: item for item in fixed_watchlist}
    for candidate in top_candidates:
        current_watch_items[candidate.symbol] = WatchlistItem(
            symbol=candidate.symbol,
            name=candidate.name,
            market=candidate.market,
            sector=candidate.sector,
            confidence=candidate.confidence,
            note=candidate.rationale,
            tags=["candidate"],
        )

    manager = WatchlistManager()
    diff = manager.reconcile(previous_watchlist, current_watch_items.values())

    actions = [
        ActionLine(symbol=item.symbol, name=item.name, action=("继续观察" if idx > 1 else top_candidates[idx].action if idx < len(top_candidates) else "继续观察"), reason=item.note or "保持跟踪")
        for idx, item in enumerate(list(current_watch_items.values())[:3])
    ]

    report = PremarketReport(
        market=MARKET_TO_LABEL[market],
        environment=MarketEnvironment(market=market, bullets=MARKET_ENV[market]),
        watchlist_diff=diff,
        top_candidates=top_candidates,
        actions=actions,
        conclusion=[
            "今天更偏均衡，优先做高辨识度主线，不适合分散追高。",
            "先看强主线是否获得二次确认，再决定是否扩大跟踪范围。",
        ],
    )

    final_items = []
    for group in [diff.added, diff.retained, diff.promoted, diff.weakened, diff.pending_removal, diff.removed]:
        final_items.extend(group)
    previous_store.save(market, final_items)
    candidate_store.save(market, top_candidates)

    out_path = repo_root / f"output_premarket_{market}.txt"
    out_path.write_text(TextReportRenderer().render_premarket(report), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
