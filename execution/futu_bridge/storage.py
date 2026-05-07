from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import FutuOrderDraft


class FutuDraftStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, market: str, drafts: Iterable[FutuOrderDraft]) -> Path:
        day = datetime.now().strftime("%Y%m%d")
        path = self.root / f"futu_drafts_{market}_{day}.json"
        payload = [asdict(item) for item in drafts]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
