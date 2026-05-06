from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


from .base import EvidenceItem, NewsItem, parse_datetime


SEMI_KEYWORDS = {
    "soxl", "soxx", "smh", "semiconductor", "chip", "chips", "ai chip", "gpu",
    "nvda", "nvidia", "amd", "avgo", "broadcom", "tsm", "tsmc", "asml",
    "amat", "lrcx", "mu", "intel", "intc", "半导体", "芯片", "英伟达",
}

MACRO_KEYWORDS = {
    "fed", "fomc", "powell", "rate", "inflation", "cpi", "pce", "payroll", "jobs",
    "unemployment", "retail sales", "pmi", "ism", "gdp", "treasury", "yield", "美元",
    "美联储", "利率", "通胀", "就业", "非农", "消费", "衰退", "美国经济",
    "usd/jpy", "jpy", "yen", "boj", "carry trade", "intervention", "日元", "套息",
}


def _term_matches(text: str, term: str) -> bool:
    if not term:
        return False
    if any(ord(ch) > 127 for ch in term) or not term.replace("/", "").replace("-", "").isalnum():
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


@dataclass
class LocalRagStore:
    items: list[NewsItem] = field(default_factory=list)

    def add_items(self, items: list[NewsItem]) -> None:
        self.items.extend(items)
        self.items.sort(key=lambda item: item.source_time)

    def search(
        self,
        query_terms: list[str],
        decision_time: datetime,
        max_items: int = 20,
        include_macro: bool = True,
    ) -> list[EvidenceItem]:
        dt = parse_datetime(decision_time)
        terms = {term.lower() for term in query_terms if term}
        if include_macro:
            terms.update(MACRO_KEYWORDS)
        terms.update(SEMI_KEYWORDS)

        evidence: list[EvidenceItem] = []
        for item in self.items:
            if item.source_time > dt or item.retrieved_at > dt:
                continue
            text = " ".join([item.title, item.content]).lower()
            score = sum(1 for term in terms if _term_matches(text, term))
            if score <= 0:
                continue
            evidence.append(EvidenceItem.from_news(item, float(score)))

        evidence.sort(key=lambda item: (item.score, item.source_time), reverse=True)
        return evidence[:max_items]
