from __future__ import annotations

from statistics import mean

from vnpy_ctastrategy import CtaTemplate, StopOrder
from vnpy.trader.object import BarData, TickData, TradeData, OrderData

from services.sim_account.models import SimAccount, SimPosition
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.risk_guard import RiskGuard
from services.strategy.timing import EntryTimingEngine, ExitTimingEngine


class AdaptiveBacktestStrategy(CtaTemplate):
    author = 'OpenClaw'

    fast_window = 5
    slow_window = 20
    fixed_size = 1
    raw_score_threshold = 0.55
    max_single_position_pct = 0.25
    max_positions = 5

    parameters = ['fast_window', 'slow_window', 'fixed_size', 'raw_score_threshold', 'max_single_position_pct', 'max_positions']
    variables = ['raw_score_value', 'last_entry_action', 'last_exit_action']

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.entry_engine = EntryTimingEngine()
        self.exit_engine = ExitTimingEngine()
        self.raw_score_engine = RawScoreEngine()
        self.risk_guard = RiskGuard(max_single_position_pct=self.max_single_position_pct, max_positions=self.max_positions)
        self.closes: list[float] = []
        self.volumes: list[float] = []
        self.raw_score_value: float = 0.0
        self.last_entry_action: str = ''
        self.last_exit_action: str = ''

    def on_init(self):
        self.load_bar(self.slow_window)

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def on_tick(self, tick: TickData):
        pass

    def _build_account(self, bar: BarData) -> SimAccount:
        nav = float(self.cta_engine.capital + getattr(self.cta_engine, 'net_pnl', 0.0))
        positions = []
        if self.pos:
            positions.append(SimPosition(symbol=bar.symbol, qty=int(abs(self.pos)), avg_price=float(bar.close_price), market_value=float(abs(self.pos) * bar.close_price), unrealized_pnl=0.0))
        return SimAccount(cash=max(nav, 0.0), nav=max(nav, 0.0), positions=positions)

    def on_bar(self, bar: BarData):
        self.closes.append(bar.close_price)
        self.volumes.append(bar.volume)
        if len(self.closes) < self.slow_window:
            return

        fast = mean(self.closes[-self.fast_window:])
        slow = mean(self.closes[-self.slow_window:])
        prev = self.closes[-2] if len(self.closes) >= 2 else bar.close_price
        momentum = (bar.close_price / prev - 1.0) if prev else 0.0
        avg_vol = mean(self.volumes[-min(len(self.volumes), 20):]) if self.volumes else 0.0
        flow_ratio = (bar.volume / avg_vol) if avg_vol else 1.0

        trend_score = 0.8 if fast > slow else 0.35
        momentum_score = min(1.0, max(0.0, 0.5 + momentum * 10))
        flow_score = min(1.0, max(0.0, flow_ratio / 2))
        quality_score = 0.55 if fast > slow else 0.45
        event_score = 0.5
        risk_penalty = min(1.0, max(0.0, abs(momentum) * 8))

        self.raw_score_value = self.raw_score_engine.score(
            RawScoreFeatures(
                trend_score=trend_score,
                momentum_score=momentum_score,
                flow_score=flow_score,
                quality_score=quality_score,
                event_score=event_score,
                risk_penalty=risk_penalty,
                legacy_score=None,
            )
        )

        rsi_proxy = 55 if fast > slow else 45
        moving_average_bullish = fast > slow
        near_resistance = bar.close_price >= max(self.closes[-self.fast_window:])

        if self.pos == 0:
            decision = self.entry_engine.decide(
                trend_score=trend_score,
                rsi=rsi_proxy,
                has_event_catalyst=False,
                near_resistance=near_resistance,
                moving_average_bullish=moving_average_bullish,
            )
            self.last_entry_action = decision.action
            account = self._build_account(bar)
            est_cost = float(bar.close_price * self.fixed_size)
            risk = self.risk_guard.can_open(account, symbol=bar.symbol, est_cost=est_cost)
            if self.raw_score_value >= self.raw_score_threshold and risk.allowed and decision.action in ('trend_following', 'pullback_buy', 'breakout_momentum'):
                self.buy(bar.close_price, self.fixed_size)
        else:
            pnl_pct = (bar.close_price / prev - 1.0) if prev else 0.0
            risk_score = max(risk_penalty, 1 - self.raw_score_value)
            decision = self.exit_engine.decide(
                pnl_pct=pnl_pct,
                rsi=65,
                trend_score=trend_score,
                risk_score=risk_score,
            )
            self.last_exit_action = decision.action
            if decision.action in ('stop_loss', 'take_profit', 'trim_or_exit', 'reduce_risk'):
                self.sell(bar.close_price, abs(self.pos))

    def on_trade(self, trade: TradeData):
        pass

    def on_order(self, order: OrderData):
        pass

    def on_stop_order(self, stop_order: StopOrder):
        pass
