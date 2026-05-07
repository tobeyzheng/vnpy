from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine
from services.strategy.timing import ExitTimingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    store = SimAccountStore(repo / 'state' / 'runs' / 'hk_sim_account.json')
    account = store.load()
    engine = SimTradingEngine(lot_size_default=100)
    exit_engine = ExitTimingEngine()

    codes = [p.symbol for p in account.positions]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes) if codes else {'items': [], 'status': 'connected', 'message': 'no positions'}
    quote_map = {item['code'].replace('HK.', '') + '.HK': float(item['price']) for item in snapshot.get('items', []) if item.get('price') is not None}

    exit_actions = []
    for pos in list(account.positions):
        price = quote_map.get(pos.symbol, pos.avg_price)
        pnl_pct = (price - pos.avg_price) / pos.avg_price if pos.avg_price else 0.0
        timing = exit_engine.decide(pnl_pct=pnl_pct, rsi=70.0, trend_score=0.6 if pnl_pct < 0 else 0.72, risk_score=0.4 if abs(pnl_pct) < 0.05 else 0.78)
        reason = timing.action if timing.action in ('stop_loss', 'take_profit', 'trim_or_exit', 'reduce_risk') else None
        if reason:
            order = engine.place_sell(account, pos.symbol, price, reason)
            exit_actions.append(asdict(order))

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
        'positions': [asdict(p) for p in account.positions],
        'orders': [asdict(o) for o in account.orders[-20:]],
        'exit_actions': exit_actions,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
