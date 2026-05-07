from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from services.futu_account import FutuAccountProvider, FutuQuoteClient
from services.sim_account import SimAccountStore, SimTradingEngine
from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.market_rules import get_market_rules
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.timing import EntryTimingEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account_path = repo / 'state' / 'runs' / 'us_sim_account.json'
    report_path = repo / 'state' / 'runs' / 'us_sim_task_report.json'
    account = SimAccountStore(account_path).load()
    engine = SimTradingEngine(lot_size_default=1)
    quote_client = FutuQuoteClient()
    candidates = UnifiedCandidateProvider(repo).load()
    us_candidates = [c for c in candidates if c.get('market') == 'us'][:30]
    codes = [c['symbol'] for c in us_candidates]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes) if codes else {'items': [], 'status': 'connected', 'message': 'no candidates'}
    raw_rows = quote_client.get_snapshot(codes) if codes else []
    raw_map = {row['code'].replace('US.', '') + '.US': row for row in raw_rows if row.get('last_price') is not None}
    quote_map = {item['code'].replace('US.', '') + '.US': item for item in snapshot.get('items', []) if item.get('price') is not None}

    budget_per_trade = 5000.0
    raw_score_engine = RawScoreEngine()
    entry_engine = EntryTimingEngine()
    filtered_out = []
    task_candidate_pool = []

    for c in us_candidates:
        symbol = c['symbol']
        item = quote_map.get(symbol)
        raw = raw_map.get(symbol)
        if item is None or raw is None:
            filtered_out.append({'symbol': symbol, 'reason': 'missing quote'})
            continue
        price = float(item['price'])
        lot_size = 1
        min_cost = engine.min_lot_cost(price, lot_size=lot_size)
        if not engine.is_affordable(price, budget_per_trade, lot_size=lot_size):
            filtered_out.append({'symbol': symbol, 'reason': f'one-unit cost {min_cost:.2f} exceeds budget {budget_per_trade:.2f}', 'lot_size': lot_size})
            continue
        market_rules = get_market_rules('us')
        legacy = float(c.get('raw_score', 0.5) or 0.5)
        features = RawScoreFeatures(
            trend_score=legacy,
            momentum_score=max(0.0, min(1.0, 0.5 + float(item.get('change_pct') or 0) / 20.0)),
            flow_score=max(0.0, min(1.0, float(item.get('turnover') or 0) / 5e9)),
            quality_score=min(1.0, max(s.get('score', 0.5) for s in c.get('signals', [{'score': 0.5}]))),
            event_score=0.75 if ('AI' in (c.get('rationale') or '') or '催化' in (c.get('rationale') or '')) else 0.5,
            risk_penalty=0.65 if abs(float(item.get('change_pct') or 0)) > 6 else 0.35,
            legacy_score=legacy,
        )
        raw_score_v2 = raw_score_engine.score(features)
        timing = entry_engine.decide(
            trend_score=raw_score_v2,
            rsi=50 + min(35, abs(float(item.get('change_pct') or 0)) * 4),
            has_event_catalyst=('AI' in (c.get('rationale') or '') or '催化' in (c.get('rationale') or '')),
            near_resistance=abs(float(item.get('change_pct') or 0)) > 4,
            moving_average_bullish=float(item.get('change_pct') or 0) > 0,
        )
        score = 0.0
        score += raw_score_v2 * 100
        score += max(0.0, 10 - abs(float(item.get('change_pct') or 0)))
        score += min(10.0, float(item.get('turnover') or 0) / 1e9)
        score += 3.0 if timing.action != 'watch_only' else -4.0
        task_candidate_pool.append({
            'symbol': symbol,
            'name': c.get('name'),
            'price': price,
            'lot_size': lot_size,
            'change_pct': item.get('change_pct'),
            'turnover': item.get('turnover'),
            'task_score': round(score, 2),
            'rationale': c.get('rationale', ''),
            'raw_score_v2': raw_score_v2,
            'entry_action': timing.action,
            'entry_reason': timing.reason,
            'invalidator': timing.invalidator,
            'suggested_size_pct': timing.suggested_size_pct,
            'market_currency': market_rules.currency,
        })

    task_candidate_pool.sort(key=lambda x: x['task_score'], reverse=True)
    selected = task_candidate_pool[:3]
    actions = []
    for row in selected:
        if row.get('entry_action') == 'watch_only':
            actions.append({'symbol': row['symbol'], 'action': 'watch_only', 'reason': row.get('entry_reason')})
            continue
        sized_budget = max(float(row['price']) * int(row['lot_size']), budget_per_trade * max(0.25, float(row.get('suggested_size_pct', 0.0) or 0.0)))
        if engine.can_open(account, sized_budget):
            order = engine.place_buy(account, row['symbol'], float(row['price']), row.get('rationale', ''), sized_budget, lot_size=int(row['lot_size']))
            actions.append({'symbol': row['symbol'], 'action': order.status, 'qty': order.qty, 'price': row['price'], 'lot_size': row['lot_size'], 'reason': order.reason})
        else:
            actions.append({'symbol': row['symbol'], 'action': 'blocked', 'reason': 'risk/budget limit'})

    engine.mark_to_market(account, {k: float(v['price']) for k, v in quote_map.items()})
    SimAccountStore(account_path).save(account)
    report = {
        'task': 'us_real_env_sim_trading_v1',
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
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
