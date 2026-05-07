from __future__ import annotations

from services.decision_engine import MarketDecision

from .models import RiskAlert, RiskEvaluation


class RiskEngine:
    def evaluate(self, decision: MarketDecision) -> RiskEvaluation:
        alerts = []
        if decision.regime == "进攻" and len(decision.signals) >= 5:
            alerts.append(RiskAlert(level="medium", message="高置信度候选较多，注意不要同时分散追高。"))
        if any((signal.confidence or 0) < 65 for signal in decision.signals):
            alerts.append(RiskAlert(level="high", message="存在低置信度标的，不应进入执行层。"))
        allowed = not any(alert.level == "high" for alert in alerts)
        return RiskEvaluation(allowed=allowed, alerts=alerts)
