from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.strategy.symbols import normalize_symbol


SUPPORTED_MARKETS = ("hong_kong", "us")
LEGACY_DYNAMIC_FILENAME = "candidate_inputs.dynamic.json"
LEGACY_STATIC_FILENAME = "candidate_inputs.json"


class UnifiedCandidateProvider:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.runs_root = self.repo_root / "state" / "runs"
        # Legacy combined files retained read-only for the compatibility window.
        self.legacy_dynamic_path = self.runs_root / LEGACY_DYNAMIC_FILENAME
        self.legacy_static_path = self.runs_root / LEGACY_STATIC_FILENAME

    # ------------------------------------------------------------------
    # Path helpers (per-market layout)
    # ------------------------------------------------------------------
    def market_dynamic_path(self, market: str) -> Path:
        return self.runs_root / f"candidate_inputs.dynamic.{market}.json"

    def market_static_path(self, market: str) -> Path:
        return self.runs_root / f"candidate_inputs.static.{market}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self, market: str | None = None) -> list[dict[str, Any]]:
        target_markets = [market] if market else list(SUPPORTED_MARKETS)
        dynamic_items: list[dict[str, Any]] = []
        static_items: list[dict[str, Any]] = []

        any_market_specific_dynamic = False
        any_market_specific_static = False
        for m in target_markets:
            dyn_path = self.market_dynamic_path(m)
            sta_path = self.market_static_path(m)
            if dyn_path.exists():
                any_market_specific_dynamic = True
                dynamic_items.extend(
                    self._read_items(dyn_path, source_label="dynamic", market_filter=m)
                )
            if sta_path.exists():
                any_market_specific_static = True
                static_items.extend(
                    self._read_items(sta_path, source_label="static", market_filter=m)
                )

        # Compatibility fallback: when no per-market dynamic/static files are
        # present (fresh checkout or pre-split state), fall back to the legacy
        # combined files so downstream consumers do not break during the
        # transition window.
        if not any_market_specific_dynamic and self.legacy_dynamic_path.exists():
            dynamic_items.extend(
                self._read_items(
                    self.legacy_dynamic_path,
                    source_label="dynamic_legacy",
                )
            )
        if not any_market_specific_static and self.legacy_static_path.exists():
            static_items.extend(
                self._read_items(
                    self.legacy_static_path,
                    source_label="static_legacy",
                )
            )

        merged = self._merge_by_symbol(dynamic_items=dynamic_items, static_items=static_items)
        if market:
            return self._normalize(self._filter_market(merged, market))
        return self._normalize(merged)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_items(
        self,
        path: Path,
        *,
        source_label: str,
        market_filter: str | None = None,
    ) -> list[dict[str, Any]]:
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
        if market_filter is not None:
            items = [
                row
                for row in items
                if str(row.get("market") or "").strip().lower() == market_filter
            ]
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
