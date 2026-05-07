from __future__ import annotations

from typing import List

from services.decision_engine import MarketDecision

from .models import PaperTradeIntent


class PaperTradeBridge:
    def build_intents(self, decision: MarketDecision) -> List[PaperTradeIntent]:
        intents: List[PaperTradeIntent] = []
        for signal in decision.signals:
            side = self._map_action(signal.action)
            intents.append(
                PaperTradeIntent(
                    symbol=signal.symbol,
                    market=signal.market,
                    side=side,
                    reason=signal.reason,
                    confidence=signal.confidence,
                    target_position_pct=self._target_position(signal.confidence or 0),
                    tags=[decision.regime, "paper"],
                )
            )
        return intents

    def _map_action(self, action: str) -> str:
        if any(k in action for k in ["买", "试错", "加仓", "回调再买"]):
            return "BUY"
        if any(k in action for k in ["减仓", "卖", "止盈", "止损"]):
            return "SELL"
        return "WATCH"

    def _target_position(self, confidence: int) -> float:
        if confidence >= 85:
            return 0.10
        if confidence >= 75:
            return 0.07
        if confidence >= 65:
            return 0.04
        return 0.0
