from __future__ import annotations

import json
from pathlib import Path

from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_sim_trade import FutuSimTradeClient
from services.sim_account import SimAccountStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    account = SimAccountStore(runs / 'hk_sim_account.json').load()
    payload = {'success': False, 'message': 'no positions available', 'order_id': None}
    if account.positions:
        pos = account.positions[0]
        reconciliation = ReconciliationGuard(runs / 'futu_sim_position_reconcile.json', fail_closed=True).evaluate(side='SELL', symbol=pos.symbol)
        if not reconciliation.allowed:
            payload = {'symbol': pos.symbol, 'qty': 0, 'price': float(pos.avg_price), 'success': False, 'message': 'reconciliation blocked: ' + ';'.join(reconciliation.reasons), 'order_id': None, 'reconciliation': reconciliation.__dict__}
        else:
            client = FutuSimTradeClient()
            positions = client.get_positions()
            futu_map = {item['symbol']: item for item in positions.get('items', [])}
            sellable = int(futu_map.get(pos.symbol, {}).get('can_sell_qty', 0) or 0)
            sell_qty = min(int(pos.qty), sellable) if sellable > 0 else 0
            if sell_qty <= 0:
                payload = {'symbol': pos.symbol, 'qty': 0, 'price': float(pos.avg_price), 'success': False, 'message': 'no futu sellable qty', 'order_id': None, 'order_status': None, 'dealt_qty': None, 'dealt_avg_price': None, 'status_check': None, 'reconciliation': reconciliation.__dict__}
            else:
                result = client.submit_limit_order(pos.symbol, 'SELL', int(sell_qty), float(pos.avg_price), reason='forced sell validation')
                status_result = client.get_order(result.order_id) if result.order_id else None
                payload = {
                    'symbol': pos.symbol,
                    'qty': int(sell_qty),
                    'price': float(pos.avg_price),
                    'success': result.success,
                    'message': result.message,
                    'order_id': result.order_id,
                    'order_status': result.status,
                    'dealt_qty': result.dealt_qty,
                    'dealt_avg_price': result.dealt_avg_price,
                    'status_check': None if not status_result else {
                        'success': status_result.success,
                        'message': status_result.message,
                        'order_id': status_result.order_id,
                        'order_status': status_result.status,
                        'dealt_qty': status_result.dealt_qty,
                        'dealt_avg_price': status_result.dealt_avg_price,
                    },
                    'reconciliation': reconciliation.__dict__,
                }
    out = runs / 'futu_sim_sell_submit_demo.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
