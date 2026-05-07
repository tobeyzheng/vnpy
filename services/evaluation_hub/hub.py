from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import EvaluationBundle, EvaluationSignal


class EvaluationHub:
    def merge(self, signals: Iterable[EvaluationSignal]) -> list[EvaluationBundle]:
        grouped: dict[tuple[str, str], list[EvaluationSignal]] = defaultdict(list)
        for signal in signals:
            grouped[(signal.symbol, signal.market)].append(signal)
        return [EvaluationBundle(symbol=symbol, market=market, signals=items) for (symbol, market), items in grouped.items()]
