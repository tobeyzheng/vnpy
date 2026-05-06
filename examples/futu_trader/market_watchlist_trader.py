from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean
from time import sleep
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT.joinpath(".vntrader").mkdir(exist_ok=True)
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy.event import Event, EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, OrderType
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_ACCOUNT, EVENT_LOG, EVENT_ORDER, EVENT_POSITION, EVENT_TICK
from vnpy.trader.object import AccountData, HistoryRequest, OrderData, OrderRequest, PositionData, SubscribeRequest, TickData
from vnpy.trader.setting import SETTINGS
from vnpy.trader.utility import round_to
from vnpy_futu import FutuGateway
from vnpy_llm.strategy_selector import StrategyDecision, StrategySelector

SETTINGS["log.active"] = True
SETTINGS["log.console"] = True
SETTINGS["log.file"] = True

GATEWAY_NAME = "FUTU"
CN_TZ = ZoneInfo("Asia/Shanghai")
HK_TZ = ZoneInfo("Asia/Hong_Kong")
US_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SymbolSetting:
    symbol: str
    exchange: Exchange
    vt_symbol: str
    name: str
    asset_type: str
    max_position_pct: float
    min_volume: int


@dataclass
class RuntimeState:
    balance: float = 0.0
    available: float = 0.0
    positions: dict[str, PositionData] | None = None
    ticks: dict[str, TickData] | None = None
    active_orders: dict[str, OrderData] | None = None
    orders: dict[str, OrderData] | None = None
    session_order_count: int = 0

    def __post_init__(self) -> None:
        self.positions = {}
        self.ticks = {}
        self.active_orders = {}
        self.orders = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立市场观察池交易器：默认只观察/打印信号，--trade 才发送模拟/实盘委托")
    parser.add_argument("--watchlist", required=True)
    parser.add_argument("--market", required=True, choices=["HK", "CN", "US"])
    parser.add_argument("--env", default="模拟", choices=["模拟", "真实"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--password", default="")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--usable-cash", type=float, default=0, help="本策略允许使用的硬资金上限；>0 时覆盖 capital 作为预算基数")
    parser.add_argument("--daily-loss-limit", type=float, default=0, help="本日最大允许亏损，包含估算交易费用；>0 时触发硬风控")
    parser.add_argument("--max-trades", type=int, default=0, help="本轮运行最大订单次数；0 表示不限制")
    parser.add_argument("--estimated-fee-per-order", type=float, default=1.0, help="每笔订单估算费用，用于亏损约束")
    parser.add_argument("--override-watchlist-limits", action="store_true", help="忽略观察池内单标仓位上限，改用命令行风控参数")
    parser.add_argument("--until-us-close", action="store_true", help="美股运行到当天常规交易收盘后退出")
    parser.add_argument("--max-total-position-pct", type=float, default=0.3)
    parser.add_argument("--single-position-pct", type=float, default=0.08)
    parser.add_argument("--fast-window", type=int, default=10)
    parser.add_argument("--slow-window", type=int, default=60)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--stop-atr", type=float, default=2.5)
    parser.add_argument("--trail-atr", type=float, default=3.5)
    parser.add_argument("--rebalance-minutes", type=int, default=30)
    parser.add_argument("--connect-wait", type=int, default=8)
    parser.add_argument("--top-n", type=int, default=6)
    parser.add_argument("--include-vt-symbols", default="", help="逗号分隔的额外标的，追加到top-n之后，如 MSFT.SMART,INTC.SMART")
    parser.add_argument("--trade", action="store_true", help="发送委托；默认 dry-run 不下单")
    parser.add_argument("--order-confirm-wait", type=float, default=3.0, help="下单后等待订单回报确认的秒数")
    parser.add_argument("--enable-strategy-selector", action="store_true", help="启用LLM/启发式策略选择层；默认保持原CTA逻辑")
    parser.add_argument("--strategy-selector-config", default="examples/futu_trader/config/llm_trading_setting.example.json")
    parser.add_argument("--strategy-selector-web-search", action="store_true")
    parser.add_argument("--strategy-selector-min-confidence", type=float, default=0.55)
    parser.add_argument("--strategy-selector-cache-minutes", type=int, default=30)
    parser.add_argument("--once", action="store_true", help="只评估一次后退出")
    return parser


def parse_vt_symbol(vt_symbol: str) -> tuple[str, Exchange]:
    symbol, exchange = vt_symbol.split(".", 1)
    return symbol, Exchange(exchange)


def load_symbols(path: Path, top_n: int, single_position_pct: float, override_watchlist_limits: bool = False, include_vt_symbols: str = "") -> list[SymbolSetting]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("selected_targets", data.get("items", []))
    rules = data.get("risk_rules", {}) if isinstance(data, dict) else {}
    if override_watchlist_limits:
        single_stock_limit = single_position_pct
        leveraged_limit = single_position_pct
    else:
        single_stock_limit = min(float(rules.get("single_stock_max_position_pct", single_position_pct)), single_position_pct)
        leveraged_limit = min(float(rules.get("leveraged_etf_max_position_pct", single_stock_limit)), single_stock_limit)

    filtered = [item for item in items if item.get("trade_filter") in {"allow_long", "watch_only"}]
    filtered.sort(key=lambda item: (item.get("trade_filter") == "allow_long", float(item.get("final_score", 0)), float(item.get("confidence", 0))), reverse=True)
    selected = filtered[:top_n]
    include_set = {item.strip().upper() for item in include_vt_symbols.split(",") if item.strip()}
    existing = {str(item.get("vt_symbol", "")).upper() for item in selected}
    for item in filtered:
        vt_symbol = str(item.get("vt_symbol", "")).upper()
        if vt_symbol in include_set and vt_symbol not in existing:
            selected.append(item)
            existing.add(vt_symbol)
    for vt_symbol in sorted(include_set - existing):
        selected.append({
            "symbol": vt_symbol.split(".", 1)[0],
            "vt_symbol": vt_symbol,
            "name": vt_symbol,
            "asset_type": "Stock",
            "trade_filter": "watch_only",
            "position_multiplier": single_position_pct,
            "final_score": 0,
            "confidence": 0,
        })
        existing.add(vt_symbol)
    settings: list[SymbolSetting] = []
    for item in selected:
        vt_symbol = str(item["vt_symbol"])
        symbol, exchange = parse_vt_symbol(vt_symbol)
        asset_type = str(item.get("asset_type", "Stock"))
        item_limit = leveraged_limit if "LEVERAGED" in asset_type.upper() else single_stock_limit
        max_pct = min(float(item.get("position_multiplier", item_limit)) or item_limit, item_limit)
        if item.get("min_volume"):
            min_volume = int(item["min_volume"])
        elif exchange in {Exchange.SSE, Exchange.SZSE, Exchange.SEHK}:
            min_volume = 100
        else:
            min_volume = 1
        settings.append(
            SymbolSetting(
                symbol=symbol,
                exchange=exchange,
                vt_symbol=vt_symbol,
                name=str(item.get("name", "")),
                asset_type=asset_type,
                max_position_pct=max_pct,
                min_volume=min_volume,
            )
        )
    return settings


def sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return mean(values[-window:])


def atr(bars, window: int) -> float:
    if len(bars) <= window:
        return 0.0
    trs: list[float] = []
    for i in range(len(bars) - window, len(bars)):
        bar = bars[i]
        prev = bars[i - 1]
        trs.append(max(bar.high_price - bar.low_price, abs(bar.high_price - prev.close_price), abs(bar.low_price - prev.close_price)))
    return mean(trs)


def rsi(values: list[float], window: int = 14) -> float:
    if len(values) <= window:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(len(values) - window, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + mean(gains) / avg_loss)


def market_is_open(market: str, now: datetime | None = None) -> bool:
    if market == "HK":
        dt = now or datetime.now(HK_TZ)
        return dt.weekday() < 5 and (time(9, 35) <= dt.time() <= time(11, 55) or time(13, 5) <= dt.time() <= time(15, 55))
    if market == "CN":
        dt = now or datetime.now(CN_TZ)
        return dt.weekday() < 5 and (time(9, 35) <= dt.time() <= time(11, 25) or time(13, 5) <= dt.time() <= time(14, 55))
    dt = now or datetime.now(US_TZ)
    return dt.weekday() < 5 and time(9, 35) <= dt.time() <= time(15, 55)


class MarketWatchlistTrader:
    def __init__(self, args: argparse.Namespace, symbols: list[SymbolSetting]) -> None:
        self.args = args
        self.symbols = symbols
        self.state = RuntimeState()
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.gateway = self.main_engine.add_gateway(FutuGateway)
        self.last_eval_at: datetime | None = None
        self.history: dict[str, list] = {}
        self.watched_vt_symbols = {symbol.vt_symbol for symbol in symbols}
        self.strategy_selector = StrategySelector.from_config_path(
            args.strategy_selector_config,
            str(PROJECT_ROOT),
            enable_llm=args.enable_strategy_selector,
            enable_web_search=args.strategy_selector_web_search,
            min_confidence=args.strategy_selector_min_confidence,
            cache_minutes=args.strategy_selector_cache_minutes,
        )

        self.event_engine.register(EVENT_LOG, self.on_log)
        self.event_engine.register(EVENT_ACCOUNT, self.on_account)
        self.event_engine.register(EVENT_POSITION, self.on_position)
        self.event_engine.register(EVENT_TICK, self.on_tick)
        self.event_engine.register(EVENT_ORDER, self.on_order)

    def on_log(self, event: Event) -> None:
        log = event.data
        print(f"LOG {log.gateway_name}: {log.msg}")

    def on_account(self, event: Event) -> None:
        account: AccountData = event.data
        self.state.balance = account.balance or self.state.balance
        self.state.available = max(account.balance - account.frozen, 0)

    def on_position(self, event: Event) -> None:
        pos: PositionData = event.data
        self.state.positions[pos.vt_symbol] = pos

    def on_tick(self, event: Event) -> None:
        tick: TickData = event.data
        self.state.ticks[tick.vt_symbol] = tick

    def on_order(self, event: Event) -> None:
        order: OrderData = event.data
        self.state.orders[order.vt_orderid] = order
        if order.is_active():
            self.state.active_orders[order.vt_orderid] = order
        else:
            self.state.active_orders.pop(order.vt_orderid, None)

    def connect(self) -> None:
        self.main_engine.connect(
            {"密码": self.args.password, "地址": self.args.host, "端口": self.args.port, "市场": self.args.market, "环境": self.args.env},
            GATEWAY_NAME,
        )
        sleep(self.args.connect_wait)
        for setting in self.symbols:
            self.main_engine.subscribe(SubscribeRequest(setting.symbol, setting.exchange), GATEWAY_NAME)
        self.refresh_history()

    def refresh_history(self) -> None:
        end = datetime.now(CN_TZ)
        start = end - timedelta(days=365)
        gateway = self.main_engine.get_gateway(GATEWAY_NAME)
        if not gateway:
            return
        for setting in self.symbols:
            req = HistoryRequest(setting.symbol, setting.exchange, start=start, end=end, interval=Interval.DAILY)
            bars = gateway.query_history(req)
            if bars:
                self.history[setting.vt_symbol] = bars

    def budget_base(self) -> float:
        return self.args.usable_cash if self.args.usable_cash > 0 else self.args.capital

    def order_count(self) -> int:
        return self.state.session_order_count

    def estimated_fees(self) -> float:
        return self.order_count() * max(self.args.estimated_fee_per_order, 0)

    def position_value(self) -> float:
        total = 0.0
        for vt_symbol, pos in (self.state.positions or {}).items():
            if vt_symbol not in self.watched_vt_symbols:
                continue
            tick = (self.state.ticks or {}).get(vt_symbol)
            price = tick.last_price if tick else pos.price
            total += max(pos.volume, 0) * max(price or 0, 0)
        return total

    def active_buy_value(self) -> float:
        total = 0.0
        for order in (self.state.active_orders or {}).values():
            if order.vt_symbol not in self.watched_vt_symbols:
                continue
            if order.direction is Direction.LONG:
                total += max(order.volume - order.traded, 0) * max(order.price, 0)
        return total

    def used_budget(self) -> float:
        return self.position_value() + self.active_buy_value() + self.estimated_fees()

    def remaining_budget(self) -> float:
        hard_budget = self.budget_base() * max(self.args.max_total_position_pct, 0)
        return max(hard_budget - self.used_budget(), 0)

    def current_loss_with_fees(self) -> float:
        pnl = sum(pos.pnl for vt_symbol, pos in (self.state.positions or {}).items() if vt_symbol in self.watched_vt_symbols)
        return max(-pnl, 0) + self.estimated_fees()

    def risk_status(self) -> dict[str, float | int | bool]:
        loss = self.current_loss_with_fees()
        return {
            "budget_base": self.budget_base(),
            "max_total_budget": self.budget_base() * max(self.args.max_total_position_pct, 0),
            "used_budget": self.used_budget(),
            "remaining_budget": self.remaining_budget(),
            "daily_loss_with_fees": loss,
            "daily_loss_limit": self.args.daily_loss_limit,
            "order_count": self.order_count(),
            "max_trades": self.args.max_trades,
            "budget_ok": self.remaining_budget() > 0,
            "loss_ok": not self.args.daily_loss_limit or loss < self.args.daily_loss_limit,
            "trade_count_ok": not self.args.max_trades or self.order_count() < self.args.max_trades,
        }

    def can_open_order(self, required_cash: float) -> tuple[bool, str]:
        status = self.risk_status()
        if self.args.max_trades and self.order_count() >= self.args.max_trades:
            return False, "max_trades_reached"
        if self.args.daily_loss_limit and self.current_loss_with_fees() >= self.args.daily_loss_limit:
            return False, "daily_loss_limit_reached"
        if required_cash + max(self.args.estimated_fee_per_order, 0) > self.remaining_budget():
            return False, "usable_cash_limit_reached"
        if not status["budget_ok"]:
            return False, "no_remaining_budget"
        return True, "ok"

    def base_position_volume(self, setting: SymbolSetting, price: float, atr_value: float, risk_fraction: float = 1.0) -> int:
        equity = self.budget_base()
        value_limit = equity * min(setting.max_position_pct, self.args.single_position_pct)
        value_limit = min(value_limit, self.remaining_budget())
        risk_limit = min(equity * 0.01, self.args.daily_loss_limit * 0.25 if self.args.daily_loss_limit else equity * 0.01)
        risk_limit *= max(min(risk_fraction, 1.0), 0.0)
        risk_per_share = max(atr_value * self.args.stop_atr, price * 0.02)
        volume = min(math.floor(value_limit / price), math.floor(risk_limit / risk_per_share))
        volume = math.floor(volume / setting.min_volume) * setting.min_volume
        return max(volume, 0)

    def build_strategy_features(self, setting: SymbolSetting, price: float, bars: list) -> dict[str, float | str]:
        closes = [bar.close_price for bar in bars]
        volumes = [bar.volume for bar in bars if bar.volume >= 0]
        fast = sma(closes, self.args.fast_window)
        slow = sma(closes, self.args.slow_window)
        sma20 = sma(closes, 20)
        atr_value = atr(bars, self.args.atr_window)
        high20 = max([bar.high_price for bar in bars[-20:]], default=0.0)
        low20 = min([bar.low_price for bar in bars[-20:]], default=0.0)
        avg_volume20 = mean(volumes[-20:]) if len(volumes) >= 20 else 0.0
        latest_volume = volumes[-1] if volumes else 0.0
        return_20d = closes[-1] / closes[-21] - 1 if len(closes) > 20 and closes[-21] else 0.0
        trend_score = 0.0
        trend_score += 0.25 if fast and price > fast else 0
        trend_score += 0.25 if slow and price > slow else 0
        trend_score += 0.25 if fast and slow and fast > slow else 0
        trend_score += 0.25 if return_20d > 0 else 0
        risk_score = min((atr_value / price * 4 if price and atr_value else 0.5) + (0.15 if rsi(closes) > 75 else 0), 1.0)
        return {
            "symbol": setting.symbol,
            "vt_symbol": setting.vt_symbol,
            "asset_type": setting.asset_type,
            "price": price,
            "fast_sma": fast,
            "slow_sma": slow,
            "sma20": sma20,
            "atr": atr_value,
            "atr_pct": atr_value / price if price and atr_value else 0.0,
            "rsi14": rsi(closes),
            "return_20d": return_20d,
            "volume_ratio": latest_volume / avg_volume20 if avg_volume20 else 1.0,
            "resistance_20d": high20,
            "support_20d": low20,
            "trend_score": trend_score,
            "risk_score": risk_score,
            "capital_score": 0.5,
            "current_position": 0,
            "remaining_budget": self.remaining_budget(),
            "daily_loss_with_fees": self.current_loss_with_fees(),
            "order_count": self.order_count(),
        }

    def target_volume(self, setting: SymbolSetting, price: float, bars: list) -> tuple[int, str]:
        closes = [bar.close_price for bar in bars]
        fast = sma(closes, self.args.fast_window)
        slow = sma(closes, self.args.slow_window)
        atr_value = atr(bars, self.args.atr_window)
        if not fast or not slow or not atr_value:
            return 0, "warmup"

        if not self.args.enable_strategy_selector:
            if price <= slow or fast <= slow:
                return 0, "trend_not_ready"
            return self.base_position_volume(setting, price, atr_value), "trend_entry"

        features = self.build_strategy_features(setting, price, bars)
        decision: StrategyDecision = self.strategy_selector.select(features)
        print(f"STRATEGY_SELECT {setting.vt_symbol}: {decision.to_dict()}")

        if not decision.allow_trade or decision.strategy in {"watch_only", "block_trade"}:
            return 0, f"{decision.strategy}:{decision.reason}"
        if decision.strategy == "trend_following":
            if price <= slow or fast <= slow:
                return 0, "trend_following_not_ready"
            return self.base_position_volume(setting, price, atr_value), "trend_following"
        if decision.strategy == "breakout_momentum":
            high20 = float(features.get("resistance_20d", 0.0) or 0.0)
            volume_ratio = float(features.get("volume_ratio", 1.0) or 1.0)
            if not high20 or price < high20 * 0.985 or volume_ratio < 1.2:
                return 0, "breakout_not_confirmed"
            return self.base_position_volume(setting, price, atr_value, 0.7), "breakout_momentum"
        if decision.strategy == "pullback_buy":
            sma20_value = float(features.get("sma20", 0.0) or 0.0)
            rsi_value = float(features.get("rsi14", 50.0) or 50.0)
            if not sma20_value or price > sma20_value * 1.03 or rsi_value > 68:
                return 0, "waiting_pullback"
            return self.base_position_volume(setting, price, atr_value, 0.5), "pullback_buy"
        return 0, f"unsupported_strategy:{decision.strategy}"

    def confirm_order(self, vt_orderid: str) -> None:
        if not vt_orderid:
            print("POST_TRADE_CHECK failed: empty_vt_orderid")
            return
        sleep(max(self.args.order_confirm_wait, 0))
        order = self.main_engine.get_order(vt_orderid)
        if not order:
            print(f"POST_TRADE_CHECK {vt_orderid}: order_not_found")
            return
        submitted = bool(order.status)
        traded = order.traded >= order.volume and order.volume > 0
        print(
            f"POST_TRADE_CHECK {vt_orderid}: submitted={submitted} traded={traded} "
            f"status={order.status.value} volume={order.volume} traded_volume={order.traded} price={order.price}"
        )

    def evaluate_symbol(self, setting: SymbolSetting) -> None:
        tick = self.state.ticks.get(setting.vt_symbol)
        bars = self.history.get(setting.vt_symbol, [])
        price = (tick.last_price or tick.ask_price_1 or tick.bid_price_1) if tick else (bars[-1].close_price if bars else 0)
        if not price or not bars:
            print(f"SKIP {setting.vt_symbol}: no price/history")
            return
        current_pos = int((self.state.positions.get(setting.vt_symbol).volume if self.state.positions and self.state.positions.get(setting.vt_symbol) else 0) or 0)
        target, reason = self.target_volume(setting, price, bars)
        diff = target - current_pos
        print(f"SIGNAL {setting.vt_symbol} price={price:.3f} pos={current_pos} target={target} diff={diff} reason={reason}")
        if not self.args.trade or diff == 0:
            return
        active_same_symbol = [order for order in (self.state.active_orders or {}).values() if order.vt_symbol == setting.vt_symbol]
        if active_same_symbol:
            print(f"BLOCK {setting.vt_symbol}: active_order_exists")
            return
        direction = Direction.LONG if diff > 0 else Direction.SHORT
        volume = abs(diff)
        order_price = round_to(price * (1.003 if diff > 0 else 0.997), 0.01)
        if direction is Direction.LONG:
            affordable_volume = math.floor(max(self.remaining_budget() - max(self.args.estimated_fee_per_order, 0), 0) / max(order_price, 1e-9))
            affordable_volume = math.floor(affordable_volume / setting.min_volume) * setting.min_volume
            volume = min(volume, affordable_volume)
            ok, reason_text = self.can_open_order(volume * order_price)
            if not ok or volume <= 0:
                print(f"BLOCK {setting.vt_symbol}: {reason_text}, risk={self.risk_status()}")
                return
        elif self.args.max_trades and self.order_count() >= self.args.max_trades:
            print(f"BLOCK {setting.vt_symbol}: max_trades_reached, risk={self.risk_status()}")
            return
        required_cash = volume * order_price if direction is Direction.LONG else 0
        print(
            f"PRE_TRADE_CHECK {setting.vt_symbol}: direction={direction.value} volume={volume} "
            f"price={order_price} required_cash={required_cash:.2f} risk={self.risk_status()}"
        )
        req = OrderRequest(setting.symbol, setting.exchange, direction, OrderType.LIMIT, volume, order_price)
        vt_orderid = self.main_engine.send_order(req, GATEWAY_NAME)
        if vt_orderid:
            self.state.session_order_count += 1
        print(f"ORDER {setting.vt_symbol} {direction.value} volume={volume} price={order_price} vt_orderid={vt_orderid}")
        self.confirm_order(vt_orderid)

    def evaluate(self) -> None:
        if not market_is_open(self.args.market):
            print(f"{self.args.market} market closed; dry evaluation uses latest history/tick only")
        self.refresh_history()
        print(f"RISK_CHECK {self.risk_status()}")
        for setting in self.symbols:
            self.evaluate_symbol(setting)
        self.last_eval_at = datetime.now(CN_TZ)

    def should_stop_at_close(self) -> bool:
        if not self.args.until_us_close or self.args.market != "US":
            return False
        now = datetime.now(US_TZ)
        return now.weekday() < 5 and now.time() >= time(16, 0)

    def run(self) -> None:
        print(f"启动 {self.args.market} 独立观察池交易器，trade={self.args.trade} symbols={[s.vt_symbol for s in self.symbols]}")
        self.connect()
        print(f"INITIAL_RISK_CHECK {self.risk_status()}")
        try:
            while True:
                if not self.last_eval_at or datetime.now(CN_TZ) - self.last_eval_at >= timedelta(minutes=self.args.rebalance_minutes):
                    self.evaluate()
                if self.args.once or self.should_stop_at_close():
                    break
                sleep(10)
        finally:
            self.main_engine.close()


def main() -> None:
    args = build_parser().parse_args()
    symbols = load_symbols(Path(args.watchlist), args.top_n, args.single_position_pct, args.override_watchlist_limits, args.include_vt_symbols)
    if not symbols:
        raise SystemExit("观察池为空或没有 allow_long/watch_only 标的")
    MarketWatchlistTrader(args, symbols).run()


if __name__ == "__main__":
    main()
