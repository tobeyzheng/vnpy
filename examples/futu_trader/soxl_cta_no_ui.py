from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean, pstdev
from time import sleep
from zoneinfo import ZoneInfo

from vnpy.event import Event, EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, OrderType, Status
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import (
    EVENT_ACCOUNT,
    EVENT_LOG,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
)
from vnpy.trader.object import (
    AccountData,
    BarData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)
from vnpy.trader.setting import SETTINGS
from vnpy.trader.utility import round_to
from vnpy_futu import FutuGateway


SYMBOL = "SOXL"
EXCHANGE = Exchange.SMART
VT_SYMBOL = f"{SYMBOL}.{EXCHANGE.value}"
GATEWAY_NAME = "FUTU"
MARKET = "US"
ENVIRONMENT = "模拟"
INITIAL_CAPITAL = 10_000.0
NY_TZ = ZoneInfo("America/New_York")
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
BEST_PARAMS_FILE = OUTPUT_DIR / "soxl_best_params.json"
GRID_RESULTS_FILE = OUTPUT_DIR / "soxl_grid_results.csv"
EQUITY_FILE = OUTPUT_DIR / "soxl_backtest_equity.csv"
TRADES_FILE = OUTPUT_DIR / "soxl_backtest_trades.csv"


@dataclass(frozen=True)
class StrategyParams:
    fast_window: int = 10
    slow_window: int = 60
    atr_window: int = 14
    risk_pct: float = 0.02
    max_pos_pct: float = 0.8
    stop_atr: float = 2.5
    trail_atr: float = 3.5
    max_drawdown_pct: float = 0.25


@dataclass
class BacktestStats:
    final_equity: float
    total_return_pct: float
    annual_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    calmar_ratio: float
    trade_count: int
    win_rate_pct: float
    score: float


@dataclass
class TradeRecord:
    datetime: str
    side: str
    price: float
    volume: int
    commission: float
    cash: float
    position: int
    equity: float
    reason: str


@dataclass
class EquityRecord:
    datetime: str
    equity: float
    cash: float
    position: int
    close_price: float
    drawdown_pct: float


class SoxlCtaStrategy:
    """SOXL long-only CTA strategy with ATR risk controls."""

    def __init__(self, params: StrategyParams) -> None:
        self.params = params

    def get_indicator(self, bars: list[BarData], index: int) -> dict[str, float] | None:
        p = self.params
        min_index = max(p.slow_window, p.atr_window) + 1
        if index < min_index:
            return None

        closes = [bar.close_price for bar in bars]
        fast = sma(closes, p.fast_window, index)
        slow = sma(closes, p.slow_window, index)
        prev_fast = sma(closes, p.fast_window, index - 1)
        prev_slow = sma(closes, p.slow_window, index - 1)
        atr = calc_atr(bars, p.atr_window, index)
        if not all([fast, slow, prev_fast, prev_slow, atr]):
            return None

        return {
            "fast": fast,
            "slow": slow,
            "prev_fast": prev_fast,
            "prev_slow": prev_slow,
            "atr": atr,
            "close": bars[index].close_price,
        }

    def should_exit(
        self,
        bars: list[BarData],
        index: int,
        entry_price: float,
        highest_close: float,
    ) -> tuple[bool, str]:
        p = self.params
        indicator = self.get_indicator(bars, index)
        if not indicator:
            return False, "warmup"

        close_price = indicator["close"]
        atr = indicator["atr"]
        stop_price = entry_price - p.stop_atr * atr
        trail_price = highest_close - p.trail_atr * atr

        if close_price <= stop_price:
            return True, "fixed_stop"
        if close_price <= trail_price:
            return True, "trailing_stop"
        if indicator["fast"] <= indicator["slow"]:
            return True, "trend_exit"

        return False, "hold"

    def calc_target_volume(
        self,
        bars: list[BarData],
        index: int,
        price: float,
        equity: float,
        available_cash: float | None = None,
    ) -> int:
        p = self.params
        indicator = self.get_indicator(bars, index)
        if not indicator or price <= 0:
            return 0

        long_signal = (
            indicator["fast"] > indicator["slow"]
            and indicator["prev_fast"] <= indicator["prev_slow"]
            or indicator["fast"] > indicator["slow"] and indicator["close"] > indicator["slow"]
        )
        if not long_signal:
            return 0

        atr = indicator["atr"]
        risk_per_share = max(atr * p.stop_atr, price * 0.02)
        risk_volume = math.floor(equity * p.risk_pct / risk_per_share)
        value_volume = math.floor(equity * p.max_pos_pct / price)

        if available_cash is not None:
            cash_volume = math.floor(max(available_cash, 0) / price)
            value_volume = min(value_volume, cash_volume)

        return max(0, min(risk_volume, value_volume))


