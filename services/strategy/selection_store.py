from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class StrategySelectionStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, market: str, payload: dict[str, Any]) -> Path:
        now = datetime.now()
        path = self.root / f"strategy_selection_{market}_{now.strftime('%Y%m%d')}.jsonl"
        row = {"recorded_at": now.isoformat(timespec="seconds"), **payload}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self._serialize(row), ensure_ascii=False, default=str) + "\n")
        return path

    def load_latest(self, market: str, symbol: str) -> dict[str, Any] | None:
        return self.load_latest_before(market, symbol)

    def load_latest_before(self, market: str, symbol: str, as_of: datetime | None = None) -> dict[str, Any] | None:
        paths = sorted(self.root.glob(f"strategy_selection_{market}_*.jsonl"), reverse=True)
        for path in paths:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            for row in reversed(rows):
                if row.get("symbol") != symbol:
                    continue
                if as_of is None:
                    return row
                recorded_at = row.get("recorded_at")
                if not recorded_at:
                    continue
                try:
                    if datetime.fromisoformat(recorded_at) <= as_of:
                        return row
                except ValueError:
                    continue
        return None

    def _serialize(self, value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, dict):
            return {k: self._serialize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize(v) for v in value]
        return value
