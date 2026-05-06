from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from .base import EvidenceItem, LlmSignal, clamp, utc_now
from .llm_client import LlmClientError, OpenAICompatibleClient


POSITIVE_TERMS = {
    "beat", "raise", "raised", "strong", "growth", "surge", "upgrade", "demand", "bullish",
    "降息", "宽松", "增长", "强劲", "上调", "超预期", "需求", "乐观",
}
NEGATIVE_TERMS = {
    "miss", "cut", "weak", "slowdown", "downgrade", "restriction", "risk", "selloff", "recession",
    "hawkish", "higher for longer", "inflation", "tariff", "ban", "紧缩", "衰退", "风险", "下调", "疲软", "限制", "通胀反弹",
}
FED_HAWKISH_TERMS = {"hawkish", "higher for longer", "rate hike", "sticky inflation", "加息", "鹰派", "更久高利率", "通胀粘性"}
FED_DOVISH_TERMS = {"dovish", "rate cut", "cuts", "easing", "降息", "鸽派", "宽松"}
JPY_RISK_TERMS = {"yen strengthens", "usd/jpy falls", "carry unwind", "intervention", "日元升值", "套息平仓", "干预"}
US_GOOD_TERMS = {"soft landing", "strong payroll", "consumer resilience", "pmi expansion", "软着陆", "就业强劲", "消费韧性", "扩张"}
US_BAD_TERMS = {"recession", "jobless", "pmi contraction", "credit stress", "衰退", "失业", "收缩", "信贷压力"}


def build_system_prompt() -> str:
    return (
        "你是量化交易系统中的信息评分模块。只能基于用户提供的证据包输出 JSON，"
        "不得编造事实，不得直接给下单指令。字段必须包含：symbol, sector_sentiment, "
        "sector_risk, macro_risk, fed_policy_bias, jpy_fx_risk, us_economy_score, event_score, "
        "confidence, trade_filter, position_multiplier, reasons。"
    )


