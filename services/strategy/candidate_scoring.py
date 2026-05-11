from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai_compute": ("AI", "算力", "chip", "芯片", "半导体", "semiconductor", "gpu", "accelerator"),
    "platform_internet": ("平台", "互联网", "广告", "游戏", "platform", "consumer internet", "ecommerce"),
    "consumer_growth": ("消费", "汽车", "销量", "消费电子", "robot", "机器人", "retail", "travel"),
    "financial_defensive": ("保险", "交易所", "回购", "dividend", "cash flow", "bank", "utility"),
}
HIGH_RISK_KEYWORDS = (
    "高波动",
    "波动较大",
    "高beta",
    "估值偏高",
    "涨速过快",
    "theme volatility",
    "volatility",
    "speculative",
    "illiquid",
    "drawdown",
)
MEDIUM_RISK_KEYWORDS = (
    "回撤",
    "竞争",
    "情绪",
    "政策",
    "regulation",
    "execution",
    "latency",
    "估值",
    "macro",
)
VALUATION_HOT_KEYWORDS = ("估值偏高", "估值过热", "valuation hot", "overvalued", "multiple expansion")
LOW_LIQUIDITY_KEYWORDS = (
    "illiquid",
    "low liquidity",
    "wide spread",
    "thin",
    "流动性不足",
    "缺少流动性",
)
SIGNAL_SOURCE_BUCKETS: dict[str, tuple[str, ...]] = {
    "classic": ("classic", "multifactor", "trend", "technical", "raw_score", "quality", "prepared_signal_pack"),
    "knot_agent": ("knot", "agent", "llm"),
    "event": ("event", "news", "catalyst", "earnings"),
    "research": ("research", "analysis", "report"),
}
GENERATED_CANDIDATE_SCORE_SOURCES = {
    "candidate_score_model",
    "dynamic_hybrid_candidate_v2",
    "static_hybrid_candidate_v2",
    "dynamic_hybrid_candidate_v3",
    "static_hybrid_candidate_v3",
}


@dataclass(frozen=True)
class ScoreComponentSpec:
    key: str
    weight: float
    label: str
    role: str = "positive"


@dataclass
class CandidateScoreComponent:
    key: str
    label: str
    score: float
    weight: float
    weighted_contribution: float
    role: str = "positive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": self.score,
            "weight": self.weight,
            "weighted_contribution": self.weighted_contribution,
            "role": self.role,
        }


@dataclass
class CandidateScorecard:
    mode: str
    model_id: str
    selection_policy: str
    score: float
    input_scores: dict[str, float]
    components: list[CandidateScoreComponent] = field(default_factory=list)
    strategy_tags: list[str] = field(default_factory=list)
    strategy_votes: dict[str, float] = field(default_factory=dict)
    source_breakdown: dict[str, float] = field(default_factory=dict)
    consensus_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "model_id": self.model_id,
            "selection_policy": self.selection_policy,
            "score": self.score,
            "input_scores": dict(self.input_scores),
            "components": [item.to_dict() for item in self.components],
            "strategy_tags": list(self.strategy_tags),
            "strategy_votes": dict(self.strategy_votes),
            "source_breakdown": dict(self.source_breakdown),
            "consensus_score": self.consensus_score,
            "risk_flags": list(self.risk_flags),
        }


class CandidateScoreModel(Protocol):
    mode: str
    model_id: str
    selection_policy: str

    def score_row(self, row: Mapping[str, Any]) -> CandidateScorecard:
        ...


