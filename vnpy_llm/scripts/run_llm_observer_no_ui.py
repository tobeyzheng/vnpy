from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import sleep

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy.event import Event, EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_ACCOUNT, EVENT_LOG, EVENT_ORDER, EVENT_POSITION, EVENT_TICK, EVENT_TRADE
from vnpy.trader.object import AccountData, OrderData, PositionData, TickData, TradeData
from vnpy_futu import FutuGateway

from vnpy_llm.config import load_config
from vnpy_llm.report import append_audit_line
from vnpy_llm.risk import RiskPolicy, make_risk_decision
from vnpy_llm.signal_store import SignalStore


class LlmEventObserver:
    def __init__(self, main_engine: MainEngine, symbol: str, audit_path: Path, signal_dir: Path) -> None:
        self.main_engine = main_engine
        self.symbol = symbol
        self.audit_path = audit_path
        self.signal_store = SignalStore(signal_dir)
        self.policy = RiskPolicy()
        event_engine = main_engine.event_engine
        event_engine.register(EVENT_TICK + symbol, self.on_tick)
        event_engine.register(EVENT_ORDER, self.on_order)
        event_engine.register(EVENT_TRADE, self.on_trade)
        event_engine.register(EVENT_POSITION, self.on_position)
        event_engine.register(EVENT_ACCOUNT, self.on_account)
        event_engine.register(EVENT_LOG, self.on_log)

    def _decision_payload(self) -> dict:
        signal = self.signal_store.latest(self.symbol)
        decision = make_risk_decision(signal, self.policy)
        return {
            "signal_id": decision.signal_id,
            "allow_new_long": decision.allow_new_long,
            "reduce_only": decision.reduce_only,
            "position_multiplier": decision.position_multiplier,
            "decision_reason": decision.reason,
        }

    def on_tick(self, event: Event) -> None:
        tick: TickData = event.data
        append_audit_line(
            self.audit_path,
            {
                "event": "tick",
                "vt_symbol": tick.vt_symbol,
                "last_price": tick.last_price,
                **self._decision_payload(),
            },
        )

    def on_order(self, event: Event) -> None:
        order: OrderData = event.data
        if order.vt_symbol != self.symbol:
            return
        append_audit_line(
            self.audit_path,
            {
                "event": "order",
                "vt_symbol": order.vt_symbol,
                "vt_orderid": order.vt_orderid,
                "status": order.status.value,
                "price": order.price,
                "volume": order.volume,
            },
        )

    def on_trade(self, event: Event) -> None:
        trade: TradeData = event.data
        if trade.vt_symbol != self.symbol:
            return
        append_audit_line(
            self.audit_path,
            {
                "event": "trade",
                "vt_symbol": trade.vt_symbol,
                "vt_tradeid": trade.vt_tradeid,
                "direction": trade.direction.value if trade.direction else "",
                "price": trade.price,
                "volume": trade.volume,
            },
        )

    def on_position(self, event: Event) -> None:
        position: PositionData = event.data
        if position.vt_symbol != self.symbol:
            return
        append_audit_line(
            self.audit_path,
            {
                "event": "position",
                "vt_symbol": position.vt_symbol,
                "volume": position.volume,
                "price": position.price,
                "pnl": position.pnl,
            },
        )

    def on_account(self, event: Event) -> None:
        account: AccountData = event.data
        append_audit_line(
            self.audit_path,
            {
                "event": "account",
                "vt_accountid": account.vt_accountid,
                "balance": account.balance,
                "available": account.available,
            },
        )

    def on_log(self, event: Event) -> None:
        log = event.data
        append_audit_line(
            self.audit_path,
            {
                "event": "log",
                "gateway_name": getattr(log, "gateway_name", ""),
                "msg": getattr(log, "msg", str(log)),
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM 旁路观察器：监听 vn.py 事件并写审计日志，不下单")
    parser.add_argument("--config", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--password", default="")
    parser.add_argument("--connect-wait", type=int, default=15)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config or None, PROJECT_ROOT)
    symbol = config.symbol.upper()
    audit_path = config.report_dir.joinpath("llm_observer_audit.jsonl")

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(FutuGateway)
    LlmEventObserver(main_engine, symbol, audit_path, config.signal_dir)
    main_engine.connect(
        {
            "密码": args.password,
            "地址": args.host,
            "端口": args.port,
            "市场": "US",
            "环境": "模拟",
        },
        "FUTU",
    )
    sleep(args.connect_wait)
    print(f"LLM旁路观察器已启动: {symbol}, audit={audit_path}")

    try:
        while True:
            sleep(10)
    except KeyboardInterrupt:
        main_engine.close()


if __name__ == "__main__":
    main()
