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


class WatchlistStatusTag(str, Enum):
    NEW_ENTRY = "新进观察"
    RESONANCE_2D = "连续共振2天"
    RESONANCE_3D_PLUS = "连续共振3天+"
    WEAKENING = "转弱观察"
    PENDING_EXIT = "拟移出重点池"
    FIXED_CORE = "固定观察"


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
    status_label: Optional[str] = None


@dataclass
class WatchlistDiff:
    added: List[WatchlistItem] = field(default_factory=list)
    retained: List[WatchlistItem] = field(default_factory=list)
    promoted: List[WatchlistItem] = field(default_factory=list)
    weakened: List[WatchlistItem] = field(default_factory=list)
    pending_removal: List[WatchlistItem] = field(default_factory=list)
    removed: List[WatchlistItem] = field(default_factory=list)
