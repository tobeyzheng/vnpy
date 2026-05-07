from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from execution.paper_bridge import PaperTradeIntent
from services.common import OrderIntent, OrderState
from services.execution_guard.reconciliation import ReconciliationGuard
from services.trade_state import OrderStateStore
from services.trade_state.state_machine import OrderStateMachine
from vnpy.trader.constant import Direction, Exchange, OrderType
from vnpy.trader.object import OrderRequest

from .models import VnpyOrderDraft


class VnpyExecutor:
    def __init__(self, root: str | Path, *, mode: str = "paper", reconciliation_guard: ReconciliationGuard | None = None, main_engine: Any = None, gateway_name: str = "FUTU", explicit_submit: bool = False):
        if mode not in {"paper", "sim", "sim_submit", "live_submit"}:
            raise ValueError("VnpyExecutor only supports paper/sim/sim_submit/live_submit mode")
        self.root = Path(root)
        self.mode = mode
        self.reconciliation_guard = reconciliation_guard
        self.main_engine = main_engine
        self.gateway_name = gateway_name
        self.explicit_submit = explicit_submit
        self.state_store = OrderStateStore(self.root / "orders")
        self.machine = OrderStateMachine()

    def execute_intent(self, intent: OrderIntent) -> OrderState:
        existing = self.state_store.load(intent.request_id)
        if existing:
            return existing

        state = self.machine.create(intent)
        self.state_store.save(state)
        state = self.machine.transition(state, "validated", note=f"executor_mode:{self.mode}")

        if self.reconciliation_guard:
            decision = self.reconciliation_guard.evaluate(side=intent.side, symbol=intent.symbol)
            if not decision.allowed:
                state = self.machine.transition(state, "rejected", note="reconciliation_blocked", snapshot={"reconciliation": decision.__dict__})
                self.state_store.save(state)
                return state

        state = self.machine.transition(state, "risk_checked", note="executor_risk_checked")
        state = self.machine.transition(state, "approved", note="explicit_submit_approved" if self.explicit_submit else "dry_run_approved")

        if self.mode in {"sim_submit", "live_submit"}:
            if not self.explicit_submit:
                state = self.machine.transition(state, "failed", note="explicit_submit_disabled")
                self.state_store.save(state)
                return state
            if not self.main_engine:
                state = self.machine.transition(state, "failed", note="main_engine_missing")
                self.state_store.save(state)
                return state
            state = self.machine.transition(state, "submitting", note="sending_to_vnpy_gateway")
            self.state_store.save(state)
            vt_orderid = self.main_engine.send_order(self._to_order_request(intent), self.gateway_name)
            if vt_orderid:
                state = self.machine.transition(state, "submitted", note="sent_to_vnpy_gateway", snapshot={"vt_orderid": vt_orderid})
                state.broker_order_id = vt_orderid
            else:
                state = self.machine.transition(state, "rejected", note="vnpy_gateway_returned_empty_orderid")
            self.state_store.save(state)
            return state

        state = self.machine.transition(state, "submitted", note="dry_run_not_sent_to_gateway")
        self.state_store.save(state)
        return state

    def execute_draft(self, draft: VnpyOrderDraft) -> OrderState:
        intent = self.intent_from_vnpy_draft(draft)
        return self.execute_intent(intent)

    def execute_paper_intent(self, intent: PaperTradeIntent) -> OrderState:
        order_intent = self.intent_from_paper_intent(intent)
        return self.execute_intent(order_intent)

    def intent_from_vnpy_draft(self, draft: VnpyOrderDraft) -> OrderIntent:
        side = str(draft.direction).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported draft direction for execution: {draft.direction}")
        request_id = self._request_id(draft.symbol, draft.market, side, draft.reason)

        return OrderIntent(
            request_id=request_id,
            symbol=draft.symbol,
            market=draft.market,
            strategy_id="vnpy_draft",
            side=side,
            qty=0,
            price=None,
            target_position_pct=float(draft.target_position_pct or 0.0),
            reason=draft.reason,
            signal_snapshot=asdict(draft),
        )

    def intent_from_paper_intent(self, intent: PaperTradeIntent) -> OrderIntent:
        side = str(intent.side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported paper intent side for execution: {intent.side}")
        request_id = self._request_id(intent.symbol, intent.market, side, intent.reason)

        return OrderIntent(
            request_id=request_id,
            symbol=intent.symbol,
            market=intent.market,
            strategy_id="paper_intent",
            side=side,
            qty=0,
            price=None,
            target_position_pct=float(intent.target_position_pct or 0.0),
            reason=intent.reason,
            signal_snapshot=asdict(intent),
        )

    def _to_order_request(self, intent: OrderIntent) -> OrderRequest:
        symbol, exchange = self._parse_symbol_exchange(intent.symbol, intent.market)
        direction = Direction.LONG if intent.side == "BUY" else Direction.SHORT
        return OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            type=OrderType.LIMIT,
            volume=max(int(intent.qty), 0),
            price=float(intent.price or 0),
            reference=intent.request_id,
        )

    def _parse_symbol_exchange(self, symbol: str, market: str) -> tuple[str, Exchange]:
        text = str(symbol).upper()
        if "." in text:
            code, suffix = text.split(".", 1)
        else:
            code = text
            suffix = str(market).upper()
        exchange_map = {
            "US": Exchange.SMART,
            "SMART": Exchange.SMART,
            "HK": Exchange.SEHK,
            "SEHK": Exchange.SEHK,
            "SH": Exchange.SSE,
            "SSE": Exchange.SSE,
            "SZ": Exchange.SZSE,
            "SZSE": Exchange.SZSE,
        }
        return code, exchange_map.get(suffix, Exchange.SMART)

    def _request_id(self, symbol: str, market: str, side: str, reason: str) -> str:
        payload = f"{self.mode}|{symbol}|{market}|{side}|{reason}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]


