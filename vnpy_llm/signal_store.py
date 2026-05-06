from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .base import LlmSignal, parse_datetime


class SignalStore:
    def __init__(self, signal_dir: str | Path) -> None:
        self.signal_dir = Path(signal_dir)

    def write(self, signal: LlmSignal) -> Path:
        self.signal_dir.mkdir(parents=True, exist_ok=True)
        safe_symbol = signal.symbol.replace(".", "_").replace("/", "_")
        path = self.signal_dir.joinpath(f"{safe_symbol}_{signal.as_of.date().isoformat()}.json")
        path.write_text(json.dumps(signal.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_file(self, path: str | Path) -> LlmSignal:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return LlmSignal.from_dict(data)

    def list_signals(self, symbol: str | None = None) -> list[LlmSignal]:
        if not self.signal_dir.exists():
            return []
        signals: list[LlmSignal] = []
        for path in self.signal_dir.glob("*.json"):
            try:
                signal = self.read_file(path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if symbol and signal.symbol != symbol.upper():
                continue
            signals.append(signal)
        signals.sort(key=lambda item: item.scored_at)
        return signals

    def latest(self, symbol: str, as_of: datetime | None = None) -> LlmSignal | None:
        decision_time = parse_datetime(as_of) if as_of else parse_datetime(datetime.now())
        visible = [
            signal
            for signal in self.list_signals(symbol)
            if signal.is_valid_at(decision_time)
        ]
        if not visible:
            return None
        visible.sort(key=lambda item: item.scored_at, reverse=True)
        return visible[0]
