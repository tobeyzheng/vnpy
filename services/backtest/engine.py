from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BacktestResult:
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trade_count: int = 0
    notes: list[str] = field(default_factory=list)


class BacktestEngine:
    def run(self, *, strategy_name: str, symbols: list[str], start: str, end: str) -> BacktestResult:
        return BacktestResult(
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            trade_count=0,
            notes=[
                f'backtest scaffold only: strategy={strategy_name}',
                f'symbols={symbols}',
                f'period={start}~{end}',
            ],
        )
