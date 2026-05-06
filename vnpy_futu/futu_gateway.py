from copy import copy
from datetime import datetime
from threading import Thread
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

from futu import (
    KLType,
    ModifyOrderOp,
    OpenFutureTradeContext,
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderBookHandlerBase,
    OrderStatus,
    OrderType as FutuOrderType,
    RET_ERROR,
    RET_OK,
    StockQuoteHandlerBase,
    TradeDealHandlerBase,
    TradeOrderHandlerBase,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

from vnpy.event import EVENT_TIMER, Event, EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, OrderType, Product, Status
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

EXCHANGE_VT2FUTU: dict[Exchange, str] = {
    Exchange.SMART: "US",
    Exchange.NYSE: "US",
    Exchange.NASDAQ: "US",
    Exchange.AMEX: "US",
    Exchange.SEHK: "HK",
    Exchange.HKFE: "HK_FUTURE",
    Exchange.SSE: "SH",
    Exchange.SZSE: "SZ",
}
EXCHANGE_FUTU2VT: dict[str, Exchange] = {
    "US": Exchange.SMART,
    "HK": Exchange.SEHK,
    "HK_FUTURE": Exchange.HKFE,
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
}

PRODUCT_VT2FUTU: dict[Product, str] = {
    Product.EQUITY: "STOCK",
    Product.INDEX: "IDX",
    Product.ETF: "ETF",
    Product.WARRANT: "WARRANT",
    Product.BOND: "BOND",
    Product.FUTURES: "FUTURE",
}

DIRECTION_VT2FUTU: dict[Direction, TrdSide] = {
    Direction.LONG: TrdSide.BUY,
    Direction.SHORT: TrdSide.SELL,
}
DIRECTION_FUTU2VT: dict[Any, tuple[Direction, Offset]] = {
    TrdSide.BUY: (Direction.LONG, Offset.NONE),
    TrdSide.SELL: (Direction.SHORT, Offset.NONE),
}
if hasattr(TrdSide, "BUY_BACK"):
    DIRECTION_FUTU2VT[getattr(TrdSide, "BUY_BACK")] = (Direction.LONG, Offset.CLOSE)
if hasattr(TrdSide, "SELL_SHORT"):
    DIRECTION_FUTU2VT[getattr(TrdSide, "SELL_SHORT")] = (Direction.SHORT, Offset.OPEN)

STATUS_FUTU2VT: dict[Any, Status] = {}
for name, status in {
    "NONE": Status.SUBMITTING,
    "UNSUBMITTED": Status.SUBMITTING,
    "WAITING_SUBMIT": Status.SUBMITTING,
    "SUBMITTING": Status.SUBMITTING,
    "SUBMITTED": Status.NOTTRADED,
    "FILLED_PART": Status.PARTTRADED,
    "FILLED_ALL": Status.ALLTRADED,
    "CANCELLED_PART": Status.CANCELLED,
    "CANCELLED_ALL": Status.CANCELLED,
    "FAILED": Status.REJECTED,
    "DISABLED": Status.CANCELLED,
    "DELETED": Status.CANCELLED,
}.items():
    if hasattr(OrderStatus, name):
        STATUS_FUTU2VT[getattr(OrderStatus, name)] = status

TRADE_MARKET_MAP: dict[str, Any] = {
    "HK": TrdMarket.HK,
    "US": TrdMarket.US,
}
if hasattr(TrdMarket, "CN"):
    TRADE_MARKET_MAP["CN"] = getattr(TrdMarket, "CN")

ENV_MAP: dict[str, TrdEnv] = {
    "真实": TrdEnv.REAL,
    "REAL": TrdEnv.REAL,
    str(TrdEnv.REAL): TrdEnv.REAL,
    "模拟": TrdEnv.SIMULATE,
    "SIMULATE": TrdEnv.SIMULATE,
    str(TrdEnv.SIMULATE): TrdEnv.SIMULATE,
}

INTERVAL_VT2FUTU: dict[Interval, Any] = {}
for interval, name in {
    Interval.MINUTE: "K_1M",
    Interval.HOUR: "K_60M",
    Interval.DAILY: "K_DAY",
    Interval.WEEKLY: "K_WEEK",
}.items():
    if hasattr(KLType, name):
        INTERVAL_VT2FUTU[interval] = getattr(KLType, name)


def convert_symbol_futu2vt(code: str) -> tuple[str, Exchange]:
    """Convert Futu symbol to VeighNa symbol."""
    code_list: list[str] = code.split(".")
    futu_exchange: str = code_list[0]
    symbol: str = ".".join(code_list[1:])
    exchange: Exchange = EXCHANGE_FUTU2VT.get(futu_exchange, Exchange.GLOBAL)
    return symbol, exchange


def convert_symbol_vt2futu(symbol: str, exchange: Exchange) -> str:
    """Convert VeighNa symbol to Futu symbol."""
    futu_exchange: str = EXCHANGE_VT2FUTU[exchange]
    return f"{futu_exchange}.{symbol}"


def generate_datetime(value: Any) -> datetime:
    """Generate timezone-aware datetime from Futu time value."""
    if isinstance(value, datetime):
        dt: datetime = value
    else:
        text: str = str(value)
        if "." in text:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")
        elif " " in text:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(text, "%Y-%m-%d")

    if dt.tzinfo:
        return dt.astimezone(CHINA_TZ)
    return dt.replace(tzinfo=CHINA_TZ)


def get_value(data: Any, *names: str, default: Any = None) -> Any:
    """Read value from pandas Series/dict with fallback field names."""
    for name in names:
        try:
            value = data.get(name, None)
        except AttributeError:
            value = data[name] if name in data else None
        if value is not None:
            return value
    return default


def to_float(value: Any, default: float = 0) -> float:
    """Convert value to float safely."""
    try:
        if value is None or value == "N/A":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class FutuGateway(BaseGateway):
    """Gateway for Futu OpenD."""

    default_name: str = "FUTU"

    default_setting: dict[str, Any] = {
        "密码": "",
        "地址": "127.0.0.1",
        "端口": 11111,
        "市场": ["HK", "US", "CN", "HK_FUTURE"],
        "环境": ["模拟", "真实"],
    }

    exchanges: list[Exchange] = [
        Exchange.SEHK,
        Exchange.SMART,
        Exchange.NYSE,
        Exchange.NASDAQ,
        Exchange.AMEX,
        Exchange.HKFE,
        Exchange.SSE,
        Exchange.SZSE,
    ]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

        self.quote_ctx: OpenQuoteContext | None = None
        self.trade_ctx: OpenSecTradeContext | OpenFutureTradeContext | None = None

        self.host: str = "127.0.0.1"
        self.port: int = 11111
        self.market: str = "HK"
        self.password: str = ""
        self.env: TrdEnv = TrdEnv.SIMULATE

        self.ticks: dict[str, TickData] = {}
        self.contracts: dict[str, ContractData] = {}
        self.trades: set[str] = set()

        self.thread: Thread | None = None
        self.count: int = 0
        self.interval: int = 3
        self.query_funcs = [self.query_account, self.query_position]
        self.query_index: int = 0
        self.timer_registered: bool = False

    def connect(self, setting: dict) -> None:
        """Connect to Futu OpenD."""
        self.password = str(setting.get("密码", ""))
        self.host = str(setting.get("地址", "127.0.0.1"))
        self.port = int(setting.get("端口", 11111))
        self.market = str(setting.get("市场", "HK"))
        self.env = ENV_MAP.get(str(setting.get("环境", "模拟")), TrdEnv.SIMULATE)

        self.connect_quote()
        self.connect_trade()

        self.thread = Thread(target=self.query_data, daemon=True)
        self.thread.start()

    def query_data(self) -> None:
        """Query initial data after connected."""
        sleep(2)
        self.query_contract()
        self.query_trade()
        self.query_order()
        self.query_position()
        self.query_account()

        if not self.timer_registered:
            self.event_engine.register(EVENT_TIMER, self.process_timer_event)
            self.timer_registered = True

    def process_timer_event(self, event: Event) -> None:
        """Query account and position regularly."""
        self.count += 1
        if self.count < self.interval:
            return
        self.count = 0

        func = self.query_funcs[self.query_index]
        func()

        self.query_index += 1
        if self.query_index >= len(self.query_funcs):
            self.query_index = 0

    def connect_quote(self) -> None:
        """Connect quote context."""
        self.quote_ctx = OpenQuoteContext(self.host, self.port)

        class QuoteHandler(StockQuoteHandlerBase):
            gateway: FutuGateway = self

            def on_recv_rsp(self, rsp_str):
                ret, content = super().on_recv_rsp(rsp_str)
                if ret != RET_OK:
                    return RET_ERROR, content
                self.gateway.process_quote(content)
                return RET_OK, content

        class OrderBookHandler(OrderBookHandlerBase):
            gateway: FutuGateway = self

            def on_recv_rsp(self, rsp_str):
                ret, content = super().on_recv_rsp(rsp_str)
                if ret != RET_OK:
                    return RET_ERROR, content
                self.gateway.process_orderbook(content)
                return RET_OK, content

        self.quote_ctx.set_handler(QuoteHandler())
        self.quote_ctx.set_handler(OrderBookHandler())
        self.quote_ctx.start()
        self.write_log("行情接口连接成功")

    def connect_trade(self) -> None:
        """Connect trade context."""
        if self.market == "HK_FUTURE":
            self.trade_ctx = OpenFutureTradeContext(host=self.host, port=self.port)
        else:
            trd_market = TRADE_MARKET_MAP.get(self.market)
            if trd_market is None:
                self.write_log(f"交易市场不支持：{self.market}")
                return

            self.trade_ctx = OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=self.host,
                port=self.port,
            )

        if self.password:
            ret, data = self.trade_ctx.unlock_trade(self.password)
            if ret != RET_OK:
                self.write_log(f"交易解锁失败：{data}")
                return

        class OrderHandler(TradeOrderHandlerBase):
            gateway: FutuGateway = self

            def on_recv_rsp(self, rsp_str):
                ret, content = super().on_recv_rsp(rsp_str)
                if ret != RET_OK:
                    return RET_ERROR, content
                self.gateway.process_order(content)
                return RET_OK, content

        class DealHandler(TradeDealHandlerBase):
            gateway: FutuGateway = self

            def on_recv_rsp(self, rsp_str):
                ret, content = super().on_recv_rsp(rsp_str)
                if ret != RET_OK:
                    return RET_ERROR, content
                self.gateway.process_deal(content)
                return RET_OK, content

        self.trade_ctx.set_handler(OrderHandler())
        self.trade_ctx.set_handler(DealHandler())
        self.trade_ctx.start()
        self.write_log("交易接口连接成功")

    def subscribe(self, req: SubscribeRequest) -> None:
        """Subscribe quote and order book."""
        if not self.quote_ctx:
            self.write_log("订阅行情失败：行情接口未连接")
            return

        try:
            futu_symbol: str = convert_symbol_vt2futu(req.symbol, req.exchange)
        except KeyError:
            self.write_log(f"订阅行情失败：不支持的交易所 {req.exchange.value}")
            return

        ret, data = self.quote_ctx.subscribe(futu_symbol, ["QUOTE", "ORDER_BOOK"], True)
        if ret != RET_OK:
            self.write_log(f"订阅行情失败：{data}")
            return

        self.write_log(f"订阅行情成功：{req.vt_symbol}")

    def send_order(self, req: OrderRequest) -> str:
        """Send order."""
        if not self.trade_ctx:
            self.write_log("委托失败：交易接口未连接")
            return ""

        if req.type != OrderType.LIMIT:
            self.write_log(f"委托失败：FUTU接口当前仅支持限价单，收到{req.type.value}")
            return ""

        side: TrdSide = DIRECTION_VT2FUTU[req.direction]
        futu_order_type = FutuOrderType.NORMAL
        adjust_limit: float = 0.05 if req.direction is Direction.LONG else -0.05

        try:
            futu_symbol: str = convert_symbol_vt2futu(req.symbol, req.exchange)
        except KeyError:
            self.write_log(f"委托失败：不支持的交易所 {req.exchange.value}")
            return ""

        ret, data = self.trade_ctx.place_order(
            req.price,
            req.volume,
            futu_symbol,
            side,
            futu_order_type,
            trd_env=self.env,
            adjust_limit=adjust_limit,
        )

        if ret != RET_OK:
            self.write_log(f"委托失败：{data}")
            return ""

        orderid: str = ""
        for _, row in data.iterrows():
            orderid = str(row["order_id"])

        if not orderid:
            self.write_log("委托失败：未返回委托号")
            return ""

        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.on_order(order)
        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """Cancel order."""
        if not self.trade_ctx:
            self.write_log("撤单失败：交易接口未连接")
            return

        ret, data = self.trade_ctx.modify_order(
            ModifyOrderOp.CANCEL,
            req.orderid,
            0,
            0,
            trd_env=self.env,
        )
        if ret != RET_OK:
            self.write_log(f"撤单失败：{data}")

    def query_contract(self) -> None:
        """Query contracts."""
        if not self.quote_ctx:
            return

        if self.market == "CN":
            markets: list[str] = ["SH", "SZ"]
        elif self.market == "HK_FUTURE":
            markets = ["HK"]
        else:
            markets = [self.market]

        for market in markets:
            for product, futu_product in PRODUCT_VT2FUTU.items():
                if self.market == "HK_FUTURE" and product != Product.FUTURES:
                    continue

                ret, data = self.quote_ctx.get_stock_basicinfo(market, futu_product)
                if ret != RET_OK:
                    self.write_log(f"查询合约信息失败：{data}")
                    continue

                for _, row in data.iterrows():
                    symbol, exchange = convert_symbol_futu2vt(row["code"])
                    if self.market == "HK_FUTURE":
                        exchange = Exchange.HKFE

                    size: float = to_float(get_value(row, "lot_size", default=1), 1)
                    pricetick: float = to_float(get_value(row, "price_spread", default=0.001), 0.001)
                    if pricetick <= 0:
                        pricetick = 0.001

                    contract: ContractData = ContractData(
                        symbol=symbol,
                        exchange=exchange,
                        name=str(row["name"]),
                        product=product,
                        size=size,
                        pricetick=pricetick,
                        min_volume=1,
                        history_data=True,
                        net_position=True,
                        gateway_name=self.gateway_name,
                    )
                    self.on_contract(contract)
                    self.contracts[contract.vt_symbol] = contract

        self.write_log("合约信息查询成功")

    def query_account(self) -> None:
        """Query account."""
        if not self.trade_ctx:
            return

        ret, data = self.trade_ctx.accinfo_query(trd_env=self.env, acc_id=0)
        if ret != RET_OK:
            self.write_log(f"查询账户资金失败：{data}")
            return

        for _, row in data.iterrows():
            total_assets: float = to_float(get_value(row, "total_assets", default=0))
            available_cash: float = to_float(
                get_value(row, "avl_withdrawal_cash", "available_cash", "cash", default=total_assets),
                total_assets,
            )
            account: AccountData = AccountData(
                accountid=f"{self.market}_{self.env}",
                balance=total_assets,
                frozen=max(total_assets - available_cash, 0),
                gateway_name=self.gateway_name,
            )
            self.on_account(account)

    def query_position(self) -> None:
        """Query positions."""
        if not self.trade_ctx:
            return

        ret, data = self.trade_ctx.position_list_query(trd_env=self.env, acc_id=0)
        if ret != RET_OK:
            self.write_log(f"查询持仓失败：{data}")
            return

        for _, row in data.iterrows():
            symbol, exchange = convert_symbol_futu2vt(row["code"])
            if self.market == "HK_FUTURE":
                exchange = Exchange.HKFE

            volume: float = to_float(get_value(row, "qty", default=0))
            can_sell: float = to_float(get_value(row, "can_sell_qty", default=volume), volume)
            position: PositionData = PositionData(
                symbol=symbol,
                exchange=exchange,
                direction=Direction.NET,
                volume=volume,
                frozen=max(volume - can_sell, 0),
                price=to_float(get_value(row, "cost_price", default=0)),
                pnl=to_float(get_value(row, "pl_val", default=0)),
                gateway_name=self.gateway_name,
            )
            self.on_position(position)

    def query_order(self) -> None:
        """Query orders."""
        if not self.trade_ctx:
            return

        ret, data = self.trade_ctx.order_list_query("", trd_env=self.env)
        if ret != RET_OK:
            self.write_log(f"查询委托失败：{data}")
            return

        self.process_order(data)

    def query_trade(self) -> None:
        """Query trades."""
        if not self.trade_ctx:
            return

        ret, data = self.trade_ctx.deal_list_query("", trd_env=self.env)
        if ret != RET_OK:
            self.write_log(f"查询成交失败：{data}")
            return

        self.process_deal(data)

    def close(self) -> None:
        """Close gateway."""
        if self.timer_registered:
            self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
            self.timer_registered = False

        if self.quote_ctx:
            self.quote_ctx.close()
            self.quote_ctx = None

        if self.trade_ctx:
            self.trade_ctx.close()
            self.trade_ctx = None

    def get_tick(self, code: str) -> TickData:
        """Get tick object from cache or create new one."""
        tick: TickData | None = self.ticks.get(code)
        if tick:
            return tick

        symbol, exchange = convert_symbol_futu2vt(code)
        if self.market == "HK_FUTURE":
            exchange = Exchange.HKFE

        vt_symbol: str = f"{symbol}.{exchange.value}"
        contract: ContractData | None = self.contracts.get(vt_symbol)
        name: str = contract.name if contract else symbol

        tick = TickData(
            symbol=symbol,
            exchange=exchange,
            datetime=datetime.now(CHINA_TZ),
            name=name,
            gateway_name=self.gateway_name,
        )
        self.ticks[code] = tick
        return tick

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """Query historical bar data."""
        bars: list[BarData] = []

        if not self.quote_ctx:
            self.write_log("查询K线失败：行情接口未连接")
            return bars

        ktype = INTERVAL_VT2FUTU.get(req.interval)
        if not ktype:
            self.write_log(f"查询K线失败：不支持{req.interval.value if req.interval else ''}周期")
            return bars

        try:
            futu_symbol: str = convert_symbol_vt2futu(req.symbol, req.exchange)
        except KeyError:
            self.write_log(f"查询K线失败：不支持的交易所 {req.exchange.value}")
            return bars

        start: str = req.start.strftime("%Y-%m-%d")
        end: str | None = req.end.strftime("%Y-%m-%d") if req.end else None
        page_req_key = None

        while True:
            ret, data, page_req_key = self.quote_ctx.request_history_kline(
                code=futu_symbol,
                start=start,
                end=end,
                ktype=ktype,
                page_req_key=page_req_key,
            )
            if ret != RET_OK:
                self.write_log(f"查询K线失败：{data}")
                break

            for _, row in data.iterrows():
                bar: BarData = BarData(
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=generate_datetime(row["time_key"]),
                    interval=req.interval,
                    volume=to_float(get_value(row, "volume", default=0)),
                    turnover=to_float(get_value(row, "turnover", default=0)),
                    open_price=to_float(row["open"]),
                    high_price=to_float(row["high"]),
                    low_price=to_float(row["low"]),
                    close_price=to_float(row["close"]),
                    gateway_name=self.gateway_name,
                )
                bars.append(bar)

            if not page_req_key:
                break

        return bars

    def process_quote(self, data) -> None:
        """Process quote push."""
        for _, row in data.iterrows():
            code: str = row["code"]
            tick: TickData = self.get_tick(code)

            time_value = get_value(row, "data_time", "svr_recv_time", default=None)
            if time_value:
                tick.datetime = generate_datetime(time_value)
            else:
                tick.datetime = datetime.now(CHINA_TZ)
            tick.localtime = datetime.now(CHINA_TZ)

            tick.open_price = to_float(get_value(row, "open_price", default=0))
            tick.high_price = to_float(get_value(row, "high_price", default=0))
            tick.low_price = to_float(get_value(row, "low_price", default=0))
            tick.pre_close = to_float(get_value(row, "prev_close_price", default=0))
            tick.last_price = to_float(get_value(row, "last_price", default=0))
            tick.volume = to_float(get_value(row, "volume", default=0))
            tick.turnover = to_float(get_value(row, "turnover", default=0))

            self.on_tick(copy(tick))

    def process_orderbook(self, data) -> None:
        """Process order book push."""
        code: str = data["code"]
        tick: TickData = self.get_tick(code)
        tick.datetime = datetime.now(CHINA_TZ)
        tick.localtime = datetime.now(CHINA_TZ)

        bids = data.get("Bid", [])
        asks = data.get("Ask", [])
        for i in range(5):
            if len(bids) > i:
                bid_data = bids[i]
                setattr(tick, f"bid_price_{i + 1}", to_float(bid_data[0]))
                setattr(tick, f"bid_volume_{i + 1}", to_float(bid_data[1]))
            if len(asks) > i:
                ask_data = asks[i]
                setattr(tick, f"ask_price_{i + 1}", to_float(ask_data[0]))
                setattr(tick, f"ask_volume_{i + 1}", to_float(ask_data[1]))

        self.on_tick(copy(tick))

    def process_order(self, data) -> None:
        """Process order push."""
        for _, row in data.iterrows():
            order_status = row["order_status"]
            if hasattr(OrderStatus, "DELETED") and order_status == OrderStatus.DELETED:
                continue

            direction, offset = DIRECTION_FUTU2VT.get(row["trd_side"], (Direction.NET, Offset.NONE))
            symbol, exchange = convert_symbol_futu2vt(row["code"])
            if self.market == "HK_FUTURE":
                exchange = Exchange.HKFE

            order: OrderData = OrderData(
                symbol=symbol,
                exchange=exchange,
                orderid=str(row["order_id"]),
                type=OrderType.LIMIT,
                direction=direction,
                offset=offset,
                price=to_float(row["price"]),
                volume=to_float(row["qty"]),
                traded=to_float(row["dealt_qty"]),
                status=STATUS_FUTU2VT.get(order_status, Status.SUBMITTING),
                datetime=generate_datetime(row["create_time"]),
                gateway_name=self.gateway_name,
            )
            self.on_order(order)

    def process_deal(self, data) -> None:
        """Process trade push."""
        for _, row in data.iterrows():
            tradeid: str = str(row["deal_id"])
            if tradeid in self.trades:
                continue
            self.trades.add(tradeid)

            direction, offset = DIRECTION_FUTU2VT.get(row["trd_side"], (Direction.NET, Offset.NONE))
            symbol, exchange = convert_symbol_futu2vt(row["code"])
            if self.market == "HK_FUTURE":
                exchange = Exchange.HKFE

            trade: TradeData = TradeData(
                symbol=symbol,
                exchange=exchange,
                orderid=str(row["order_id"]),
                tradeid=tradeid,
                direction=direction,
                offset=offset,
                price=to_float(row["price"]),
                volume=to_float(row["qty"]),
                datetime=generate_datetime(row["create_time"]),
                gateway_name=self.gateway_name,
            )
            self.on_trade(trade)
