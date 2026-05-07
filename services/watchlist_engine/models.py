from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class WatchlistState(str, Enum):
    FIXED = "fixed"
    NEW = "new"
    RETAINED = "retained"
    PROMOTED = "promoted"
    WEAKENED = "weakened"
    PENDING_REMOVAL = "pending_removal"
    REMOVED = "removed"


@dataclass
class WatchlistItem:
    symbol: str
    name: str
    market: str
    sector: Optional[str] = None
    state: WatchlistState = WatchlistState.NEW
    note: str = ""
    resonance_days: int = 0
    weak_days: int = 0
    confidence: Optional[int] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class WatchlistDiff:
    added: List[WatchlistItem] = field(default_factory=list)
    retained: List[WatchlistItem] = field(default_factory=list)
    promoted: List[WatchlistItem] = field(default_factory=list)
    weakened: List[WatchlistItem] = field(default_factory=list)
    pending_removal: List[WatchlistItem] = field(default_factory=list)
    removed: List[WatchlistItem] = field(default_factory=list)
