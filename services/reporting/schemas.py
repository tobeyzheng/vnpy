from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from services.candidate_engine.models import Candidate
from services.watchlist_engine.models import WatchlistDiff


@dataclass
class MarketEnvironment:
    market: str
    bullets: List[str] = field(default_factory=list)


@dataclass
class ActionLine:
    symbol: str
    name: str
    action: str
    reason: str


@dataclass
class PremarketReport:
    market: str
    environment: MarketEnvironment
    watchlist_diff: WatchlistDiff
    top_candidates: List[Candidate] = field(default_factory=list)
    actions: List[ActionLine] = field(default_factory=list)
    conclusion: List[str] = field(default_factory=list)


@dataclass
class MiddayMarketSummary:
    strongest: str
    weakest: str
    key_change: str


@dataclass
class MiddayReport:
    a_share: MiddayMarketSummary
    hong_kong: MiddayMarketSummary
    actions: List[ActionLine] = field(default_factory=list)
    conclusion: List[str] = field(default_factory=list)
