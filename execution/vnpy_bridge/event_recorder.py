from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from vnpy.trader.event import EVENT_ACCOUNT, EVENT_ORDER, EVENT_POSITION, EVENT_TRADE


class VnpyEventRecorder:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = []

    def register(self, event_engine: Any) -> None:
        event_engine.register(EVENT_ORDER, self.on_order)
        event_engine.register(EVENT_TRADE, self.on_trade)
        event_engine.register(EVENT_POSITION, self.on_position)
        event_engine.register(EVENT_ACCOUNT, self.on_account)

    def on_order(self, event: Any) -> None:
        self._append("order", event.data)

    def on_trade(self, event: Any) -> None:
        self._append("trade", event.data)

    def on_position(self, event: Any) -> None:
        self._append("position", event.data)

    def on_account(self, event: Any) -> None:
        self._append("account", event.data)

    def _append(self, event_type: str, data: Any) -> None:
        payload = {
            "event_type": event_type,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "data": self._serialize(data),
        }
        self.events.append(payload)
        path = self.root / f"vnpy_gateway_events_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _serialize(self, data: Any) -> dict[str, Any]:
        if is_dataclass(data):
            return asdict(data)
        if hasattr(data, "__dict__"):
            return dict(data.__dict__)
        if isinstance(data, dict):
            return data
        return {"repr": repr(data)}
