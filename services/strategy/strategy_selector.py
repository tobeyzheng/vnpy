from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services.strategy.timing import TimingDecision

ALLOWED_STRATEGIES = {
    "trend_following",
    "breakout_momentum",
    "pullback_buy",
    "watch_only",
    "block_trade",
}


@dataclass(frozen=True)
class StrategySelection:
    strategy_id: str
    allow_trade: bool
    confidence: float
    reason: str
    source: str = "rules"
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategySelector:
    """Select a strategy from structured factors.

    This selector is intentionally deterministic for production pipelines and
    historical backtests. External LLM/Knot outputs should be converted into
    structured fields before reaching this layer, rather than allowing the LLM
    to size positions or submit orders directly.
    """

    def select(self, *, features: dict[str, Any], timing: TimingDecision | None = None) -> StrategySelection:
        risk_score = self._float(features.get("risk_score"), 0.5)
        capital_score = self._float(features.get("capital_score"), 0.5)
        trend_score = self._float(features.get("trend_score"), 0.5)
        raw_score = self._float(features.get("raw_score"), 0.5)
        rsi = self._float(features.get("rsi"), 50.0)
        near_resistance = bool(features.get("near_resistance", False))
        has_event_catalyst = bool(features.get("has_event_catalyst", False))
        moving_average_bullish = bool(features.get("moving_average_bullish", False))
        missing_fields = int(features.get("missing_fields", 0) or 0)

        if risk_score > 0.7:
            return StrategySelection(
                strategy_id="block_trade",
                allow_trade=False,
                confidence=0.82,
                reason="风险评分过高，阻断交易",
                risk_flags=["risk_score_high"],
            )
        if capital_score < 0.3:
            return StrategySelection(
                strategy_id="block_trade",
                allow_trade=False,
                confidence=0.78,
                reason="资金承接偏弱，阻断交易",
                risk_flags=["capital_score_low"],
            )
        if missing_fields >= 3:
            return StrategySelection(
                strategy_id="watch_only",
                allow_trade=False,
                confidence=0.68,
                reason="关键字段不足，观察等待",
                risk_flags=["insufficient_features"],
            )
        if rsi > 80 and trend_score >= 0.8:
            return StrategySelection(
                strategy_id="pullback_buy",
                allow_trade=True,
                confidence=0.72,
                reason="趋势强但短线过热，只允许回踩买入策略",
                risk_flags=["overheated"],
            )
        if rsi > 80:
            return StrategySelection(
                strategy_id="watch_only",
                allow_trade=False,
                confidence=0.7,
                reason="短线过热且趋势确认不足，观察等待",
                risk_flags=["overheated"],
            )
        if near_resistance and has_event_catalyst:
            return StrategySelection(
                strategy_id="breakout_momentum",
                allow_trade=True,
                confidence=0.76,
                reason="临近关键位且存在事件催化，使用突破动量策略",
            )
        if moving_average_bullish and trend_score >= 0.68 and rsi < 70 and raw_score >= 0.55:
            return StrategySelection(
                strategy_id="trend_following",
                allow_trade=True,
                confidence=0.78,
                reason="趋势、均线和综合评分健康，使用趋势跟踪策略",
            )
        if timing and timing.action in ALLOWED_STRATEGIES:
            return StrategySelection(
                strategy_id=timing.action,
                allow_trade=timing.action not in {"watch_only", "block_trade"},
                confidence=timing.confidence,
                reason=timing.reason,
                source="timing_fallback",
                metadata={"invalidator": timing.invalidator},
            )
        return StrategySelection(
            strategy_id="watch_only",
            allow_trade=False,
            confidence=0.62,
            reason="未满足策略入场条件，观察等待",
        )

    def _float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
