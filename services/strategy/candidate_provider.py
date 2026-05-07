from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.strategy.symbols import normalize_symbol


class UnifiedCandidateProvider:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.dynamic_path = repo_root / "state" / "runs" / "candidate_inputs.dynamic.json"
        self.static_path = repo_root / "state" / "runs" / "candidate_inputs.json"

    def load(self, market: str | None = None) -> list[dict[str, Any]]:
        dynamic_items = self._read_items(self.dynamic_path)
        static_items = self._read_items(self.static_path)
        if market:
            market_dynamic = self._filter_market(dynamic_items, market)
            if market_dynamic:
                return self._normalize(market_dynamic)
            return self._normalize(self._filter_market(static_items, market))

        dynamic_markets = {row.get("market") for row in dynamic_items if isinstance(row, dict) and row.get("market")}
        merged = list(dynamic_items)
        merged.extend(row for row in static_items if row.get("market") not in dynamic_markets)
        return self._normalize(merged)

    def _read_items(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw_items = data.get("items", [])
        elif isinstance(data, list):
            raw_items = data
        else:
            raw_items = []
        return [dict(row) for row in raw_items if isinstance(row, dict)]

    def _filter_market(self, items: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
        return [row for row in items if row.get("market") == market]

    def _normalize(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for row in items:
            item = dict(row)
            item["symbol"] = normalize_symbol(item.get("symbol", ""), item.get("market"))
            normalized.append(item)
        return normalized
