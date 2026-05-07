from __future__ import annotations

import argparse
from pathlib import Path

from execution.futu_bridge import FutuDraftStore, FutuPaperBridge
from execution.paper_bridge import PaperIntentStore, PaperTradeBridge
from execution.vnpy_bridge import VnpySignalBridge
from services.candidate_engine import CandidateRanker, CandidateStateStore
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, DemoCandidateProvider
from services.decision_engine import DecisionEngine
from services.futu_account import FutuAccountProvider
from services.futu_opend import OpenDClient
from services.reporting import ActionLine, MarketEnvironment, PremarketReport, TextReportRenderer
from services.watchlist_engine import WatchlistItem, WatchlistManager, WatchlistStateStore
from services.watchlist_engine.configs import load_fixed_watchlist
from services.common.config_loader import load_yaml
from services.scoring_engine import MarketScorer

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


def format_account_block(summary) -> list[str]:
    lines = []
    lines.append(f"账户状态：{summary.status} | env={summary.env} | accounts={summary.account_count}")
    if summary.total_assets is not None:
        lines.append(f"总资产：{summary.total_assets}")
    if summary.cash is not None:
        lines.append(f"现金：{summary.cash}")
    if summary.buying_power is not None:
        lines.append(f"购买力：{summary.buying_power}")
    if summary.positions:
        lines.append("持仓：" + ", ".join(f"{p.code} x {p.qty}" for p in summary.positions[:5]))
    if summary.orders:
        lines.append("订单：" + ", ".join(f"{o.code} {o.side} {o.status}" for o in summary.orders[:5]))
    lines.append(f"备注：{summary.message}")
    return lines


def run_market(market: str) -> Path:
    repo_root = Path(__file__).resolve().parents[1]

    scoring_cfg = load_yaml(repo_root / "configs/scoring/score_model.yaml")
    weights = scoring_cfg["markets"][market]["weights"]
    scorer = MarketScorer(weights)

    provider = CompositeCandidateProvider([DemoCandidateProvider()])
    inputs = provider.get_candidate_inputs(market)
    candidates = [candidate_from_input(item) for item in inputs]
    for candidate in candidates:
        factor_map = build_factor_map(market, candidate.symbol, [e.source for e in candidate.evidence])
        candidate.confidence = scorer.to_confidence(scorer.score(factor_map))

    ranker = CandidateRanker()
    top_candidates = ranker.top_n(candidates, n=5)

    fixed_watchlist = load_fixed_watchlist(repo_root / "configs/watchlists" / MARKET_TO_CONFIG[market])
    watchlist_store = WatchlistStateStore(repo_root / "state/watchlists")
    candidate_store = CandidateStateStore(repo_root / "state/candidates")
    previous_watchlist = watchlist_store.load(market)

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

    diff = WatchlistManager().reconcile(previous_watchlist, current_watch_items.values())
    decision = DecisionEngine().decide(market, top_candidates)
    actions = [ActionLine(symbol=s.symbol, name=s.name, action=s.action, reason=s.reason) for s in decision.signals[:5]]

    intents = PaperTradeBridge().build_intents(decision)
    intent_path = PaperIntentStore(repo_root / "state/runs").save(market, intents)
    vnpy_drafts = VnpySignalBridge().build_drafts(intents)
    futu_drafts = FutuPaperBridge().build_drafts(intents)
    futu_draft_path = FutuDraftStore(repo_root / "state/runs").save(market, futu_drafts)
    opend_probe = OpenDClient().probe()

    account_provider = FutuAccountProvider()
    account_summary = account_provider.get_summary()
    watchlist_codes = [d.code for d in futu_drafts[:5]]
    quote_summary = account_provider.get_watchlist_snapshot(watchlist_codes)

    summary = []
    summary.extend(format_account_block(account_summary))
    summary.extend(decision.summary)
    summary.append(f"已生成 {len(intents)} 条 paper-trade intents：{intent_path.name}")
    summary.append(f"已生成 {len(vnpy_drafts)} 条 vnpy draft requests（仅草案，不提交）。")
    summary.append(f"已生成 {len(futu_drafts)} 条 futu draft requests：{futu_draft_path.name}")
    summary.append(f"Futu OpenD 连通性：{'可连接' if opend_probe.reachable else '未连接'} ({opend_probe.host}:{opend_probe.port})")
    summary.append(f"观察池快照：status={quote_summary.get('status')}, items={len(quote_summary.get('items', []))}, note={quote_summary.get('message')}")

    report = PremarketReport(
        market=MARKET_TO_LABEL[market],
        environment=MarketEnvironment(market=market, bullets=MARKET_ENV[market]),
        watchlist_diff=diff,
        top_candidates=top_candidates,
        actions=actions,
        conclusion=summary,
    )

    final_items = []
    for group in [diff.added, diff.retained, diff.promoted, diff.weakened, diff.pending_removal, diff.removed]:
        final_items.extend(group)
    watchlist_store.save(market, final_items)
    candidate_store.save(market, top_candidates)

    out_path = repo_root / f"output_premarket_{market}.txt"
    out_path.write_text(TextReportRenderer().render_premarket(report), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["us", "hong_kong", "a_share"], required=True)
    args = parser.parse_args()
    print(run_market(args.market))


if __name__ == "__main__":
    main()
