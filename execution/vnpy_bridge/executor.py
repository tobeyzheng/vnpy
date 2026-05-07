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

from .models import VnpyOrderDraft


class VnpyExecutor:
    def __init__(self, root: str | Path, *, mode: str = "paper", reconciliation_guard: ReconciliationGuard | None = None):
        if mode not in {"paper", "sim"}:
            raise ValueError("VnpyExecutor only supports paper/sim mode")
        self.root = Path(root)
        self.mode = mode
        self.reconciliation_guard = reconciliation_guard
        self.state_store = OrderStateStore(self.root / "orders")
        self.machine = OrderStateMachine()

    def execute_intent(self, intent: OrderIntent) -> OrderState:
        state = self.machine.create(intent)
        state = self.machine.transition(state, "validated", note=f"executor_mode:{self.mode}")

        if self.mode == "sim" and self.reconciliation_guard:
            decision = self.reconciliation_guard.evaluate(side=intent.side, symbol=intent.symbol)
            if not decision.allowed:
                state = self.machine.transition(state, "rejected", note="reconciliation_blocked", snapshot={"reconciliation": decision.__dict__})
                self.state_store.save(state)
                return state

        state = self.machine.transition(state, "risk_checked", note="executor_scaffold_risk_checked")
        state = self.machine.transition(state, "approved", note="paper_sim_only_no_live")
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

    def _request_id(self, symbol: str, market: str, side: str, reason: str) -> str:
        payload = f"{self.mode}|{symbol}|{market}|{side}|{reason}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]


class VnpyGatewayEventBridge:
    def __init__(self, root: str | Path):
        self.state_store = OrderStateStore(Path(root) / "orders")

    def order_event_to_state(self, order: Any) -> OrderState:
        side = "SELL" if getattr(getattr(order, "direction", None), "name", "") == "SHORT" else "BUY"
        state = OrderState(
            request_id=str(getattr(order, "reference", "") or getattr(order, "vt_orderid", "")),
            symbol=str(getattr(order, "vt_symbol", getattr(order, "symbol", ""))),
            market=str(getattr(getattr(order, "exchange", None), "value", "")),
            side=side,
            qty=int(float(getattr(order, "volume", 0) or 0)),
            price=float(getattr(order, "price", 0) or 0),
            filled_qty=int(float(getattr(order, "traded", 0) or 0)),
            broker_order_id=str(getattr(order, "vt_orderid", "")),
            status="submitted",
            strategy_id="vnpy_gateway_event",
            reason="order_event",
        )

        status = str(getattr(getattr(order, "status", None), "name", getattr(order, "status", "submitted")))
        state = OrderStateMachine().apply_broker_order(
            state,
            broker_order_id=state.broker_order_id,
            broker_status=status,
            filled_qty=state.filled_qty,
            avg_fill_price=state.price,
            snapshot={"event_type": "order", "status": status},
        )
        self.state_store.save(state)
        return state