def sma(values: list[float], window: int, index: int) -> float:
    if window <= 0 or index + 1 < window:
        return 0
    window_values = values[index - window + 1: index + 1]
    return sum(window_values) / window


def calc_atr(bars: list[BarData], window: int, index: int) -> float:
    if window <= 0 or index < window:
        return 0

    trs: list[float] = []
    start = index - window + 1
    for i in range(start, index + 1):
        bar = bars[i]
        prev_close = bars[i - 1].close_price
        tr = max(
            bar.high_price - bar.low_price,
            abs(bar.high_price - prev_close),
            abs(bar.low_price - prev_close),
        )
        trs.append(tr)

    return sum(trs) / len(trs)


def is_us_market_time(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY_TZ)
    if now.weekday() >= 5:
        return False
    return time(9, 35) <= now.time() <= time(15, 55)


def completed_daily_bars(bars: list[BarData]) -> list[BarData]:
    if not bars:
        return []

    today = datetime.now(NY_TZ).date()
    completed = [bar for bar in bars if bar.datetime.date() < today]
    return completed or bars[:-1]


def backtest_cta(
    bars: list[BarData],
    params: StrategyParams,
    capital: float = INITIAL_CAPITAL,
    commission_rate: float = 0.0003,
    slippage_bps: float = 5,
) -> tuple[BacktestStats, list[EquityRecord], list[TradeRecord]]:
    strategy = SoxlCtaStrategy(params)
    min_index = max(params.slow_window, params.atr_window) + 1

    cash = capital
    position = 0
    entry_price = 0.0
    highest_close = 0.0
    peak_equity = capital
    trading_stopped = False
    equity_curve: list[EquityRecord] = []
    trades: list[TradeRecord] = []
    round_trips: list[float] = []
    current_trade_cost = 0.0

    for signal_index in range(min_index, len(bars) - 1):
        signal_bar = bars[signal_index]
        trade_bar = bars[signal_index + 1]
        open_price = trade_bar.open_price or trade_bar.close_price
        close_price = trade_bar.close_price
        equity_before_trade = cash + position * signal_bar.close_price
        target_volume = position
        reason = "hold"

        if position:
            highest_close = max(highest_close, signal_bar.close_price)
            exit_now, reason = strategy.should_exit(bars, signal_index, entry_price, highest_close)
            if exit_now or trading_stopped:
                target_volume = 0
        elif not trading_stopped:
            target_volume = strategy.calc_target_volume(
                bars,
                signal_index,
                open_price,
                equity_before_trade,
            )
            reason = "trend_entry" if target_volume else "flat"

        diff = target_volume - position
        if diff:
            side = "buy" if diff > 0 else "sell"
            volume = abs(diff)
            fill_price = apply_slippage(open_price, side, slippage_bps)
            turnover = fill_price * volume
            commission = turnover * commission_rate

            if diff > 0:
                affordable = math.floor(cash / (fill_price * (1 + commission_rate)))
                volume = min(volume, affordable)
                if volume <= 0:
                    diff = 0
                else:
                    turnover = fill_price * volume
                    commission = turnover * commission_rate
                    cash -= turnover + commission
                    position += volume
                    entry_price = fill_price
                    highest_close = signal_bar.close_price
                    current_trade_cost = turnover + commission
            else:
                volume = min(volume, position)
                turnover = fill_price * volume
                commission = turnover * commission_rate
                cash += turnover - commission
                position -= volume
                if current_trade_cost:
                    round_trips.append((turnover - commission - current_trade_cost) / current_trade_cost)
                current_trade_cost = 0.0
                if position == 0:
                    entry_price = 0.0
                    highest_close = 0.0

            if diff:
                equity_after_trade = cash + position * close_price
                trades.append(
                    TradeRecord(
                        datetime=trade_bar.datetime.isoformat(),
                        side=side,
                        price=round(fill_price, 4),
                        volume=volume,
                        commission=round(commission, 4),
                        cash=round(cash, 2),
                        position=position,
                        equity=round(equity_after_trade, 2),
                        reason=reason,
                    )
                )

        equity = cash + position * close_price
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0

        if drawdown >= params.max_drawdown_pct:
            trading_stopped = True

        equity_curve.append(
            EquityRecord(
                datetime=trade_bar.datetime.isoformat(),
                equity=round(equity, 2),
                cash=round(cash, 2),
                position=position,
                close_price=round(close_price, 4),
                drawdown_pct=round(drawdown * 100, 4),
            )
        )

    if position and bars:
        last_bar = bars[-1]
        side = "sell"
        fill_price = apply_slippage(last_bar.close_price, side, slippage_bps)
        turnover = fill_price * position
        commission = turnover * commission_rate
        cash += turnover - commission
        trades.append(
            TradeRecord(
                datetime=last_bar.datetime.isoformat(),
                side=side,
                price=round(fill_price, 4),
                volume=position,
                commission=round(commission, 4),
                cash=round(cash, 2),
                position=0,
                equity=round(cash, 2),
                reason="final_close",
            )
        )
        position = 0

    stats = calculate_stats(equity_curve, trades, round_trips, capital)
    return stats, equity_curve, trades


def apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    adjustment = slippage_bps / 10_000
    if side == "buy":
        return price * (1 + adjustment)
    return price * (1 - adjustment)


def calculate_stats(
    equity_curve: list[EquityRecord],
    trades: list[TradeRecord],
    round_trips: list[float],
    capital: float,
) -> BacktestStats:
    if not equity_curve:
        return BacktestStats(capital, 0, 0, 0, 0, 0, 0, 0, -999)

    final_equity = equity_curve[-1].equity
    total_return = final_equity / capital - 1
    max_drawdown = max((record.drawdown_pct for record in equity_curve), default=0) / 100

    returns: list[float] = []
    previous = capital
    for record in equity_curve:
        if previous:
            returns.append(record.equity / previous - 1)
        previous = record.equity

    if len(returns) > 2 and pstdev(returns) > 0:
        sharpe = mean(returns) / pstdev(returns) * math.sqrt(252)
    else:
        sharpe = 0.0

    days = max(len(equity_curve), 1)
    annual_return = (final_equity / capital) ** (252 / days) - 1 if final_equity > 0 else -1
    calmar = annual_return / max(max_drawdown, 0.01)
    win_rate = sum(1 for value in round_trips if value > 0) / len(round_trips) if round_trips else 0
    score = calmar + 0.2 * sharpe - max_drawdown

    return BacktestStats(
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return * 100, 4),
        annual_return_pct=round(annual_return * 100, 4),
        max_drawdown_pct=round(max_drawdown * 100, 4),
        sharpe_ratio=round(sharpe, 4),
        calmar_ratio=round(calmar, 4),
        trade_count=len(trades),
        win_rate_pct=round(win_rate * 100, 4),
        score=round(score, 6),
    )