def normalize_score(value: Any, *, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return round(default, 4)
    return round(max(0.0, min(1.0, score)), 4)


def is_generated_candidate_score_source(source: Any) -> bool:
    return str(source or "").strip().lower() in GENERATED_CANDIDATE_SCORE_SOURCES


def infer_theme_bucket(row: Mapping[str, Any]) -> str:
    text = _row_text(row)
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return theme
    return "general"


def infer_risk_level(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("risk_level") or "").strip().lower()
    if explicit in {"low", "medium", "high"}:
        return explicit
    risk_penalty = _risk_penalty_score(row)
    if risk_penalty >= 0.7:
        return "high"
    if risk_penalty >= 0.45:
        return "medium"
    return "low"


def build_explanation_summary(row: Mapping[str, Any]) -> str:
    for key in (
        "explanation_summary",
        "llm_reason",
        "llm_summary",
        "research_note",
        "research_summary",
        "analysis_summary",
        "rationale",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


class WeightedCandidateScoreModel:
    mode: str = "dynamic"
    model_id: str = "candidate_score_model"
    selection_policy: str = "candidate_score_model_v1"
    component_specs: tuple[ScoreComponentSpec, ...] = ()

    def score_row(self, row: Mapping[str, Any]) -> CandidateScorecard:
        inputs = self.build_inputs(row)
        components: list[CandidateScoreComponent] = []
        score = 0.0
        for spec in self.component_specs:
            component_score = normalize_score(inputs.get(spec.key), default=0.5)
            contribution = round(spec.weight * component_score, 4)
            if spec.role == "penalty":
                contribution = round(-contribution, 4)
            score += contribution
            components.append(
                CandidateScoreComponent(
                    key=spec.key,
                    label=spec.label,
                    score=component_score,
                    weight=spec.weight,
                    weighted_contribution=contribution,
                    role=spec.role,
                )
            )
        source_breakdown = _source_breakdown(row)
        strategy_votes = self.strategy_votes(inputs)
        consensus_score = _consensus_score(strategy_votes)
        risk_flags = self.risk_flags(inputs, row)
        score = normalize_score(score, default=0.0)
        return CandidateScorecard(
            mode=self.mode,
            model_id=self.model_id,
            selection_policy=self.selection_policy,
            score=score,
            input_scores={key: normalize_score(value, default=0.5) for key, value in inputs.items()},
            components=components,
            strategy_tags=self.strategy_tags(strategy_votes, row),
            strategy_votes=strategy_votes,
            source_breakdown=source_breakdown,
            consensus_score=consensus_score,
            risk_flags=risk_flags,
        )

    def build_inputs(self, row: Mapping[str, Any]) -> dict[str, float]:
        raise NotImplementedError

    def strategy_votes(self, inputs: Mapping[str, float]) -> dict[str, float]:
        raise NotImplementedError

    def strategy_tags(self, votes: Mapping[str, float], row: Mapping[str, Any]) -> list[str]:
        tags = [key for key, value in sorted(votes.items(), key=lambda item: item[1], reverse=True) if value >= 0.58]
        theme_bucket = str(row.get("theme_bucket") or infer_theme_bucket(row))
        if theme_bucket != "general":
            tags.append(theme_bucket)
        return list(dict.fromkeys(tags[:4]))

    def risk_flags(self, inputs: Mapping[str, float], row: Mapping[str, Any]) -> list[str]:
        flags: list[str] = []
        if normalize_score(inputs.get("risk_penalty"), default=0.0) >= 0.7:
            flags.append("high_risk_penalty")
        if normalize_score(inputs.get("liquidity_score", inputs.get("liquidity")), default=0.5) <= 0.35:
            flags.append("thin_liquidity")
        if normalize_score(inputs.get("volatility_score"), default=0.0) >= 0.7:
            flags.append("high_volatility")
        if normalize_score(inputs.get("explanation_ready_score"), default=0.5) <= 0.35:
            flags.append("needs_research")
        text = _row_text(row).lower()
        if any(keyword.lower() in text for keyword in VALUATION_HOT_KEYWORDS):
            flags.append("valuation_hot")
        for flag in row.get("risk_flags") or []:
            value = str(flag).strip()
            if value:
                flags.append(value)
        return sorted(dict.fromkeys(flags))


class DynamicCandidateScoreModel(WeightedCandidateScoreModel):
    mode = "dynamic"
    model_id = "dynamic_hybrid_candidate_v3"
    selection_policy = "dynamic_hybrid_market_complete_v3"
    component_specs = (
        ScoreComponentSpec("trend_score", 0.22, "Trend"),
        ScoreComponentSpec("relative_strength_score", 0.16, "Relative Strength"),
        ScoreComponentSpec("flow_score", 0.14, "Flow"),
        ScoreComponentSpec("event_score", 0.14, "Event"),
        ScoreComponentSpec("quality_score", 0.10, "Quality"),
        ScoreComponentSpec("liquidity_score", 0.08, "Liquidity"),
        ScoreComponentSpec("regime_fit_score", 0.06, "Regime Fit"),
        ScoreComponentSpec("knot_overlay_score", 0.10, "Knot Overlay"),
        ScoreComponentSpec("risk_penalty", 0.10, "Risk Penalty", role="penalty"),
    )

    def build_inputs(self, row: Mapping[str, Any]) -> dict[str, float]:
        trend = _trend_score(row)
        relative_strength = _relative_strength_score(row, fallback=trend)
        flow = _flow_score(row)
        event = _event_score(row)
        quality = _quality_score(row)
        liquidity = _liquidity_score(row)
        volatility = _volatility_score(row)
        regime_fit = _regime_fit_score(row, trend=trend, relative_strength=relative_strength, event=event)
        knot = _knot_overlay_score(row, fallback=(event + quality) / 2)
        explanation_ready = _explanation_ready_score(row)
        return {
            "trend_score": trend,
            "relative_strength_score": relative_strength,
            "flow_score": flow,
            "event_score": event,
            "quality_score": quality,
            "liquidity_score": liquidity,
            "volatility_score": volatility,
            "regime_fit_score": regime_fit,
            "knot_overlay_score": knot,
            "explanation_ready_score": explanation_ready,
            "risk_penalty": _risk_penalty_score(row, volatility_score=volatility),
        }

    def strategy_votes(self, inputs: Mapping[str, float]) -> dict[str, float]:
        return {
            "trend_following": normalize_score((inputs["trend_score"] + inputs["relative_strength_score"] + inputs["flow_score"]) / 3, default=0.5),
            "event_driven": normalize_score((inputs["event_score"] + inputs["flow_score"] + inputs["regime_fit_score"]) / 3, default=0.5),
            "quality_growth": normalize_score((inputs["quality_score"] + inputs["trend_score"] + inputs["liquidity_score"]) / 3, default=0.5),
            "knot_overlay": normalize_score((inputs["knot_overlay_score"] + inputs["event_score"] + inputs["explanation_ready_score"]) / 3, default=0.5),
        }


class StaticCandidateScoreModel(WeightedCandidateScoreModel):
    mode = "static"
    model_id = "static_hybrid_candidate_v3"
    selection_policy = "stable_baseline_hybrid_v3"
    component_specs = (
        ScoreComponentSpec("quality_score", 0.24, "Quality"),
        ScoreComponentSpec("stability_score", 0.18, "Stability"),
        ScoreComponentSpec("liquidity_score", 0.14, "Liquidity"),
        ScoreComponentSpec("sector_leadership_score", 0.12, "Sector Leadership"),
        ScoreComponentSpec("trend_health_score", 0.10, "Trend Health"),
        ScoreComponentSpec("valuation_score", 0.08, "Valuation"),
        ScoreComponentSpec("knot_research_score", 0.08, "Knot Research"),
        ScoreComponentSpec("explanation_ready_score", 0.06, "Explanation Ready"),
        ScoreComponentSpec("risk_penalty", 0.10, "Risk Penalty", role="penalty"),
    )

    def build_inputs(self, row: Mapping[str, Any]) -> dict[str, float]:
        quality = _quality_score(row)
        liquidity = _liquidity_score(row)
        volatility = _volatility_score(row)
        stability = _stability_score(row, quality_score=quality, liquidity_score=liquidity, volatility_score=volatility)
        trend_health = _trend_health_score(row)
        sector_leadership = _sector_leadership_score(row, trend_health_score=trend_health)
        valuation = _valuation_score(row)
        knot_research = _knot_overlay_score(row, fallback=_explanation_ready_score(row))
        explanation_ready = _explanation_ready_score(row)
        return {
            "quality_score": quality,
            "stability_score": stability,
            "liquidity_score": liquidity,
            "sector_leadership_score": sector_leadership,
            "trend_health_score": trend_health,
            "valuation_score": valuation,
            "knot_research_score": knot_research,
            "explanation_ready_score": explanation_ready,
            "volatility_score": volatility,
            "risk_penalty": _risk_penalty_score(row, volatility_score=volatility),
        }

    def strategy_votes(self, inputs: Mapping[str, float]) -> dict[str, float]:
        return {
            "quality_growth": normalize_score((inputs["quality_score"] + inputs["trend_health_score"] + inputs["sector_leadership_score"]) / 3, default=0.5),
            "defensive_baseline": normalize_score((inputs["stability_score"] + inputs["liquidity_score"] + inputs["valuation_score"]) / 3, default=0.5),
            "research_priority": normalize_score((inputs["knot_research_score"] + inputs["explanation_ready_score"] + inputs["quality_score"]) / 3, default=0.5),
        }


class CandidateScoringService:
    def __init__(self, models: Mapping[str, CandidateScoreModel] | None = None):
        self.models = dict(models or {
            "dynamic": DynamicCandidateScoreModel(),
            "static": StaticCandidateScoreModel(),
        })

    def model_for(self, mode: str) -> CandidateScoreModel:
        normalized_mode = str(mode or "dynamic").strip().lower()
        if normalized_mode not in self.models:
            raise KeyError(f"Unsupported candidate scoring mode: {mode}")
        return self.models[normalized_mode]

    def score_row(self, row: Mapping[str, Any], *, mode: str) -> CandidateScorecard:
        return self.model_for(mode).score_row(row)

    def selection_policy_for(self, mode: str) -> str:
        return self.model_for(mode).selection_policy

    def model_metadata(self, mode: str) -> dict[str, Any]:
        model = self.model_for(mode)
        specs = getattr(model, "component_specs", ())
        return {
            "mode": mode,
            "model_id": getattr(model, "model_id", "candidate_score_model"),
            "selection_policy": getattr(model, "selection_policy", "candidate_score_model_v1"),
            "components": [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "weight": spec.weight,
                    "role": spec.role,
                }
                for spec in specs
            ],
        }

    def enrich_row(
        self,
        row: Mapping[str, Any],
        *,
        mode: str,
        generated_at: str | None = None,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        item = dict(row)
        scorecard = self.score_row(item, mode=mode)
        previous_raw_score = item.get("raw_score")
        item["candidate_type"] = mode
        item["selection_policy"] = scorecard.selection_policy
        item["legacy_raw_score"] = normalize_score(previous_raw_score, default=0.5) if previous_raw_score not in {None, ""} else None
        item["raw_score"] = scorecard.score
        for key, value in scorecard.input_scores.items():
            item[key] = value
        item["theme_bucket"] = str(item.get("theme_bucket") or infer_theme_bucket(item))
        item["risk_level"] = str(item.get("risk_level") or infer_risk_level(item))
        item["consensus_score"] = scorecard.consensus_score
        item["strategy_tags"] = list(scorecard.strategy_tags)
        item["strategy_votes"] = dict(scorecard.strategy_votes)
        item["source_breakdown"] = dict(scorecard.source_breakdown)
        item["risk_flags"] = sorted(dict.fromkeys([*(item.get("risk_flags") or []), *scorecard.risk_flags]))
        item["generated_at"] = str(generated_at or item.get("generated_at") or "")
        item["as_of_date"] = str(as_of_date or item.get("as_of_date") or "")
        item["confidence_source"] = str(item.get("confidence_source") or f"{scorecard.model_id}").strip()
        item["explanation_summary"] = str(item.get("explanation_summary") or self._build_generated_summary(item, scorecard)).strip()
        item["explanation_ready"] = bool(item["explanation_summary"])
        item["research_note"] = str(item.get("research_note") or self._build_research_note(item, scorecard)).strip()
        item["rationale"] = str(item.get("rationale") or self._build_rationale(item, scorecard)).strip()
        item["risk"] = str(item.get("risk") or self._build_risk(item, scorecard)).strip()
        item["action_hint"] = str(item.get("action_hint") or self._build_action_hint(item, scorecard)).strip()
        item["signals"] = self._enrich_signals(item.get("signals"), item=item, scorecard=scorecard)
        item["scoring"] = scorecard.to_dict()
        return item

    def _enrich_signals(self, signals: Any, *, item: Mapping[str, Any], scorecard: CandidateScorecard) -> list[dict[str, Any]]:
        normalized = [dict(signal) for signal in signals if isinstance(signal, Mapping)] if isinstance(signals, list) else []
        normalized.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "market": str(item.get("market") or ""),
                "source": scorecard.model_id,
                "category": "candidate_score",
                "score": scorecard.score,
                "summary": self._build_generated_summary(item, scorecard),
            }
        )
        return normalized

    def _build_generated_summary(self, row: Mapping[str, Any], scorecard: CandidateScorecard) -> str:
        existing = build_explanation_summary(row)
        if existing:
            return existing
        top_votes = sorted(scorecard.strategy_votes.items(), key=lambda item: item[1], reverse=True)[:2]
        strategy_text = ", ".join(name for name, _ in top_votes) or scorecard.mode
        return f"{str(row.get('name') or row.get('symbol') or 'Candidate')} ranks via {strategy_text} with a {scorecard.mode} score of {scorecard.score:.2f}."

    def _build_research_note(self, row: Mapping[str, Any], scorecard: CandidateScorecard) -> str:
        note = str(row.get("research_note") or "").strip()
        if note:
            return note
        sources = [key for key, share in scorecard.source_breakdown.items() if share >= 0.2]
        source_text = ", ".join(sources) if sources else "classic"
        return f"Primary evidence mix: {source_text}; consensus {scorecard.consensus_score:.2f}."

    def _build_rationale(self, row: Mapping[str, Any], scorecard: CandidateScorecard) -> str:
        rationale = str(row.get("rationale") or "").strip()
        if rationale:
            return rationale
        inputs = scorecard.input_scores
        if scorecard.mode == "dynamic":
            return (
                f"Trend {inputs.get('trend_score', 0.5):.2f}, relative strength {inputs.get('relative_strength_score', 0.5):.2f}, "
                f"and event support {inputs.get('event_score', 0.5):.2f} place it in the active opportunity bucket."
            )
        return (
            f"Quality {inputs.get('quality_score', 0.5):.2f}, stability {inputs.get('stability_score', 0.5):.2f}, "
            f"and liquidity {inputs.get('liquidity_score', 0.5):.2f} make it a stable baseline candidate."
        )

    def _build_risk(self, row: Mapping[str, Any], scorecard: CandidateScorecard) -> str:
        risk = str(row.get("risk") or "").strip()
        if risk:
            return risk
        flags = list(scorecard.risk_flags)
        if not flags:
            return "Normal portfolio and execution review still applies."
        return "Key risks: " + ", ".join(flags[:3])

    def _build_action_hint(self, row: Mapping[str, Any], scorecard: CandidateScorecard) -> str:
        action_hint = str(row.get("action_hint") or "").strip()
        if action_hint:
            return action_hint
        top_strategy = next(iter(sorted(scorecard.strategy_votes.items(), key=lambda item: item[1], reverse=True)), ("observe_only", 0.0))[0]
        if scorecard.mode == "dynamic":
            if top_strategy == "event_driven":
                return "Review catalyst follow-through and avoid chasing exhausted one-day spikes."
            if top_strategy == "trend_following":
                return "Prefer pullback entries or breakout confirmation with liquidity support."
            return "Keep on the active watchlist and refresh after new market evidence."
        if top_strategy == "defensive_baseline":
            return "Keep in the baseline watchlist and reassess on broad-market pullbacks."
        return "Retain as a baseline research candidate and review during weekly universe refresh."


