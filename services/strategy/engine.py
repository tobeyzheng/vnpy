from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.common import StrategySignal
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.timing import EntryTimingEngine, ExitTimingEngine, TimingDecision

from .registry import StrategyRegistry


@dataclass
class StrategyEvaluation:
    signal: StrategySignal
    raw_score: float
    entry_timing: TimingDecision
    features: RawScoreFeatures
    task_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class StrategyEngine:
    def __init__(self, registry: StrategyRegistry | None = None, strategy_id: str = "raw_score_timing_v1"):
        self.registry = registry or StrategyRegistry()
        self.definition = self.registry.get(strategy_id)
        self.raw_score_engine = RawScoreEngine()
        self.entry_engine = EntryTimingEngine()
        self.exit_engine = ExitTimingEngine()

    def evaluate_candidate(
        self,
        *,
        candidate: dict[str, Any],
        quote: dict[str, Any],
        flow_divisor: float,
        has_event_catalyst: bool,
    ) -> StrategyEvaluation:
        legacy = float(candidate.get("raw_score", 0.5) or 0.5)
        change_pct = float(quote.get("change_pct") or 0)
        turnover = float(quote.get("turnover") or 0)
        features = RawScoreFeatures(
            trend_score=legacy,
            momentum_score=max(0.0, min(1.0, 0.5 + change_pct / 20.0)),
            flow_score=max(0.0, min(1.0, turnover / flow_divisor)),
            quality_score=self._quality_score(candidate),
            event_score=0.75 if has_event_catalyst else 0.5,
            risk_penalty=0.65 if abs(change_pct) > 6 else 0.35,
            legacy_score=legacy,
        )
        raw_score = self.raw_score_engine.score(features)
        timing = self.entry_engine.decide(
            trend_score=raw_score,
            rsi=50 + min(35, abs(change_pct) * 4),
            has_event_catalyst=has_event_catalyst,
            near_resistance=abs(change_pct) > 4,
            moving_average_bullish=change_pct > 0,
        )
        task_score = self._task_score(raw_score=raw_score, change_pct=change_pct, turnover=turnover, action=timing.action)
        signal = StrategySignal(
            strategy_id=self.definition.strategy_id,
            symbol=str(candidate.get("symbol", "")),
            market=str(candidate.get("market", "")),
            direction="long" if timing.action in self.definition.tradable_actions else "flat",
            score=raw_score,
            confidence=timing.confidence,
            allow_trade=raw_score >= self.definition.min_raw_score and timing.action in self.definition.tradable_actions,
            target_position_pct=timing.suggested_size_pct,
            reason=timing.reason,
            risk_flags=[],
            metadata={"entry_action": timing.action, "invalidator": timing.invalidator},
        )
        return StrategyEvaluation(signal=signal, raw_score=raw_score, entry_timing=timing, features=features, task_score=task_score)

    def evaluate_bar(
        self,
        *,
        symbol: str,
        market: str,
        close_price: float,
        prev_close: float,
        fast: float,
        slow: float,
        volume: float,
        avg_volume: float,
        near_resistance: bool,
        event_score: float = 0.5,
    ) -> StrategyEvaluation:
        momentum = (close_price / prev_close - 1.0) if prev_close else 0.0
        flow_ratio = (volume / avg_volume) if avg_volume else 1.0
        trend_score = 0.8 if fast > slow else 0.35
        features = RawScoreFeatures(
            trend_score=trend_score,
            momentum_score=min(1.0, max(0.0, 0.5 + momentum * 10)),
            flow_score=min(1.0, max(0.0, flow_ratio / 2)),
            quality_score=0.55 if fast > slow else 0.45,
            event_score=event_score,
            risk_penalty=min(1.0, max(0.0, abs(momentum) * 8)),
            legacy_score=None,
        )
        raw_score = self.raw_score_engine.score(features)
        timing = self.entry_engine.decide(
            trend_score=raw_score,
            rsi=55 if fast > slow else 45,
            has_event_catalyst=event_score > 0.6,
            near_resistance=near_resistance,
            moving_average_bullish=fast > slow,
        )
        signal = StrategySignal(
            strategy_id=self.definition.strategy_id,
            symbol=symbol,
            market=market,
            direction="long" if timing.action in self.definition.tradable_actions else "flat",
            score=raw_score,
            confidence=timing.confidence,
            allow_trade=raw_score >= self.definition.min_raw_score and timing.action in self.definition.tradable_actions,
            target_position_pct=timing.suggested_size_pct,
            reason=timing.reason,
            risk_flags=[],
            metadata={"entry_action": timing.action, "invalidator": timing.invalidator, "trend_score": trend_score},
        )
        return StrategyEvaluation(signal=signal, raw_score=raw_score, entry_timing=timing, features=features, task_score=raw_score * 100)

    def evaluate_exit(self, *, pnl_pct: float, rsi: float, trend_score: float, risk_score: float) -> TimingDecision:
        return self.exit_engine.decide(pnl_pct=pnl_pct, rsi=rsi, trend_score=trend_score, risk_score=risk_score)

    def _quality_score(self, candidate: dict[str, Any]) -> float:
        signals = candidate.get("signals") or [{"score": 0.5}]
        return min(1.0, max(float(s.get("score", 0.5) or 0.5) for s in signals))

    def _task_score(self, *, raw_score: float, change_pct: float, turnover: float, action: str) -> float:
        score = raw_score * 100
        score += max(0.0, 10 - abs(change_pct))
        score += min(10.0, turnover / 1e9)
        score += 3.0 if action != "watch_only" else -4.0
        return round(score, 2)
