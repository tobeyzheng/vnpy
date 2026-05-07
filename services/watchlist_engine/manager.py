from __future__ import annotations

from typing import Dict, Iterable

from .models import WatchlistDiff, WatchlistItem, WatchlistState, WatchlistStatusTag


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
            if item.state == WatchlistState.FIXED:
                item.status_label = WatchlistStatusTag.FIXED_CORE.value

            if symbol not in prev_map:
                if item.state != WatchlistState.FIXED:
                    item.state = WatchlistState.NEW
                    item.status_label = WatchlistStatusTag.NEW_ENTRY.value
                diff.added.append(item)
                continue

            old = prev_map[symbol]
            item.resonance_days = old.resonance_days + 1
            item.weak_days = 0

            if item.state == WatchlistState.FIXED:
                item.status_label = WatchlistStatusTag.FIXED_CORE.value
                diff.retained.append(item)
            elif item.resonance_days >= 3:
                item.state = WatchlistState.PROMOTED
                item.status_label = WatchlistStatusTag.RESONANCE_3D_PLUS.value
                diff.promoted.append(item)
            elif item.resonance_days >= 2:
                item.state = WatchlistState.RETAINED
                item.status_label = WatchlistStatusTag.RESONANCE_2D.value
                diff.retained.append(item)
            else:
                item.state = WatchlistState.RETAINED
                diff.retained.append(item)

        for symbol, old in prev_map.items():
            if symbol in curr_map:
                continue
            if old.state == WatchlistState.FIXED:
                old.state = WatchlistState.WEAKENED
                old.status_label = WatchlistStatusTag.WEAKENING.value
                old.weak_days += 1
                diff.weakened.append(old)
                continue

            old.weak_days += 1
            if old.weak_days >= 3:
                old.state = WatchlistState.PENDING_REMOVAL
                old.status_label = WatchlistStatusTag.PENDING_EXIT.value
                diff.pending_removal.append(old)
            else:
                old.state = WatchlistState.WEAKENED
                old.status_label = WatchlistStatusTag.WEAKENING.value
                diff.weakened.append(old)

        return diff
