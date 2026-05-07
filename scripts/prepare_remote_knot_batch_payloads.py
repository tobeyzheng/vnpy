from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.knot_runtime.remote_runtime import RemoteKnotAgentRuntime
from services.sim_account import SimAccountStore


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runtime = RemoteKnotAgentRuntime(repo)
    runs = repo / 'state' / 'runs'

    # refresh payloads
    rows = json.loads((runs / 'candidate_inputs.json').read_text(encoding='utf-8'))
    hk = [r for r in rows if r.get('market') == 'hong_kong'][:15]
    codes = [r['symbol'] for r in hk]
    quote = FutuQuoteClient().get_snapshot(codes)
    watch = FutuAccountProvider().get_watchlist_snapshot(codes)
    raw_map = {r['code'].replace('HK.', '') + '.HK': r for r in quote if r.get('last_price') is not None}
    watch_map = {i['code'].replace('HK.', '') + '.HK': i for i in watch.get('items', []) if i.get('price') is not None}
    refresh_tasks = []
    for row in hk:
        sym = row['symbol']
        rr = raw_map.get(sym)
        ww = watch_map.get(sym)
        if not rr or not ww:
            continue
        payload = {
            'trend_score': float(row.get('raw_score', 0.6) or 0.6),
            'rsi': 50 + min(35, abs(float(ww.get('change_pct') or 0)) * 4),
            'risk_score': 0.45 if float(ww.get('turnover') or 0) > 1e8 else 0.58,
            'capital_score': 0.75 if float(ww.get('turnover') or 0) > 1e8 else 0.55,
            'has_event_catalyst': '主题' in (row.get('rationale') or '') or '催化' in (row.get('rationale') or ''),
            'near_resistance': abs(float(ww.get('change_pct') or 0)) > 4,
            'moving_average_bullish': float(ww.get('change_pct') or 0) > 0,
            'missing_fields': 0,
        }
        refresh_tasks.append({'symbol': sym, 'market': 'hong_kong', 'task_type': 'evaluation', 'payload': payload, 'prompt': runtime.build_prompt(symbol=sym, market='hong_kong', payload=payload, task_type='evaluation')})

    # intraday payloads
    account = SimAccountStore(runs / 'hk_sim_account.json').load()
    pos_codes = [p.symbol for p in account.positions]
    snap = FutuAccountProvider().get_watchlist_snapshot(pos_codes) if pos_codes else {'items': []}
    snap_map = {i['code'].replace('HK.', '') + '.HK': i for i in snap.get('items', []) if i.get('price') is not None}
    intraday_tasks = []
    for p in account.positions:
        q = snap_map.get(p.symbol)
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
        intraday_tasks.append({'symbol': p.symbol, 'market': 'hong_kong', 'task_type': 'strategy_select', 'payload': payload, 'prompt': runtime.build_prompt(symbol=p.symbol, market='hong_kong', payload=payload, task_type='strategy_select')})

    out = {'refresh_tasks': refresh_tasks, 'intraday_tasks': intraday_tasks, 'integration_mode': 'openclaw-session-orchestrated-remote'}
    path = runs / 'remote_knot_batch_tasks.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps({'refresh_tasks': len(refresh_tasks), 'intraday_tasks': len(intraday_tasks), 'integration_mode': out['integration_mode']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
