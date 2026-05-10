from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.common import StrategySignal
from services.strategy.candidate_scoring import CandidateScoringService, normalize_score
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.timing import EntryTimingEngine, ExitTimingEngine, TimingDecision

from .registry import StrategyRegistry
from .strategy_selector import StrategySelection, StrategySelector


@dataclass
class StrategyEvaluation:
    signal: StrategySignal
    raw_score: float
    entry_timing: TimingDecision
    features: RawScoreFeatures
    task_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class StrategyEngine:
    def __init__(self, registry: StrategyRegistry | None = None, strategy_id: str = "raw_score_timing_v1", enable_strategy_selection: bool = True):
        self.registry = registry or StrategyRegistry()
        self.definition = self.registry.get(strategy_id)
        self.raw_score_engine = RawScoreEngine()
        self.entry_engine = EntryTimingEngine()
        self.exit_engine = ExitTimingEngine()
        self.enable_strategy_selection = enable_strategy_selection
        self.strategy_selector = StrategySelector()
        self.candidate_scoring = CandidateScoringService()

    def evaluate_candidate(
        self,
        *,
        candidate: dict[str, Any],
        quote: dict[str, Any],
        flow_divisor: float,
        has_event_catalyst: bool,
        external_strategy_selection: StrategySelection | None = None,
    ) -> StrategyEvaluation:
        enriched_candidate = self._enriched_candidate(candidate, quote=quote, has_event_catalyst=has_event_catalyst)
        features = self._features_from_candidate(
            enriched_candidate,
            quote=quote,
            flow_divisor=flow_divisor,
            has_event_catalyst=has_event_catalyst,
        )
        raw_score = self.raw_score_engine.score(features)
        change_pct = float(quote.get("change_pct") or 0)
        turnover = float(quote.get("turnover") or 0)
        timing = self.entry_engine.decide(
            trend_score=raw_score,
            rsi=50 + min(35, abs(change_pct) * 4),
            has_event_catalyst=has_event_catalyst,
            near_resistance=abs(change_pct) > 4,
            moving_average_bullish=change_pct > 0,
        )
        selection = self._select_strategy(
            features={
                "raw_score": raw_score,
                "trend_score": features.trend_score,
                "risk_score": features.risk_penalty,
                "capital_score": features.flow_score,
                "rsi": 50 + min(35, abs(change_pct) * 4),
                "near_resistance": abs(change_pct) > 4,
                "has_event_catalyst": has_event_catalyst,
                "moving_average_bullish": change_pct > 0,
                "missing_fields": self._missing_fields(enriched_candidate, quote),
            },
            timing=timing,
        )
        selection = self._merge_external_selection(selection, external_strategy_selection)
        effective_timing = self._apply_selection(timing, selection)
        task_score = self._task_score(raw_score=raw_score, change_pct=change_pct, turnover=turnover, action=effective_timing.action)
        signal = StrategySignal(
            strategy_id=self.definition.strategy_id,
            symbol=str(enriched_candidate.get("symbol", "")),
            market=str(enriched_candidate.get("market", "")),
            direction="long" if selection.allow_trade and effective_timing.action in self.definition.tradable_actions else "flat",
            score=raw_score,
            confidence=min(effective_timing.confidence, selection.confidence),
            allow_trade=raw_score >= self.definition.min_raw_score and selection.allow_trade and effective_timing.action in self.definition.tradable_actions,
            target_position_pct=effective_timing.suggested_size_pct if selection.allow_trade else 0.0,
            reason=effective_timing.reason,
            risk_flags=sorted(set([*selection.risk_flags, *(enriched_candidate.get("risk_flags") or [])])),
            metadata={
                "entry_action": effective_timing.action,
                "invalidator": effective_timing.invalidator,
                "strategy_selection": selection.to_dict(),
                "candidate_scoring": enriched_candidate.get("scoring", {}),
            },
        )
        return StrategyEvaluation(
            signal=signal,
            raw_score=raw_score,
            entry_timing=effective_timing,
            features=features,
            task_score=task_score,
            metadata={
                "strategy_selection": selection.to_dict(),
                "candidate_scoring": enriched_candidate.get("scoring", {}),
                "candidate": enriched_candidate,
            },
        )

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
        selection = self._select_strategy(
            features={
                "raw_score": raw_score,
                "trend_score": trend_score,
                "risk_score": features.risk_penalty,
                "capital_score": features.flow_score,
                "rsi": 55 if fast > slow else 45,
                "near_resistance": near_resistance,
                "has_event_catalyst": event_score > 0.6,
                "moving_average_bullish": fast > slow,
                "missing_fields": 0,
            },
            timing=timing,
        )
        effective_timing = self._apply_selection(timing, selection)
        signal = StrategySignal(
            strategy_id=self.definition.strategy_id,
            symbol=symbol,
            market=market,
            direction="long" if selection.allow_trade and effective_timing.action in self.definition.tradable_actions else "flat",
            score=raw_score,
            confidence=min(effective_timing.confidence, selection.confidence),
            allow_trade=raw_score >= self.definition.min_raw_score and selection.allow_trade and effective_timing.action in self.definition.tradable_actions,
            target_position_pct=effective_timing.suggested_size_pct if selection.allow_trade else 0.0,
            reason=effective_timing.reason,
            risk_flags=selection.risk_flags,
            metadata={"entry_action": effective_timing.action, "invalidator": effective_timing.invalidator, "trend_score": trend_score, "strategy_selection": selection.to_dict()},
        )
        return StrategyEvaluation(signal=signal, raw_score=raw_score, entry_timing=effective_timing, features=features, task_score=raw_score * 100, metadata={"strategy_selection": selection.to_dict()})

    def evaluate_exit(self, *, pnl_pct: float, rsi: float, trend_score: float, risk_score: float) -> TimingDecision:
        return self.exit_engine.decide(pnl_pct=pnl_pct, rsi=rsi, trend_score=trend_score, risk_score=risk_score)

    def _enriched_candidate(self, candidate: dict[str, Any], *, quote: dict[str, Any], has_event_catalyst: bool) -> dict[str, Any]:
        merged = dict(candidate)
        merged["quote"] = dict(quote or {})
        merged["change_pct"] = merged["quote"].get("change_pct", merged.get("change_pct"))
        merged["turnover"] = merged["quote"].get("turnover", merged.get("turnover"))
        merged["has_event_catalyst"] = bool(has_event_catalyst or merged.get("has_event_catalyst"))
        mode = str(merged.get("candidate_type") or "dynamic")
        return self.candidate_scoring.enrich_row(merged, mode=mode)

    def _features_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        quote: dict[str, Any],
        flow_divisor: float,
        has_event_catalyst: bool,
    ) -> RawScoreFeatures:
        legacy = self._legacy_score(candidate)
        change_pct = float(quote.get("change_pct") or 0)
        turnover = float(quote.get("turnover") or 0)
        flow_from_quote = max(0.0, min(1.0, turnover / flow_divisor)) if flow_divisor else normalize_score(candidate.get("flow_score"), default=0.5)
        event_from_quote = 0.75 if has_event_catalyst else normalize_score(candidate.get("event_score"), default=0.5)
        momentum_from_quote = max(0.0, min(1.0, 0.5 + change_pct / 20.0))
        risk_penalty = max(normalize_score(candidate.get("risk_penalty"), default=0.35), 0.65 if abs(change_pct) > 6 else 0.35)
        return RawScoreFeatures(
            trend_score=normalize_score(candidate.get("trend_score"), default=legacy),
            momentum_score=max(normalize_score(candidate.get("relative_strength_score"), default=0.5), momentum_from_quote),
            flow_score=max(normalize_score(candidate.get("flow_score"), default=0.5), flow_from_quote),
            quality_score=normalize_score(candidate.get("quality_score"), default=self._quality_score(candidate)),
            event_score=max(normalize_score(candidate.get("event_score"), default=0.5), event_from_quote),
            risk_penalty=risk_penalty,
            legacy_score=legacy,
        )

    def _select_strategy(self, *, features: dict[str, Any], timing: TimingDecision) -> StrategySelection:
        if not self.enable_strategy_selection:
            return StrategySelection(
                strategy_id=timing.action,
                allow_trade=timing.action in self.definition.tradable_actions,
                confidence=timing.confidence,
                reason=timing.reason,
                source="timing_only",
            )
        return self.strategy_selector.select(features=features, timing=timing)

    def _merge_external_selection(self, rules: StrategySelection, external: StrategySelection | None) -> StrategySelection:
        if external is None:
            return rules
        metadata = {**external.metadata, "rules_selection": rules.to_dict()}
        if external.strategy_id in {"watch_only", "block_trade"}:
            return StrategySelection(
                strategy_id=external.strategy_id,
                allow_trade=False,
                confidence=min(external.confidence, 0.95),
                reason=external.reason,
                source=external.source,
                risk_flags=sorted(set([*rules.risk_flags, *external.risk_flags, "external_selection_block"])),
                metadata=metadata,
            )
        if rules.allow_trade:
            return StrategySelection(
                strategy_id=external.strategy_id,
                allow_trade=external.allow_trade,
                confidence=min(rules.confidence, external.confidence),
                reason=external.reason,
                source=external.source,
                risk_flags=sorted(set([*rules.risk_flags, *external.risk_flags])),
                metadata=metadata,
            )
        return StrategySelection(
            strategy_id=rules.strategy_id,
            allow_trade=False,
            confidence=rules.confidence,
            reason=rules.reason,
            source=rules.source,
            risk_flags=sorted(set([*rules.risk_flags, "external_selection_not_allowed_by_rules"])),
            metadata={**rules.metadata, "external_selection": external.to_dict()},
        )

    def _apply_selection(self, timing: TimingDecision, selection: StrategySelection) -> TimingDecision:
        if selection.strategy_id in {"watch_only", "block_trade"}:
            return TimingDecision(selection.strategy_id, selection.reason, selection.confidence, timing.invalidator, 0.0)
        suggested_size = timing.suggested_size_pct or self._default_size(selection.strategy_id)
        return TimingDecision(selection.strategy_id, selection.reason, min(timing.confidence, selection.confidence), timing.invalidator, suggested_size)

    def _default_size(self, strategy_id: str) -> float:
        return {
            "trend_following": 0.18,
            "breakout_momentum": 0.12,
            "pullback_buy": 0.10,
        }.get(strategy_id, 0.0)

    def _missing_fields(self, candidate: dict[str, Any], quote: dict[str, Any]) -> int:
        required = [candidate.get("symbol"), candidate.get("market"), quote.get("change_pct"), quote.get("turnover"), quote.get("price", quote.get("last_price"))]
        return sum(1 for value in required if value in {None, ""})

    def _quality_score(self, candidate: dict[str, Any]) -> float:
        signals = candidate.get("signals") or [{"score": 0.5}]
        return min(1.0, max(float(s.get("score", 0.5) or 0.5) for s in signals))

    def _legacy_score(self, candidate: dict[str, Any]) -> float:
        if candidate.get("legacy_raw_score") not in {None, ""}:
            return normalize_score(candidate.get("legacy_raw_score"), default=0.5)
        return normalize_score(candidate.get("raw_score"), default=0.5)

    def _task_score(self, *, raw_score: float, change_pct: float, turnover: float, action: str) -> float:
        score = raw_score * 100
        score += max(0.0, 10 - abs(change_pct))
        score += min(10.0, turnover / 1e9)
        score += 3.0 if action != "watch_only" else -4.0
        return round(score, 2)
