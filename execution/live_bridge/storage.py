from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import LiveOrderRequest


class LiveOrderAuditStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, order: LiveOrderRequest, status: str, notes: list[str]) -> Path:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = self.root / f'live_order_audit_{ts}.json'
        payload = {
            'status': status,
            'notes': notes,
            'request_id': order.request_id,
            'mode': order.mode,
            'source': order.source,
            'order': asdict(order),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return path
