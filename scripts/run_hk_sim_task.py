from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from services.futu_account import FutuQuoteClient
from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account_path = repo / 'state' / 'runs' / 'hk_sim_account.json'
    report_path = repo / 'state' / 'runs' / 'hk_sim_task_report.json'
    account = SimAccountStore(account_path).load()
    engine = SimTradingEngine(lot_size_default=100)
    quote_client = FutuQuoteClient()

    candidates = json.loads((repo / 'state' / 'runs' / 'candidate_inputs.json').read_text(encoding='utf-8'))
    hk_candidates = [c for c in candidates if c.get('market') == 'hong_kong'][:30]
    codes = [c['symbol'] for c in hk_candidates]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes)
    raw_rows = quote_client.get_snapshot(codes)
    raw_map = {row['code'].replace('HK.', '') + '.HK': row for row in raw_rows if row.get('last_price') is not None}
    quote_map = {item['code'].replace('HK.', '') + '.HK': item for item in snapshot.get('items', []) if item.get('price') is not None}

    budget_per_trade = 20000.0
    filtered_out = []
    task_candidate_pool = []

    for c in hk_candidates:
        symbol = c['symbol']
        item = quote_map.get(symbol)
        raw = raw_map.get(symbol)
        if item is None or raw is None:
            filtered_out.append({'symbol': symbol, 'reason': 'missing quote'})
            continue
        price = float(item['price'])
        lot_size = int(raw.get('lot_size') or 100)
        min_cost = engine.min_lot_cost(price, lot_size=lot_size)
        if not engine.is_affordable(price, budget_per_trade, lot_size=lot_size):
            filtered_out.append({'symbol': symbol, 'reason': f'one-lot cost {min_cost:.2f} exceeds budget {budget_per_trade:.2f}', 'lot_size': lot_size})
            continue
        score = 0.0
        score += max(0.0, float(c.get('raw_score', 0)) * 100)
        score += max(0.0, 10 - abs(float(item.get('change_pct') or 0)))
        score += min(10.0, float(item.get('turnover') or 0) / 1e9)
        task_candidate_pool.append({
            'symbol': symbol,
            'name': c.get('name'),
            'price': price,
            'lot_size': lot_size,
            'change_pct': item.get('change_pct'),
            'turnover': item.get('turnover'),
            'task_score': round(score, 2),
            'rationale': c.get('rationale', ''),
        })

    task_candidate_pool.sort(key=lambda x: x['task_score'], reverse=True)
    selected = task_candidate_pool[:3]

    actions = []
    for row in selected:
        if engine.can_open(account, budget_per_trade):
            order = engine.place_buy(account, row['symbol'], float(row['price']), row.get('rationale', ''), budget_per_trade, lot_size=int(row['lot_size']))
            actions.append({'symbol': row['symbol'], 'action': order.status, 'qty': order.qty, 'price': row['price'], 'lot_size': row['lot_size'], 'reason': order.reason})
        else:
            actions.append({'symbol': row['symbol'], 'action': 'blocked', 'reason': 'risk/budget limit'})

    engine.mark_to_market(account, {k: float(v['price']) for k, v in quote_map.items()})
    SimAccountStore(account_path).save(account)

    report = {
        'task': 'hk_real_env_sim_trading_v1',
        'cash': account.cash,
        'nav': account.nav,
        'positions': [asdict(p) for p in account.positions],
        'orders': [asdict(o) for o in account.orders[-10:]],
        'actions': actions,
        'task_candidate_pool': selected,
        'filtered_out': filtered_out,
        'snapshot_status': snapshot.get('status'),
        'snapshot_message': snapshot.get('message'),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(report_path)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
