from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.futu_opend import OpenDConfig


@dataclass
class SimTradeResult:
    success: bool
    message: str
    order_id: Optional[str] = None


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

    def submit_limit_order(self, code: str, side: str, qty: int, price: float, reason: str = '') -> SimTradeResult:
        if self._futu is None:
            return SimTradeResult(False, self._import_error or 'futu sdk unavailable')
        futu = self._futu
        if (self.config.trd_env or 'SIMULATE').upper() != 'SIMULATE':
            return SimTradeResult(False, 'REAL trading is blocked; SIMULATE only')
        trd_side = futu.TrdSide.BUY if side.upper() == 'BUY' else futu.TrdSide.SELL
        ctx = futu.OpenSecTradeContext(host=self.config.host, port=self.config.port)
        try:
            ret, accounts = ctx.get_acc_list()
            if ret != futu.RET_OK:
                return SimTradeResult(False, f'get_acc_list failed: {accounts}')
            target = None
            for _, row in accounts.iterrows():
                env = str(row.get('trd_env', '')).upper()
                if 'SIM' in env:
                    target = row
                    break
            if target is None:
                target = accounts.iloc[0]
            acc_id = int(target['acc_id'])
            ret, data = ctx.place_order(
                price=price,
                qty=qty,
                code=code.replace('.HK', '').replace('HK.', 'HK.'),
                trd_side=trd_side,
                order_type=futu.OrderType.NORMAL,
                trd_env=futu.TrdEnv.SIMULATE,
                acc_id=acc_id,
                remark=reason[:64] if reason else ''
            )
            if ret != futu.RET_OK:
                return SimTradeResult(False, f'place_order failed: {data}')
            order_id = None
            if hasattr(data, 'iloc') and len(data) > 0:
                order_id = str(data.iloc[0].get('order_id', ''))
            return SimTradeResult(True, 'ok', order_id=order_id)
        except Exception as e:
            return SimTradeResult(False, str(e))
        finally:
            ctx.close()
