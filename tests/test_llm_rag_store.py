from __future__ import annotations

from vnpy_llm.base import NewsItem, utc_now
from vnpy_llm.rag_store import LocalRagStore


def test_rag_ignores_metadata_only_matches() -> None:
    now = utc_now()
    item = NewsItem(
        item_id="irrelevant",
        title="Crypto remittance headline",
        content="A jurisdiction changed remittance rules.",
        source="market_feed",
        source_time=now,
        retrieved_at=now,
        symbols=["SOXL", "NVDA"],
        tags=["semiconductor", "macro"],
    )
    evidence = LocalRagStore([item]).search(["SOXL", "NVDA"], now)
    assert evidence == []


def test_rag_matches_content_terms() -> None:
    now = utc_now()
    item = NewsItem(
        item_id="amd-news",
        title="AMD stock rises on AI chip demand",
        content="The report mentioned strong GPU demand.",
        source="market_feed",
        source_time=now,
        retrieved_at=now,
    )
    evidence = LocalRagStore([item]).search(["AMD"], now)
    assert len(evidence) == 1
    assert evidence[0].item_id == "amd-news"
