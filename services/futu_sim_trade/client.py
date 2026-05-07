from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.futu_account import FutuQuoteClient
from services.futu_opend import OpenDConfig


@dataclass
class SimTradeResult:
    success: bool
    message: str
    order_id: Optional[str] = None
    status: Optional[str] = None
    dealt_qty: Optional[int] = None
    dealt_avg_price: Optional[float] = None
    submitted_price: Optional[float] = None


class FutuSimTradeClient:
    def __init__(self, config: OpenDConfig | None = None):
        self.config = config or OpenDConfig()
        self._futu = None
        self._import_error = None
        try:
            import futu  # type: ignore
            self._futu = futu
        except Exception as e:
            self._import_error = str(e)

    def _normalize_code(self, code: str) -> str:
        return FutuQuoteClient().normalize_code(code)

    def _get_snapshot_row(self, code: str):
        rows = FutuQuoteClient().get_snapshot([code])
        return rows[0] if rows else {}

    def _get_lot_size(self, code: str) -> int:
        row = self._get_snapshot_row(code)
        if row and row.get('lot_size') not in (None, 0, 0.0):
            return int(row['lot_size'])
        return 100

    def _normalize_qty(self, code: str, qty: int) -> int:
        lot = self._get_lot_size(code)
        return (int(qty) // lot) * lot

    def _tick_size(self, price: float) -> float:
        p = float(price)
        if p < 0.25:
            return 0.001
        if p < 0.5:
            return 0.005
        if p < 10:
            return 0.01
        if p < 20:
            return 0.02
        if p < 100:
            return 0.05
        if p < 200:
            return 0.1
        if p < 500:
            return 0.2
        if p < 1000:
            return 0.5
        if p < 2000:
            return 1.0
        if p < 5000:
            return 2.0
        return 5.0

    def _normalize_price(self, price: float, side: str) -> float:
        tick = self._tick_size(price)
        p = float(price)
        units = p / tick
        if side.upper() == 'BUY':
            normalized = int(units) * tick
        else:
            import math
            normalized = math.ceil(units) * tick
        digits = max(0, len(str(tick).split('.')[-1]) if '.' in str(tick) else 0)
        return round(normalized, digits)

    def _get_sim_acc(self, ctx, futu):
        ret, accounts = ctx.get_acc_list()
        if ret != futu.RET_OK:
            raise RuntimeError(f'get_acc_list failed: {accounts}')
        target = None
        for _, row in accounts.iterrows():
            env = str(row.get('trd_env', '')).upper()
            if 'SIM' in env:
                target = row
                break
        if target is None:
            target = accounts.iloc[0]
        return int(target['acc_id'])

    def submit_limit_order(self, code: str, side: str, qty: int, price: float, reason: str = '') -> SimTradeResult:
        if self._futu is None:
            return SimTradeResult(False, self._import_error or 'futu sdk unavailable')
        futu = self._futu
        if (self.config.trd_env or 'SIMULATE').upper() != 'SIMULATE':
            return SimTradeResult(False, 'REAL trading is blocked; SIMULATE only')

        code = self._normalize_code(code)
        qty = self._normalize_qty(code, qty)
        if qty <= 0:
            return SimTradeResult(False, 'normalized qty is zero after lot-size adjustment')
        norm_price = self._normalize_price(price, side)

        trd_side = futu.TrdSide.BUY if side.upper() == 'BUY' else futu.TrdSide.SELL
        ctx = futu.OpenSecTradeContext(host=self.config.host, port=self.config.port)
        try:
            acc_id = self._get_sim_acc(ctx, futu)
            ret, data = ctx.place_order(price=float(norm_price), qty=int(qty), code=code, trd_side=trd_side, order_type=futu.OrderType.NORMAL, trd_env=futu.TrdEnv.SIMULATE, acc_id=acc_id, remark=reason[:64] if reason else '')
            if ret != futu.RET_OK:
                return SimTradeResult(False, f'place_order failed: {data}', submitted_price=norm_price)
            order_id = None
            status = None
            dealt_qty = None
            dealt_avg_price = None
            if hasattr(data, 'iloc') and len(data) > 0:
                row = data.iloc[0]
                order_id = str(row.get('order_id', ''))
                status = str(row.get('order_status', ''))
                dealt_qty = int(row.get('dealt_qty', 0) or 0)
                dap = row.get('dealt_avg_price', None)
                dealt_avg_price = float(dap) if dap not in (None, '') else None
            return SimTradeResult(True, 'ok', order_id=order_id, status=status, dealt_qty=dealt_qty, dealt_avg_price=dealt_avg_price, submitted_price=norm_price)
        except Exception as e:
            return SimTradeResult(False, str(e), submitted_price=norm_price)
        finally:
            ctx.close()

    def get_order(self, order_id: str) -> SimTradeResult:
        if self._futu is None:
            return SimTradeResult(False, self._import_error or 'futu sdk unavailable')
        futu = self._futu
        ctx = futu.OpenSecTradeContext(host=self.config.host, port=self.config.port)
        try:
            acc_id = self._get_sim_acc(ctx, futu)
            ret, data = ctx.order_list_query(order_id=order_id, trd_env=futu.TrdEnv.SIMULATE, acc_id=acc_id)
            if ret != futu.RET_OK:
                return SimTradeResult(False, f'order_list_query failed: {data}', order_id=order_id)
            if hasattr(data, 'iloc') and len(data) > 0:
                row = data.iloc[0]
                status = str(row.get('order_status', ''))
                dealt_qty = int(row.get('dealt_qty', 0) or 0)
                dap = row.get('dealt_avg_price', None)
                dealt_avg_price = float(dap) if dap not in (None, '') else None
                return SimTradeResult(True, 'ok', order_id=order_id, status=status, dealt_qty=dealt_qty, dealt_avg_price=dealt_avg_price)
            return SimTradeResult(False, 'order not found', order_id=order_id)
        except Exception as e:
            return SimTradeResult(False, str(e), order_id=order_id)
        finally:
            ctx.close()
