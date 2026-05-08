from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExternalSelection:
    strategy_id: str
    allow_trade: bool
    confidence: float
    reason: str
    position_multiplier: float = 1.0
    risk_flags: list[str] = field(default_factory=list)
    source: str = "external"
    recorded_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ExternalSelectionProvider:
    def latest(self, market: str, symbol: str, as_of: datetime | None = None) -> ExternalSelection | None:
        raise NotImplementedError


class JsonlSelectionReplayProvider(ExternalSelectionProvider):
    """Replay structured external/KnotAgent strategy selections without calling LLM."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def latest(self, market: str, symbol: str, as_of: datetime | None = None) -> ExternalSelection | None:
        rows = self._rows(market)
        normalized = self._normalize_symbol(symbol)
        for row in reversed(rows):
            if self._normalize_symbol(str(row.get("symbol", ""))) != normalized:
                continue
            recorded_at = str(row.get("recorded_at") or row.get("time") or "")
            if as_of and recorded_at:
                try:
                    if datetime.fromisoformat(recorded_at) > as_of:
                        continue
                except ValueError:
                    pass
            payload = self._payload(row)
            if not payload:
                continue
            return ExternalSelection(
                strategy_id=str(payload.get("strategy_id") or payload.get("strategy") or "watch_only"),
                allow_trade=bool(payload.get("allow_trade", False)),
                confidence=max(0.0, min(float(payload.get("confidence", 0.0) or 0.0), 1.0)),
                reason=str(payload.get("reason") or "external selection"),
                position_multiplier=max(0.0, min(float(payload.get("position_multiplier", 1.0) or 1.0), 1.0)),
                risk_flags=[str(item) for item in payload.get("risk_flags", payload.get("risk_notes", []))],
                source=str(payload.get("source") or row.get("runtime") or "knot_agent_replay"),
                recorded_at=recorded_at,
                metadata={"raw": row},
            )
        return None

    def _rows(self, market: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        patterns = [f"strategy_selection_{market}_*.jsonl"]
        names = ["knot_agent_raw_output_us.json" if market == "us" else "knot_agent_raw_output_hk.json"]
        for pattern in patterns:
            for path in sorted(self.root.glob(pattern)):
                rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        for name in names:
            path = self.root / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                rows.extend(item for item in data.get("results", []) if isinstance(item, dict))
        return rows

    def _payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("strategy_selection", "remote_result", "parsed"):
            value = row.get(key)
            if isinstance(value, dict):
                return value
        return None

    def _normalize_symbol(self, symbol: str) -> str:
        text = symbol.upper().strip()
        for prefix in ("US.", "HK."):
            text = text.replace(prefix, "")
        for suffix in (".US", ".HK", ".SMART", ".SEHK"):
            text = text.replace(suffix, "")
        return text