def grid_search(
    bars: list[BarData],
    capital: float,
    commission_rate: float,
    slippage_bps: float,
) -> tuple[StrategyParams, BacktestStats, list[dict]]:
    fast_windows = [5, 10, 20]
    slow_windows = [30, 60, 120]
    atr_windows = [10, 14, 20]
    risk_pcts = [0.01, 0.02, 0.03]
    max_pos_pcts = [0.5, 0.75, 0.95]
    stop_atrs = [1.5, 2.5, 3.5]
    trail_atrs = [2.5, 3.5, 4.5]
    max_drawdowns = [0.15, 0.25, 0.35]

    rows: list[dict] = []
    best_params: StrategyParams | None = None
    best_stats: BacktestStats | None = None

    for fast in fast_windows:
        for slow in slow_windows:
            if fast >= slow:
                continue
            for atr in atr_windows:
                for risk_pct in risk_pcts:
                    for max_pos_pct in max_pos_pcts:
                        for stop_atr in stop_atrs:
                            for trail_atr in trail_atrs:
                                for max_dd in max_drawdowns:
                                    params = StrategyParams(
                                        fast_window=fast,
                                        slow_window=slow,
                                        atr_window=atr,
                                        risk_pct=risk_pct,
                                        max_pos_pct=max_pos_pct,
                                        stop_atr=stop_atr,
                                        trail_atr=trail_atr,
                                        max_drawdown_pct=max_dd,
                                    )
                                    stats, _, _ = backtest_cta(
                                        bars,
                                        params,
                                        capital=capital,
                                        commission_rate=commission_rate,
                                        slippage_bps=slippage_bps,
                                    )
                                    row = {
                                        **{f"param_{key}": value for key, value in asdict(params).items()},
                                        **{f"stat_{key}": value for key, value in asdict(stats).items()},
                                    }
                                    rows.append(row)

                                    if best_stats is None or stats.score > best_stats.score:
                                        best_params = params
                                        best_stats = stats

    if best_params is None or best_stats is None:
        raise RuntimeError("参数网格没有产生有效结果，请检查历史数据长度。")

    rows.sort(key=lambda item: item["stat_score"], reverse=True)
    return best_params, best_stats, rows



def create_main_engine(args: argparse.Namespace) -> MainEngine:
    SETTINGS["log.active"] = True
    SETTINGS["log.console"] = True
    SETTINGS["log.file"] = True

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(FutuGateway)

    event_engine.register(EVENT_LOG, print_log_event)
    main_engine.connect(
        {
            "密码": args.password,
            "地址": args.host,
            "端口": args.port,
            "市场": MARKET,
            "环境": ENVIRONMENT,
        },
        GATEWAY_NAME,
    )
    sleep(args.connect_wait)
    return main_engine


def print_log_event(event: Event) -> None:
    log = event.data
    dt = getattr(log, "time", datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    gateway_name = getattr(log, "gateway_name", "")
    msg = getattr(log, "msg", str(log))
    print(f"[{dt}] [{gateway_name}] {msg}")


def fetch_history(main_engine: MainEngine, days: int) -> list[BarData]:
    end = datetime.now()
    start = end - timedelta(days=days)
    req = HistoryRequest(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        interval=Interval.DAILY,
        start=start,
        end=end,
    )
    bars = main_engine.query_history(req, GATEWAY_NAME)
    bars = [bar for bar in bars if bar.open_price > 0 and bar.close_price > 0]
    bars.sort(key=lambda bar: bar.datetime)
    if not bars:
        raise RuntimeError("未获取到 SOXL 日线历史数据，请检查 OpenD 行情权限和网络。")
    return bars


def save_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def save_dataclass_rows(path: Path, rows: list) -> None:
    save_rows(path, [asdict(row) for row in rows])


def save_best_params(params: StrategyParams, stats: BacktestStats) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": VT_SYMBOL,
        "capital": INITIAL_CAPITAL,
        "params": asdict(params),
        "stats": asdict(stats),
        "updated_at": datetime.now().isoformat(),
    }
    BEST_PARAMS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_best_params() -> StrategyParams:
    payload = json.loads(BEST_PARAMS_FILE.read_text(encoding="utf-8"))
    return StrategyParams(**payload["params"])


