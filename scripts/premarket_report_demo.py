from __future__ import annotations

from pathlib import Path

from services.candidate_engine import Candidate, CandidateRanker, SignalEvidence, CandidateStateStore
from services.common.config_loader import load_yaml
from services.reporting import ActionLine, MarketEnvironment, PremarketReport, TextReportRenderer
from services.scoring_engine import MarketScorer
from services.watchlist_engine import WatchlistItem, WatchlistManager, WatchlistStateStore


def build_demo_candidates() -> list[Candidate]:
    return [
        Candidate(
            symbol="NVDA.US",
            name="NVIDIA",
            market="us",
            sector="AI/Chip",
            rationale="趋势、事件催化与龙头属性共振，且仍具主线辨识度",
            risk="短期涨幅较大，若利率和风险偏好逆转则波动会放大",
            action="回调再买",
            evidence=[
                SignalEvidence(source="trend", category="technical", summary="uptrend intact", score=0.88),
                SignalEvidence(source="earnings", category="event", summary="AI demand narrative", score=0.84),
            ],
        ),
        Candidate(
            symbol="AVGO.US",
            name="Broadcom",
            market="us",
            sector="Semiconductor",
            rationale="AI链景气度延续，趋势与基本面匹配度较高",
            risk="高位震荡后若成交放大滞涨，可能先进入消化阶段",
            action="可小仓试错",
            evidence=[
                SignalEvidence(source="trend", category="technical", summary="relative strength", score=0.82),
                SignalEvidence(source="fundamental", category="quality", summary="profit quality", score=0.79),
            ],
        ),
        Candidate(
            symbol="MSFT.US",
            name="Microsoft",
            market="us",
            sector="Software/AI",
            rationale="软件与AI应用双重属性，适合做高质量核心观察",
            risk="若大盘切换到防守风格，弹性可能弱于高beta芯片股",
            action="继续观察",
            evidence=[
                SignalEvidence(source="fundamental", category="quality", summary="quality leader", score=0.78),
                SignalEvidence(source="macro", category="style", summary="safer AI exposure", score=0.73),
            ],
        ),
    ]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scoring_cfg = load_yaml(repo_root / "configs/scoring/score_model.yaml")
    us_weights = scoring_cfg["markets"]["us"]["weights"]
    scorer = MarketScorer(us_weights)

    candidates = build_demo_candidates()
    for candidate in candidates:
        factors = {
            "trend": 0.85 if "trend" in [e.source for e in candidate.evidence] else 0.6,
            "fundamentals": 0.8 if candidate.symbol in {"AVGO.US", "MSFT.US"} else 0.72,
            "macro_regime": 0.7,
            "event_catalyst": 0.82 if candidate.symbol == "NVDA.US" else 0.68,
            "risk_indicators": 0.66,
        }
        score = scorer.score(factors)
        candidate.confidence = scorer.to_confidence(score)

    ranker = CandidateRanker()
    top_candidates = ranker.top_n(candidates, n=5)

    watchlist_store = WatchlistStateStore(repo_root / "state/watchlists")
    candidate_store = CandidateStateStore(repo_root / "state/candidates")

    previous_watchlist = watchlist_store.load("us")
    current_watch_items = [
        WatchlistItem(
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            sector=item.sector,
            confidence=item.confidence,
            note=item.rationale,
            tags=["demo"],
        )
        for item in top_candidates
    ]

    manager = WatchlistManager()
    diff = manager.reconcile(previous_watchlist, current_watch_items)

    renderer = TextReportRenderer()
    report = PremarketReport(
        market="美股",
        environment=MarketEnvironment(
            market="us",
            bullets=[
                "纳指主线仍偏AI与半导体，但高位波动会放大",
                "10Y美债与美元若同步走强，将压制高beta追涨",
                "盘前更适合挑龙头和二次确认，不适合无脑扩仓",
            ],
        ),
        watchlist_diff=diff,
        top_candidates=top_candidates,
        actions=[
            ActionLine(symbol="NVDA.US", name="NVIDIA", action="回调再买", reason="主线地位仍强，但不宜盘前情绪化追高"),
            ActionLine(symbol="AVGO.US", name="Broadcom", action="可小仓试错", reason="基本面与趋势匹配度较好，适合试仓跟踪"),
        ],
        conclusion=[
            "今天更偏均衡，不适合对高位强势股一次性重仓。",
            "优先盯龙头分歧后的承接，而不是追盘前最热一跳。",
        ],
    )

    all_watch_items = []
    for group in [diff.added, diff.retained, diff.promoted, diff.weakened, diff.pending_removal, diff.removed]:
        all_watch_items.extend(group)
    watchlist_store.save("us", all_watch_items)
    candidate_store.save("us", top_candidates)

    output_path = repo_root / "output_premarket_demo.txt"
    output_path.write_text(renderer.render_premarket(report), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
