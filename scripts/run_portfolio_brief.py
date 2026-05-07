from __future__ import annotations

import json
from pathlib import Path

from services.healthcheck import HealthcheckService
from services.portfolio.risk import PortfolioRiskGuard
from services.portfolio.summary import build_portfolio_summary


def load_json(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    runs = repo / 'state' / 'runs'
    hk = load_json(runs / 'hk_sim_close_report.json')
    us = load_json(runs / 'us_sim_close_report.json')
    summary = build_portfolio_summary([hk, us])
    risk_guard = PortfolioRiskGuard()
    hk_nav = float(hk.get('nav', 0) or 0)
    us_nav = float(us.get('nav', 0) or 0)
    hk_risk = risk_guard.check(total_nav=summary.total_nav, market_nav=hk_nav, total_positions=summary.position_count)
    us_risk = risk_guard.check(total_nav=summary.total_nav, market_nav=us_nav, total_positions=summary.position_count)
    health = HealthcheckService(repo).run()
    out = {
        'portfolio_summary': {
            'total_cash': summary.total_cash,
            'total_nav': summary.total_nav,
            'market_count': summary.market_count,
            'position_count': summary.position_count,
        },
        'portfolio_risk': {
            'hong_kong': {'allowed': hk_risk.allowed, 'reason': hk_risk.reason},
            'us': {'allowed': us_risk.allowed, 'reason': us_risk.reason},
        },
        'concentration': {
            'hong_kong_nav_pct': round(hk_nav / summary.total_nav, 4) if summary.total_nav else 0.0,
            'us_nav_pct': round(us_nav / summary.total_nav, 4) if summary.total_nav else 0.0,
        },
        'markets': {
            'hong_kong': {'cash': hk.get('cash'), 'nav': hk.get('nav'), 'positions': hk.get('positions', [])},
            'us': {'cash': us.get('cash'), 'nav': us.get('nav'), 'positions': us.get('positions', [])},
        },
        'healthcheck': health,
        'daily_brief': {
            'status': health.get('status'),
            'alerts': health.get('alerts', []),
            'execution_blocked': health.get('status') == 'blocked',
        },
    }
    path = runs / 'portfolio_brief.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
