from __future__ import annotations

import json

from vnpy_llm.trusted_sources import TrustedNewsSource, _parse_rss, write_news_jsonl
from vnpy_llm.base import utc_now


def test_parse_rss_item(tmp_path) -> None:
    source = TrustedNewsSource(
        source_id="fed_test",
        name="Fed Test",
        kind="rss",
        url="https://www.federalreserve.gov/feeds/press_all.xml",
        trust_score=0.98,
        tags=["fed", "macro"],
        required_domain="federalreserve.gov",
    )
    xml = """
    <rss><channel><item>
      <title>FOMC keeps rates unchanged</title>
      <description>Policy statement mentions inflation and labor market.</description>
      <link>https://www.federalreserve.gov/test.htm</link>
      <pubDate>Wed, 06 May 2026 14:00:00 GMT</pubDate>
      <guid>fed-test-1</guid>
    </item></channel></rss>
    """
    items = _parse_rss(source, xml, utc_now())
    assert len(items) == 1
    assert items[0].source == "fed_test"
    assert "inflation" in items[0].content
    assert items[0].tags == ["fed", "macro"]

    output = write_news_jsonl(items, tmp_path / "news.jsonl")
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert row["item_id"] == "fed-test-1"
