from __future__ import annotations

from vnpy.alpha.strategy.template import AlphaStrategy
from vnpy.trader.object import BarData, TradeData

from services.strategy.timing import EntryTimingEngine, ExitTimingEngine


class AdaptiveAlphaStrategy(AlphaStrategy):
    author = 'OpenClaw'

    def on_init(self) -> None:
        self.entry_engine = EntryTimingEngine()
        self.exit_engine = ExitTimingEngine()
        self.history: dict[str, list[float]] = {vt_symbol: [] for vt_symbol in self.vt_symbols}

    def on_bars(self, bars: dict[str, BarData]) -> None:
        for vt_symbol, bar in bars.items():
            closes = self.history.setdefault(vt_symbol, [])
            closes.append(bar.close_price)
            if len(closes) < 20:
                continue
            fast = sum(closes[-5:]) / 5
            slow = sum(closes[-20:]) / 20
            trend_score = 0.75 if fast > slow else 0.45
            rsi_proxy = 55 if fast > slow else 45
            pos = self.get_pos(vt_symbol)
            if pos == 0:
                d = self.entry_engine.decide(
                    trend_score=trend_score,
                    rsi=rsi_proxy,
                    has_event_catalyst=False,
                    near_resistance=False,
                    moving_average_bullish=fast > slow,
                )
                if d.action in ('trend_following', 'pullback_buy', 'breakout_momentum'):
                    self.buy(vt_symbol, bar.close_price, 1)
            else:
                prev = closes[-2] if len(closes) >= 2 else bar.close_price
                pnl_pct = (bar.close_price - prev) / prev if prev else 0.0
                d = self.exit_engine.decide(
                    pnl_pct=pnl_pct,
                    rsi=65,
                    trend_score=trend_score,
                    risk_score=0.4,
                )
                if d.action in ('stop_loss', 'take_profit', 'trim_or_exit', 'reduce_risk'):
                    self.sell(vt_symbol, bar.close_price, abs(pos))

    def on_trade(self, trade: TradeData) -> None:
        pass
