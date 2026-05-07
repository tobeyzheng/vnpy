from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from services.backtest.portfolio_engine import PortfolioBacktestEngine


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    engine = PortfolioBacktestEngine(initial_cash=80000.0, max_single_position_pct=0.25, max_total_positions=4, max_market_exposure_pct=0.65)
    report = engine.run(
        ['SOXL.SMART', '00700.SEHK', '09988.SEHK', '01810.SEHK'],
        datetime(2024, 1, 1),
        datetime(2024, 12, 31),
    )
    out = repo / 'state' / 'runs' / 'shared_cash_portfolio_backtest_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(out)
    print(json.dumps(report['portfolio'], ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