def build_user_prompt(symbol: str, evidence: list[EvidenceItem]) -> str:
    payload = [item.to_dict() for item in evidence]
    return (
        f"目标标的：{symbol}\n"
        "请评估半导体行业、美联储态度、日元汇率/套息风险、美国国内经济趋势对该标的的影响。\n"
        "trade_filter 只能是 allow_long、block_long、reduce_only。position_multiplier 必须在 0 到 1。\n"
        f"证据包 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _count_terms(text: str, terms: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _heuristic_payload(symbol: str, evidence: list[EvidenceItem]) -> dict[str, Any]:
    text = "\n".join([f"{item.title}\n{item.snippet}" for item in evidence])
    positive = _count_terms(text, POSITIVE_TERMS)
    negative = _count_terms(text, NEGATIVE_TERMS)
    fed_hawkish = _count_terms(text, FED_HAWKISH_TERMS)
    fed_dovish = _count_terms(text, FED_DOVISH_TERMS)
    jpy_risk_terms = _count_terms(text, JPY_RISK_TERMS)
    us_good = _count_terms(text, US_GOOD_TERMS)
    us_bad = _count_terms(text, US_BAD_TERMS)

    total = max(positive + negative, 1)
    sector_sentiment = clamp((positive - negative) / total, -1.0, 1.0)
    sector_risk = clamp(0.35 + negative * 0.08 - positive * 0.03)
    macro_risk = clamp(0.35 + fed_hawkish * 0.12 + us_bad * 0.1 - fed_dovish * 0.06 - us_good * 0.05)
    jpy_fx_risk = clamp(0.25 + jpy_risk_terms * 0.18)
    us_economy_score = clamp((us_good - us_bad) / max(us_good + us_bad, 1), -1.0, 1.0)

    if fed_hawkish > fed_dovish:
        fed_policy_bias = "hawkish"
    elif fed_dovish > fed_hawkish:
        fed_policy_bias = "dovish"
    else:
        fed_policy_bias = "neutral"

    combined_risk = max(sector_risk, macro_risk, jpy_fx_risk)
    if combined_risk >= 0.85:
        trade_filter = "block_long"
        multiplier = 0.0
    elif combined_risk >= 0.75:
        trade_filter = "reduce_only"
        multiplier = 0.3
    elif sector_sentiment > 0:
        trade_filter = "allow_long"
        multiplier = clamp(1.0 - combined_risk * 0.5)
    else:
        trade_filter = "block_long"
        multiplier = 0.0

    confidence = clamp(0.35 + min(len(evidence), 10) * 0.05)
    reasons = [
        f"证据数量={len(evidence)}，正面词={positive}，负面词={negative}",
        f"美联储倾向={fed_policy_bias}，鹰派线索={fed_hawkish}，鸽派线索={fed_dovish}",
        f"日元/套息风险线索={jpy_risk_terms}，美国经济好/坏线索={us_good}/{us_bad}",
    ]
    return {
        "symbol": symbol,
        "sector_sentiment": round(sector_sentiment, 4),
        "sector_risk": round(sector_risk, 4),
        "macro_risk": round(macro_risk, 4),
        "fed_policy_bias": fed_policy_bias,
        "jpy_fx_risk": round(jpy_fx_risk, 4),
        "us_economy_score": round(us_economy_score, 4),
        "event_score": round(sector_sentiment - combined_risk * 0.3, 4),
        "confidence": round(confidence, 4),
        "trade_filter": trade_filter,
        "position_multiplier": round(multiplier, 4),
        "reasons": reasons,
    }


def _as_score(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if isinstance(value, bool):
        score = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        score = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        mapped = {
            "very_negative": -1.0,
            "bearish": -0.7,
            "negative": -0.5,
            "weak": -0.3,
            "neutral": 0.0,
            "mixed": 0.0,
            "positive": 0.5,
            "bullish": 0.7,
            "very_positive": 1.0,
            "low": 0.25,
            "medium": 0.5,
            "moderate": 0.5,
            "high": 0.8,
            "extreme": 1.0,
        }
        if text in mapped:
            score = mapped[text]
        else:
            try:
                score = float(text.rstrip("%"))
                if text.endswith("%"):
                    score /= 100.0
            except ValueError:
                score = default
    else:
        score = default
    return max(minimum, min(maximum, score))


def _normalize_payload(symbol: str, evidence: list[EvidenceItem], payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    latest_source = max((item.source_time for item in evidence), default=now)
    source_ids = [item.item_id for item in evidence]
    signal_id = f"{symbol}:{now.strftime('%Y%m%dT%H%M%SZ')}"
    normalized = {
        "signal_id": payload.get("signal_id", signal_id),
        "symbol": payload.get("symbol", symbol),
        "as_of": payload.get("as_of", now.isoformat()),
        "valid_until": payload.get("valid_until", (now + timedelta(hours=24)).isoformat()),
        "source_time": payload.get("source_time", latest_source.isoformat()),
        "retrieved_at": payload.get("retrieved_at", now.isoformat()),
        "scored_at": payload.get("scored_at", now.isoformat()),
        "sector_sentiment": _as_score(payload.get("sector_sentiment", 0.0), 0.0, minimum=-1.0, maximum=1.0),
        "sector_risk": _as_score(payload.get("sector_risk", 0.5), 0.5),
        "macro_risk": _as_score(payload.get("macro_risk", 0.5), 0.5),
        "fed_policy_bias": payload.get("fed_policy_bias", "neutral"),
        "jpy_fx_risk": _as_score(payload.get("jpy_fx_risk", 0.5), 0.5),
        "us_economy_score": _as_score(payload.get("us_economy_score", 0.0), 0.0, minimum=-1.0, maximum=1.0),
        "event_score": _as_score(payload.get("event_score", 0.0), 0.0, minimum=-1.0, maximum=1.0),
        "confidence": _as_score(payload.get("confidence", 0.0), 0.0),
        "trade_filter": payload.get("trade_filter", "block_long"),
        "position_multiplier": _as_score(payload.get("position_multiplier", 0.0), 0.0),
        "reasons": payload.get("reasons", []),
        "sources": payload.get("sources", source_ids),
        "evidence_count": payload.get("evidence_count", len(evidence)),
    }
    if normalized["trade_filter"] not in {"allow_long", "block_long", "reduce_only"}:
        normalized["trade_filter"] = "block_long"
        normalized["position_multiplier"] = 0.0
    return normalized


def score_evidence(
    symbol: str,
    evidence: list[EvidenceItem],
    client: OpenAICompatibleClient | None = None,
) -> LlmSignal:
    payload: dict[str, Any]
    if client and client.is_configured() and evidence:
        try:
            payload = client.complete_json(build_system_prompt(), build_user_prompt(symbol, evidence))
        except LlmClientError:
            payload = _heuristic_payload(symbol, evidence)
    else:
        payload = _heuristic_payload(symbol, evidence)
    return LlmSignal.from_dict(_normalize_payload(symbol, evidence, payload))
