from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account_path = repo / 'state' / 'runs' / 'hk_sim_account.json'
    report_path = repo / 'state' / 'runs' / 'hk_sim_task_report.json'
    account = SimAccountStore(account_path).load()
    engine = SimTradingEngine(lot_size_default=100)

    candidates = json.loads((repo / 'state' / 'runs' / 'candidate_inputs.json').read_text(encoding='utf-8'))
    hk_candidates = [c for c in candidates if c.get('market') == 'hong_kong'][:10]
    codes = [c['symbol'] for c in hk_candidates]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes)
    quote_map = {item['code'].replace('HK.', '') + '.HK': float(item['price']) for item in snapshot.get('items', []) if item.get('price') is not None}

    actions = []
    budget_per_trade = 2000.0
    affordable_candidates = []
    filtered_out = []
    for c in hk_candidates:
        symbol = c['symbol']
        price = quote_map.get(symbol)
        if price is None:
            filtered_out.append({'symbol': symbol, 'reason': 'missing quote'})
            continue
        min_cost = engine.min_lot_cost(price, lot_size=100)
        if not engine.is_affordable(price, budget_per_trade, lot_size=100):
            filtered_out.append({'symbol': symbol, 'reason': f'one-lot cost {min_cost:.2f} exceeds budget {budget_per_trade:.2f}'})
            continue
        affordable_candidates.append((c, price))

    for c, price in affordable_candidates[:3]:
        symbol = c['symbol']
        if engine.can_open(account, budget_per_trade):
            order = engine.place_buy(account, symbol, price, c.get('rationale', ''), budget_per_trade, lot_size=100)
            actions.append({'symbol': symbol, 'action': order.status, 'qty': order.qty, 'price': price, 'reason': order.reason})
        else:
            actions.append({'symbol': symbol, 'action': 'blocked', 'reason': 'risk/budget limit'})

    engine.mark_to_market(account, quote_map)
    SimAccountStore(account_path).save(account)

    report = {
        'task': 'hk_real_env_sim_trading_v1',
        'cash': account.cash,
        'nav': account.nav,
        'positions': [p.__dict__ for p in account.positions],
        'orders': [o.__dict__ for o in account.orders[-10:]],
        'actions': actions,
        'filtered_out': filtered_out,
        'quotes': quote_map,
        'snapshot_status': snapshot.get('status'),
        'snapshot_message': snapshot.get('message'),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(report_path)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
