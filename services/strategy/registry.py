from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    min_raw_score: float = 0.55
    tradable_actions: tuple[str, ...] = ("trend_following", "pullback_buy", "breakout_momentum")
    metadata: dict[str, Any] = field(default_factory=dict)


class StrategyRegistry:
    def __init__(self):
        self._items: dict[str, StrategyDefinition] = {}
        self.register(StrategyDefinition(strategy_id="raw_score_timing_v1", name="Raw Score + Timing v1"))

    def register(self, definition: StrategyDefinition) -> None:
        self._items[definition.strategy_id] = definition

    def get(self, strategy_id: str = "raw_score_timing_v1") -> StrategyDefinition:
        if strategy_id not in self._items:
            raise KeyError(f"unknown strategy_id: {strategy_id}")
        return self._items[strategy_id]

    def list(self) -> list[StrategyDefinition]:
        return list(self._items.values())
