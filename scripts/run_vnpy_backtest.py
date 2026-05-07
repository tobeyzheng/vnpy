from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from services.backtest.vnpy_adapter import VnpyBacktestAdapter


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    adapter = VnpyBacktestAdapter()
    stats, _engine = adapter.run(
        vt_symbol='SOXL.SMART',
        interval='1d',
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
        rate=0.0,
        slippage=0.05,
        size=1,
        pricetick=0.01,
        capital=20000,
    )
    out = repo / 'state' / 'runs' / 'vnpy_backtest_report.json'
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(out)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
