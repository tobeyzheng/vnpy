from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    store = SimAccountStore(repo / 'state' / 'runs' / 'hk_sim_account.json')
    account = store.load()
    engine = SimTradingEngine(lot_size_default=100)

    codes = [p.symbol for p in account.positions]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes) if codes else {'items': [], 'status': 'connected', 'message': 'no positions'}
    quote_map = {item['code'].replace('HK.', '') + '.HK': float(item['price']) for item in snapshot.get('items', []) if item.get('price') is not None}

    exit_actions = []
    for pos in list(account.positions):
        price = quote_map.get(pos.symbol, pos.avg_price)
        reason = engine.evaluate_exit_reason(pos, price)
        if reason:
            order = engine.place_sell(account, pos.symbol, price, reason)
            exit_actions.append(order.__dict__)

    engine.mark_to_market(account, quote_map)
    store.save(account)

    out = repo / 'state' / 'runs' / 'hk_sim_close_report.json'
    drawdown = 0.0
    if account.initial_cash > 0:
        drawdown = max(0.0, (account.initial_cash - account.nav) / account.initial_cash)
    report = {
        'task': 'hk_real_env_sim_trading_v1',
        'cash': account.cash,
        'nav': account.nav,
        'realized_pnl': account.realized_pnl,
        'drawdown_pct': drawdown,
        'drawdown_limit_pct': account.max_drawdown_limit_pct,
        'risk_status': 'stop_new_trades' if drawdown >= account.max_drawdown_limit_pct else 'normal',
        'positions': [p.__dict__ for p in account.positions],
        'orders': [o.__dict__ for o in account.orders[-20:]],
        'exit_actions': exit_actions,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