class VnpyGatewayEventBridge:
    def __init__(self, root: str | Path):
        self.state_store = OrderStateStore(Path(root) / "orders")

    def order_event_to_state(self, order: Any) -> OrderState:
        side = "SELL" if getattr(getattr(order, "direction", None), "name", "") == "SHORT" else "BUY"
        broker_order_id = str(getattr(order, "vt_orderid", ""))
        request_id = str(getattr(order, "reference", "") or "")
        state = self.state_store.load(request_id) if request_id else None
        if state is None:
            state = self.state_store.find_by_broker_order_id(broker_order_id)
        if state is None:
            state = OrderState(
                request_id=request_id or broker_order_id,
                symbol=str(getattr(order, "vt_symbol", getattr(order, "symbol", ""))),
                market=str(getattr(getattr(order, "exchange", None), "value", "")),
                side=side,
                qty=int(float(getattr(order, "volume", 0) or 0)),
                price=float(getattr(order, "price", 0) or 0),
                filled_qty=int(float(getattr(order, "traded", 0) or 0)),
                broker_order_id=broker_order_id,
                status="submitted",
                strategy_id="vnpy_gateway_event",
                reason="order_event",
            )

        status = str(getattr(getattr(order, "status", None), "name", getattr(order, "status", "submitted")))
        traded = int(float(getattr(order, "traded", state.filled_qty) or 0))
        avg_price = float(getattr(order, "price", state.price or 0) or 0)
        state = OrderStateMachine().apply_broker_order(
            state,
            broker_order_id=broker_order_id or state.broker_order_id,
            broker_status=status,
            filled_qty=traded,
            avg_fill_price=avg_price if traded > 0 else state.avg_fill_price,
            snapshot={"event_type": "order", "status": status, "traded": traded},
        )
        self.state_store.save(state)
        return state

    def trade_event_to_state(self, trade: Any) -> OrderState | None:
        broker_order_id = str(getattr(trade, "vt_orderid", "") or getattr(trade, "orderid", ""))
        state = self.state_store.find_by_broker_order_id(broker_order_id)
        if state is None:
            return None
        traded = int(float(getattr(trade, "volume", 0) or 0))
        price = float(getattr(trade, "price", state.price or 0) or 0)
        filled_qty = max(state.filled_qty, traded)
        state = OrderStateMachine().apply_broker_order(
            state,
            broker_order_id=broker_order_id,
            broker_status="FILLED_ALL" if filled_qty >= state.qty else "FILLED_PART",
            filled_qty=filled_qty,
            avg_fill_price=price,
            snapshot={"event_type": "trade", "tradeid": str(getattr(trade, "vt_tradeid", "")), "filled_qty": filled_qty, "price": price},
        )
        self.state_store.save(state)
        return state