def _nested_value(row: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = row
        found = True
        for part in path.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in {None, ""}:
            return current
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_text(row: Mapping[str, Any]) -> str:
    parts = [
        str(row.get(key) or "")
        for key in (
            "name",
            "rationale",
            "risk",
            "action_hint",
            "theme_bucket",
            "research_note",
            "explanation_summary",
            "liquidity_note",
            "sector",
            "industry",
            "event_type",
        )
    ]
    for signal in row.get("signals") or []:
        if isinstance(signal, Mapping):
            parts.extend(
                str(signal.get(key) or "")
                for key in ("source", "category", "summary")
            )
    return " ".join(parts)


def _best_signal_score(row: Mapping[str, Any], *, categories: Iterable[str] = (), sources: Iterable[str] = ()) -> float | None:
    normalized_categories = [value.lower() for value in categories]
    normalized_sources = [value.lower() for value in sources]
    scores: list[float] = []
    for signal in row.get("signals") or []:
        if not isinstance(signal, Mapping):
            continue
        category_text = str(signal.get("category") or "").lower()
        source_text = str(signal.get("source") or "").lower()
        summary_text = str(signal.get("summary") or "").lower()
        if normalized_categories and not any(keyword in f"{category_text} {summary_text}" for keyword in normalized_categories):
            if not normalized_sources:
                continue
        if normalized_sources and not any(keyword in f"{source_text} {summary_text}" for keyword in normalized_sources):
            if not normalized_categories:
                continue
        scores.append(normalize_score(signal.get("score"), default=0.5))
    return max(scores) if scores else None


def _trend_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "trend_score", "trend_health_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    signal_score = _best_signal_score(row, categories=("trend", "momentum", "technical"), sources=("trend", "classic"))
    if signal_score is not None:
        return signal_score
    return normalize_score(row.get("raw_score"), default=0.5)


def _relative_strength_score(row: Mapping[str, Any], *, fallback: float) -> float:
    direct = _nested_value(row, "relative_strength_score", "relative_strength_20d")
    if direct is not None:
        value = _as_float(direct)
        if value is not None and value > 1.0:
            value = max(-20.0, min(20.0, value))
            return normalize_score(0.5 + value / 20.0, default=fallback)
        return normalize_score(value, default=fallback)
    change_pct = _as_float(_nested_value(row, "quote.change_pct", "change_pct", "quote_change_pct"))
    if change_pct is not None:
        return normalize_score(0.5 + max(-10.0, min(10.0, change_pct)) / 20.0, default=fallback)
    return normalize_score(fallback, default=0.5)


def _generated_score_source(row: Mapping[str, Any]) -> str:
    return str(_nested_value(row, "scoring.model_id", "confidence_source") or "").strip().lower()


def _trusted_override(row: Mapping[str, Any], *paths: str) -> Any:
    value = _nested_value(row, *paths)
    if value is None:
        return None
    if is_generated_candidate_score_source(_generated_score_source(row)):
        return None
    return value


def _weighted_score(
    components: Iterable[tuple[float, float | None]],
    *,
    default: float = 0.5,
    missing_default: float = 0.5,
) -> float:
    parts = list(components)
    if not parts or all(score is None for _, score in parts):
        return normalize_score(default, default=default)
    total_weight = sum(weight for weight, _ in parts) or 1.0
    blended = sum(weight * (score if score is not None else missing_default) for weight, score in parts) / total_weight
    return normalize_score(blended, default=default)


def _absolute_turnover_score(row: Mapping[str, Any]) -> float | None:
    turnover = _as_float(_nested_value(row, "avg_daily_turnover", "avg_daily_value", "quote.turnover", "turnover"))
    if turnover is None:
        return None
    if turnover >= 10_000_000_000:
        return 0.98
    if turnover >= 5_000_000_000:
        return 0.94
    if turnover >= 2_000_000_000:
        return 0.88
    if turnover >= 1_000_000_000:
        return 0.80
    if turnover >= 300_000_000:
        return 0.68
    if turnover >= 100_000_000:
        return 0.58
    if turnover >= 30_000_000:
        return 0.46
    if turnover >= 10_000_000:
        return 0.34
    if turnover >= 5_000_000:
        return 0.26
    return 0.18


def _turnover_ratio_score(row: Mapping[str, Any]) -> float | None:
    turnover_ratio = _as_float(_nested_value(row, "turnover_ratio", "quote.turnover_ratio"))
    if turnover_ratio is None:
        return None
    bounded = max(0.0, min(turnover_ratio, 1.5))
    return normalize_score(0.18 + (bounded / 1.5) ** 0.5 * 0.74, default=0.5)


def _spread_bps(row: Mapping[str, Any]) -> float | None:
    direct = _as_float(_trusted_override(row, "spread_bps", "relative_spread_bps", "bid_ask_spread_bps"))
    if direct is not None:
        return max(0.0, direct)
    relative = _as_float(_trusted_override(row, "relative_spread", "bid_ask_spread"))
    if relative is None:
        return None
    relative = abs(relative)
    if relative <= 0.01:
        return relative * 10_000.0
    if relative <= 1.0:
        return relative * 100.0
    return relative


def _spread_score(row: Mapping[str, Any]) -> float | None:
    direct = _trusted_override(row, "spread_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    spread_bps = _spread_bps(row)
    if spread_bps is None:
        return None
    if spread_bps <= 5:
        return 0.95
    if spread_bps <= 10:
        return 0.85
    if spread_bps <= 20:
        return 0.72
    if spread_bps <= 35:
        return 0.58
    if spread_bps <= 50:
        return 0.45
    if spread_bps <= 100:
        return 0.25
    return 0.12


def _depth_score(row: Mapping[str, Any]) -> float | None:
    direct = _trusted_override(row, "depth_score", "order_book_depth_score", "liquidity_depth_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    depth = _as_float(_trusted_override(row, "order_book_depth", "quote.order_book_depth"))
    if depth is None:
        return None
    if depth <= 1.0:
        return normalize_score(depth, default=0.5)
    if depth >= 50_000_000:
        return 0.92
    if depth >= 10_000_000:
        return 0.78
    if depth >= 1_000_000:
        return 0.64
    if depth >= 100_000:
        return 0.48
    return 0.32


def _mentions_low_liquidity(row: Mapping[str, Any]) -> bool:
    text = _row_text(row).lower()
    return any(keyword.lower() in text for keyword in LOW_LIQUIDITY_KEYWORDS)


def _flow_score(row: Mapping[str, Any]) -> float:
    direct = _trusted_override(row, "flow_score", "capital_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    participation = _turnover_ratio_score(row)
    scale = _absolute_turnover_score(row)
    signal_score = _best_signal_score(row, categories=("flow", "liquidity"), sources=("event", "classic", "market_data"))
    return _weighted_score(
        (
            (0.50, participation),
            (0.35, scale),
            (0.15, signal_score),
        ),
        default=0.5,
        missing_default=0.5,
    )


def _event_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "event_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    if bool(row.get("has_event_catalyst")):
        freshness = _as_float(row.get("event_freshness_days"))
        if freshness is None:
            return 0.78
        if freshness <= 2:
            return 0.82
        if freshness <= 7:
            return 0.72
        return 0.6
    sentiment = _as_float(row.get("news_sentiment_score"))
    if sentiment is not None:
        return normalize_score(0.5 + sentiment / 2.0, default=0.5)
    signal_score = _best_signal_score(row, categories=("event", "news", "catalyst"), sources=("event", "news", "research"))
    return normalize_score(signal_score, default=0.5)


def _quality_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "quality_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    signal_score = _best_signal_score(row, categories=("quality", "fundamental", "composite"), sources=("classic", "research"))
    if signal_score is not None:
        return signal_score
    market_cap = _as_float(_nested_value(row, "market_cap", "free_float_market_cap"))
    if market_cap is not None:
        if market_cap >= 500_000_000_000:
            return 0.9
        if market_cap >= 100_000_000_000:
            return 0.8
        if market_cap >= 20_000_000_000:
            return 0.68
    return normalize_score(row.get("max_signal_score"), default=0.55)


def compute_candidate_liquidity_score(row: Mapping[str, Any]) -> float:
    return _liquidity_score(row)


def _liquidity_score(row: Mapping[str, Any]) -> float:
    manual_override = _nested_value(row, "liquidity_override_score", "liquidity_score_override", "manual_liquidity_score")
    if manual_override is not None:
        return normalize_score(manual_override, default=0.5)
    direct = _trusted_override(row, "liquidity_score", "liquidity")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    absolute_turnover = _absolute_turnover_score(row)
    turnover_ratio = _turnover_ratio_score(row)
    spread = _spread_score(row)
    depth = _depth_score(row)
    liquidity = _weighted_score(
        (
            (0.55, absolute_turnover),
            (0.25, turnover_ratio),
            (0.12, spread),
            (0.08, depth),
        ),
        default=0.5,
        missing_default=0.5,
    )
    if _mentions_low_liquidity(row):
        liquidity = normalize_score(liquidity - 0.18, default=0.5)
    return liquidity


def _volatility_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "volatility_score", "atr_pct_14d", "realized_vol_20d")
    value = _as_float(direct)
    if value is not None:
        if value > 1.0:
            value = value / 100.0 if value > 10 else value
        return normalize_score(min(1.0, value / 0.08), default=0.4)
    text = _row_text(row).lower()
    if any(keyword.lower() in text for keyword in HIGH_RISK_KEYWORDS):
        return 0.8
    if any(keyword.lower() in text for keyword in MEDIUM_RISK_KEYWORDS):
        return 0.58
    return 0.35


def _regime_fit_score(row: Mapping[str, Any], *, trend: float, relative_strength: float, event: float) -> float:
    direct = _nested_value(row, "regime_fit_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    return normalize_score((trend + relative_strength + event) / 3, default=0.5)


def _knot_overlay_score(row: Mapping[str, Any], *, fallback: float) -> float:
    direct = _nested_value(row, "knot_overlay_score", "knot_research_score")
    if direct is not None:
        return normalize_score(direct, default=fallback)
    signal_score = _best_signal_score(row, categories=("research",), sources=("knot", "agent", "llm"))
    confidence_source = str(row.get("confidence_source") or "").lower()
    if signal_score is not None:
        return signal_score
    if any(keyword in confidence_source for keyword in ("knot", "agent", "llm")):
        return normalize_score(fallback + 0.08, default=fallback)
    return normalize_score(fallback, default=0.5)


def _explanation_ready_score(row: Mapping[str, Any]) -> float:
    if bool(row.get("explanation_ready")):
        return 1.0
    summary = build_explanation_summary(row)
    if summary:
        return 0.85
    if str(row.get("rationale") or "").strip():
        return 0.65
    return 0.3


def _risk_penalty_score(row: Mapping[str, Any], *, volatility_score: float | None = None) -> float:
    direct = _nested_value(row, "risk_penalty")
    if direct is not None:
        return normalize_score(direct, default=0.35)
    volatility = volatility_score if volatility_score is not None else _volatility_score(row)
    risk_level = str(row.get("risk_level") or "").strip().lower()
    penalty = 0.2 + volatility * 0.45
    if risk_level == "high":
        penalty += 0.25
    elif risk_level == "medium":
        penalty += 0.1
    text = _row_text(row).lower()
    if any(keyword.lower() in text for keyword in HIGH_RISK_KEYWORDS):
        penalty += 0.2
    elif any(keyword.lower() in text for keyword in MEDIUM_RISK_KEYWORDS):
        penalty += 0.1
    drawdown = _as_float(row.get("drawdown_60d"))
    if drawdown is not None:
        penalty += min(0.18, abs(drawdown))
    change_pct = _as_float(_nested_value(row, "quote.change_pct", "change_pct", "quote_change_pct"))
    if change_pct is not None and abs(change_pct) >= 6:
        penalty += 0.12
    return normalize_score(penalty, default=0.35)


def _stability_score(row: Mapping[str, Any], *, quality_score: float, liquidity_score: float, volatility_score: float) -> float:
    direct = _nested_value(row, "stability_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    drawdown = _as_float(row.get("drawdown_60d"))
    drawdown_penalty = min(0.35, abs(drawdown)) if drawdown is not None else 0.12
    base = quality_score * 0.45 + liquidity_score * 0.25 + (1.0 - volatility_score) * 0.2 + (1.0 - drawdown_penalty) * 0.1
    return normalize_score(base, default=0.5)


def _trend_health_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "trend_health_score", "trend_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    return _trend_score(row)


def _sector_leadership_score(row: Mapping[str, Any], *, trend_health_score: float) -> float:
    direct = _nested_value(row, "sector_leadership_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    market_cap = _as_float(_nested_value(row, "market_cap", "free_float_market_cap"))
    cap_score = 0.55
    if market_cap is not None:
        if market_cap >= 500_000_000_000:
            cap_score = 0.92
        elif market_cap >= 100_000_000_000:
            cap_score = 0.82
        elif market_cap >= 20_000_000_000:
            cap_score = 0.68
    return normalize_score(trend_health_score * 0.55 + cap_score * 0.45, default=0.5)


def _valuation_score(row: Mapping[str, Any]) -> float:
    direct = _nested_value(row, "valuation_score")
    if direct is not None:
        return normalize_score(direct, default=0.5)
    text = _row_text(row).lower()
    if any(keyword.lower() in text for keyword in VALUATION_HOT_KEYWORDS):
        return 0.35
    risk_level = str(row.get("risk_level") or "").strip().lower()
    if risk_level == "high":
        return 0.45
    return 0.62


def _source_breakdown(row: Mapping[str, Any]) -> dict[str, float]:
    scores = Counter()
    confidence_source = str(row.get("confidence_source") or "").lower()
    for bucket, keywords in SIGNAL_SOURCE_BUCKETS.items():
        if any(keyword in confidence_source for keyword in keywords):
            scores[bucket] += 2.0
    for signal in row.get("signals") or []:
        if not isinstance(signal, Mapping):
            continue
        source_text = f"{signal.get('source', '')} {signal.get('category', '')} {signal.get('summary', '')}".lower()
        signal_score = normalize_score(signal.get("score"), default=0.5)
        matched = False
        for bucket, keywords in SIGNAL_SOURCE_BUCKETS.items():
            if any(keyword in source_text for keyword in keywords):
                scores[bucket] += max(0.5, signal_score)
                matched = True
        if not matched:
            scores["classic"] += 0.5
    if not scores:
        scores["classic"] = 1.0
    total = sum(scores.values()) or 1.0
    return {key: round(value / total, 4) for key, value in sorted(scores.items())}


def _consensus_score(votes: Mapping[str, float]) -> float:
    if not votes:
        return 0.0
    ordered = sorted(votes.values(), reverse=True)
    average = sum(ordered) / len(ordered)
    spread = max(ordered) - min(ordered)
    return normalize_score(average - spread * 0.25, default=0.5)
