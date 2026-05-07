from __future__ import annotations

from services.sim_account.models import SimAccount, SimOrder, SimPosition


class SimTradingEngine:
    def __init__(self, lot_size_default: int = 100):
        self.lot_size_default = lot_size_default

    def can_open(self, account: SimAccount, budget_hkd: float) -> bool:
        max_loss_nav = account.initial_cash * (1 - account.max_drawdown_limit_pct)
        return account.nav > max_loss_nav and account.cash >= budget_hkd

    def min_lot_cost(self, price: float, lot_size: int | None = None) -> float:
        lot = lot_size or self.lot_size_default
        return float(price) * lot

    def is_affordable(self, price: float, budget_hkd: float, lot_size: int | None = None) -> bool:
        return self.min_lot_cost(price, lot_size) <= budget_hkd

    def place_buy(self, account: SimAccount, symbol: str, price: float, reason: str, budget_hkd: float, lot_size: int | None = None) -> SimOrder:
        lot = lot_size or self.lot_size_default
        qty = int(budget_hkd // (price * lot)) * lot
        if qty <= 0:
            return SimOrder(symbol=symbol, side='BUY', qty=0, price=price, status='rejected', reason='budget insufficient for one lot')
        cost = qty * price
        if cost > account.cash:
            return SimOrder(symbol=symbol, side='BUY', qty=0, price=price, status='rejected', reason='cash insufficient')
        account.cash -= cost
        account.orders.append(SimOrder(symbol=symbol, side='BUY', qty=qty, price=price, status='filled', reason=reason))
        existing = next((p for p in account.positions if p.symbol == symbol), None)
        if existing:
            total_cost = existing.avg_price * existing.qty + cost
            existing.qty += qty
            existing.avg_price = total_cost / existing.qty
        else:
            account.positions.append(SimPosition(symbol=symbol, qty=qty, avg_price=price))
        return account.orders[-1]

    def place_sell(self, account: SimAccount, symbol: str, price: float, reason: str) -> SimOrder:
        existing = next((p for p in account.positions if p.symbol == symbol), None)
        if not existing or existing.qty <= 0:
            return SimOrder(symbol=symbol, side='SELL', qty=0, price=price, status='rejected', reason='no position to sell')
        qty = existing.qty
        proceeds = qty * price
        realized = (price - existing.avg_price) * qty
        account.cash += proceeds
        account.realized_pnl += realized
        account.orders.append(SimOrder(symbol=symbol, side='SELL', qty=qty, price=price, status='filled', reason=reason))
        account.positions = [p for p in account.positions if p.symbol != symbol]
        return account.orders[-1]

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
