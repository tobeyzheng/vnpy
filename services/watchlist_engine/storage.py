from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

from .models import WatchlistItem, WatchlistState


class WatchlistStateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, market: str) -> Path:
        return self.root / f"{market}.json"

    def load(self, market: str) -> List[WatchlistItem]:
        path = self._path(market)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        items: List[WatchlistItem] = []
        for row in data:
            row = dict(row)
            row["state"] = WatchlistState(row.get("state", WatchlistState.NEW.value))
            items.append(WatchlistItem(**row))
        return items

    def save(self, market: str, items: Iterable[WatchlistItem]) -> Path:
        path = self._path(market)
        payload = [asdict(item) for item in items]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
