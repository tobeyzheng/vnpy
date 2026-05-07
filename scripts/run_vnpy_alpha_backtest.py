from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from services.backtest.vnpy_alpha_adapter import VnpyAlphaBacktestAdapter


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    adapter = VnpyAlphaBacktestAdapter()
    stats, _engine = adapter.run(
        vt_symbols=['NVDA.US'],
        interval='1d',
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
        rates={'NVDA.US': 0.0},
        slippages={'NVDA.US': 0.05},
        sizes={'NVDA.US': 1},
        priceticks={'NVDA.US': 0.01},
        capitals={'NVDA.US': 20000},
    )
    out = repo / 'state' / 'runs' / 'vnpy_alpha_backtest_report.json'
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(out)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
