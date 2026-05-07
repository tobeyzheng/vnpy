from __future__ import annotations

from pathlib import Path
from typing import List

from services.common.config_loader import load_yaml

from .models import WatchlistItem, WatchlistState


def load_fixed_watchlist(path: str | Path) -> List[WatchlistItem]:
    data = load_yaml(path)
    market = data["market"]
    result = []
    for row in data.get("fixed_watchlist", []):
        result.append(
            WatchlistItem(
                symbol=row["symbol"],
                name=row["name"],
                market=market,
                sector=row.get("sector"),
                state=WatchlistState.FIXED,
                tags=["fixed"],
            )
        )
    return result
