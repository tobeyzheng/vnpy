from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

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
