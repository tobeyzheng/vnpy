from __future__ import annotations

from datetime import datetime

from vnpy.alpha.strategy.backtesting import BacktestingEngine
from vnpy.trader.constant import Interval

from services.backtest.vnpy_alpha_bridge import AdaptiveAlphaStrategy


class VnpyAlphaBacktestAdapter:
    def run(self, *, vt_symbols: list[str], interval: str, start: datetime, end: datetime, rates: dict[str, float] | None = None, slippages: dict[str, float] | None = None, sizes: dict[str, float] | None = None, priceticks: dict[str, float] | None = None, capitals: dict[str, float] | None = None):
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbols=vt_symbols,
            interval=Interval.DAILY if interval == '1d' else Interval.MINUTE,
            start=start,
            end=end,
            rates=rates or {},
            slippages=slippages or {},
            sizes=sizes or {},
            priceticks=priceticks or {},
            capitals=capitals or {},
        )
        engine.add_strategy(AdaptiveAlphaStrategy, {})
        engine.load_data()
        engine.run_backtesting()
        stats = engine.calculate_statistics(output=False)
        return stats, engine
