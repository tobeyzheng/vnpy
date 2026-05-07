from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider
from services.knot_runtime.remote_runtime import RemoteKnotAgentRuntime
from services.sim_account import SimAccountStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account = SimAccountStore(repo / 'state' / 'runs' / 'hk_sim_account.json').load()
    codes = [p.symbol for p in account.positions]
    watch = FutuAccountProvider().get_watchlist_snapshot(codes) if codes else {'items': []}
    watch_map = {i['code'].replace('HK.', '') + '.HK': i for i in watch.get('items', []) if i.get('price') is not None}
    runtime = RemoteKnotAgentRuntime(repo)
    decisions = []
    for p in account.positions:
        q = watch_map.get(p.symbol)
        if not q:
            continue
        current = float(q['price'])
        pnl_pct = (current - p.avg_price) / p.avg_price if p.avg_price else 0.0
        payload = {
            'trend_score': 0.7 if pnl_pct >= 0 else 0.55,
            'rsi': 60 + min(25, abs(float(q.get('change_pct') or 0)) * 3),
            'risk_score': 0.4 if abs(pnl_pct) < 0.05 else 0.72,
            'capital_score': 0.7,
            'has_event_catalyst': False,
            'near_resistance': float(q.get('change_pct') or 0) > 5,
            'moving_average_bullish': pnl_pct > 0,
            'missing_fields': 0,
        }
        item = runtime.evaluate_symbol(symbol=p.symbol, market='hong_kong', payload=payload, task_type='strategy_select')
        item['position'] = {'qty': p.qty, 'avg_price': p.avg_price, 'current_price': current, 'pnl_pct': pnl_pct}
        decisions.append(item)
    out = {'market': 'hong_kong', 'mode': 'intraday', 'results': decisions}
    path = runtime.save_raw_output('knot_agent_intraday_decision_hk.json', out)
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
