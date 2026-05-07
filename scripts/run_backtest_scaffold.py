from __future__ import annotations

import json
from pathlib import Path

from services.backtest.engine import BacktestEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    result = BacktestEngine().run(
        strategy_name='adaptive_quant_engine_v1',
        symbols=['01810.HK', 'NVDA.US'],
        start='2025-01-01',
        end='2025-12-31',
    )
    out = {
        'strategy': 'adaptive_quant_engine_v1',
        'result': {
            'total_return_pct': result.total_return_pct,
            'max_drawdown_pct': result.max_drawdown_pct,
            'trade_count': result.trade_count,
            'notes': result.notes,
        }
    }
    path = repo / 'state' / 'runs' / 'backtest_scaffold_report.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
