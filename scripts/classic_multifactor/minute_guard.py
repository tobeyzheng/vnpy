from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Mapping


@dataclass(frozen=True)
class MinuteTradeGuardConfig:
    max_intraday_trades: int = 4
    entry_cooldown_minutes: int = 30
    min_hold_minutes: int = 20
    no_new_entry_after: str = "15:30"

    @classmethod
    def from_setting(cls, setting: Mapping[str, Any] | None) -> "MinuteTradeGuardConfig":
        """Factory method shared by backtest / CTA / flow / live_task.

        Keeps a single construction path so that a newly added knob only needs
        to be declared here, not in four places.
        """
        data = dict(setting or {})
        return cls(
            max_intraday_trades=int(data.get("max_intraday_trades", 0) or 0),
            entry_cooldown_minutes=int(data.get("entry_cooldown_minutes", 0) or 0),
            min_hold_minutes=int(data.get("min_hold_minutes", 0) or 0),
            no_new_entry_after=str(data.get("no_new_entry_after", "") or ""),
        )


@dataclass(frozen=True)
class MinuteGuardDecision:
    allowed: bool
    reason: str = ""


class MinuteTradeGuard:
    """Intraday frequency guard for 1m trading.

    The guard is intentionally state-light. Callers own the persisted state so the
    same guard can be reused in vn.py CTA strategy, lightweight backtest and SIM flows.
    """

    def __init__(self, config: MinuteTradeGuardConfig):
        self.config = config

    def can_enter(self, now: datetime, *, trade_times: list[datetime], last_trade_at: datetime | None, exchange_tz: str | None = None) -> MinuteGuardDecision:
        compare_now = self._to_exchange_time(now, exchange_tz)
        today_count = self._today_count(compare_now, trade_times, exchange_tz)
        if self.config.max_intraday_trades > 0 and today_count >= self.config.max_intraday_trades:
            return MinuteGuardDecision(False, "max_intraday_trades_reached")
        cutoff = self._cutoff_time()
        if cutoff and compare_now.time() >= cutoff:
            return MinuteGuardDecision(False, "no_new_entry_after_cutoff")
        if last_trade_at and self.config.entry_cooldown_minutes > 0:
            if now - last_trade_at < timedelta(minutes=self.config.entry_cooldown_minutes):
                return MinuteGuardDecision(False, "entry_cooldown")
        return MinuteGuardDecision(True, "ok")

    def can_exit(self, now: datetime, *, entry_at: datetime | None, hard_exit: bool = False) -> MinuteGuardDecision:
        if hard_exit:
            return MinuteGuardDecision(True, "hard_exit")
        if entry_at and self.config.min_hold_minutes > 0:
            if now - entry_at < timedelta(minutes=self.config.min_hold_minutes):
                return MinuteGuardDecision(False, "min_hold_minutes")
        return MinuteGuardDecision(True, "ok")

    def _today_count(self, now: datetime, trade_times: list[datetime], exchange_tz: str | None = None) -> int:
        target_date = now.date()
        count = 0
        for value in trade_times:
            converted = self._to_exchange_time(value, exchange_tz) if exchange_tz else value
            if converted.date() == target_date:
                count += 1
        return count

    def _to_exchange_time(self, value: datetime, exchange_tz: str | None) -> datetime:
        if not exchange_tz:
            return value
        try:
            from zoneinfo import ZoneInfo
        except Exception:  # pragma: no cover - py<3.9 fallback path
            return value
        try:
            tz = ZoneInfo(exchange_tz)
        except Exception:
            return value
        if value.tzinfo is None:
            # Treat naive datetimes as UTC, matching vnpy DB convention.
            from datetime import timezone
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(tz)

    def _cutoff_time(self) -> time | None:
        text = str(self.config.no_new_entry_after or "").strip()
        if not text:
            return None
        hour, minute = text.split(":", 1)
        return time(int(hour), int(minute))
