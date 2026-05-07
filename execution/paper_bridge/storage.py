from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import PaperTradeIntent


class PaperIntentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, market: str, intents: Iterable[PaperTradeIntent]) -> Path:
        day = datetime.now().strftime("%Y%m%d")
        path = self.root / f"paper_intents_{market}_{day}.json"
        payload = [asdict(item) for item in intents]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
