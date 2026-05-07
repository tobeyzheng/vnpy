from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import SimAccount


class SimAccountStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> SimAccount:
        if not self.path.exists():
            return SimAccount()
        data = json.loads(self.path.read_text(encoding='utf-8'))
        account = SimAccount(
            base_currency=data.get('base_currency', 'HKD'),
            initial_cash=float(data.get('initial_cash', 10000.0)),
            cash=float(data.get('cash', 10000.0)),
            nav=float(data.get('nav', 10000.0)),
            realized_pnl=float(data.get('realized_pnl', 0.0)),
            max_drawdown_limit_pct=float(data.get('max_drawdown_limit_pct', 0.20)),
        )
        for row in data.get('positions', []):
            account.positions.append(__import__('services.sim_account.models', fromlist=['SimPosition']).SimPosition(**row))
        for row in data.get('orders', []):
            account.orders.append(__import__('services.sim_account.models', fromlist=['SimOrder']).SimOrder(**row))
        return account

    def save(self, account: SimAccount) -> Path:
        self.path.write_text(json.dumps(asdict(account), ensure_ascii=False, indent=2), encoding='utf-8')
        return self.path
