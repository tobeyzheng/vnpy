from __future__ import annotations

from collections import Counter

from services.decision_engine import MarketDecision

from .models import RiskAlert, RiskEvaluation


class RiskEngine:
    def evaluate(self, decision: MarketDecision) -> RiskEvaluation:
        alerts = []
        if decision.regime == "进攻" and len(decision.signals) >= 5:
            alerts.append(RiskAlert(level="medium", message="高置信度候选较多，注意不要同时分散追高。"))
        if any((signal.confidence or 0) < 65 for signal in decision.signals):
            alerts.append(RiskAlert(level="high", message="存在低置信度标的，不应进入执行层。"))

        symbol_counts = Counter(s.symbol.split('.')[0] for s in decision.signals)
        if any(count > 1 for count in symbol_counts.values()):
            alerts.append(RiskAlert(level="low", message="存在重复/高相关信号，需检查是否过度集中。"))

        if len(decision.signals) > 3:
            alerts.append(RiskAlert(level="medium", message="同一轮建议标的较多，建议限制单日新增仓位数量。"))

        if any("减仓" in s.action or "卖" in s.action for s in decision.signals) and any("买" in s.action or "试错" in s.action for s in decision.signals):
            alerts.append(RiskAlert(level="low", message="同时存在买卖动作，需确认当前市场 regime 是否混乱。"))

        allowed = not any(alert.level == "high" for alert in alerts)
        return RiskEvaluation(allowed=allowed, alerts=alerts)
