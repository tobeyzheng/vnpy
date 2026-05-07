from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.strategy.strategy_selector import ALLOWED_STRATEGIES, StrategySelection


class ExternalStrategySelectionStore:
    def __init__(self, repo_root: Path):
        self.runs = repo_root / "state" / "runs"

    def load_latest(self, market: str, symbol: str) -> StrategySelection | None:
        for row in reversed(self._load_rows(market)):
            if self._normalize_symbol(str(row.get("symbol", ""))) != self._normalize_symbol(symbol):
                continue
            data = self._selection_payload(row)
            if not data:
                continue
            strategy_id = str(data.get("strategy_id") or data.get("strategy") or "watch_only")
            if strategy_id not in ALLOWED_STRATEGIES:
                strategy_id = "watch_only"
            confidence = max(0.0, min(float(data.get("confidence", 0.0) or 0.0), 1.0))
            allow_trade = bool(data.get("allow_trade", strategy_id not in {"watch_only", "block_trade"}))
            return StrategySelection(
                strategy_id=strategy_id,
                allow_trade=allow_trade,
                confidence=confidence,
                reason=str(data.get("reason", "external strategy selection")),
                source=str(row.get("runtime") or data.get("source") or "knot_agent"),
                risk_flags=[str(item) for item in data.get("risk_flags", data.get("risk_notes", []))],
                metadata={"raw": row},
            )
        return None

    def _load_rows(self, market: str) -> list[dict[str, Any]]:
        names = [
            "knot_agent_raw_output_hk.json" if market == "hong_kong" else "knot_agent_raw_output_us.json",
            "knot_agent_intraday_decision_hk.json" if market == "hong_kong" else "knot_agent_intraday_decision_us.json",
        ]
        rows: list[dict[str, Any]] = []
        for name in names:
            path = self.runs / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = data.get("results", []) if isinstance(data, dict) else []
            rows.extend(dict(item) for item in items if isinstance(item, dict))
        return rows

    def _selection_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("remote_result", "parsed", "strategy_selection"):
            value = row.get(key)
            if isinstance(value, dict):
                return value
        return None

    def _normalize_symbol(self, symbol: str) -> str:
        text = symbol.upper()
        for prefix in ("HK.", "US."):
            text = text.replace(prefix, "")
        return text
