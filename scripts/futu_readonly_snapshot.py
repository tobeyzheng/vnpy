from __future__ import annotations

import json
from pathlib import Path

from services.futu_account import FutuAccountProvider


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    provider = FutuAccountProvider()
    summary = provider.get_summary()
    out = {
        "status": summary.status,
        "account_count": summary.account_count,
        "env": summary.env,
        "total_assets": summary.total_assets,
        "cash": summary.cash,
        "buying_power": summary.buying_power,
        "positions": [p.__dict__ for p in summary.positions],
        "orders": [o.__dict__ for o in summary.orders],
        "message": summary.message,
    }
    path = repo_root / "state" / "runs" / "futu_readonly_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
