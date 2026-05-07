from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from services.backtest.vnpy_adapter import VnpyBacktestAdapter


def run_one(adapter: VnpyBacktestAdapter, vt_symbol: str) -> dict:
    stats, _ = adapter.run(
        vt_symbol=vt_symbol,
        interval='1d',
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
        rate=0.0,
        slippage=0.05,
        size=1,
        pricetick=0.01 if 'SMART' in vt_symbol else 0.01,
        capital=20000,
    )
    return stats


def to_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    adapter = VnpyBacktestAdapter()
    symbols = ['SOXL.SMART', '00700.SEHK', '09988.SEHK', '01810.SEHK']
    results: dict[str, dict] = {}
    for s in symbols:
        results[s] = run_one(adapter, s)

    total_capital = sum(to_float(r.get('capital', 0)) for r in results.values())
    total_end_balance = sum(to_float(r.get('end_balance', 0)) for r in results.values())
    total_net_pnl = sum(to_float(r.get('total_net_pnl', 0)) for r in results.values())
    total_trade_count = sum(int(float(r.get('total_trade_count', 0))) for r in results.values())
    avg_sharpe = sum(to_float(r.get('sharpe_ratio', 0)) for r in results.values()) / len(results) if results else 0.0
    worst_dd = min(to_float(r.get('max_ddpercent', 0)) for r in results.values()) if results else 0.0

    report = {
        'portfolio': {
            'symbols': symbols,
            'start': '2024-01-01',
            'end': '2024-12-31',
            'total_capital': total_capital,
            'total_end_balance': total_end_balance,
            'total_net_pnl': total_net_pnl,
            'total_return_pct': ((total_end_balance / total_capital - 1) * 100) if total_capital else 0.0,
            'total_trade_count': total_trade_count,
            'avg_sharpe_ratio': avg_sharpe,
            'worst_max_ddpercent': worst_dd,
        },
        'per_symbol': results,
    }

    out = repo / 'state' / 'runs' / 'vnpy_portfolio_backtest_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(out)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
