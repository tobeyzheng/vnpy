from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from services.common import OrderState

from .models import ApprovalRecord, TradeStateRecord


class ApprovalStateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record: ApprovalRecord) -> Path:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = self.root / f'approval_state_{ts}.json'
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding='utf-8')
        return path


class TradeStateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record: TradeStateRecord) -> Path:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = self.root / f'trade_state_{ts}.json'
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding='utf-8')
        return path


class OrderStateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, state: OrderState) -> Path:
        path = self.root / f'{state.request_id}.json'
        path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def load(self, request_id: str) -> OrderState | None:
        path = self.root / f'{request_id}.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return OrderState(**data)

    def list(self) -> list[OrderState]:
        states: list[OrderState] = []
        for path in sorted(self.root.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                states.append(OrderState(**data))
            except Exception:
                continue
        return states

    def summary(self) -> dict[str, int]:
        states = self.list()
        open_status = {'created', 'validated', 'risk_checked', 'approval_required', 'approved', 'submitting', 'submitted', 'partial_filled', 'cancel_requested'}
        failed_status = {'rejected', 'expired', 'failed'}
        return {
            'total': len(states),
            'open': sum(1 for s in states if s.status in open_status),
            'failed': sum(1 for s in states if s.status in failed_status),
            'filled': sum(1 for s in states if s.status == 'filled'),
            'reconciled': sum(1 for s in states if s.status == 'reconciled'),
        }
