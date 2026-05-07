from __future__ import annotations

import json
from pathlib import Path
from typing import List

from services.signals import CandidateInput, RawSignal

from .base import CandidateProvider


class FileCandidateProvider(CandidateProvider):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_candidate_inputs(self, market: str) -> List[CandidateInput]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        result: List[CandidateInput] = []
        for row in data:
            if row.get("market") != market:
                continue
            signals = [
                RawSignal(
                    symbol=s["symbol"],
                    market=s["market"],
                    source=s["source"],
                    category=s["category"],
                    score=float(s["score"]),
                    summary=s["summary"],
                )
                for s in row.get("signals", [])
            ]
            result.append(
                CandidateInput(
                    symbol=row["symbol"],
                    market=row["market"],
                    name=row["name"],
                    rationale=row.get("rationale", ""),
                    risk=row.get("risk", ""),
                    raw_score=float(row.get("raw_score", 0)),
                    confidence_source=row.get("confidence_source", "file_provider"),
                    action_hint=row.get("action_hint", "继续观察"),
                    signals=signals,
                )
            )
        return result
