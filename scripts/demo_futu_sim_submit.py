from __future__ import annotations

import json
from pathlib import Path

from services.futu_sim_trade import FutuSimTradeClient


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    task_report = json.loads((repo / 'state' / 'runs' / 'hk_sim_task_report.json').read_text(encoding='utf-8'))
    if not task_report.get('task_candidate_pool'):
        payload = {'success': False, 'message': 'no task candidates available', 'order_id': None}
    else:
        row = task_report['task_candidate_pool'][0]
        client = FutuSimTradeClient()
        result = client.submit_limit_order(row['symbol'], 'BUY', int(row['lot_size']), float(row['price']), reason=row.get('rationale', ''))
        payload = {'symbol': row['symbol'], 'qty': int(row['lot_size']), 'price': float(row['price']), 'success': result.success, 'message': result.message, 'order_id': result.order_id}
    out = repo / 'state' / 'runs' / 'futu_sim_submit_demo.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
