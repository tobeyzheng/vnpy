from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, pstdev

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData

from services.portfolio.risk import PortfolioExposureHelper, PortfolioRiskGuard
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.selection_store import StrategySelectionStore


@dataclass
class PortfolioPosition:
    vt_symbol: str
    qty: int
    avg_price: float
    market: str


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, PortfolioPosition] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


def parse_vt_symbol(vt_symbol: str) -> tuple[str, Exchange, str]:
    symbol, exchange = vt_symbol.split('.', 1)
    exchange = exchange.upper()
    if exchange == 'SMART':
        return symbol, Exchange.SMART, 'us'
    if exchange == 'SEHK':
        return symbol, Exchange.SEHK, 'hong_kong'
    raise ValueError(vt_symbol)


class PortfolioBacktestEngine:
    def __init__(self, *, initial_cash: float = 80000.0, max_single_position_pct: float = 0.25, max_total_positions: int = 8, max_market_exposure_pct: float = 0.85, strategy_selection_root: str | None = None):
        self.initial_cash = initial_cash
        self.max_single_position_pct = max_single_position_pct
        self.max_total_positions = max_total_positions
        self.max_market_exposure_pct = max_market_exposure_pct
        self.strategy_engine = StrategyEngine()
        self.strategy_selection_store = StrategySelectionStore(strategy_selection_root) if strategy_selection_root else None
        self.portfolio_guard = PortfolioRiskGuard(max_market_exposure_pct=max_market_exposure_pct, max_total_positions=max_total_positions)
        self.exposure_helper = PortfolioExposureHelper()
        self.db = get_database()

    def load_bars(self, vt_symbol: str, start: datetime, end: datetime) -> list[BarData]:
        symbol, exchange, _market = parse_vt_symbol(vt_symbol)
        bars = self.db.load_bar_data(symbol, exchange, Interval.DAILY, start, end)
        bars.sort(key=lambda x: x.datetime)
        return bars

    def run(self, vt_symbols: list[str], start: datetime, end: datetime) -> dict:
        series: dict[str, list[BarData]] = {s: self.load_bars(s, start, end) for s in vt_symbols}
        state = PortfolioState(cash=self.initial_cash)
        close_hist: dict[str, list[float]] = {s: [] for s in vt_symbols}

        all_dates = sorted({bar.datetime for bars in series.values() for bar in bars})
        bar_map = {(s, bar.datetime): bar for s, bars in series.items() for bar in bars}

        for dt in all_dates:
            # exit first
            for s in list(state.positions.keys()):
                bar = bar_map.get((s, dt))
                if not bar:
                    continue
                close_hist[s].append(bar.close_price)
                hist = close_hist[s]
                if len(hist) < 20:
                    continue
                fast = mean(hist[-5:])
                slow = mean(hist[-20:])
                prev = hist[-2] if len(hist) >= 2 else bar.close_price
                pnl_pct = (bar.close_price / prev - 1.0) if prev else 0.0
                trend_score = 0.8 if fast > slow else 0.35
                risk_score = max(0.0, min(1.0, abs(pnl_pct) * 8))
                should_exit = pnl_pct <= -0.08 or pnl_pct >= 0.15 or (trend_score < 0.5 and risk_score > 0.04)
                if should_exit:
                    pos = state.positions.pop(s)
                    proceeds = pos.qty * bar.close_price
                    state.cash += proceeds
                    state.trades.append({'datetime': dt.isoformat(), 'vt_symbol': s, 'side': 'SELL', 'qty': pos.qty, 'price': bar.close_price, 'cash_after': state.cash})

            # then entry scan
            total_nav = state.cash + sum(pos.qty * (bar_map.get((sym, dt)).close_price if bar_map.get((sym, dt)) else pos.avg_price) for sym, pos in state.positions.items())
            market_navs = {'us': 0.0, 'hong_kong': 0.0}
            for sym, pos in state.positions.items():
                market = parse_vt_symbol(sym)[2]
                px = bar_map.get((sym, dt)).close_price if bar_map.get((sym, dt)) else pos.avg_price
                market_navs[market] += pos.qty * px

            candidates: list[tuple[str, float, BarData, str]] = []
            for s in vt_symbols:
                if s in state.positions:
                    continue
                bar = bar_map.get((s, dt))
                if not bar:
                    continue
                close_hist[s].append(bar.close_price)
                hist = close_hist[s]
                if len(hist) < 20:
                    continue
                fast = mean(hist[-5:])
                slow = mean(hist[-20:])
                prev = hist[-2] if len(hist) >= 2 else bar.close_price
                market = parse_vt_symbol(s)[2]
                evaluation = self.strategy_engine.evaluate_bar(
                    symbol=s,
                    market=market,
                    close_price=float(bar.close_price),
                    prev_close=float(prev),
                    fast=float(fast),
                    slow=float(slow),
                    volume=float(bar.volume),
                    avg_volume=0.0,
                    near_resistance=bar.close_price >= max(hist[-5:]),
                )
                replay = self.strategy_selection_store.load_latest_before(market, s, dt) if self.strategy_selection_store else None
                strategy_selection = (replay or {}).get("strategy_selection", {})
                if strategy_selection and strategy_selection.get("strategy_id") in {"watch_only", "block_trade"}:
                    continue
                candidates.append((s, evaluation.raw_score, bar, market))

            candidates.sort(key=lambda x: x[1], reverse=True)
            for s, raw, bar, market in candidates:
                if raw < 0.55:
                    continue
                if len(state.positions) >= self.max_total_positions:
                    break
                total_nav = state.cash + sum(pos.qty * (bar_map.get((sym, dt)).close_price if bar_map.get((sym, dt)) else pos.avg_price) for sym, pos in state.positions.items())
                budget = min(total_nav * self.max_single_position_pct, state.cash)
                qty = int(budget // bar.close_price)
                if qty <= 0:
                    continue
                est_cost = qty * bar.close_price
                exposure_check = self.exposure_helper.check_market_addition(total_nav=total_nav if total_nav > 0 else state.cash, market_nav=market_navs.get(market, 0.0), add_cost=est_cost, max_market_exposure_pct=self.max_market_exposure_pct)
                if not exposure_check.allowed:
                    continue
                state.cash -= est_cost
                state.positions[s] = PortfolioPosition(vt_symbol=s, qty=qty, avg_price=bar.close_price, market=market)
                market_navs[market] += est_cost
                state.trades.append({'datetime': dt.isoformat(), 'vt_symbol': s, 'side': 'BUY', 'qty': qty, 'price': bar.close_price, 'raw_score': raw, 'cash_after': state.cash})

            nav = state.cash + sum(pos.qty * (bar_map.get((sym, dt)).close_price if bar_map.get((sym, dt)) else pos.avg_price) for sym, pos in state.positions.items())
            state.history.append({'datetime': dt.isoformat(), 'cash': state.cash, 'nav': nav, 'position_count': len(state.positions)})

        navs = [row['nav'] for row in state.history]
        returns = []
        for i in range(1, len(navs)):
            prev = navs[i-1]
            returns.append((navs[i] / prev - 1) if prev else 0.0)
        peak = navs[0] if navs else self.initial_cash
        max_dd = 0.0
        for n in navs:
            peak = max(peak, n)
            dd = (n / peak - 1) * 100 if peak else 0.0
            max_dd = min(max_dd, dd)
        end_balance = navs[-1] if navs else self.initial_cash
        total_return_pct = (end_balance / self.initial_cash - 1) * 100 if self.initial_cash else 0.0
        avg_ret = mean(returns) if returns else 0.0
        std_ret = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0.0
        return {
            'portfolio': {
                'symbols': vt_symbols,
                'start': start.date().isoformat(),
                'end': end.date().isoformat(),
                'initial_cash': self.initial_cash,
                'end_balance': end_balance,
                'total_net_pnl': end_balance - self.initial_cash,
                'total_return_pct': total_return_pct,
                'total_trade_count': len(state.trades),
                'max_ddpercent': max_dd,
                'sharpe_ratio': sharpe,
                'open_positions': {k: {'qty': v.qty, 'avg_price': v.avg_price, 'market': v.market} for k, v in state.positions.items()},
            },
            'history': state.history,
            'trades': state.trades,
        }
