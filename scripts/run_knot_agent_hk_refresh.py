from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuQuoteClient, FutuAccountProvider
from services.knot_runtime.runtime import KnotAgentRuntime


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    rows = json.loads((repo / 'state' / 'runs' / 'candidate_inputs.json').read_text(encoding='utf-8'))
    hk = [r for r in rows if r.get('market') == 'hong_kong']
    codes = [r['symbol'] for r in hk]
    quote = FutuQuoteClient().get_snapshot(codes)
    watch = FutuAccountProvider().get_watchlist_snapshot(codes)
    raw_map = {r['code'].replace('HK.', '') + '.HK': r for r in quote if r.get('last_price') is not None}
    watch_map = {i['code'].replace('HK.', '') + '.HK': i for i in watch.get('items', []) if i.get('price') is not None}

    runtime = KnotAgentRuntime(repo)
    outputs = []
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
        outputs.append(runtime.evaluate_symbol(symbol=sym, market='hong_kong', payload=payload, task_type='evaluation'))

    out = {'market': 'hong_kong', 'results': outputs}
    path = runtime.save_raw_output('knot_agent_raw_output_hk.json', out)
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
