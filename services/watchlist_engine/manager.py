from __future__ import annotations

from typing import Dict, Iterable

from .models import WatchlistDiff, WatchlistItem, WatchlistState


class WatchlistManager:
    """Manage fixed + dynamic watchlist lifecycle."""

    def reconcile(
        self,
        previous: Iterable[WatchlistItem],
        current_candidates: Iterable[WatchlistItem],
    ) -> WatchlistDiff:
        prev_map: Dict[str, WatchlistItem] = {item.symbol: item for item in previous}
        curr_map: Dict[str, WatchlistItem] = {item.symbol: item for item in current_candidates}

        diff = WatchlistDiff()

        for symbol, item in curr_map.items():
            if symbol not in prev_map:
                item.state = WatchlistState.NEW
                diff.added.append(item)
                continue

            old = prev_map[symbol]
            item.resonance_days = old.resonance_days + 1
            item.weak_days = 0

            if item.resonance_days >= 3:
                item.state = WatchlistState.PROMOTED
                diff.promoted.append(item)
            else:
                item.state = WatchlistState.RETAINED
                diff.retained.append(item)

        for symbol, old in prev_map.items():
            if symbol in curr_map:
                continue

            old.weak_days += 1
            if old.weak_days >= 3:
                old.state = WatchlistState.PENDING_REMOVAL
                diff.pending_removal.append(old)
            else:
                old.state = WatchlistState.WEAKENED
                diff.weakened.append(old)

        return diff
