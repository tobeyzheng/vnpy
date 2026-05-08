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
    def __init__(
        self,
        path: str | Path,
        *,
        max_age_minutes: int = 60,
        fail_closed: bool = True,
        cold_start_sentinel: str | Path | None = None,
    ):
        self.path = Path(path)
        self.max_age_minutes = max_age_minutes
        self.fail_closed = fail_closed
        self.cold_start_sentinel = Path(cold_start_sentinel) if cold_start_sentinel else None

    def evaluate(self, *, side: str | None = None, symbol: str | None = None) -> ReconciliationDecision:
        if not self.path.exists():
            # Cold-start exemption: if a sentinel path is configured and has never
            # been created, treat this as first-time bootstrap and allow with warn.
            # Once the reconciliation file has ever been produced (sentinel exists),
            # a missing file must block under fail_closed semantics.
            if self.fail_closed and self.cold_start_sentinel is not None and not self.cold_start_sentinel.exists():
                return ReconciliationDecision(
                    True,
                    ["cold start: no reconciliation yet"],
                    "warn",
                )
            if self.fail_closed:
                return ReconciliationDecision(False, ["reconciliation file missing"], "block")
            return ReconciliationDecision(True, ["reconciliation file missing"], "warn")

        reasons: list[str] = []
        diff_symbols: list[str] = []
        if self._is_stale():
            reasons.append("reconciliation file stale")

        data = json.loads(self.path.read_text(encoding="utf-8"))
        # File exists → record the sentinel so subsequent missing-file cases block.
        self._touch_sentinel()
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

    def _touch_sentinel(self) -> None:
        if self.cold_start_sentinel is None:
            return
        try:
            self.cold_start_sentinel.parent.mkdir(parents=True, exist_ok=True)
            if not self.cold_start_sentinel.exists():
                self.cold_start_sentinel.write_text("", encoding="utf-8")
        except OSError:
            # Sentinel write failure must not break the guard; fall back silently.
            pass

    def to_dict(self, decision: ReconciliationDecision) -> dict[str, Any]:
        return {
            "allowed": decision.allowed,
            "reasons": decision.reasons,
            "blocking_level": decision.blocking_level,
            "diff_symbols": decision.diff_symbols,
        }
