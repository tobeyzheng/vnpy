from __future__ import annotations

from services.decision_engine import MarketDecision

from .models import ApprovalDecision


class ApprovalGate:
    def __init__(self, mode: str = "research"):
        self.mode = mode

    def evaluate(self, decision: MarketDecision) -> ApprovalDecision:
        if self.mode == "research":
            return ApprovalDecision(self.mode, False, "research mode: never submit orders", ["report_only"])
        if self.mode == "paper":
            return ApprovalDecision(self.mode, True, "paper mode: allow simulated execution only", ["paper_only"])
        if self.mode == "semi_auto":
            return ApprovalDecision(self.mode, False, "semi-auto mode: manual approval required", ["manual_approval"])
        return ApprovalDecision(self.mode, False, "blocked mode: execution disabled", ["blocked"])
