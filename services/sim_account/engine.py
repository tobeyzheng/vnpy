from __future__ import annotations

import math
from dataclasses import asdict

from services.common import OrderIntent
from services.sim_account.models import CostBreakdown, SimAccount, SimOrder, SimPosition
from services.trade_state import OrderStateStore
from services.trade_state.state_machine import OrderStateMachine


class SimTradingEngine:
    def __init__(self, lot_size_default: int = 100, commission_rate: float = 0.0003, platform_fee: float = 15.0, settlement_rate: float = 0.00002, stamp_duty_rate_sell: float = 0.0013, slippage_bps: float = 8.0, order_state_store: OrderStateStore | None = None):
        self.lot_size_default = lot_size_default
        self.commission_rate = commission_rate
        self.platform_fee = platform_fee
        self.settlement_rate = settlement_rate
        self.stamp_duty_rate_sell = stamp_duty_rate_sell
        self.slippage_bps = slippage_bps
        self.order_state_store = order_state_store
        self.order_state_machine = OrderStateMachine()

    def can_open(self, account: SimAccount, budget_hkd: float) -> bool:
        max_loss_nav = account.initial_cash * (1 - account.max_drawdown_limit_pct)
        return account.nav > max_loss_nav and account.cash >= budget_hkd

    def _exec_price(self, price: float, side: str) -> float:
        slip = price * (self.slippage_bps / 10000.0)
        return price + slip if side.upper() == 'BUY' else max(0.001, price - slip)

    def _costs(self, qty: int, price: float, side: str) -> CostBreakdown:
        gross = float(qty) * float(price)
        commission = max(3.0, gross * self.commission_rate)
        settlement = gross * self.settlement_rate
        stamp = math.ceil(gross * self.stamp_duty_rate_sell * 100) / 100.0 if side.upper() == 'SELL' else 0.0
        slippage = abs(self._exec_price(price, side) - price) * qty
        total = commission + self.platform_fee + settlement + stamp + slippage
        net = gross - total if side.upper() == 'SELL' else gross + total
        return CostBreakdown(gross_amount=round(gross, 4), commission=round(commission, 4), platform_fee=round(self.platform_fee, 4), settlement_fee=round(settlement, 4), stamp_duty=round(stamp, 4), slippage=round(slippage, 4), total_fees=round(total, 4), net_amount=round(net, 4))

    def min_lot_cost(self, price: float, lot_size: int | None = None) -> float:
        lot = lot_size or self.lot_size_default
        return self._costs(lot, float(price), 'BUY').net_amount

    def is_affordable(self, price: float, budget_hkd: float, lot_size: int | None = None) -> bool:
        return self.min_lot_cost(price, lot_size) <= budget_hkd

    def place_buy(self, account: SimAccount, symbol: str, price: float, reason: str, budget_hkd: float, lot_size: int | None = None) -> SimOrder:
        lot = lot_size or self.lot_size_default
        qty = int(budget_hkd // self.min_lot_cost(price, lot_size=lot)) * lot
        if qty <= 0:
            return SimOrder(symbol=symbol, side='BUY', qty=0, price=price, status='rejected', reason='budget insufficient for one lot')
        exec_price = self._exec_price(price, 'BUY')
        fees = self._costs(qty, price, 'BUY')
        cash_needed = fees.net_amount
        if cash_needed > account.cash:
            return SimOrder(symbol=symbol, side='BUY', qty=0, price=price, status='rejected', reason='cash insufficient')
        account.cash -= cash_needed
        order = SimOrder(symbol=symbol, side='BUY', qty=qty, price=price, status='filled', reason=reason, filled_qty=qty, avg_fill_price=exec_price, gross_amount=fees.gross_amount, fees=fees)
        account.orders.append(order)
        self._record_order_state(order)
        existing = next((p for p in account.positions if p.symbol == symbol), None)
        if existing:
            total_cost = existing.avg_price * existing.qty + exec_price * qty
            existing.qty += qty
            existing.avg_price = total_cost / existing.qty
        else:
            account.positions.append(SimPosition(symbol=symbol, qty=qty, avg_price=exec_price))
        return order

    def place_sell(self, account: SimAccount, symbol: str, price: float, reason: str) -> SimOrder:
        existing = next((p for p in account.positions if p.symbol == symbol), None)
        if not existing or existing.qty <= 0:
            return SimOrder(symbol=symbol, side='SELL', qty=0, price=price, status='rejected', reason='no position to sell')
        qty = existing.qty
        exec_price = self._exec_price(price, 'SELL')
        fees = self._costs(qty, price, 'SELL')
        realized = (exec_price - existing.avg_price) * qty - fees.total_fees
        account.cash += fees.net_amount
        account.realized_pnl += realized
        order = SimOrder(symbol=symbol, side='SELL', qty=qty, price=price, status='filled', reason=reason, filled_qty=qty, avg_fill_price=exec_price, gross_amount=fees.gross_amount, fees=fees)
        account.orders.append(order)
        self._record_order_state(order)
        account.positions = [p for p in account.positions if p.symbol != symbol]
        return order

    def _record_order_state(self, order: SimOrder) -> None:
        if not self.order_state_store or order.qty <= 0:
            return
        request_id = order.order_id or f"sim_{len(self.order_state_store.list()) + 1}_{order.symbol}_{order.side}"
        intent = OrderIntent(
            request_id=request_id,
            symbol=order.symbol,
            market="sim",
            strategy_id="sim_trading_engine",
            side=order.side,
            qty=order.qty,
            price=order.price,
            target_position_pct=0.0,
            reason=order.reason,
            signal_snapshot={"submit_status": order.submit_status},
        )
        state = self.order_state_machine.create(intent)
        state = self.order_state_machine.transition(state, "validated", note="sim_order_created")
        state = self.order_state_machine.transition(state, "risk_checked", note="sim_order_risk_checked")
        state = self.order_state_machine.transition(state, "approved", note="sim_order_approved")
        state = self.order_state_machine.transition(state, "submitted", note="sim_order_submitted")
        state = self.order_state_machine.apply_broker_order(
            state,
            broker_order_id=request_id,
            broker_status="FILLED_ALL" if order.status == "filled" else order.status,
            filled_qty=order.filled_qty,
            avg_fill_price=order.avg_fill_price,
            snapshot={"sim_order": asdict(order)},
        )
        self.order_state_store.save(state)

    def evaluate_exit_reason(self, pos: SimPosition, current_price: float) -> str | None:
        pnl_pct = (current_price - pos.avg_price) / pos.avg_price if pos.avg_price else 0.0
        if pnl_pct <= -0.08:
            return 'stop_loss'
        if pnl_pct >= 0.15:
            return 'take_profit'
        return None

    def mark_to_market(self, account: SimAccount, quotes: dict[str, float]) -> SimAccount:
        nav = account.cash
        for pos in account.positions:
            px = float(quotes.get(pos.symbol, pos.avg_price))
            pos.market_value = px * pos.qty
            pos.unrealized_pnl = (px - pos.avg_price) * pos.qty
            nav += pos.market_value
        account.nav = nav
        return account
