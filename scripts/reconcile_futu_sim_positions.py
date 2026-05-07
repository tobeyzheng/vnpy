from __future__ import annotations

import json
from pathlib import Path

from services.futu_sim_trade import FutuSimTradeClient
from services.sim_account import SimAccountStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account = SimAccountStore(repo / 'state' / 'runs' / 'hk_sim_account.json').load()
    client = FutuSimTradeClient()
    futu = client.get_positions()
    local_map = {p.symbol: {'qty': p.qty, 'avg_price': p.avg_price} for p in account.positions}
    futu_map = {p['symbol']: p for p in futu.get('items', [])}
    symbols = sorted(set(local_map) | set(futu_map))
    diffs = []
    for sym in symbols:
        local_qty = int(local_map.get(sym, {}).get('qty', 0) or 0)
        futu_qty = int(futu_map.get(sym, {}).get('qty', 0) or 0)
        can_sell_qty = int(futu_map.get(sym, {}).get('can_sell_qty', 0) or 0)
        diffs.append({
            'symbol': sym,
            'local_qty': local_qty,
            'futu_qty': futu_qty,
            'futu_can_sell_qty': can_sell_qty,
            'qty_match': local_qty == futu_qty,
            'sellable_match': local_qty <= can_sell_qty if can_sell_qty else local_qty == 0,
            'local_avg_price': local_map.get(sym, {}).get('avg_price'),
            'futu_cost_price': futu_map.get(sym, {}).get('cost_price'),
        })
    out = {
        'success': futu.get('success', False),
        'message': futu.get('message'),
        'diffs': diffs,
    }
    path = repo / 'state' / 'runs' / 'futu_sim_position_reconcile.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
