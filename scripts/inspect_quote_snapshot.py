from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuQuoteClient


def main() -> None:
    client = FutuQuoteClient()
    rows = client.get_snapshot(['00700.HK', '300308.SZ', 'NVDA.US'])
    out = Path('state/runs/quote_snapshot_inspect.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out)
    print(json.dumps(rows[:3], ensure_ascii=False))


if __name__ == '__main__':
    main()
