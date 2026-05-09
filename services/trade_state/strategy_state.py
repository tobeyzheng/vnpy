from __future__ import annotations

"""Cross-process strategy state persistence for classic_multifactor live.

Historically each ``run.py live`` invocation started with empty memory, which
meant ``entry_at / entry_price / highest_close / last_signal / confirm_bars``
all reset to zero every 5 minutes. As a result:

* ``min_hold_minutes`` never fired (entry_at always None).
* ``atr_trailing_stop`` had no ``highest_close`` to work against.
* ``entry_cooldown_minutes`` collapsed to zero between loop ticks.

This module writes a tiny JSON blob under
``state/runs/classic_multifactor/<task_tag>_strategy_state.json`` so the next
invocation can rebuild these fields deterministically. The file is written with
``os.replace`` to guarantee atomic swap against concurrent readers.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class StrategyState:
    symbol: str = ""
    entry_at: str | None = None  # ISO-8601 in exchange tz
    entry_price: float = 0.0
    highest_close: float = 0.0
    last_signal: str = ""
    last_trade_at: str | None = None
    confirm_bars: int = 0
    today_trades_count: int = 0
    today_date: str | None = None  # YYYY-MM-DD exchange local
    audit: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            symbol=str(data.get("symbol", "") or ""),
            entry_at=(data.get("entry_at") or None),
            entry_price=float(data.get("entry_price", 0.0) or 0.0),
            highest_close=float(data.get("highest_close", 0.0) or 0.0),
            last_signal=str(data.get("last_signal", "") or ""),
            last_trade_at=(data.get("last_trade_at") or None),
            confirm_bars=int(data.get("confirm_bars", 0) or 0),
            today_trades_count=int(data.get("today_trades_count", 0) or 0),
            today_date=(data.get("today_date") or None),
            audit=list(data.get("audit") or []),
        )


class StrategyStateStore:
    """JSON-backed store keyed by ``(task_tag, symbol)``.

    Files live under::

        state/runs/classic_multifactor/<task_tag>_strategy_state.json

    Each file is a dict ``{symbol: StrategyState.to_dict()}``.
    """

    def __init__(self, repo_root: Path, task_tag: str):
        self.repo_root = Path(repo_root)
        self.task_tag = task_tag.strip() or "default"
        self.base_dir = self.repo_root / "state" / "runs" / "classic_multifactor"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / f"{self.task_tag}_strategy_state.json"

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def load_all(self) -> dict[str, StrategyState]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): StrategyState.from_dict(v if isinstance(v, dict) else {})
            for k, v in raw.items()
        }

    def get(self, symbol: str) -> StrategyState:
        all_ = self.load_all()
        return all_.get(symbol, StrategyState(symbol=symbol))

    # ------------------------------------------------------------------
    # Write helpers — always go through _save
    # ------------------------------------------------------------------
    def _save(self, all_: dict[str, StrategyState]) -> None:
        payload = {k: v.to_dict() for k, v in all_.items()}
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".strategy_state.", dir=str(self.base_dir))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    def update(self, symbol: str, mutate) -> StrategyState:
        all_ = self.load_all()
        current = all_.get(symbol, StrategyState(symbol=symbol))
        new_state = mutate(current) or current
        if not isinstance(new_state, StrategyState):
            new_state = current
        all_[symbol] = new_state
        self._save(all_)
        return new_state

    def set(self, symbol: str, state: StrategyState) -> None:
        all_ = self.load_all()
        all_[symbol] = state
        self._save(all_)

    def clear(self, symbol: str) -> None:
        all_ = self.load_all()
        if symbol in all_:
            all_[symbol] = StrategyState(symbol=symbol)
            self._save(all_)

    # ------------------------------------------------------------------
    # High-level lifecycle hooks
    # ------------------------------------------------------------------
    def update_on_buy(self, symbol: str, *, price: float, at: datetime, last_signal: str = "") -> StrategyState:
        def _mutate(state: StrategyState) -> StrategyState:
            state.symbol = symbol
            state.entry_at = at.isoformat()
            state.entry_price = float(price)
            state.highest_close = max(state.highest_close, float(price))
            state.last_signal = last_signal or "classic_multifactor_entry"
            state.last_trade_at = at.isoformat()
            state.today_trades_count += 1
            state.today_date = at.date().isoformat()
            return state

        return self.update(symbol, _mutate)

    def update_on_high(self, symbol: str, close_price: float) -> StrategyState | None:
        def _mutate(state: StrategyState) -> StrategyState:
            if close_price > state.highest_close:
                state.highest_close = float(close_price)
            return state

        return self.update(symbol, _mutate)

    def clear_on_sell(self, symbol: str, *, at: datetime) -> StrategyState:
        def _mutate(state: StrategyState) -> StrategyState:
            state.entry_at = None
            state.entry_price = 0.0
            state.highest_close = 0.0
            state.last_signal = "flat"
            state.last_trade_at = at.isoformat()
            return state

        return self.update(symbol, _mutate)

    def rollover_if_new_day(self, symbol: str, today: date) -> StrategyState:
        """Reset today-scoped counters when the exchange-local day flips."""

        def _mutate(state: StrategyState) -> StrategyState:
            today_iso = today.isoformat()
            if state.today_date != today_iso:
                state.today_date = today_iso
                state.today_trades_count = 0
            return state

        return self.update(symbol, _mutate)

    def append_audit(self, symbol: str, entry: dict[str, Any], *, keep: int = 100) -> None:
        def _mutate(state: StrategyState) -> StrategyState:
            state.audit.append(entry)
            if len(state.audit) > keep:
                state.audit = state.audit[-keep:]
            return state

        self.update(symbol, _mutate)

    def reconcile_with_futu(
        self,
        symbol: str,
        *,
        futu_entry_price: float | None,
        futu_qty: float | None,
        futu_last_trade_at: datetime | None,
        now: datetime,
        deviation_threshold: float = 0.05,
    ) -> StrategyState:
        """Reconcile local state with Futu account snapshot.

        Rule (requirements 2.4): if local ``entry_price`` deviates from the
        broker's cost basis by more than ``deviation_threshold`` (default 5%),
        the broker wins and the deviation is written into ``audit``.
        """
        def _mutate(state: StrategyState) -> StrategyState:
            # No broker data -> no-op.
            if futu_qty is None or futu_qty <= 0:
                if state.entry_price and not futu_qty:
                    # Broker says flat while we think we have a position.
                    state.audit.append({
                        "at": now.isoformat(),
                        "event": "local_position_cleared_from_broker",
                        "local_entry_price": state.entry_price,
                        "broker_qty": futu_qty,
                    })
                    state.entry_at = None
                    state.entry_price = 0.0
                    state.highest_close = 0.0
                    state.last_signal = "flat"
                return state

            broker_price = float(futu_entry_price or 0.0)
            if broker_price <= 0:
                return state

            if state.entry_price <= 0:
                # We had nothing locally — rebuild from broker snapshot.
                state.entry_price = broker_price
                state.entry_at = (futu_last_trade_at or now).isoformat()
                state.highest_close = max(state.highest_close, broker_price)
                state.last_signal = state.last_signal or "rebuilt_from_broker"
                state.audit.append({
                    "at": now.isoformat(),
                    "event": "rebuilt_from_broker",
                    "broker_entry_price": broker_price,
                    "broker_qty": futu_qty,
                })
                return state

            deviation = abs(broker_price - state.entry_price) / state.entry_price
            if deviation > deviation_threshold:
                state.audit.append({
                    "at": now.isoformat(),
                    "event": "entry_price_deviation_over_threshold",
                    "local_entry_price": state.entry_price,
                    "broker_entry_price": broker_price,
                    "deviation": round(deviation, 6),
                    "threshold": deviation_threshold,
                })
                state.entry_price = broker_price
                state.highest_close = max(state.highest_close, broker_price)
            return state

        return self.update(symbol, _mutate)


__all__ = ["StrategyState", "StrategyStateStore"]
