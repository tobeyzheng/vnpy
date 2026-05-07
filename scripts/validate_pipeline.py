from __future__ import annotations

import json
from pathlib import Path

from execution.futu_bridge import FutuDraftStore, FutuPaperBridge
from execution.paper_bridge import PaperIntentStore, PaperTradeBridge
from execution.vnpy_bridge import VnpySignalBridge
from services.candidate_engine import CandidateRanker
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, DemoCandidateProvider
from services.decision_engine import DecisionEngine
from services.futu_account import FutuAccountProvider
from services.futu_opend import OpenDClient
from services.scoring_engine import MarketScorer
from services.common.config_loader import load_yaml


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    market = "us"
    scoring_cfg = load_yaml(repo_root / "configs/scoring/score_model.yaml")
    weights = scoring_cfg["markets"][market]["weights"]
    scorer = MarketScorer(weights)

    provider = CompositeCandidateProvider([DemoCandidateProvider()])
    inputs = provider.get_candidate_inputs(market)
    candidates = [candidate_from_input(item) for item in inputs]
    for candidate in candidates:
        candidate.confidence = scorer.to_confidence(0.75)

    top_candidates = CandidateRanker().top_n(candidates, n=5)
    decision = DecisionEngine().decide(market, top_candidates)
    intents = PaperTradeBridge().build_intents(decision)
    vnpy_drafts = VnpySignalBridge().build_drafts(intents)
    futu_drafts = FutuPaperBridge().build_drafts(intents)

    runs = repo_root / "state" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    paper_path = PaperIntentStore(runs).save(market, intents)
    futu_path = FutuDraftStore(runs).save(market, futu_drafts)

    opend = OpenDClient().probe()
    account = FutuAccountProvider().get_summary()
    quote = FutuAccountProvider().get_watchlist_snapshot([d.code for d in futu_drafts[:3]])

    result = {
        "market": market,
        "candidate_count": len(candidates),
        "top_count": len(top_candidates),
        "decision_count": len(decision.signals),
        "paper_intent_count": len(intents),
        "vnpy_draft_count": len(vnpy_drafts),
        "futu_draft_count": len(futu_drafts),
        "paper_path": str(paper_path),
        "futu_path": str(futu_path),
        "opend_reachable": opend.reachable,
        "opend_message": opend.message,
        "account_status": account.status,
        "account_message": account.message,
        "quote_status": quote.get("status"),
        "quote_message": quote.get("message"),
        "quote_items": quote.get("items", []),
    }
    out_path = runs / "validation_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
