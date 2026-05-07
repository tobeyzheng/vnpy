from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class ReconciliationDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    blocking_level: str = "ok"
    diff_symbols: list[str] = field(default_factory=list)


class ReconciliationGuard:
    def __init__(self, path: str | Path, *, max_age_minutes: int = 60, fail_closed: bool = True):
        self.path = Path(path)
        self.max_age_minutes = max_age_minutes
        self.fail_closed = fail_closed

    def evaluate(self, *, side: str | None = None, symbol: str | None = None) -> ReconciliationDecision:
        if not self.path.exists():
            if self.fail_closed:
                return ReconciliationDecision(False, ["reconciliation file missing"], "block")
            return ReconciliationDecision(True, ["reconciliation file missing"], "warn")

        reasons: list[str] = []
        diff_symbols: list[str] = []
        if self._is_stale():
            reasons.append("reconciliation file stale")

        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not data.get("success", False):
            reasons.append(f"reconciliation source failed: {data.get('message')}")

        for row in data.get("diffs", []) or []:
            row_symbol = str(row.get("symbol", ""))
            if symbol and row_symbol != symbol:
                continue
            qty_match = bool(row.get("qty_match", False))
            sellable_match = bool(row.get("sellable_match", False))
            if not qty_match:
                reasons.append(f"qty mismatch: {row_symbol}")
                diff_symbols.append(row_symbol)
            if (side or "").upper() == "SELL" and not sellable_match:
                reasons.append(f"sellable mismatch: {row_symbol}")
                diff_symbols.append(row_symbol)

        if reasons:
            return ReconciliationDecision(False if self.fail_closed else True, reasons, "block" if self.fail_closed else "warn", sorted(set(diff_symbols)))
        return ReconciliationDecision(True, [], "ok", [])

    def _is_stale(self) -> bool:
        try:
            mtime = datetime.fromtimestamp(self.path.stat().st_mtime)
        except OSError:
            return True
        return datetime.now() - mtime > timedelta(minutes=self.max_age_minutes)

    def to_dict(self, decision: ReconciliationDecision) -> dict[str, Any]:
        return {
            "allowed": decision.allowed,
            "reasons": decision.reasons,
            "blocking_level": decision.blocking_level,
            "diff_symbols": decision.diff_symbols,
        }
