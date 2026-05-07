from __future__ import annotations

from typing import Iterable, List

from services.evaluation_hub.models import EvaluationSignal

ALLOWED_STRATEGIES = {"trend_following", "breakout_momentum", "pullback_buy", "watch_only", "block_trade"}


class StrategySelectionEvaluationAdapter:
    """Convert Knot/LLM strategy-selection JSON rows into EvaluationSignal rows."""

    def from_rows(self, rows: Iterable[dict]) -> List[EvaluationSignal]:
        signals: List[EvaluationSignal] = []
        for row in rows:
            strategy = str(row.get("strategy_id") or row.get("strategy") or "watch_only")
            if strategy not in ALLOWED_STRATEGIES:
                strategy = "watch_only"
            confidence = float(row.get("confidence", 0.0) or 0.0)
            allow_trade = bool(row.get("allow_trade", strategy not in {"watch_only", "block_trade"}))
            score = confidence if allow_trade else min(confidence, 0.49)
            signals.append(
                EvaluationSignal(
                    symbol=str(row.get("symbol", "")),
                    market=str(row.get("market", "")),
                    source=str(row.get("source", "strategy_selection")),
                    dimension="strategy_selection",
                    score=score,
                    confidence=confidence,
                    summary=str(row.get("reason", "")),
                    risks=[str(item) for item in row.get("risk_notes", row.get("risk_flags", []))],
                    action_bias="long" if allow_trade else "neutral",
                    meta={"strategy_id": strategy, "allow_trade": allow_trade, "raw": row},
                )
            )
        return signals
