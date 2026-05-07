from __future__ import annotations

from typing import Iterable, List

from services.candidate_engine.models import Candidate

from .models import DecisionSignal, MarketDecision


class DecisionEngine:
    def decide(self, market: str, candidates: Iterable[Candidate]) -> MarketDecision:
        selected: List[DecisionSignal] = []
        for candidate in candidates:
            action = candidate.action or "继续观察"
            reason = candidate.rationale or "等待更清晰信号"
            selected.append(
                DecisionSignal(
                    symbol=candidate.symbol,
                    name=candidate.name,
                    market=candidate.market,
                    action=action,
                    reason=reason,
                    confidence=candidate.confidence,
                    source_candidate=candidate,
                    tags=["decision"],
                )
            )
        regime = self._infer_regime(selected)
        summary = self._build_summary(regime, selected)
        return MarketDecision(market=market, regime=regime, signals=selected, summary=summary)

    def _infer_regime(self, signals: List[DecisionSignal]) -> str:
        if not signals:
            return "防守"
        avg_conf = sum((s.confidence or 0) for s in signals) / len(signals)
        if avg_conf >= 80:
            return "进攻"
        if avg_conf >= 65:
            return "均衡"
        return "防守"

    def _build_summary(self, regime: str, signals: List[DecisionSignal]) -> List[str]:
        if regime == "进攻":
            return ["高置信度候选较多，可优先跟踪主线龙头。"]
        if regime == "均衡":
            return ["当前更适合做精选跟踪，不适合大面积追高。"]
        return ["高质量信号有限，优先控制节奏和仓位。"]
