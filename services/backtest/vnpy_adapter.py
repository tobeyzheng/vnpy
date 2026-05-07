from __future__ import annotations

from datetime import datetime

from vnpy_ctastrategy.backtesting import BacktestingEngine
from vnpy.trader.constant import Interval

from services.backtest.vnpy_strategy_bridge import AdaptiveBacktestStrategy


class VnpyBacktestAdapter:
    def run(self, *, vt_symbol: str, interval: str, start: datetime, end: datetime, rate: float, slippage: float, size: int, pricetick: float, capital: float) -> tuple[dict, object]:
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol=vt_symbol,
            interval=Interval.DAILY if interval == '1d' else Interval.MINUTE,
            start=start,
            end=end,
            rate=rate,
            slippage=slippage,
            size=size,
            pricetick=pricetick,
            capital=int(capital),
        )
        engine.add_strategy(AdaptiveBacktestStrategy, {})
        engine.load_data()
        engine.run_backtesting()
        df = engine.calculate_result()
        if df is None or getattr(df, 'empty', False):
            return {
                'status': 'no_data',
                'message': 'backtest ran but no historical data/trades were available for the requested symbol and period',
                'vt_symbol': vt_symbol,
                'start': start.isoformat(),
                'end': end.isoformat(),
            }, engine
        stats = engine.calculate_statistics(df=df, output=False)
        return stats, engine