def run_optimize(args: argparse.Namespace) -> None:
    main_engine = create_main_engine(args)
    try:
        bars = fetch_history(main_engine, args.days)
        best_params, best_stats, rows = grid_search(
            bars,
            capital=args.capital,
            commission_rate=args.commission_rate,
            slippage_bps=args.slippage_bps,
        )
        save_rows(GRID_RESULTS_FILE, rows)
        save_best_params(best_params, best_stats)
        stats, equity, trades = backtest_cta(
            bars,
            best_params,
            capital=args.capital,
            commission_rate=args.commission_rate,
            slippage_bps=args.slippage_bps,
        )
        save_dataclass_rows(EQUITY_FILE, equity)
        save_dataclass_rows(TRADES_FILE, trades)
        print_result("参数优化完成", best_params, stats)
        print(f"优化明细: {GRID_RESULTS_FILE}")
        print(f"最佳参数: {BEST_PARAMS_FILE}")
        print(f"回测权益: {EQUITY_FILE}")
        print(f"交易记录: {TRADES_FILE}")
    finally:
        main_engine.close()


def run_backtest(args: argparse.Namespace) -> None:
    main_engine = create_main_engine(args)
    try:
        bars = fetch_history(main_engine, args.days)
        params = load_best_params() if BEST_PARAMS_FILE.exists() else StrategyParams()
        stats, equity, trades = backtest_cta(
            bars,
            params,
            capital=args.capital,
            commission_rate=args.commission_rate,
            slippage_bps=args.slippage_bps,
        )
        save_dataclass_rows(EQUITY_FILE, equity)
        save_dataclass_rows(TRADES_FILE, trades)
        print_result("回测完成", params, stats)
        print(f"回测权益: {EQUITY_FILE}")
        print(f"交易记录: {TRADES_FILE}")
    finally:
        main_engine.close()


def print_result(title: str, params: StrategyParams, stats: BacktestStats) -> None:
    print(f"\n{title}: {VT_SYMBOL}")
    print("参数:", json.dumps(asdict(params), ensure_ascii=False))
    print("统计:", json.dumps(asdict(stats), ensure_ascii=False))


