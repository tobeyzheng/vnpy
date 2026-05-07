from __future__ import annotations

import json
from pathlib import Path

from services.futu_sim_trade import FutuSimTradeClient
from services.sim_account import SimAccountStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account = SimAccountStore(repo / 'state' / 'runs' / 'hk_sim_account.json').load()
    payload = {'success': False, 'message': 'no positions available', 'order_id': None}
    if account.positions:
        pos = account.positions[0]
        client = FutuSimTradeClient()
        result = client.submit_limit_order(pos.symbol, 'SELL', int(pos.qty), float(pos.avg_price), reason='forced sell validation')
        status_result = client.get_order(result.order_id) if result.order_id else None
        payload = {
            'symbol': pos.symbol,
            'qty': int(pos.qty),
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
        }
    out = repo / 'state' / 'runs' / 'futu_sim_sell_submit_demo.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
