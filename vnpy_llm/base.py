from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_now_isoformat() -> str:
    return beijing_now().isoformat()


def parse_datetime(value: datetime | str | None, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    elif default is not None:
        dt = default
    else:
        dt = utc_now()

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_datetime(value: datetime) -> str:
    return parse_datetime(value).isoformat()


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


@dataclass(frozen=True)
class NewsItem:
    item_id: str
    title: str
    content: str
    source: str
    source_time: datetime
    retrieved_at: datetime
    symbols: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        title = str(data.get("title", ""))
        content = str(data.get("content", data.get("summary", "")))
        source = str(data.get("source", "local"))
        source_time = parse_datetime(data.get("source_time") or data.get("published_at") or data.get("time"))
        retrieved_at = parse_datetime(data.get("retrieved_at"), source_time)
        item_id = str(data.get("item_id") or data.get("id") or f"{source}:{source_time.isoformat()}:{title[:80]}")
        symbols = [str(item).upper() for item in data.get("symbols", [])]
        tags = [str(item).lower() for item in data.get("tags", [])]
        return cls(
            item_id=item_id,
            title=title,
            content=content,
            source=source,
            source_time=source_time,
            retrieved_at=retrieved_at,
            symbols=symbols,
            tags=tags,
            url=str(data.get("url", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "source_time": format_datetime(self.source_time),
            "retrieved_at": format_datetime(self.retrieved_at),
            "symbols": self.symbols,
            "tags": self.tags,
            "url": self.url,
        }


@dataclass(frozen=True)
class EvidenceItem:
    item_id: str
    title: str
    snippet: str
    source: str
    source_time: datetime
    url: str = ""
    score: float = 0.0

    @classmethod
    def from_news(cls, item: NewsItem, score: float) -> "EvidenceItem":
        text = " ".join([item.title, item.content]).strip()
        snippet = text[:700]
        return cls(
            item_id=item.item_id,
            title=item.title,
            snippet=snippet,
            source=item.source,
            source_time=item.source_time,
            url=item.url,
            score=score,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "snippet": self.snippet,
            "source": self.source,
            "source_time": format_datetime(self.source_time),
            "url": self.url,
            "score": self.score,
        }


@dataclass(frozen=True)
class LlmSignal:
    signal_id: str
    symbol: str
    as_of: datetime
    valid_until: datetime
    source_time: datetime
    retrieved_at: datetime
    scored_at: datetime
    sector_sentiment: float
    sector_risk: float
    macro_risk: float
    fed_policy_bias: str
    jpy_fx_risk: float
    us_economy_score: float
    event_score: float
    confidence: float
    trade_filter: str
    position_multiplier: float
    reasons: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    evidence_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LlmSignal":
        now = utc_now()
        as_of = parse_datetime(data.get("as_of"), now)
        return cls(
            signal_id=str(data.get("signal_id", f"{data.get('symbol', 'UNKNOWN')}:{as_of.date()}")),
            symbol=str(data.get("symbol", "")).upper(),
            as_of=as_of,
            valid_until=parse_datetime(data.get("valid_until"), as_of),
            source_time=parse_datetime(data.get("source_time"), as_of),
            retrieved_at=parse_datetime(data.get("retrieved_at"), as_of),
            scored_at=parse_datetime(data.get("scored_at"), now),
            sector_sentiment=clamp(float(data.get("sector_sentiment", 0.0)), -1.0, 1.0),
            sector_risk=clamp(float(data.get("sector_risk", 0.5))),
            macro_risk=clamp(float(data.get("macro_risk", 0.5))),
            fed_policy_bias=str(data.get("fed_policy_bias", "neutral")),
            jpy_fx_risk=clamp(float(data.get("jpy_fx_risk", 0.5))),
            us_economy_score=clamp(float(data.get("us_economy_score", 0.0)), -1.0, 1.0),
            event_score=clamp(float(data.get("event_score", 0.0)), -1.0, 1.0),
            confidence=clamp(float(data.get("confidence", 0.0))),
            trade_filter=str(data.get("trade_filter", "block_long")),
            position_multiplier=clamp(float(data.get("position_multiplier", 0.0))),
            reasons=[str(item) for item in data.get("reasons", [])],
            sources=[str(item) for item in data.get("sources", [])],
            evidence_count=int(data.get("evidence_count", len(data.get("sources", [])))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "as_of": format_datetime(self.as_of),
            "valid_until": format_datetime(self.valid_until),
            "source_time": format_datetime(self.source_time),
            "retrieved_at": format_datetime(self.retrieved_at),
            "scored_at": format_datetime(self.scored_at),
            "sector_sentiment": self.sector_sentiment,
            "sector_risk": self.sector_risk,
            "macro_risk": self.macro_risk,
            "fed_policy_bias": self.fed_policy_bias,
            "jpy_fx_risk": self.jpy_fx_risk,
            "us_economy_score": self.us_economy_score,
            "event_score": self.event_score,
            "confidence": self.confidence,
            "trade_filter": self.trade_filter,
            "position_multiplier": self.position_multiplier,
            "reasons": self.reasons,
            "sources": self.sources,
            "evidence_count": self.evidence_count,
        }

    def is_visible_at(self, decision_time: datetime) -> bool:
        dt = parse_datetime(decision_time)
        return self.source_time <= dt and self.retrieved_at <= dt and self.scored_at <= dt

    def is_valid_at(self, decision_time: datetime) -> bool:
        dt = parse_datetime(decision_time)
        return self.is_visible_at(dt) and self.as_of <= dt <= self.valid_until


@dataclass(frozen=True)
class RiskDecision:
    allow_new_long: bool
    reduce_only: bool
    position_multiplier: float
    reason: str
    signal_id: str | None = None
