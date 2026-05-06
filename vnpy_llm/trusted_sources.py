from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any

from .base import NewsItem, utc_now

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 vnpy-llm-news/1.0"


@dataclass(frozen=True)
class TrustedNewsSource:
    source_id: str
    name: str
    kind: str
    url: str
    trust_score: float
    tags: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    enabled: bool = True
    required_domain: str = ""
    max_age_days: int = 14

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrustedNewsSource":
        return cls(
            source_id=str(data.get("source_id", data.get("id", ""))),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "rss")),
            url=str(data.get("url", "")),
            trust_score=float(data.get("trust_score", 0.8)),
            tags=[str(item).lower() for item in data.get("tags", [])],
            symbols=[str(item).upper() for item in data.get("symbols", [])],
            enabled=bool(data.get("enabled", True)),
            required_domain=str(data.get("required_domain", "")),
            max_age_days=int(data.get("max_age_days", 14)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_TRUSTED_NEWS_SOURCES: list[TrustedNewsSource] = [
    TrustedNewsSource(
        source_id="fed_monetary_policy",
        name="Federal Reserve Board - Monetary Policy Press Releases",
        kind="rss",
        url="https://www.federalreserve.gov/feeds/press_monetary.xml",
        trust_score=0.99,
        tags=["official", "fed", "fomc", "monetary_policy", "macro"],
        required_domain="federalreserve.gov",
        max_age_days=45,
    ),
    TrustedNewsSource(
        source_id="fed_speeches",
        name="Federal Reserve Board - Speeches",
        kind="rss",
        url="https://www.federalreserve.gov/feeds/speeches.xml",
        trust_score=0.96,
        tags=["official", "fed", "speech", "macro"],
        required_domain="federalreserve.gov",
        max_age_days=14,
    ),
    TrustedNewsSource(
        source_id="fed_testimony",
        name="Federal Reserve Board - Testimony",
        kind="rss",
        url="https://www.federalreserve.gov/feeds/testimony.xml",
        trust_score=0.95,
        tags=["official", "fed", "testimony", "macro"],
        required_domain="federalreserve.gov",
        max_age_days=21,
    ),
    TrustedNewsSource(
        source_id="bls_latest_releases",
        name="U.S. Bureau of Labor Statistics - Latest Economic Releases",
        kind="web_page",
        url="https://blsmon1.bls.gov/newsroom/",
        trust_score=0.96,
        tags=["official", "bls", "jobs", "payroll", "cpi", "inflation", "us_economy"],
        required_domain="blsmon1.bls.gov",
        max_age_days=3,
        enabled=False,
    ),
    TrustedNewsSource(
        source_id="bea_current_releases",
        name="U.S. Bureau of Economic Analysis - Current Releases",
        kind="web_page",
        url="https://www.bea.gov/news/current-releases",
        trust_score=0.96,
        tags=["official", "bea", "gdp", "pce", "income", "us_economy"],
        required_domain="bea.gov",
        max_age_days=3,
    ),
    TrustedNewsSource(
        source_id="census_economic_indicators",
        name="U.S. Census Bureau - Economic Indicators",
        kind="web_page",
        url="https://www.census.gov/economic-indicators/",
        trust_score=0.94,
        tags=["official", "census", "retail_sales", "durable_goods", "housing", "us_economy"],
        required_domain="census.gov",
        max_age_days=3,
        enabled=False,
    ),
    TrustedNewsSource(
        source_id="treasury_interest_rates",
        name="U.S. Treasury - Interest Rates",
        kind="web_page",
        url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
        trust_score=0.94,
        tags=["official", "treasury", "yield", "rates", "macro"],
        required_domain="home.treasury.gov",
        max_age_days=3,
        enabled=False,
    ),
    TrustedNewsSource(
        source_id="boj_latest",
        name="Bank of Japan - Announcements",
        kind="web_page",
        url="https://www.boj.or.jp/en/",
        trust_score=0.93,
        tags=["official", "boj", "jpy", "yen", "carry_trade", "fx"],
        required_domain="boj.or.jp",
        max_age_days=5,
    ),
    TrustedNewsSource(
        source_id="ism_report_on_business",
        name="ISM Report On Business",
        kind="web_page",
        url="https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/",
        trust_score=0.86,
        tags=["pmi", "ism", "manufacturing", "services", "us_economy"],
        required_domain="ismworld.org",
        max_age_days=5,
        enabled=False,
    ),
    TrustedNewsSource(
        source_id="yahoo_soxl_semis_headlines",
        name="Yahoo Finance - SOXL/Semiconductor Headlines",
        kind="rss",
        url="https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXL,NVDA,AMD,AVGO,TSM,ASML,AMAT,LRCX,MU,INTC,SOXX,SMH&region=US&lang=en-US",
        trust_score=0.72,
        tags=["market_news", "semiconductor", "soxl", "stocks"],
        symbols=["SOXL", "NVDA", "AMD", "AVGO", "TSM", "ASML", "AMAT", "LRCX", "MU", "INTC", "SOXX", "SMH"],
        required_domain="feeds.finance.yahoo.com",
        max_age_days=7,
    ),
]


def write_default_sources(path: str | Path, overwrite: bool = False) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": utc_now().isoformat(),
        "sources": [source.to_dict() for source in DEFAULT_TRUSTED_NEWS_SOURCES],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_trusted_sources(path: str | Path) -> list[TrustedNewsSource]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("sources", data if isinstance(data, list) else [])
    return [TrustedNewsSource.from_dict(row) for row in rows]


def _url_domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _validate_source_url(source: TrustedNewsSource) -> None:
    domain = _url_domain(source.url)
    required = source.required_domain.lower().strip()
    if required and not (domain == required or domain.endswith(f".{required}")):
        raise ValueError(f"source {source.source_id} domain {domain} does not match {required}")


def _fetch_text(url: str, timeout_seconds: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def _parse_time(value: str, default: datetime) -> datetime:
    text = value.strip()
    if not text:
        return default
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return default
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return child.text.strip()
    return ""


def _child_link(node: ET.Element) -> str:
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if href:
            return href
        if child.text:
            return child.text.strip()
    return ""


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(source: TrustedNewsSource, text: str, retrieved_at: datetime) -> list[NewsItem]:
    root = ET.fromstring(text)
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    items: list[NewsItem] = []
    cutoff = retrieved_at - timedelta(days=source.max_age_days)
    for node in nodes:
        title = _child_text(node, ("title",))
        content = _child_text(node, ("description", "summary", "content"))
        link = _child_link(node)
        published = _child_text(node, ("pubdate", "published", "updated", "date"))
        source_time = _parse_time(published, retrieved_at)
        if source_time < cutoff:
            continue
        item_id = _child_text(node, ("guid", "id")) or f"{source.source_id}:{source_time.isoformat()}:{title[:80]}"
        items.append(
            NewsItem(
                item_id=item_id,
                title=_strip_html(title),
                content=_strip_html(content),
                source=source.source_id,
                source_time=source_time,
                retrieved_at=retrieved_at,
                symbols=source.symbols,
                tags=source.tags,
                url=link,
            )
        )
    return items


def _parse_web_page(source: TrustedNewsSource, text: str, retrieved_at: datetime) -> list[NewsItem]:
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    title = _strip_html(title_match.group(1)) if title_match else source.name
    meta_match = re.search(r'(?is)<meta\s+[^>]*(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](.*?)["\']', text)
    description = _strip_html(meta_match.group(1)) if meta_match else ""
    body = _strip_html(text)
    content = "\n".join(part for part in [description, body[:4000]] if part)
    return [
        NewsItem(
            item_id=f"{source.source_id}:{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}",
            title=title or source.name,
            content=content,
            source=source.source_id,
            source_time=retrieved_at,
            retrieved_at=retrieved_at,
            symbols=source.symbols,
            tags=source.tags,
            url=source.url,
        )
    ]


def fetch_trusted_source(source: TrustedNewsSource, timeout_seconds: int = 20) -> list[NewsItem]:
    if not source.enabled:
        return []
    _validate_source_url(source)
    retrieved_at = utc_now()
    text = _fetch_text(source.url, timeout_seconds)
    if source.kind == "rss":
        return _parse_rss(source, text, retrieved_at)
    if source.kind == "web_page":
        return _parse_web_page(source, text, retrieved_at)
    raise ValueError(f"unsupported source kind: {source.kind}")


def write_news_jsonl(items: list[NewsItem], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    unique: dict[str, NewsItem] = {item.item_id: item for item in items}
    ordered = sorted(unique.values(), key=lambda item: (item.source_time, item.source, item.title))
    with target.open("w", encoding="utf-8", newline="") as f:
        for item in ordered:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
    return target
