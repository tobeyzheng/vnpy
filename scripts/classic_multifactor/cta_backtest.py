from __future__ import annotations

from datetime import datetime
from typing import Any

from vnpy.trader.constant import Interval
from vnpy_ctastrategy.backtesting import BacktestingEngine

from scripts.classic_multifactor.strategy import ClassicMultiFactorCtaStrategy


class ClassicCtaBacktestRunner:
    """Official vn.py CTA BacktestingEngine runner."""

    def run(
        self,
        *,
        vt_symbol: str,
        interval: str,
        start: datetime,

        end: datetime,
        capital: float,
        rate: float,
        slippage: float,
        size: int,
        pricetick: float,
        setting: dict[str, Any],
    ) -> tuple[dict[str, Any], Any]:
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol=vt_symbol,
            interval=Interval.MINUTE if interval == "1m" else Interval.DAILY,

            start=start,
            end=end,
            rate=rate,
            slippage=slippage,
            size=size,
            pricetick=pricetick,
            capital=int(capital),
        )
        engine.add_strategy(ClassicMultiFactorCtaStrategy, setting)
        engine.load_data()
        engine.run_backtesting()
        df = engine.calculate_result()
        if df is None or getattr(df, "empty", False):
            return {
                "status": "no_data",
                "message": "vn.py CTA backtest ran but no result was produced",
                "vt_symbol": vt_symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }, engine
        stats = engine.calculate_statistics(df=df, output=False)
        stats = {"status": "ok", "engine": "vnpy_cta_backtesting", **stats}
        try:
            stats["trade_count_runtime"] = len(engine.get_all_trades())
        except Exception:
            pass
        return stats, engine
