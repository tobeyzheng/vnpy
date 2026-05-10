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
        dynamic_items = self._read_items(self.dynamic_path, source_label="dynamic")
        static_items = self._read_items(self.static_path, source_label="static")
        merged = self._merge_by_symbol(dynamic_items=dynamic_items, static_items=static_items)
        if market:
            return self._normalize(self._filter_market(merged, market))
        return self._normalize(merged)

    def _read_items(self, path: Path, *, source_label: str) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw_items = data.get("items", [])
        elif isinstance(data, list):
            raw_items = data
        else:
            raw_items = []
        items = [dict(row) for row in raw_items if isinstance(row, dict)]
        for row in items:
            row.setdefault("candidate_source", source_label)
        return items

    def _merge_by_symbol(self, *, dynamic_items: list[dict[str, Any]], static_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        ordered_keys: list[tuple[str, str]] = []

        for row in static_items:
            key = self._row_key(row)
            if key is None:
                continue
            if key not in merged:
                ordered_keys.append(key)
            merged[key] = dict(row)

        for row in dynamic_items:
            key = self._row_key(row)
            if key is None:
                continue
            if key not in merged:
                ordered_keys.append(key)
                merged[key] = dict(row)
                continue
            merged[key] = self._merge_row(base=merged[key], override=row)

        return [merged[key] for key in ordered_keys if key in merged]

    def _merge_row(self, *, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        merged.update(override)
        merged["candidate_source"] = "dynamic_override"
        merged["merged_from_sources"] = sorted(
            {
                str(base.get("candidate_source") or "static"),
                str(override.get("candidate_source") or "dynamic"),
            }
        )
        return merged

    def _row_key(self, row: dict[str, Any]) -> tuple[str, str] | None:
        market = str(row.get("market") or "").strip()
        symbol = normalize_symbol(row.get("symbol", ""), market)
        if not market or not symbol:
            return None
        return market, symbol

    def _filter_market(self, items: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
        return [row for row in items if row.get("market") == market]

    def _normalize(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for row in items:
            item = dict(row)
            item["symbol"] = normalize_symbol(item.get("symbol", ""), item.get("market"))
            normalized.append(item)
        return normalized
