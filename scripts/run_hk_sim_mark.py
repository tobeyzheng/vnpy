from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account_path = repo / 'state' / 'runs' / 'hk_sim_account.json'
    out_path = repo / 'state' / 'runs' / 'hk_sim_mark_report.json'
    store = SimAccountStore(account_path)
    account = store.load()
    engine = SimTradingEngine(lot_size_default=100)

    codes = [p.symbol for p in account.positions]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes)
    quote_map = {item['code'].replace('HK.', '') + '.HK': float(item['price']) for item in snapshot.get('items', []) if item.get('price') is not None}
    engine.mark_to_market(account, quote_map)
    store.save(account)

    report = {
        'cash': account.cash,
        'nav': account.nav,
        'positions': [p.__dict__ for p in account.positions],
        'quotes': quote_map,
        'snapshot_status': snapshot.get('status'),
        'snapshot_message': snapshot.get('message'),
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out_path)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