class HeadlessLiveTrader:
    def __init__(self, main_engine: MainEngine, args: argparse.Namespace, params: StrategyParams) -> None:
        self.main_engine = main_engine
        self.args = args
        self.params = params
        self.strategy = SoxlCtaStrategy(params)
        self.position = 0
        self.avg_price = 0.0
        self.account_balance = args.capital
        self.available_cash = args.capital
        self.active_orders: dict[str, datetime] = {}
        self.last_eval_at: datetime | None = None
        self.last_history_refresh_at: datetime | None = None
        self.history_bars: list[BarData] = []

        event_engine = main_engine.event_engine
        event_engine.register(EVENT_TICK + VT_SYMBOL, self.on_tick)
        event_engine.register(EVENT_ORDER, self.on_order)
        event_engine.register(EVENT_TRADE, self.on_trade)
        event_engine.register(EVENT_POSITION, self.on_position)
        event_engine.register(EVENT_ACCOUNT, self.on_account)

    def start(self) -> None:
        self.refresh_history(force=True)
        self.main_engine.subscribe(SubscribeRequest(SYMBOL, EXCHANGE), GATEWAY_NAME)
        print(f"无界面模拟交易已启动: {VT_SYMBOL}, 参数={asdict(self.params)}")

        while True:
            sleep(3)
            self.cancel_timeout_orders()
            if self.args.force_trade:
                self.evaluate(None)

    def refresh_history(self, force: bool = False) -> None:
        now = datetime.now()
        if (
            not force
            and self.last_history_refresh_at
            and now - self.last_history_refresh_at < timedelta(minutes=self.args.history_refresh_minutes)
        ):
            return

        bars = fetch_history(self.main_engine, self.args.days)
        self.history_bars = completed_daily_bars(bars)
        self.last_history_refresh_at = now
        print(f"历史数据刷新完成: {len(self.history_bars)} 根日线")

    def on_tick(self, event: Event) -> None:
        tick: TickData = event.data
        self.evaluate(tick)

    def on_order(self, event: Event) -> None:
        order: OrderData = event.data
        if order.vt_symbol != VT_SYMBOL:
            return

        if order.status in {Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED}:
            self.active_orders.setdefault(order.vt_orderid, datetime.now())
        else:
            self.active_orders.pop(order.vt_orderid, None)
        print(f"委托更新: {order.vt_orderid} {order.direction} {order.price} {order.volume} {order.status.value}")

    def on_trade(self, event: Event) -> None:
        trade: TradeData = event.data
        if trade.vt_symbol != VT_SYMBOL:
            return

        volume = int(trade.volume)
        if trade.direction == Direction.LONG:
            self.position += volume
        elif trade.direction == Direction.SHORT:
            self.position = max(0, self.position - volume)
        print(f"成交更新: {trade.direction} {trade.price} x {trade.volume}, 当前持仓={self.position}")

    def on_position(self, event: Event) -> None:
        position: PositionData = event.data
        if position.vt_symbol != VT_SYMBOL:
            return
        self.position = int(position.volume)
        self.avg_price = float(position.price or self.avg_price)
        print(f"持仓更新: {VT_SYMBOL} {self.position} 股, 成本={self.avg_price}")

    def on_account(self, event: Event) -> None:
        account: AccountData = event.data
        self.account_balance = float(account.balance or self.account_balance)
        self.available_cash = float(account.available or self.available_cash)
        print(f"账户更新: balance={self.account_balance:.2f}, available={self.available_cash:.2f}")

    def evaluate(self, tick: TickData | None) -> None:
        now = datetime.now(NY_TZ)
        if not self.args.force_trade and not is_us_market_time(now):
            return

        if self.last_eval_at and datetime.now() - self.last_eval_at < timedelta(minutes=self.args.rebalance_minutes):
            return

        self.refresh_history()
        if not self.history_bars:
            return

        if self.active_orders:
            print(f"存在活动委托，跳过本轮调仓: {list(self.active_orders)}")
            return

        price = self.get_trade_price(tick)
        if price <= 0:
            return

        last_index = len(self.history_bars) - 1
        equity = max(self.account_balance, self.available_cash + self.position * price, self.args.capital)
        available_cash = max(self.available_cash * (1 - self.args.cash_buffer_pct), 0)

        if self.position:
            highest_close = max(bar.close_price for bar in self.history_bars[-self.params.slow_window:])
            exit_now, reason = self.strategy.should_exit(
                self.history_bars,
                last_index,
                self.avg_price or price,
                highest_close,
            )
            target = 0 if exit_now else self.position
        else:
            reason = "trend_entry"
            target = self.strategy.calc_target_volume(
                self.history_bars,
                last_index,
                price,
                equity,
                available_cash,
            )

        target = max(0, int(target))
        diff = target - self.position
        self.last_eval_at = datetime.now()

        print(
            f"调仓检查: price={price:.4f}, pos={self.position}, target={target}, "
            f"diff={diff}, reason={reason}, equity={equity:.2f}"
        )

        if diff > 0:
            order_price = self.get_buy_price(tick, price)
            self.send_order(Direction.LONG, diff, order_price)
        elif diff < 0:
            order_price = self.get_sell_price(tick, price)
            self.send_order(Direction.SHORT, min(abs(diff), self.position), order_price)

    def get_trade_price(self, tick: TickData | None) -> float:
        if not tick:
            return self.history_bars[-1].close_price if self.history_bars else 0
        return tick.last_price or tick.ask_price_1 or tick.bid_price_1 or 0

    def get_buy_price(self, tick: TickData | None, fallback: float) -> float:
        price = tick.ask_price_1 if tick and tick.ask_price_1 else fallback
        return round_to(price * (1 + self.args.limit_price_add), 0.01)

    def get_sell_price(self, tick: TickData | None, fallback: float) -> float:
        price = tick.bid_price_1 if tick and tick.bid_price_1 else fallback
        return round_to(price * (1 - self.args.limit_price_add), 0.01)

    def send_order(self, direction: Direction, volume: int, price: float) -> None:
        if volume <= 0 or price <= 0:
            return

        req = OrderRequest(
            symbol=SYMBOL,
            exchange=EXCHANGE,
            direction=direction,
            offset=Offset.NONE,
            type=OrderType.LIMIT,
            volume=volume,
            price=price,
            reference="soxl_cta_no_ui",
        )
        vt_orderid = self.main_engine.send_order(req, GATEWAY_NAME)
        if vt_orderid:
            self.active_orders[vt_orderid] = datetime.now()
            print(f"发出委托: {direction.value} {volume} 股 @ {price}, vt_orderid={vt_orderid}")

    def cancel_timeout_orders(self) -> None:
        now = datetime.now()
        if not self.active_orders:
            return

        all_orders = self.main_engine.get_all_active_orders()
        order_map = {order.vt_orderid: order for order in all_orders if order.vt_symbol == VT_SYMBOL}
        for vt_orderid, created_at in list(self.active_orders.items()):
            if now - created_at < timedelta(seconds=self.args.order_timeout):
                continue

            order = order_map.get(vt_orderid)
            if not order:
                self.active_orders.pop(vt_orderid, None)
                continue

            self.main_engine.cancel_order(order.create_cancel_request(), GATEWAY_NAME)
            self.active_orders.pop(vt_orderid, None)
            print(f"撤销超时委托: {vt_orderid}")


