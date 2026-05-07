from __future__ import annotations

import json
from pathlib import Path

from services.execution_guard.reconciliation import ReconciliationGuard
from services.futu_sim_trade import FutuSimTradeClient


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    task_report = json.loads((runs / 'hk_sim_task_report.json').read_text(encoding='utf-8'))
    if not task_report.get('task_candidate_pool'):
        payload = {'success': False, 'message': 'no task candidates available', 'order_id': None}
    else:
        row = task_report['task_candidate_pool'][0]
        reconciliation = ReconciliationGuard(runs / 'futu_sim_position_reconcile.json', fail_closed=True).evaluate(side='BUY', symbol=row['symbol'])
        if not reconciliation.allowed:
            payload = {'symbol': row['symbol'], 'success': False, 'message': 'reconciliation blocked: ' + ';'.join(reconciliation.reasons), 'order_id': None, 'reconciliation': reconciliation.__dict__}
        else:
            client = FutuSimTradeClient()
            result = client.submit_limit_order(row['symbol'], 'BUY', int(row['lot_size']), float(row['price']), reason=row.get('rationale', ''))
            status_result = client.get_order(result.order_id) if result.order_id else None
            payload = {'symbol': row['symbol'], 'qty': int(row['lot_size']), 'price': float(row['price']), 'success': result.success, 'message': result.message, 'order_id': result.order_id, 'order_status': getattr(result, 'status', None), 'dealt_qty': getattr(result, 'dealt_qty', None), 'dealt_avg_price': getattr(result, 'dealt_avg_price', None), 'status_check': None if not status_result else {'success': status_result.success, 'message': status_result.message, 'order_id': status_result.order_id, 'order_status': status_result.status, 'dealt_qty': status_result.dealt_qty, 'dealt_avg_price': status_result.dealt_avg_price}, 'reconciliation': reconciliation.__dict__}
    out = runs / 'futu_sim_submit_demo.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
