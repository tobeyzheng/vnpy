from __future__ import annotations

import json
from pathlib import Path

from execution.futu_bridge import FutuPaperBridge
from execution.paper_bridge import PaperTradeBridge
from execution.vnpy_bridge import VnpySignalBridge
from services.approval_gate import ApprovalGate
from services.candidate_engine.adapters import candidate_from_input
from services.candidate_engine.providers import CompositeCandidateProvider, DemoCandidateProvider
from services.decision_engine import DecisionEngine
from services.futu_account import FutuAccountProvider, FutuSdkClient
from services.futu_opend import OpenDClient
from services.risk_engine import RiskEngine


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    market = "us"

    provider = CompositeCandidateProvider([DemoCandidateProvider()])
    inputs = provider.get_candidate_inputs(market)
    candidates = [candidate_from_input(i) for i in inputs]
    for c in candidates:
        c.confidence = c.confidence or 75

    decision = DecisionEngine().decide(market, candidates)
    approval = ApprovalGate(mode="paper").evaluate(decision)
    risk = RiskEngine().evaluate(decision)
    intents = PaperTradeBridge().build_intents(decision) if approval.allowed and risk.allowed else []
    vnpy_drafts = VnpySignalBridge().build_drafts(intents)
    futu_drafts = FutuPaperBridge().build_drafts(intents)

    sdk = FutuSdkClient()
    sdk_avail = sdk.availability()
    opend = OpenDClient().probe()
    account = FutuAccountProvider().get_summary()

    result = {
        "python_runtime": True,
        "opend_reachable": opend.reachable,
        "opend_message": opend.message,
        "sdk_available": sdk_avail.available,
        "sdk_message": sdk_avail.message,
        "candidate_count": len(candidates),
        "decision_count": len(decision.signals),
        "approval_allowed": approval.allowed,
        "approval_reason": approval.reason,
        "risk_allowed": risk.allowed,
        "risk_alerts": [f"{a.level}:{a.message}" for a in risk.alerts],
        "paper_intent_count": len(intents),
        "vnpy_draft_count": len(vnpy_drafts),
        "futu_draft_count": len(futu_drafts),
        "readonly_account_status": account.status,
        "readonly_account_message": account.message,
        "trade_submit_live": False,
        "trade_flow_status": "draft_only" if futu_drafts else "blocked",
    }

    out = repo_root / "state" / "runs" / "trade_flow_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