def run_live(args: argparse.Namespace) -> None:
    main_engine = create_main_engine(args)
    try:
        if args.optimize or not BEST_PARAMS_FILE.exists():
            bars = fetch_history(main_engine, args.days)
            best_params, best_stats, rows = grid_search(
                bars,
                capital=args.capital,
                commission_rate=args.commission_rate,
                slippage_bps=args.slippage_bps,
            )
            save_rows(GRID_RESULTS_FILE, rows)
            save_best_params(best_params, best_stats)
            print_result("实盘前参数优化完成", best_params, best_stats)
            params = best_params
        else:
            params = load_best_params()

        trader = HeadlessLiveTrader(main_engine, args, params)
        trader.start()
    except KeyboardInterrupt:
        print("收到退出信号，正在关闭。")
    finally:
        main_engine.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SOXL 富途模拟无界面 CTA 交易/回测/优化脚本")
    parser.add_argument("--mode", choices=["optimize", "backtest", "live"], default="optimize")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--password", default="")
    parser.add_argument("--connect-wait", type=int, default=5)
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--optimize", action="store_true", help="live 模式启动前重新网格搜索参数")
    parser.add_argument("--rebalance-minutes", type=int, default=30)
    parser.add_argument("--history-refresh-minutes", type=int, default=60)
    parser.add_argument("--order-timeout", type=int, default=60)
    parser.add_argument("--limit-price-add", type=float, default=0.001)
    parser.add_argument("--cash-buffer-pct", type=float, default=0.02)
    parser.add_argument("--force-trade", action="store_true", help="忽略美股常规交易时段限制，仅用于测试")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"标的: {VT_SYMBOL}（SOXL，3倍做多半导体ETF）")
    print(f"富途环境: {ENVIRONMENT}, 市场: {MARKET}, 初始回测资金: {args.capital:.2f} USD")

    if args.mode == "optimize":
        run_optimize(args)
    elif args.mode == "backtest":
        run_backtest(args)
    elif args.mode == "live":
        run_live(args)


if __name__ == "__main__":
    main()
