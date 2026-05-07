from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from services.futu_account import FutuQuoteClient
from services.futu_account import FutuAccountProvider
from services.sim_account import SimAccountStore, SimTradingEngine
from services.strategy.candidate_provider import UnifiedCandidateProvider
from services.strategy.market_rules import get_market_rules
from services.strategy.raw_score import RawScoreEngine, RawScoreFeatures
from services.strategy.timing import EntryTimingEngine
from services.strategy.risk_guard import RiskGuard


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    account_path = repo / 'state' / 'runs' / 'hk_sim_account.json'
    report_path = repo / 'state' / 'runs' / 'hk_sim_task_report.json'
    account = SimAccountStore(account_path).load()
    engine = SimTradingEngine(lot_size_default=100)
    quote_client = FutuQuoteClient()

    candidates = UnifiedCandidateProvider(repo).load()
    hk_candidates = [c for c in candidates if c.get('market') == 'hong_kong'][:30]
    codes = [c['symbol'] for c in hk_candidates]
    snapshot = FutuAccountProvider().get_watchlist_snapshot(codes)
    raw_rows = quote_client.get_snapshot(codes)
    raw_map = {row['code'].replace('HK.', '') + '.HK': row for row in raw_rows if row.get('last_price') is not None}
    quote_map = {item['code'].replace('HK.', '') + '.HK': item for item in snapshot.get('items', []) if item.get('price') is not None}

    budget_per_trade = 20000.0
    raw_score_engine = RawScoreEngine()
    entry_engine = EntryTimingEngine()
    risk_guard = RiskGuard()
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
        market_rules = get_market_rules(c.get('market', 'hong_kong'))
        legacy = float(c.get('raw_score', 0.5) or 0.5)
        features = RawScoreFeatures(
            trend_score=legacy,
            momentum_score=max(0.0, min(1.0, 0.5 + float(item.get('change_pct') or 0) / 20.0)),
            flow_score=max(0.0, min(1.0, float(item.get('turnover') or 0) / 2e9)),
            quality_score=min(1.0, max(s.get('score', 0.5) for s in c.get('signals', [{'score': 0.5}]))),
            event_score=0.75 if ('主题' in (c.get('rationale') or '') or '催化' in (c.get('rationale') or '')) else 0.5,
            risk_penalty=0.65 if abs(float(item.get('change_pct') or 0)) > 6 else 0.35,
            legacy_score=legacy,
        )
        raw_score_v2 = raw_score_engine.score(features)
        timing = entry_engine.decide(
            trend_score=raw_score_v2,
            rsi=50 + min(35, abs(float(item.get('change_pct') or 0)) * 4),
            has_event_catalyst=('主题' in (c.get('rationale') or '') or '催化' in (c.get('rationale') or '')),
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
        sized_budget = max(float(row['price']) * int(row['lot_size']), budget_per_trade * max(0.25, float(row.get('suggested_size_pct', 0.0) or 0.0)))
        est_cost = engine.min_lot_cost(float(row['price']), lot_size=int(row['lot_size']))
        guard = risk_guard.can_open(account, symbol=row['symbol'], est_cost=est_cost)
        if row.get('entry_action') == 'watch_only':
            actions.append({'symbol': row['symbol'], 'action': 'watch_only', 'reason': row.get('entry_reason')})
        elif engine.can_open(account, budget_per_trade) and guard.allowed:
            order = engine.place_buy(account, row['symbol'], float(row['price']), row.get('rationale', ''), sized_budget, lot_size=int(row['lot_size']))
            actions.append({'symbol': row['symbol'], 'action': order.status, 'qty': order.qty, 'price': row['price'], 'lot_size': row['lot_size'], 'reason': order.reason})
        else:
            actions.append({'symbol': row['symbol'], 'action': 'blocked', 'reason': guard.reason if not guard.allowed else 'risk/budget limit'})

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
