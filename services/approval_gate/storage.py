from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import ApprovalDecision


class ApprovalLogStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, market: str, decision: ApprovalDecision) -> Path:
        day = datetime.now().strftime('%Y%m%d')
        path = self.root / f"approval_{market}_{day}.json"
        path.write_text(json.dumps(asdict(decision), ensure_ascii=False, indent=2), encoding='utf-8')
        return path
